"""기계가 읽는 오류 코드가 붙은 HTTP 오류.

본문은 {"detail": "<사람이 읽는 이유>", "code": "<기계용 코드>"}다. detail은 문구가 바뀔 수 있어서 CLI·CI는 code로 갈라
처리한다 (코드 목록은 docs/deploy-api-contract.md). HTTPException을 이어받아서 처리기가 없는 앱(라우터만 붙인 테스트 앱)에서도
지금처럼 {"detail": ...}로 나간다 — code만 빠진다.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


async def _handle(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code, headers=exc.headers)


def install(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle)
