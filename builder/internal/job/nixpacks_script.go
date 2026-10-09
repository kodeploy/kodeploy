package job

// 원본 nixpacks_buildkit_job.yaml.j2의 init 컨테이너(nixpacks) 스크립트 — 글자 하나 바꾸지 않고 옮겼다
// (testdata/original-nixpacks-*.json이 그 렌더 결과이고 job_test.go가 둘을 비교한다).
// nixpacks를 받아 소스를 clone하고(private면 GIT_AUTH_TOKEN으로), 프로젝트 경로를 정하고(비면 자동 탐색),
// Dockerfile을 만들어 PSS restricted에 맞게 후처리한다. 결과는 /workspace에 남고 main(BuildKit)이 읽는다.
const nixpacksScript = `set -eu
# arch 자동 감지 — OCI ARM/x86 동시 지원
case "$(uname -m)" in
  x86_64) ARCH="x86_64-unknown-linux-musl" ;;
  aarch64) ARCH="aarch64-unknown-linux-musl" ;;
  *) echo "Unsupported arch: $(uname -m)"; exit 1 ;;
esac

echo "[1/5] downloading nixpacks ${NIXPACKS_VERSION} ($ARCH)..."
apk add --no-cache curl >/dev/null
curl -sSL "https://github.com/railwayapp/nixpacks/releases/download/${NIXPACKS_VERSION}/nixpacks-${NIXPACKS_VERSION}-${ARCH}.tar.gz" \
  | tar xz -C /usr/local/bin

echo "[2/5] cloning ${REPO_URL} (${BRANCH})..."
# private repo: 토큰을 URL rewrite(insteadOf)로 주입 — REPO_URL/로그엔 토큰 안 보임(set -x 꺼짐)
if [ -n "${GIT_AUTH_TOKEN:-}" ]; then
  git config --global url."https://x-access-token:${GIT_AUTH_TOKEN}@github.com/".insteadOf "https://github.com/"
fi
git clone --depth 1 -b "$BRANCH" "$REPO_URL" /workspace/src

# PROJECT_PATH 결정 — 사용자 명시값이 있으면 그대로, 없으면 자동 탐색.
# 자동 탐색: 표준 manifest 파일을 max-depth 3까지 검색해 가장 먼저 발견된 dirname.
# 결과를 로그에 명시 → 사용자가 "왜 이 디렉토리가 빌드됐는지" 알 수 있음.
echo "[3/5] resolving project path..."
if [ -z "$PROJECT_PATH" ]; then
  # awk로 path의 '/' 개수(=깊이) 세서 정렬 → 가장 root에 가까운 manifest 우선.
  # busybox find는 -printf 미지원이라 awk로 우회 (depth 정보를 path 안에 인코딩).
  DETECTED=$(find /workspace/src -maxdepth 3 -type f \
    \( -name pom.xml -o -name build.gradle -o -name build.gradle.kts \
       -o -name requirements.txt -o -name pyproject.toml -o -name Pipfile \
       -o -name package.json -o -name go.mod -o -name Cargo.toml \
       -o -name Gemfile -o -name composer.json \) 2>/dev/null \
    | awk -F/ '{print NF" "$0}' | sort -n | head -1 | cut -d' ' -f2- | xargs -r dirname || true)
  if [ -n "$DETECTED" ]; then
    PROJECT_PATH="${DETECTED#/workspace/src/}"
    [ "$PROJECT_PATH" = "/workspace/src" ] && PROJECT_PATH=""
    echo "  → auto-detected: ${PROJECT_PATH:-<root>} (found manifest in $DETECTED)"
  else
    echo "  → no manifest found, using <root> (nixpacks will likely fail)"
  fi
else
  echo "  → using user-specified: $PROJECT_PATH"
fi
FULL_PATH="/workspace/src${PROJECT_PATH:+/$PROJECT_PATH}"
echo -n "$FULL_PATH" > /workspace/PROJECT_PATH  # main container가 읽음

echo "[4/5] running nixpacks build..."
cd "$FULL_PATH"
nixpacks build . --out .
nixpacks plan . --format json > .nixpacks/plan.json

echo "[5/5] post-processing Dockerfile for PSS restricted..."
cat >> "$FULL_PATH/.nixpacks/Dockerfile" <<'PATCH_EOF'

# === KoDeploy: PSS restricted 호환 (Phase 0 검증됨) ===
ENV PATH=/opt/venv/bin:/root/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/usr/local/bin:/usr/bin:/bin:/sbin
RUN (id 1000 >/dev/null 2>&1 || useradd -u 1000 -m -d /home/kodeploy -s /bin/bash kodeploy) && \
    chown -R 1000:1000 /app /opt 2>/dev/null || true && \
    chmod -R a+rX /root/.nix-profile 2>/dev/null || true
USER 1000
ENTRYPOINT ["/bin/sh", "-c"]
PATCH_EOF

# 결과를 stdout으로 출력 — 백엔드가 init container 로그에서 추출
echo "===KODEPLOY_DOCKERFILE_START==="
cat "$FULL_PATH/.nixpacks/Dockerfile"
echo "===KODEPLOY_DOCKERFILE_END==="
echo "===KODEPLOY_PLAN_START==="
cat "$FULL_PATH/.nixpacks/plan.json"
echo "===KODEPLOY_PLAN_END==="
`
