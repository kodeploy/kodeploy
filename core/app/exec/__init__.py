"""kodeploy-exec 연결 (exec 게이트웨이 지시서 6-3). core는 pods/exec 대신 테넌트 ns의 kodeploy-exec에 요청한다.

client.py = 내부 문 클라이언트 (서명, 티켓 발급, query·browse·dump·restore 세션)
router.py = 웹용 POST /apps/{app_id}/exec-tickets, GET /apps/{app_id}/exec-features
"""
