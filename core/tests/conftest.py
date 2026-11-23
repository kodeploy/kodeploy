"""테스트 전체 공용 — 바깥 네트워크를 부르는 조회를 막는다."""

import pytest

from app.ci import service as ci_service


# "GitHub Actions 배포 허용"을 켤 때 GitHub에 저장소 ID를 묻는다. 테스트에서는 묻지 않고 "못 받음"으로 둔다 —
# 진짜로 부르면 acme/api 같은 이름이 실제 저장소와 겹쳐 결과가 바뀔 수 있다. ID를 보는 테스트는 이걸 다시 바꿔 끼운다.
@pytest.fixture(autouse=True)
def _no_github_repo_lookup(monkeypatch):
    monkeypatch.setattr(ci_service, "github_repo_id", lambda name, token=None: None)
