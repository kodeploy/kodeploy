"""회원 탈퇴 — 내가 소유한 앱을 모두 지우고, 앱 밖에 남은 개인 데이터와 계정을 지운다.

순서가 중요하다: 앱 삭제가 K8s·R2·도메인 같은 외부 리소스를 먼저 치우므로 앱을 먼저 지운다.
앱 삭제 중 하나라도 실패하면 거기서 멈춰 계정을 남긴다 — 다시 탈퇴를 누르면 남은 앱부터 이어서 지운다.
앱 삭제 자체(status.delete_app)와 공유 정리(sharing.purge)는 앱 삭제 API와 같은 코드를 쓴다.
"""

import asyncio

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.apps import service as apps_service
from app.apps.model import AppInvite, AppMember
from app.auth.model import User, UserSession
from app.community.model import Comment, Post
from app.deploy import status
from app.deploy.build import v2
from app.deploy.model import Build, BuildRecord


# 앱 밖에 남은 이 유저의 데이터를 지우고 계정 행도 지운다 (동기 DB 작업).
# 지우는 것: 멤버십, 보낸·받은 초대, 로그인 세션, 커뮤니티 글·댓글, 빌드 운영 기록.
# 남의 앱에서 이 유저가 한 배포 기록(builds)은 그 앱의 이력이라 남기고, 이 유저를 가리키는 칸만 비운다.
def purge_user_rows(db: Session, user: User) -> None:
    db.query(AppMember).filter(AppMember.user_id == user.id).delete()
    conds = [AppInvite.invited_by == user.id, AppInvite.github_login == user.login.lower()]
    if user.email:
        conds.append(AppInvite.email == user.email.lower())
    db.query(AppInvite).filter(or_(*conds)).delete(synchronize_session=False)

    db.query(Comment).filter(Comment.user_id == user.id).delete()
    post_ids = [pid for (pid,) in db.query(Post.id).filter(Post.user_id == user.id).all()]
    if post_ids:
        db.query(Comment).filter(Comment.post_id.in_(post_ids)).delete(synchronize_session=False)
        db.query(Post).filter(Post.id.in_(post_ids)).delete(synchronize_session=False)

    db.query(BuildRecord).filter(BuildRecord.user_id == user.id).delete()
    db.query(Build).filter(Build.user_id == user.id).update({Build.user_id: None})
    db.query(UserSession).filter(UserSession.user_id == user.id).delete()
    db.delete(user)
    db.commit()


# 탈퇴. 못 하면 ValueError(화면에 보여도 되는 이유).
async def delete_account(db: Session, user: User) -> None:
    if user.role == "root":
        raise ValueError("root 계정은 탈퇴할 수 없습니다")
    for app in apps_service.list_user_apps(db, user.id):
        if v2.is_v2(app):
            await v2.request_delete(user, app)        # Application이 사라진 뒤에야 ns를 지운다
        await asyncio.to_thread(status.delete_app, db, app)   # K8s·R2 호출이 동기라 메인 루프를 막지 않게
    await asyncio.to_thread(purge_user_rows, db, user)
