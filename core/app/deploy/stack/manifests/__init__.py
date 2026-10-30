"""K8s 매니페스트(dict)·Dockerfile 빌더 모음. 워크로드(Deployment·DB·Route)는 차트(kodeploy-charts)가 그린다.

build.py        = 정적 사이트 Dockerfile 본문
tenant.py       = 테넌트 ns 프로비저닝 (Namespace + core RoleBinding + ghcr-auth Secret)
dependencies.py = 의존성 Secret (mysql·postgres·redis 자격증명, R2) + 로컬 볼륨 PVC
"""

from app.deploy.stack.manifests.build import static_dockerfile
from app.deploy.stack.manifests.dependencies import mysql, postgres, redis, storage, volume
from app.deploy.stack.manifests.tenant import tenant

__all__ = [
    "mysql",
    "postgres",
    "redis",
    "static_dockerfile",
    "storage",
    "tenant",
    "volume",
]
