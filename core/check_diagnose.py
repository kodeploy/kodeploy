"""게이트웨이 연동 계측기 검증 — 실 코드 경로(diagnose._call)로 왕복 1회.

프롬프트를 튜닝하기 전에 "계측기가 작동하는가"부터 확인한다. 이게 안 되는 상태에서
_PLATFORM_CONTEXT를 손보면 전부 헛수고다.

실행:
    cd core
    LLM_API_KEY='...' AI_DIAGNOSE=true .venv/bin/python check_diagnose.py

확인하는 것:
    1. 게이트웨이가 strict json_schema를 뒤쪽 모델까지 통과시키는가
    2. Diagnosis 스키마대로 파싱되는가 (필드 순서·enum 강제)
    3. cause_category ↔ kodeploy_specific 일관성
    4. 진단 내용이 실제로 KoDeploy 제약을 짚는가 (사람이 읽고 판단)
"""

import json
import sys

from app import config
from app.deploy.build import diagnose

# 정답을 아는 케이스 2개. 각각 sentinel(정답에만 나올 문자열)을 같이 둔다 —
# "그럴듯한가"는 주관적이라 판정 기준이 못 된다.
CASES = [
    {
        "name": "포트 불일치 (127.0.0.1 바인딩)",
        "expect_category": "port_mismatch",
        "sentinels": ["0.0.0.0", "포트"],
        "case": """\
이미지 빌드는 성공했지만, 컨테이너가 정상 기동에 실패해 배포가 타임아웃됐습니다.
플랫폼은 선택 포트로 TCP probe를 보내며, 응답이 없으면 이 상태가 됩니다.

## 선언된 스택
- 런타임: python
- 빌드 모드: dockerfile
- 선택 포트: 8000
- DB: none
- Redis: 미사용
- 리소스 상한: memory 600Mi / cpu 300m / ephemeral 1024Mi

## 앱 Pod 상태
- Pod phase: Running
- 재시작 횟수: 0

## 실제 빌드에 사용된 Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
USER 1000
CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]

## 현재 인스턴스 런타임 로그
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
""",
    },
    {
        # 실제로 일어나는 비-root 실패. securityContext가 UID 1000을 강제하므로 이미지의
        # USER 유무는 무관하고, 빌드 시 root로 만들어진 경로에 못 쓰는 게 진짜 증상이다.
        # "USER를 추가하세요"가 나오면 제약 목록이 여전히 틀렸다는 신호다.
        "name": "비-root — root 소유 경로 쓰기 실패(EACCES)",
        "expect_category": "non_root",
        "sentinels": ["chown", "1000"],
        "forbid": ["USER 지시어", "USER를 추가"],
        "case": """\
이미지 빌드는 성공했지만, 컨테이너가 정상 기동에 실패해 배포가 타임아웃됐습니다.

## 선언된 스택
- 런타임: javascript
- 빌드 모드: dockerfile
- 선택 포트: 3000

## 앱 Pod 상태
- Pod phase: Running
- 재시작 횟수: 4
- 현재 app: CrashLoopBackOff
- 직전 종료: Error (exit 1)

## 실제 빌드에 사용된 Dockerfile
FROM node:22-alpine
WORKDIR /app
COPY . .
RUN npm ci
RUN mkdir -p /app/uploads
EXPOSE 3000
CMD ["node", "server.js"]

## 현재 인스턴스 런타임 로그
node:internal/fs/utils:349
    throw err;
    ^
Error: EACCES: permission denied, open '/app/uploads/.keep'
    at Object.openSync (node:fs:596:3)
    at Object.writeFileSync (node:fs:2322:35)
    at Object.<anonymous> (/app/server.js:12:4)
""",
    },
]


def main() -> int:
    if not diagnose.is_configured():
        print("✗ is_configured()=False — LLM_API_KEY와 AI_DIAGNOSE=true 둘 다 필요합니다.")
        return 1

    print(f"base_url : {config.LLM_BASE_URL}")
    print(f"model    : {config.LLM_MODEL}\n")

    failed = 0
    for c in CASES:
        print("=" * 72)
        print(f"[{c['name']}]  기대 범주: {c['expect_category']}")
        # _call은 LLM 쪽 실패로 예외를 안 던진다 — 결말이 outcome으로 온다.
        # 레이턴시도 _call이 직접 재므로(운영에서 build_records에 남는 값과 동일 계측점)
        # 여기서 따로 재지 않는다.
        result = diagnose._call(c["case"])
        if result.outcome != "ok":
            print(f"  ✗ 호출 실패: outcome={result.outcome} ({result.latency_ms}ms)")
            failed += 1
            continue

        raw = result.payload
        d = json.loads(raw)
        hit = [s for s in c["sentinels"] if s in raw]
        # forbid: 나오면 안 되는 문구. 제약 목록이 틀렸을 때 모델이 그대로 따라간 흔적.
        bad = [s for s in c.get("forbid", []) if s in raw]
        ok_cat = d["cause_category"] == c["expect_category"]

        tokens = (
            f"{result.prompt_tokens}→{result.completion_tokens}"
            if result.prompt_tokens is not None
            else "(게이트웨이가 usage 미제공)"
        )
        print(f"  소요        : {result.latency_ms / 1000:.1f}s")
        print(f"  토큰        : {tokens}")
        print(f"  범주        : {d['cause_category']}  {'✓' if ok_cat else '✗ (기대와 다름)'}")
        print(f"  플랫폼 제약  : {d['kodeploy_specific']}  {'✗ 자기모순' if result.inconsistent else ''}")
        print(f"  sentinel    : {hit or '✗ 하나도 없음'}")
        if c.get("forbid"):
            print(f"  금지 문구    : {bad and '✗ ' + str(bad) or '✓ 없음'}")
        print(f"\n  제목: {d['title']}")
        print(f"  원인: {d['cause']}")
        print(f"  근거: {d['evidence']}")
        for i, s in enumerate(d["fix_steps"], 1):
            print(f"    {i}. {s['text']}" + (f"\n       $ {s['command']}" if s["command"] else ""))
        if d["alternative_label"]:
            print(f"  대안: {d['alternative_label']}")
            for i, s in enumerate(d["alternative_steps"], 1):
                print(f"    {i}. {s['text']}" + (f"\n       $ {s['command']}" if s["command"] else ""))
        print()
        if not ok_cat or not hit or bad:
            failed += 1

    print("=" * 72)
    if failed:
        print(f"✗ {failed}/{len(CASES)} 케이스가 기대와 다릅니다 — 위 내용을 읽고 판단할 것.")
        print("  스키마는 통과했는데 내용만 다르면 _PLATFORM_CONTEXT 보강 대상입니다.")
    else:
        print(f"✓ {len(CASES)}/{len(CASES)} 통과 — 게이트웨이 strict json_schema 정상,")
        print("  진단이 플랫폼 제약을 짚고 있습니다. AI_DIAGNOSE=true로 켜도 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
