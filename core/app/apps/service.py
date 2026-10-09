"""앱 조회·생성 — core가 앱 속성을 읽고 쓰는 유일한 입구.

2단계까지는 유저당 앱이 하나라 get_user_app이 그 하나를 돌려준다. 앱을 여러 개 만들 수 있게 되면
호출하는 쪽이 app_id를 들고 get_app으로 바뀐다.
"""

import uuid

from sqlalchemy.orm import Session

from app.apps.model import App, Tier
from app.auth.model import User


def get_user_app(db: Session, owner_id: uuid.UUID) -> App | None:
    return db.query(App).filter(App.owner_id == owner_id).order_by(App.created_at, App.id).first()


def get_app(db: Session, app_id: uuid.UUID) -> App | None:
    return db.get(App, app_id)


def list_user_apps(db: Session, owner_id: uuid.UUID) -> list[App]:
    return db.query(App).filter(App.owner_id == owner_id).order_by(App.created_at, App.id).all()


# 유저가 소유한 앱 하나. 남의 앱이거나 없으면 None — 호출하는 쪽이 404로 가려서 존재 여부를 알리지 않는다.
def get_owned_app(db: Session, owner_id: uuid.UUID, app_id: uuid.UUID) -> App | None:
    return db.query(App).filter(App.id == app_id, App.owner_id == owner_id).first()


# 새 앱의 ns 이름. 유저의 첫 앱은 기존 tenant-<유저 hex8> 규칙을 그대로 쓰고(삭제 뒤 다시 만들어도 같은 ns),
# 이후 앱은 app-<앱 id hex8>이다 — 빌더 검증(^(tenant|app)-[a-f0-9]{8}$)이 둘 다 받는다.
def new_namespace(db: Session, owner_id: uuid.UUID, app_id: uuid.UUID) -> str:
    if db.query(App).filter(App.owner_id == owner_id).count() == 0:
        return f"tenant-{owner_id.hex[:8]}"
    return f"app-{app_id.hex[:8]}"


def create_app(db: Session, owner_id: uuid.UUID, name: str) -> App:
    app_id = uuid.uuid4()
    app = App(id=app_id, owner_id=owner_id, name=name, namespace=new_namespace(db, owner_id, app_id))
    db.add(app)
    db.commit()
    return app


# ns 이름에서 hex8 부분 — GHCR 이미지 경로(<hex8>/<app>)와 빌더 검증이 ns의 hex8을 기준으로 한다.
def namespace_hex(namespace: str) -> str:
    return namespace.split("-", 1)[1]


# 유저가 만들 수 있는 앱 수 (등급의 max_apps). None이면 무제한.
# 앱마다 ns·DB·Argo Application이 생겨 자원이 앱 수만큼 늘어서 등급으로 막는다.
# 등급 행이 없는 이례적인 경우(FK가 막아 오지 않는다)는 가장 보수적으로 1개.
def app_limit(db: Session, user: User) -> int | None:
    tier = db.get(Tier, user.tier)
    return tier.max_apps if tier else 1


def at_app_limit(db: Session, user: User) -> bool:
    limit = app_limit(db, user)
    return limit is not None and db.query(App).filter(App.owner_id == user.id).count() >= limit


# 이 앱이 지금까지 배포한 저장소들 (비교용 키). 편집 권한으로 초대받은 사람은 이 저장소로만 배포할 수 있다 —
# 주인의 GitHub 연결(installation)은 주인의 모든 저장소에 열려 있어서, 아무 저장소나 받게 두면 안 된다.
def app_repo_keys(db: Session, app: App) -> set[str]:
    from app.deploy.build.naming import repo_key
    from app.deploy.model import Build

    rows = db.query(Build.repo_url).filter(Build.app_id == app.id).distinct().all()
    return {repo_key(r[0]) for r in rows if r[0]}


# 이 앱의 저장소를 받을 때 쓸 GitHub 연결(installation id). 앱 주인의 것이다 — 편집 권한으로 초대받은 사람은
# 자기 연결로는 주인의 비공개 저장소를 볼 수 없다.
def repo_installation_id(db: Session, app: App) -> int | None:
    owner = db.get(User, app.owner_id)
    return owner.github_installation_id if owner else None
