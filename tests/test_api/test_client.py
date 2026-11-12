import httpx
import pytest

from jqcli.api.client import ApiClient
from jqcli.errors import ApiError, NetworkError, NotAuthenticatedError, NotFoundError


def make_client(handler):
    return ApiClient("https://example.test", token="tok", transport=httpx.MockTransport(handler))


def test_client_adds_auth_header():
    def handler(request):
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    assert client.get("/ping") == {"ok": True}


def test_client_sends_configured_cookie():
    def handler(request):
        assert request.headers["cookie"] == "sid=abc; theme=dark"
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=abc; theme=dark",
        transport=httpx.MockTransport(handler),
    )

    assert client.get("/ping") == {"ok": True}


@pytest.mark.parametrize(
    "cookie",
    [
        "sid=specific; sid=root",
        r'sid="abc\073def"',
    ],
)
def test_client_preserves_duplicate_and_quoted_cookie_header_values(cookie):
    def handler(request):
        assert request.headers["cookie"] == cookie
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie=cookie,
        transport=httpx.MockTransport(handler),
    )

    assert client.get("/ping") == {"ok": True}


@pytest.mark.parametrize("target", ["https://other.test/ping", "https://sub.example.test/ping"])
def test_client_rejects_cross_origin_request_before_sending_credentials(target):
    def handler(request):
        raise AssertionError("cross-origin request must not be sent")

    client = ApiClient(
        "https://example.test",
        cookie="sid=abc",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ApiError):
        client.get(target)


def test_client_rejects_automatic_redirect_following():
    client = make_client(lambda request: httpx.Response(200, json={"ok": True}))

    with pytest.raises(ApiError):
        client.get("/ping", follow_redirects=True)


def test_client_rejects_absolute_url_with_userinfo():
    client = make_client(lambda request: pytest.fail("userinfo URL must not be requested"))

    with pytest.raises(ApiError):
        client.get("https://user:password@example.test/ping")


def test_client_maps_malformed_absolute_url_to_api_error():
    client = make_client(lambda request: pytest.fail("malformed URL must not be requested"))

    with pytest.raises(ApiError):
        client.get("https://example.test:invalid/ping")


def test_client_keeps_cookies_set_during_session():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, text="ok", headers={"set-cookie": "research=ready; Path=/"})
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=abc",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/ping") == {"ok": True}
    assert set(requests[1].headers["cookie"].split("; ")) == {"sid=abc", "research=ready"}
    assert client.get_cookie("research") == "ready"
    assert client.get_cookie("missing") is None


def test_client_cookie_header_getter_is_path_aware_and_rejects_cross_origin():
    client = ApiClient(
        "https://example.test",
        cookie="sid=private",
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    client._client.cookies.set("narrow", "value", domain="example.test", path="/user/tester/")

    assert client.get_cookie_header("/outside") == "sid=private"
    assert set(client.get_cookie_header("/user/tester/api").split("; ")) == {
        "sid=private",
        "narrow=value",
    }
    with pytest.raises(ApiError):
        client.get_cookie_header("https://evil.example/capture")


def test_client_preserves_raw_initial_cookie_when_merging_a_distinct_session_cookie():
    requests = []
    initial = r'sid=specific;  sid=root; encoded="abc\073def"'

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            assert request.headers["cookie"] == initial
            return httpx.Response(200, text="ok", headers={"set-cookie": "research=ready; Path=/"})
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie=initial,
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/ping") == {"ok": True}
    assert requests[1].headers["cookie"] == f"{initial}; research=ready"


def test_client_replaces_initial_cookie_after_server_sets_the_same_name():
    requests = []
    initial = "prefs=O'Reilly; sid=old"

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, text="ok", headers={"set-cookie": "sid=new; Path=/"})
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie=initial,
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/ping") == {"ok": True}
    assert requests[1].headers["cookie"] == "prefs=O'Reilly; sid=new"


def test_client_never_restores_initial_cookie_after_root_session_value_expires():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text="ok",
                headers={"set-cookie": "sid=new; Max-Age=1; Path=/"},
            )
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=old; keep=yes",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    client._client.cookies.clear()  # Deterministically simulate expiry of sid=new.
    assert client.get("/next") == {"ok": True}
    assert requests[1].headers["cookie"] == "keep=yes"


def test_client_keeps_host_initial_cookie_beside_parent_domain_session_cookie():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text="ok",
                headers={"set-cookie": "sid=domain; Domain=.example.test; Path=/"},
            )
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://sub.example.test",
        cookie="sid=host",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/next") == {"ok": True}
    assert requests[1].headers["cookie"] == "sid=host; sid=domain"


def test_client_interleaves_narrow_initial_and_parent_domain_cookie_paths():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text="ok",
                headers=httpx.Headers(
                    [
                        ("set-cookie", "sid=narrow; Path=/research/"),
                        ("set-cookie", "sid=domain; Domain=.example.test; Path=/"),
                    ]
                ),
            )
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://sub.example.test",
        cookie="sid=host",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/research/item") == {"ok": True}
    assert requests[1].headers["cookie"] == "sid=narrow; sid=host; sid=domain"


def test_client_does_not_restore_an_initial_cookie_deleted_by_the_server():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text="ok",
                headers={"set-cookie": "sid=gone; Max-Age=0; Path=/"},
            )
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=old",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/ping") == {"ok": True}
    assert "cookie" not in requests[1].headers


