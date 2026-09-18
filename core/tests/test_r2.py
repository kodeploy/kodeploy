"""R2 모듈의 네트워크 무관 부분 고정 — 버킷 이름 규칙 + ListObjectsV2 XML 파서.

S3 호출 자체(SigV4)는 실 토큰 검증 영역이라 제외. 파서는 스토리지 패널 UI의
입력이라 네임스페이스/URL 인코딩 처리를 고정한다.
"""

import pytest

from app.deploy.stack import r2

_NS = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'


def test_bucket_name_prefix():
    # "kd-" 접두사: R2 최소 길이(3자) 보장 + KoDeploy 생성 버킷 식별
    assert r2.bucket_name("foo") == "kd-foo"


def test_parse_list_xml_truncated_with_url_encoding():
    xml = f"""<?xml version="1.0"?>
<ListBucketResult {_NS}>
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>tok==</NextContinuationToken>
  <Contents>
    <Key>img/a b.png</Key><Size>123</Size>
    <LastModified>2026-01-01T00:00:00Z</LastModified>
  </Contents>
</ListBucketResult>"""
    out = r2._parse_list_xml(xml, "https://pub.example")
    assert out["next"] == "tok=="
    obj = out["objects"][0]
    assert obj["key"] == "img/a b.png"
    assert obj["size"] == 123
    assert obj["url"] == "https://pub.example/img/a%20b.png"  # 공백 인코딩, '/' 보존


def test_parse_list_xml_not_truncated_and_no_public_base():
    xml = f"""<?xml version="1.0"?>
<ListBucketResult {_NS}>
  <IsTruncated>false</IsTruncated>
  <Contents><Key>k</Key><Size>abc</Size></Contents>
</ListBucketResult>"""
    out = r2._parse_list_xml(xml, "")
    assert out["next"] is None              # truncated=false면 토큰 무시
    assert out["objects"][0]["size"] == 0   # 비숫자 Size 방어
    assert out["objects"][0]["url"] is None # public base 없으면 URL 없음


# --- 미리보기 본문 읽기 (get_object_text) ---
# 여기서 조용히 깨질 수 있는 것: 상한을 넘겨 파일을 통째로 들고 오는 것. 수백 MB짜리 로그를
# 미리보기로 열었을 때 core 메모리와 응답이 같이 터지므로, 상한과 truncated 플래그를 고정한다.

class _FakeStream:
    def __init__(self, chunks, status=200):
        self._chunks, self.status_code = chunks, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    def __init__(self, chunks, status=200):
        self._chunks, self._status = chunks, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None):
        return _FakeStream(self._chunks, self._status)


def _patch_client(monkeypatch, chunks, status=200):
    monkeypatch.setattr(r2, "_sign_request", lambda *a, **kw: ("https://x/y", {}))
    monkeypatch.setattr(r2.httpx, "Client", lambda **kw: _FakeClient(chunks, status))


def test_get_object_text_small_file_is_not_truncated(monkeypatch):
    _patch_client(monkeypatch, [b'{"a": 1}'])
    text, truncated = r2.get_object_text({}, "a.json")
    assert text == '{"a": 1}'
    assert truncated is False


def test_get_object_text_stops_at_limit(monkeypatch):
    # 상한의 몇 배짜리 파일 — 상한만큼만 들고 오고 truncated로 알린다
    _patch_client(monkeypatch, [b"x" * 1000] * 10)
    text, truncated = r2.get_object_text({}, "big.log", max_bytes=2500)
    assert len(text) == 2500
    assert truncated is True


def test_get_object_text_decodes_binary_without_crashing(monkeypatch):
    _patch_client(monkeypatch, [b"\xff\xfe\x00bad"])
    text, _ = r2.get_object_text({}, "x.zip")
    assert isinstance(text, str)          # 깨진 바이트도 예외 없이 문자열로


def test_get_object_text_missing_key_is_error(monkeypatch):
    _patch_client(monkeypatch, [b""])
    with pytest.raises(r2.R2Error):
        r2.get_object_text({}, "")


def test_get_object_text_404_is_error(monkeypatch):
    _patch_client(monkeypatch, [], status=404)
    with pytest.raises(r2.R2Error):
        r2.get_object_text({}, "gone.txt")
