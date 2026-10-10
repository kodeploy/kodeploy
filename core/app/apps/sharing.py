"""앱 공유 — 접근 판정, 초대(이메일 또는 GitHub 아이디)·수락·거절, 멤버 관리.

권한은 세 단계다: owner(apps.owner_id) > editor > viewer.
  viewer: 상태·배포 이력·로그·메트릭을 본다
  editor: 거기에 더해 배포(재배포)와 환경변수
  owner : 나머지 전부 (터미널, DB, 저장소, 도메인, 삭제, 공유 관리)
라우트가 어느 단계를 요구하는지는 deploy 라우터의 의존성이 정한다. 기본은 owner라서 명시하지 않은 동작은
멤버에게 막혀 있다.
"""

import re
import uuid

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.apps.model import App, AppInvite, AppMember
from app.auth.model import User

ROLES = ("viewer", "editor")
RANK = {"viewer": 1, "editor": 2, "owner": 3}

# 한 앱에 붙일 수 있는 멤버+대기 초대 수. 무한정 쌓이는 것을 막는 상한이다.
MAX_SHARES_PER_APP = 10

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class ShareError(ValueError):
    """화면에 그대로 보여도 되는 이유."""


# 유저가 이 앱에서 갖는 단계. 접근 권한이 없으면 None — 호출하는 쪽이 404로 가려 존재 여부를 알리지 않는다.
def access(db: Session, user_id: uuid.UUID, app_id: uuid.UUID) -> tuple[App, str] | None:
    app = db.get(App, app_id)
    if app is None:
        return None
    if app.owner_id == user_id:
        return app, "owner"
    member = db.get(AppMember, (app_id, user_id))
    return (app, member.role) if member else None


# 플랫폼 관리자(users.role)의 최소 단계 — root는 모든 앱에서 주인과 같고, admin은 보기만 한다.
ADMIN_FLOOR = {"root": "owner", "admin": "viewer"}


# access에 관리자 단계를 더한다: (앱, 단계, 관리자 권한으로 들어왔는가). 자기 단계(주인·멤버)가 같거나 높으면 그쪽이고
# 관리자 표시는 False다. 관리자 권한으로 바꾼 동작은 호출한 쪽이 기록한다 (admin/audit.py).
def access_for(db: Session, user: User, app_id: uuid.UUID) -> tuple[App, str, bool] | None:
    app = db.get(App, app_id)
    if app is None:
        return None
    own = None
    if app.owner_id == user.id:
        own = "owner"
    else:
        member = db.get(AppMember, (app_id, user.id))
        own = member.role if member else None
    floor = ADMIN_FLOOR.get(user.role or "")
    if floor and (own is None or RANK[floor] > RANK[own]):
        return app, floor, True
    return (app, own, False) if own else None


# 내 앱 + 공유받은 앱. (앱, 내 단계, 주인 아이디) — 내 앱이 먼저, 각각 만든 순서.
def list_accessible(db: Session, user_id: uuid.UUID) -> list[tuple[App, str, str | None]]:
    owned = db.query(App).filter(App.owner_id == user_id).order_by(App.created_at, App.id).all()
    shared = (
        db.query(App, AppMember.role)
        .join(AppMember, AppMember.app_id == App.id)
        .filter(AppMember.user_id == user_id)
        .order_by(App.created_at, App.id)
        .all()
    )
    owner_ids = {a.owner_id for a, _ in shared}
    logins = dict(db.query(User.id, User.login).filter(User.id.in_(owner_ids)).all()) if owner_ids else {}
    return [(a, "owner", None) for a in owned] + [(a, role, logins.get(a.owner_id)) for a, role in shared]


def _parse_target(target: str) -> tuple[str | None, str | None]:
    t = (target or "").strip()
    if not t:
        raise ShareError("초대할 이메일 또는 GitHub 아이디를 입력하세요")
    if "@" in t and not t.startswith("@"):
        if not _EMAIL_RE.match(t):
            raise ShareError("올바른 이메일이 아닙니다")
        return t.lower(), None
    login = t.removeprefix("@")
    if not _LOGIN_RE.match(login):
        raise ShareError("올바른 GitHub 아이디가 아닙니다")
    return None, login.lower()


def _user_for(db: Session, email: str | None, login: str | None) -> User | None:
    if email:
        return db.query(User).filter(func.lower(User.email) == email).first()
    return db.query(User).filter(func.lower(User.login) == login).first()


