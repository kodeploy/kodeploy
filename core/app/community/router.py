"""community 게시판 엔드포인트.

POST   /community           — 글 작성
GET    /community           — 글 목록 (비밀글은 본인/관리자만 content 노출)
GET    /community/blog      — velog 블로그 글 목록 (RSS 프록시, 캐시)
GET    /community/{id}      — 글 상세 + 댓글
POST   /community/{id}/comments — 댓글 작성
DELETE /community/{id}      — 글 삭제 (본인/관리자)
DELETE /community/comments/{id} — 댓글 삭제 (본인/관리자)
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import ADMIN_ROLES, get_current_user, get_current_user_optional
from app.auth.model import User
from app.community import blog
from app.community.model import Comment, Post
from app.community.schemas import (
    CommentCreate,
    CommentOut,
    PostCreate,
    PostDetail,
    PostOut,
)
from app.shared.db import get_db

router = APIRouter(prefix="/community", tags=["community"])

# 비밀글 열람·타인 글/댓글 삭제 가능 여부 — users.role 기반 (옛 ADMIN_LOGIN 하드코딩 대체)
def _is_admin(user: User) -> bool:
    return user.role in ADMIN_ROLES


def _resolve_login(db: Session, user_id) -> str:
    u = db.query(User).filter_by(id=user_id).first()
    return u.login if u else "unknown"


def _mask_post(db: Session, post: Post, user: User | None, comment_count: int) -> PostOut:
    mine = user is not None and post.user_id == user.id
    visible = not post.is_secret or mine or (user is not None and _is_admin(user))
    return PostOut(
        id=post.id,
        author=_resolve_login(db, post.user_id),
        content=post.content if visible else "비밀글입니다.",
        is_secret=post.is_secret,
        is_mine=mine,
        comment_count=comment_count,
        created_at=post.created_at,
    )


# 비밀글에 달린 댓글은 댓글 자체가 비밀이 아니어도 비밀로 본다 — 비밀글에 단 답변이 목록에서
# 그대로 보이면 글을 가린 의미가 없다. 비밀 댓글은 댓글 작성자 · 글 작성자 · 관리자에게만 보인다.
def _mask_comment(db: Session, comment: Comment, post: Post, user: User | None) -> CommentOut:
    mine = user is not None and comment.user_id == user.id
    secret = comment.is_secret or post.is_secret
    visible = (
        not secret
        or mine
        or (user is not None and (post.user_id == user.id or _is_admin(user)))
    )
    return CommentOut(
        id=comment.id,
        post_id=comment.post_id,
        author=_resolve_login(db, comment.user_id),
        content=comment.content if visible else "비밀 댓글입니다.",
        is_secret=secret,
        is_mine=mine,
        created_at=comment.created_at,
    )


@router.post("", response_model=PostOut)
def create_post(
    req: PostCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PostOut:
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="내용을 입력해주세요")
    post = Post(user_id=user.id, content=req.content.strip(), is_secret=req.is_secret)
    db.add(post)
    db.commit()
    db.refresh(post)
    return _mask_post(db, post, user, 0)


@router.get("", response_model=list[PostOut])
def list_posts(
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> list[PostOut]:
    posts = db.query(Post).order_by(Post.created_at.desc()).all()
    result = []
    for p in posts:
        count = db.query(Comment).filter_by(post_id=p.id).count()
        result.append(_mask_post(db, p, user, count))
    return result


# velog 블로그 글 목록 — RSS 프록시 (1시간 메모리 캐시). 실패 시 빈 리스트.
# 인증 불필요(공개 글) + /{post_id} GET보다 위에 등록 ("blog"가 post_id로 잡히지 않게).
@router.get("/blog")
def list_blog_posts() -> list[dict]:
    return blog.fetch_blog_posts()


@router.get("/{post_id}", response_model=PostDetail)
def get_post(
    post_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
) -> PostDetail:
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없습니다")
    mine = user is not None and post.user_id == user.id
    visible = not post.is_secret or mine or (user is not None and _is_admin(user))
    comments = db.query(Comment).filter_by(post_id=post_id).order_by(Comment.created_at).all()
    return PostDetail(
        id=post.id,
        author=_resolve_login(db, post.user_id),
        content=post.content if visible else "비밀글입니다.",
        is_secret=post.is_secret,
        is_mine=mine,
        comments=[_mask_comment(db, c, post, user) for c in comments],
        created_at=post.created_at,
    )


@router.post("/{post_id}/comments", response_model=CommentOut)
def create_comment(
    post_id: int,
    req: CommentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CommentOut:
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없습니다")
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="내용을 입력해주세요")
    comment = Comment(
        post_id=post_id,
        user_id=user.id,
        content=req.content.strip(),
        is_secret=req.is_secret,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _mask_comment(db, comment, post, user)


@router.delete("/{post_id}")
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    post = db.query(Post).filter_by(id=post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="글을 찾을 수 없습니다")
    if post.user_id != user.id and not _is_admin(user):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다")
    db.query(Comment).filter_by(post_id=post_id).delete()
    db.delete(post)
    db.commit()
    return {"status": "deleted"}


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    comment = db.query(Comment).filter_by(id=comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="댓글을 찾을 수 없습니다")
    if comment.user_id != user.id and not _is_admin(user):
        raise HTTPException(status_code=403, detail="삭제 권한이 없습니다")
    db.delete(comment)
    db.commit()
    return {"status": "deleted"}
