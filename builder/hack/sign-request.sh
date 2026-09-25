#!/usr/bin/env bash
# 서명한 요청을 빌더에 보낸다 (지시서 3-3 규칙).
#
#   HMAC_SECRET=... hack/sign-request.sh POST   /internal/deploys hack/examples/build.json
#   HMAC_SECRET=... hack/sign-request.sh DELETE /internal/deploys/e2e00001
#
# BUILDER_URL 기본값은 http://127.0.0.1:8080 (kubectl port-forward svc/kodeploy-builder 8080:8080).
# 비밀값은 인자로 넘기지 않고 환경변수로만 읽는다 (ps에 보이지 않게).
set -euo pipefail

if [ $# -lt 2 ]; then
  sed -n '2,9p' "$0"
  exit 2
fi
method=$1
path=$2
file=${3:-}
url=${BUILDER_URL:-http://127.0.0.1:8080}
: "${HMAC_SECRET:?HMAC_SECRET is required}"
export HMAC_SECRET

body=""
if [ -n "$file" ]; then
  body=$(cat "$file")          # 보내는 바이트와 서명하는 바이트가 같아야 한다 (끝 줄바꿈 제거 포함)
fi
ts=$(date +%s)
sig=$(printf '%s\n%s\n%s\n%s' "$ts" "$method" "$path" "$body" | python3 -c '
import hashlib, hmac, os, sys
print(hmac.new(os.environ["HMAC_SECRET"].encode(), sys.stdin.buffer.read(), hashlib.sha256).hexdigest())')

args=(-sS -X "$method" "$url$path"
  -H "X-Kodeploy-Timestamp: $ts"
  -H "X-Kodeploy-Signature: $sig"
  -w '\nHTTP %{http_code}\n')
if [ -n "$body" ]; then
  args+=(-H "Content-Type: application/json" --data-binary "$body")
fi
curl "${args[@]}"
