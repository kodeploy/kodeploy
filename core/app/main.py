"""FastAPI 진입점. 도메인 라우터를 등록하고 시작 시 DB 마이그레이션을 실행한다."""

from contextlib import asynccontextmanager

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


from app.admin.router import router as admin_router
from app.auth.router import router as auth_router
from app.community.router import router as community_router
from app.config import ALLOWED_ORIGINS
from app.deploy.router import router as deploy_router
from app.internal.router import router as internal_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    command.upgrade(Config("alembic.ini"), "head")
    yield


app = FastAPI(title="KoDeploy", lifespan=lifespan)

# Cloudflare Pages 등 별도 도메인 프론트에서 호출 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(deploy_router)
app.include_router(community_router)
app.include_router(admin_router)
app.include_router(internal_router)


# K8s liveness/readiness probe 엔드포인트
@app.get("/healthz")
def healthz():
    return {"status": "ok"}
