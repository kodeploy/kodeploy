"""deploy 도메인 DB CRUD."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.deploy.model import Build, SavedQuery


# build_id로 단건 조회 (user_id 주면 소유자 일치까지 검증 — 다른 user 빌드 마스킹)
def get_build(
    db: Session, build_id: str, user_id: "uuid.UUID | None" = None,
) -> Build | None:
    q = db.query(Build).filter_by(build_id=build_id)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.first()


# 빌드 목록 (user_id 주면 본인 것만). 최신순.
def list_builds(
    db: Session, user_id: "uuid.UUID | None" = None,
) -> list[Build]:
    q = db.query(Build)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.order_by(Build.created_at.desc()).all()


def get_build_by_app_name(
    db: Session, app_name: str, user_id: "uuid.UUID | None" = None,
) -> Build | None:
    q = db.query(Build).filter_by(app_name=app_name)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.order_by(Build.created_at.desc()).first()


# 새 빌드 row 생성/저장
def create_build(db: Session, build: Build) -> Build:
    db.add(build)
    db.commit()
    db.refresh(build)
    return build


# status/error/logs 등 부분 필드 갱신
def update_build(db: Session, build_id: str, **fields) -> None:
    db.query(Build).filter_by(build_id=build_id).update(fields)
    db.commit()


# 최신 **서버** 빌드 (정적 슬롯 빌드·env_change 이벤트 row 제외).
# AppLayout이 프론트에서 하는 판정(runtime!=="static" && kind!=="env_change")과 같은 규칙 —
# 앱의 현재 DB 종류처럼 "서버 슬롯의 선언"을 서버 쪽에서 물어볼 때 쓴다.
def get_server_build(db: Session, user_id: "uuid.UUID") -> Build | None:
    return (
        db.query(Build)
        .filter(
            Build.user_id == user_id,
            Build.runtime != "static",
            Build.kind != "env_change",
        )
        .order_by(Build.created_at.desc())
        .first()
    )


# ── 저장된 쿼리 (DB 콘솔) ────────────────────────────────────────────────────
# 전부 (user_id, app_name, db_type) 세 축을 WHERE에 건다. 단건 조회도 id만으로 찾지
# 않는다 — 남의 id를 찍어도 스코프에서 빠져 None이 되고, router가 404로 마스킹한다.

def list_saved_queries(
    db: Session, user_id: "uuid.UUID", app_name: str, db_type: str,
) -> list[SavedQuery]:
    return (
        db.query(SavedQuery)
        .filter_by(user_id=user_id, app_name=app_name, db_type=db_type)
        .order_by(SavedQuery.created_at.desc(), SavedQuery.id.desc())
        .all()
    )


def get_saved_query(
    db: Session, query_id: int, user_id: "uuid.UUID", app_name: str, db_type: str,
) -> SavedQuery | None:
    return (
        db.query(SavedQuery)
        .filter_by(id=query_id, user_id=user_id, app_name=app_name, db_type=db_type)
        .first()
    )


def count_saved_queries(
    db: Session, user_id: "uuid.UUID", app_name: str, db_type: str,
) -> int:
    return (
        db.query(SavedQuery)
        .filter_by(user_id=user_id, app_name=app_name, db_type=db_type)
        .count()
    )


def create_saved_query(
    db: Session, *, user_id: "uuid.UUID", app_name: str, db_type: str,
    name: str, sql: str,
) -> SavedQuery:
    row = SavedQuery(
        user_id=user_id, app_name=app_name, db_type=db_type,
        name=name, sql_text=sql,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# 부분 갱신 — None은 "안 건드림"이라 이름만/SQL만 바꾸는 요청을 그대로 받는다.
def update_saved_query(
    db: Session, row: SavedQuery, *, name: str | None = None, sql: str | None = None,
) -> SavedQuery:
    if name is not None:
        row.name = name
    if sql is not None:
        row.sql_text = sql
    db.commit()
    db.refresh(row)
    return row


def delete_saved_query(db: Session, row: SavedQuery) -> None:
    db.delete(row)
    db.commit()