def invite(db: Session, app: App, inviter: User, target: str, role: str) -> AppInvite:
    if role not in ROLES:
        raise ShareError("권한은 보기(viewer) 또는 편집(editor)입니다")
    email, login = _parse_target(target)

    existing_user = _user_for(db, email, login)
    if existing_user is not None:
        if existing_user.id == app.owner_id:
            raise ShareError("앱 주인입니다")
        if db.get(AppMember, (app.id, existing_user.id)):
            raise ShareError("이미 이 앱의 멤버입니다")

    pending = db.query(AppInvite).filter(AppInvite.app_id == app.id)
    if pending.filter(AppInvite.email == email if email else AppInvite.github_login == login).first():
        raise ShareError("이미 초대했습니다 — 상대의 수락을 기다리고 있어요")
    members = db.query(AppMember).filter(AppMember.app_id == app.id).count()
    if members + pending.count() >= MAX_SHARES_PER_APP:
        raise ShareError(f"한 앱에는 멤버와 초대를 합쳐 최대 {MAX_SHARES_PER_APP}명까지 붙일 수 있습니다")

    row = AppInvite(app_id=app.id, role=role, invited_by=inviter.id, email=email, github_login=login)
    db.add(row)
    db.commit()
    return row


# 이 유저에게 온 초대 — 이메일 또는 GitHub 아이디가 맞는 것.
def _matches(inv: AppInvite, user: User) -> bool:
    return bool(
        (inv.email and user.email and inv.email == user.email.lower())
        or (inv.github_login and inv.github_login == user.login.lower())
    )


def pending_for(db: Session, user: User) -> list[tuple[AppInvite, App, str | None]]:
    conds = []
    if user.email:
        conds.append(AppInvite.email == user.email.lower())
    conds.append(AppInvite.github_login == user.login.lower())
    rows = db.query(AppInvite).filter(or_(*conds)).order_by(AppInvite.created_at).all()
    out = []
    for inv in rows:
        app = db.get(App, inv.app_id)
        if app is None:                      # 앱이 지워졌다 — 고아 초대는 건너뛴다
            continue
        owner = db.get(User, app.owner_id)
        out.append((inv, app, owner.login if owner else None))
    return out


def _my_invite(db: Session, invite_id: uuid.UUID, user: User) -> AppInvite:
    inv = db.get(AppInvite, invite_id)
    if inv is None or not _matches(inv, user):
        raise ShareError("초대를 찾을 수 없습니다")   # 남의 초대인지 없는 건지 알리지 않는다
    return inv


def accept(db: Session, invite_id: uuid.UUID, user: User) -> App:
    inv = _my_invite(db, invite_id, user)
    app = db.get(App, inv.app_id)
    if app is None:
        db.delete(inv)
        db.commit()
        raise ShareError("이미 삭제된 앱입니다")
    if app.owner_id != user.id and not db.get(AppMember, (app.id, user.id)):
        db.add(AppMember(app_id=app.id, user_id=user.id, role=inv.role))
    db.delete(inv)
    db.commit()
    return app


def decline(db: Session, invite_id: uuid.UUID, user: User) -> None:
    db.delete(_my_invite(db, invite_id, user))
    db.commit()


def cancel(db: Session, app: App, invite_id: uuid.UUID) -> None:
    inv = db.get(AppInvite, invite_id)
    if inv is None or inv.app_id != app.id:
        raise ShareError("초대를 찾을 수 없습니다")
    db.delete(inv)
    db.commit()


def members_of(db: Session, app: App) -> dict:
    rows = (
        db.query(AppMember, User)
        .join(User, User.id == AppMember.user_id)
        .filter(AppMember.app_id == app.id)
        .order_by(AppMember.created_at)
        .all()
    )
    invites = db.query(AppInvite).filter(AppInvite.app_id == app.id).order_by(AppInvite.created_at).all()
    return {
        "members": [
            {"user_id": str(u.id), "login": u.login, "avatar_url": u.avatar_url, "role": m.role}
            for m, u in rows
        ],
        "invites": [
            {"id": str(i.id), "email": i.email, "github_login": i.github_login, "role": i.role}
            for i in invites
        ],
    }


def set_role(db: Session, app: App, user_id: uuid.UUID, role: str) -> None:
    if role not in ROLES:
        raise ShareError("권한은 보기(viewer) 또는 편집(editor)입니다")
    m = db.get(AppMember, (app.id, user_id))
    if m is None:
        raise ShareError("멤버를 찾을 수 없습니다")
    m.role = role
    db.commit()


# 멤버를 내보내거나(주인) 스스로 나간다 (멤버 본인).
def remove_member(db: Session, app: App, user_id: uuid.UUID) -> None:
    m = db.get(AppMember, (app.id, user_id))
    if m is None:
        raise ShareError("멤버를 찾을 수 없습니다")
    db.delete(m)
    db.commit()


# 앱을 지울 때 공유 흔적도 같이 — 남기면 지워진 앱을 가리키는 멤버·초대가 쌓인다.
def purge(db: Session, app_id: uuid.UUID) -> None:
    db.query(AppMember).filter(AppMember.app_id == app_id).delete()
    db.query(AppInvite).filter(AppInvite.app_id == app_id).delete()
