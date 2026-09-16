"""_call의 결말(outcome) 매핑 고정 — 이 계측이 곧 대시보드의 분모다.

_call은 LLM 쪽 실패로 예외를 던지지 않는다. 실패도 세어야 할 결말이라, raise로 빠져나가면
호출 시도 자체가 build_records에서 사라져 성공률·잘림률의 분모가 없어지기 때문이다.
그 성질이 조용히 깨지면(어느 분기 하나가 다시 raise하면) 대시보드는 "문제 없음"처럼 보이므로
분기 5개를 전부 고정한다.

실 게이트웨이 호출은 check_diagnose.py의 영역 — 여기서는 응답 모양만 흉내 낸다.
"""

import json

import pytest

from app.deploy.build import diagnose


class _Msg:
    def __init__(self, parsed=None, refusal=None):
        self.parsed = parsed
        self.refusal = refusal


class _Choice:
    def __init__(self, finish_reason="stop", parsed=None, refusal=None):
        self.finish_reason = finish_reason
        self.message = _Msg(parsed, refusal)


class _Usage:
    def __init__(self, p=11, c=22):
        self.prompt_tokens = p
        self.completion_tokens = c


class _Resp:
    def __init__(self, choice, usage=None):
        self.choices = [choice]
        self.usage = usage


class _FakeClient:
    """openai.OpenAI 대역 — chat.completions.parse만 흉내."""

    def __init__(self, resp=None, exc=None):
        self._resp, self._exc = resp, exc
        outer = self

        class _Completions:
            def parse(self, **kwargs):
                if outer._exc:
                    raise outer._exc
                return outer._resp

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


@pytest.fixture
def fake(monkeypatch):
    def _install(resp=None, exc=None):
        monkeypatch.setattr(
            diagnose, "_get_client", lambda: _FakeClient(resp=resp, exc=exc)
        )

    return _install


def _diagnosis(category="non_root", kodeploy_specific=True):
    return diagnose.Diagnosis(
        cause="root 소유 경로에 쓰기 실패",
        evidence="EACCES: permission denied",
        cause_category=category,
        kodeploy_specific=kodeploy_specific,
        fix_steps=[
            diagnose.FixStep(
                text="쓰기 경로의 소유자를 UID 1000으로 바꾸세요.",
                command="COPY --chown=1000:1000 . .",
            )
        ],
        alternative_label="",
        alternative_steps=[],
        title="쓰기 경로의 소유권을 확인해 주세요.",
    )


def test_ok_carries_payload_category_and_usage(fake):
    fake(resp=_Resp(_Choice(parsed=_diagnosis()), usage=_Usage()))
    r = diagnose._call("case")
    assert r.outcome == "ok"
    assert json.loads(r.payload)["cause_category"] == "non_root"
    assert (r.prompt_tokens, r.completion_tokens) == (11, 22)
    assert r.cause_category == "non_root"
    assert r.inconsistent is False
    assert r.model == diagnose.config.LLM_MODEL


def test_length_is_recorded_not_raised(fake):
    # max_tokens에서 JSON이 잘린 경우 — 유저 화면엔 카드가 안 뜰 뿐이라
    # 이 축이 없으면 아무도 모른 채 지나간다. payload는 없지만 토큰은 남는다.
    fake(resp=_Resp(_Choice(finish_reason="length"), usage=_Usage()))
    r = diagnose._call("case")
    assert r.outcome == "length"
    assert r.payload is None
    assert r.prompt_tokens == 11


def test_refusal_is_recorded(fake):
    fake(resp=_Resp(_Choice(refusal="거절"), usage=_Usage()))
    assert diagnose._call("case").outcome == "refusal"


def test_parsed_none_is_parse_error(fake):
    # 게이트웨이가 strict json_schema를 뒤쪽 제공자까지 안 넘긴 경우의 대표 증상.
    fake(resp=_Resp(_Choice(parsed=None), usage=_Usage()))
    assert diagnose._call("case").outcome == "parse_error"


def test_api_exception_becomes_api_error(fake):
    fake(exc=RuntimeError("connection reset"))
    r = diagnose._call("case")
    assert r.outcome == "api_error"
    assert r.payload is None
    assert r.latency_ms >= 0          # 실패 호출도 소요시간은 남는다


def test_usage_absent_leaves_tokens_none(fake):
    # 게이트웨이가 usage를 안 실어 보내도 진단 자체는 성공이어야 한다.
    fake(resp=_Resp(_Choice(parsed=_diagnosis()), usage=None))
    r = diagnose._call("case")
    assert r.outcome == "ok"
    assert (r.prompt_tokens, r.completion_tokens) == (None, None)


def test_inconsistent_flag_set_when_category_contradicts_flag(fake):
    # 플랫폼 범주인데 kodeploy_specific=False → 자기모순. 결과는 그대로 쓰되 지표로 남긴다.
    fake(resp=_Resp(_Choice(parsed=_diagnosis("oom", kodeploy_specific=False))))
    r = diagnose._call("case")
    assert r.outcome == "ok"
    assert r.inconsistent is True


def test_user_category_with_flag_false_is_consistent(fake):
    fake(resp=_Resp(_Choice(parsed=_diagnosis("user_build_error", False))))
    assert diagnose._call("case").inconsistent is False


def test_platform_categories_cover_the_constraint_list():
    # _PLATFORM_CATEGORIES가 CauseCategory에서 유저측·미상만 뺀 나머지와 정확히 일치해야
    # 자기모순 판정이 성립한다. 범주를 추가하고 이 집합을 안 고치면 조용히 어긋난다.
    from typing import get_args

    all_categories = set(get_args(diagnose.CauseCategory))
    assert diagnose._PLATFORM_CATEGORIES | {
        "user_build_error",
        "user_runtime_crash",
        "unknown",
    } == all_categories
