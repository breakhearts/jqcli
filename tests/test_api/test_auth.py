import httpx
import pytest

from jqcli.api.auth import extract_page_token, login_with_password, reusable_session
from jqcli.errors import ApiError


def test_extract_page_token_from_window_data():
    html = '<script>window.tokenData={name:"token",value:"abc123"}</script>'

    assert extract_page_token(html) == "abc123"


def test_login_with_password_success(monkeypatch):
    def handler(request):
        if request.url.path == "/user/login/index":
            return httpx.Response(200, text='<script>window.tokenData={name:"token",value:"tok"}</script>')
        assert request.url.path == "/user/login/doLogin"
        assert b"CyLoginForm%5Busername%5D=u" in request.content
        assert b"CyLoginForm%5Bpwd%5D=p" in request.content
        return httpx.Response(200, json={"code": "00000", "data": {"user": {}}}, headers={"set-cookie": "sid=abc; Path=/"})

    class FakeClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), *args, **kwargs)

    monkeypatch.setattr("jqcli.api.auth.httpx.Client", FakeClient)

    result = login_with_password("https://example.test", "u", "p")

    assert result["payload"]["code"] == "00000"
    assert "sid=abc" in result["cookie"]


def test_login_with_password_failure(monkeypatch):
    def handler(request):
        if request.url.path == "/user/login/index":
            return httpx.Response(200, text='<script>window.tokenData={name:"token",value:"tok"}</script>')
        return httpx.Response(200, json={"code": "20000", "msg": "bad"})

    class FakeClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), *args, **kwargs)

    monkeypatch.setattr("jqcli.api.auth.httpx.Client", FakeClient)

    with pytest.raises(ApiError):
        login_with_password("https://example.test", "u", "p")


@pytest.mark.parametrize(("is_login", "expected"), [(0, None), (1, "sid=rotated")])
def test_reusable_session_checks_login_and_preserves_rotated_cookie(monkeypatch, is_login, expected):
    def handler(request):
        assert request.url.path == "/user/index/isLogin"
        assert request.headers["cookie"] == "sid=stored"
        return httpx.Response(
            200, json={"code": "00000", "data": {"isLogin": is_login}}, headers={"set-cookie": "sid=rotated; Path=/"}
        )

    class FakeClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("jqcli.api.auth.httpx.Client", FakeClient)
    assert reusable_session("sid=stored", timeout=30) == expected

