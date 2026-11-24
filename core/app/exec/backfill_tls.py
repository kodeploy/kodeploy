"""이미 있는 테넌트 ns에 kodeploy-exec-tls Secret을 채운다 (TLS로 옮길 때 한 번 돌리는 운영 작업).

새 ns는 배포 때 resources._ensure_tenant_ns가 HMAC 키와 함께 만든다. 이미 떠 있는 앱은 다음 배포 전까지 없어서,
core Pod 안에서 한 번 돌린다:  python -m app.exec.backfill_tls
이미 있는 ns는 그대로 둔다(409). 새로 만든 ns의 exec Pod는 인증서를 마운트하려면 다시 떠야 한다(차트 롤아웃).
"""

import sys

from kubernetes.client.exceptions import ApiException

from app.apps.model import App
from app.deploy.stack import manifests
from app.shared import k8s
from app.shared.db import SessionLocal


def main() -> int:
    with SessionLocal() as db:
        namespaces = sorted({a.namespace for a in db.query(App).all() if a.namespace})
    core = k8s.core_v1()
    failed = 0
    for ns in namespaces:
        try:
            core.create_namespaced_secret(namespace=ns, body=manifests.exec_tls(ns))
            print(ns, "created")
        except ApiException as e:
            if e.status == 409:
                print(ns, "exists")
            else:
                failed += 1
                print(ns, f"error {e.status}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
