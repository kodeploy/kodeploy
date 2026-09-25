"""원본 core 템플릿(buildkit_job.yaml.j2)을 렌더해 job 패키지의 골든 파일을 만든다.

    cd kodeploy && core/.venv/bin/python builder/hack/render-original-job.py cache > builder/internal/job/testdata/original-cache.json
    cd kodeploy && core/.venv/bin/python builder/hack/render-original-job.py nocache > builder/internal/job/testdata/original-nocache.json

매개변수는 internal/job/job_test.go의 goldenParams와 같아야 한다.
"""

import json
import os
import sys

mode = sys.argv[1]
os.environ["GHCR_USER"] = "yuntyu01"
os.environ["BUILD_REGISTRY_CACHE"] = "true" if mode == "cache" else "false"
os.environ.pop("BUILD_ACTIVE_DEADLINE_SECONDS", None)
os.environ.pop("BUILD_TIMEOUT_SECONDS", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "core"))

from app.deploy.stack.manifests.build import buildkit_job  # noqa: E402

if mode == "cache":
    subdir, filename = "backend", "Dockerfile.prod"
else:
    subdir, filename = "", "Dockerfile"

job = buildkit_job(
    build_id="3f9a2c1d",
    user_id="d6d8b75985524d6f9a9000665e7ca0da",
    image="ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:3f9a2c1d",
    repo_url="https://github.com/yuntyu01/kodeploy-test-spring.git",
    branch="r2-test-v1",
    dockerfile_subdir=subdir,
    dockerfile_filename=filename,
)
print(json.dumps(job, indent=2, sort_keys=True, ensure_ascii=False))