@pytest.mark.parametrize(
    "set_cookie",
    [
        "sid=new; Domain=.test; Path=/",
        "sid=new; Path=relative",
        "sid=new; Max-Age=abc; Path=/",
    ],
)
def test_client_does_not_suppress_initial_cookie_for_rejected_set_cookie(set_cookie):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, text="ok", headers={"set-cookie": set_cookie})
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=old; keep=yes",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/next") == {"ok": True}
    assert requests[1].headers["cookie"] == "sid=old; keep=yes"


@pytest.mark.parametrize(
    "set_cookie",
    [
        "sid=gone; Max-Age=0; Domain=; Path=/",
        "sid=gone; Max-Age=0; Domain=.; Path=/",
        "sid=gone; Max-Age=0; Domain=..example.test; Path=/",
        "sid=gone; Max-Age=0; Domain=.example.test; Path=/",
    ],
)
def test_client_domain_deletion_does_not_remove_modeled_host_initial_cookie(set_cookie):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, text="ok", headers={"set-cookie": set_cookie})
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=host",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/next") == {"ok": True}
    assert requests[1].headers["cookie"] == "sid=host"


def test_client_models_initial_cookie_as_root_when_a_narrow_cookie_is_deleted():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text="ok",
                headers={"set-cookie": "sid=gone; Max-Age=0; Path=/research/"},
            )
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=root",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/research/item") == {"ok": True}
    assert requests[1].headers["cookie"] == "sid=root"


def test_client_orders_narrow_session_cookie_before_initial_root_cookie():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                text="ok",
                headers={"set-cookie": "sid=specific; Path=/research/"},
            )
        return httpx.Response(200, json={"ok": True})

    client = ApiClient(
        "https://example.test",
        cookie="sid=root",
        transport=httpx.MockTransport(handler),
    )

    client.get_text("/bootstrap")
    assert client.get("/research/item") == {"ok": True}
    assert requests[1].headers["cookie"] == "sid=specific; sid=root"
    assert client.get_cookie("sid", "/research/item") == "root"


def test_client_get_cookie_matches_the_actual_request_path():
    def handler(request):
        return httpx.Response(
            200,
            json={"ok": True},
            headers=httpx.Headers(
                [
                    ("set-cookie", "_xsrf=wrong; Path=/unrelated/very/long/path/"),
                    ("set-cookie", "_xsrf=correct; Path=/user/tester/"),
                ]
            ),
        )

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))
    client.get("/bootstrap")

    assert client.get_cookie("_xsrf", "/user/tester/api/contents/demo.ipynb") == "correct"
    assert client.get_cookie("_xsrf", "/unrelated/very/long/path/item") == "wrong"


def test_client_get_cookie_uses_server_last_wins_for_duplicate_matching_paths():
    def handler(request):
        return httpx.Response(
            200,
            json={"ok": True},
            headers=httpx.Headers(
                [
                    ("set-cookie", "_xsrf=specific; Path=/user/tester/"),
                    ("set-cookie", "_xsrf=root; Path=/"),
                ]
            ),
        )

    client = ApiClient("https://example.test", transport=httpx.MockTransport(handler))
    client.get("/bootstrap")

    assert client.get_cookie("_xsrf", "/user/tester/api/contents/demo.ipynb") == "root"


def test_client_get_cookie_decodes_quoted_value_like_the_server():
    client = ApiClient("https://example.test", cookie=r'_xsrf="abc\073def"')

    assert client.get_cookie("_xsrf", "/user/tester/api/contents/demo.ipynb") == "abc;def"


def test_client_patch_returns_json():
    def handler(request):
        assert request.method == "PATCH"
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)

    assert client.patch("/item", json={"name": "new"}) == {"ok": True}


def test_client_can_return_text_response():
    client = make_client(lambda request: httpx.Response(200, text="<html></html>"))

    assert client.get_text("/page") == "<html></html>"


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (302, ApiError),
        (401, NotAuthenticatedError),
        (403, NotAuthenticatedError),
        (404, NotFoundError),
        (500, ApiError),
    ],
)
def test_client_maps_error_status(status_code, error_type):
    client = make_client(lambda request: httpx.Response(status_code, json={"error": "x"}))

    with pytest.raises(error_type):
        client.get("/x")


def test_client_reports_gateway_forbidden_as_api_error():
    client = make_client(
        lambda request: httpx.Response(
            403,
            html="<html><center><h1>403 Forbidden</h1></center><hr><center>nginx</center></html>",
        )
    )

    with pytest.raises(ApiError, match="服务端网关拒绝访问") as caught:
        client.get("/private")
    assert caught.value.details == {"status_code": 403}


def test_client_maps_login_redirect_to_not_authenticated():
    client = make_client(
        lambda request: httpx.Response(302, headers={"location": "/user/login/index"})
    )

    with pytest.raises(NotAuthenticatedError):
        client.get("/private")


def test_client_can_return_redirect_for_explicit_manual_handling():
    client = make_client(
        lambda request: httpx.Response(307, headers={"location": "/next"})
    )

    response = client.request_response("POST", "/start", allow_redirect_status=True, data={"x": "y"})

    assert response.status_code == 307
    assert response.headers["location"] == "/next"


def test_client_maps_network_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    client = make_client(handler)

    with pytest.raises(NetworkError):
        client.get("/x")
