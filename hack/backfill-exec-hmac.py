#!/usr/bin/env python3
"""기존 테넌트 ns에 kodeploy-exec HMAC Secret(kodeploy-exec-hmac)을 만든다.

새 ns는 core가 _ensure_tenant_ns에서 같이 만든다. 이 스크립트는 그 코드가 들어가기 전에 생긴 ns용이고,
차트에 exec 리소스를 올리기 전에 한 번 돌리면 된다 (Secret이 없으면 exec Pod가 ContainerCreating에 머문다).

- 기본은 dry-run: 어느 ns에 없는지만 출력한다. --apply를 붙여야 만든다.
- 이미 있으면 건드리지 않는다 (키를 바꾸면 떠 있는 exec Pod와 어긋난다).
- 키 값은 출력하지 않는다. Secret 모양은 core와 같은 함수(manifests.exec_hmac)로 만든다.
- 관리자 kubeconfig로 돈다 (core SA에는 ns 목록 권한이 없다).

  core/.venv/bin/python hack/backfill-exec-hmac.py                 # dry-run
  core/.venv/bin/python hack/backfill-exec-hmac.py --apply
  core/.venv/bin/python hack/backfill-exec-hmac.py --context <ctx> --apply
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from kubernetes import client, config  # noqa: E402
from kubernetes.client.exceptions import ApiException  # noqa: E402

from app.deploy.stack.manifests import exec_hmac  # noqa: E402
from app.deploy.stack.manifests.tenant import EXEC_HMAC_SECRET  # noqa: E402

# deploy/k8s/core/admission-policy.yaml과 같은 규칙
TENANT_NS = re.compile(r"^(tenant|app)-[0-9a-f]{8}$")


def tenant_namespaces(core: client.CoreV1Api) -> list[str]:
    names = []
    for ns in core.list_namespace(label_selector="tenant").items:
        if not TENANT_NS.match(ns.metadata.name):
            continue
        if ns.status and ns.status.phase == "Terminating":
            continue
        names.append(ns.metadata.name)
    return sorted(names)


def has_secret(core: client.CoreV1Api, ns: str) -> bool:
    try:
        core.read_namespaced_secret(EXEC_HMAC_SECRET, ns)
        return True
    except ApiException as e:
        if e.status == 404:
            return False
        raise


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="없는 ns에 Secret을 만든다 (기본은 dry-run)")
    ap.add_argument("--context", help="kubeconfig context")
    args = ap.parse_args()

    config.load_kube_config(context=args.context)
    core = client.CoreV1Api()

    missing, present, created = [], 0, 0
    for ns in tenant_namespaces(core):
        if has_secret(core, ns):
            present += 1
            print(f"present  {ns}")
            continue
        if not args.apply:
            missing.append(ns)
            print(f"missing  {ns}  (dry-run: --apply로 만든다)")
            continue
        try:
            core.create_namespaced_secret(ns, exec_hmac(ns))
            created += 1
            print(f"created  {ns}")
        except ApiException as e:
            if e.status != 409:
                raise
            present += 1
            print(f"present  {ns}")

    mode = "apply" if args.apply else "dry-run"
    print(f"\n[{mode}] present={present} created={created} missing={len(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
