from __future__ import annotations

import secrets
from http.cookiejar import Cookie as JarCookie
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import urlsplit

import httpx

from jqcli.errors import ApiError, NetworkError, NotAuthenticatedError, NotFoundError


def _cookie_segments(value: str | None) -> list[tuple[str, str]]:
    """Split a Cookie header without decoding, deduplicating, or rewriting it."""
    if not value:
        return []

    raw_segments: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif quote is not None and character == "\\":
            escaped = True
        elif quote is not None and character == quote:
            quote = None
        elif quote is None and character == '"':
            quote = character
        elif quote is None and character == ";":
            raw_segments.append(value[start:index].strip())
            start = index + 1
    raw_segments.append(value[start:].strip())

    parsed: list[tuple[str, str]] = []
    for segment in raw_segments:
        name, separator, _ = segment.partition("=")
        name = name.strip()
        if separator and name and not name.startswith("$"):
            parsed.append((name, segment))
    return parsed


class ApiClient:
    def __init__(
        self,
        api_base: str,
        *,
        token: str | None = None,
        cookie: str | None = None,
        timeout: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.cookie = cookie
        self._initial_cookie_header = cookie
        self._shadowed_initial_cookie_names: set[str] = set()
        self._client = httpx.Client(
            base_url=self.api_base,
            timeout=timeout,
            transport=transport,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def get_cookie(self, name: str, path: str = "/") -> str | None:
        """Return the server-visible value from a same-origin request Cookie header."""
        self._validate_request_target(path)
        header = self._cookie_header_for(path)
        value: str | None = None
        for cookie_name, segment in _cookie_segments(header):
            if cookie_name == name:
                # Tornado/SimpleCookie resolves duplicate request Cookie names by
                # keeping the last value, so the XSRF header must do the same.
                value = segment.partition("=")[2]
        if header:
            parsed = SimpleCookie()
            try:
                parsed.load(header)
            except CookieError:
                parsed.clear()
            if name in parsed:
                return parsed[name].value
        return value

    def get_cookie_header(self, path: str = "/") -> str | None:
        """Return the Cookie header for a same-origin target.

        This is intentionally a getter rather than a property: cookie Path and
        Secure matching depend on the target URL.  Callers must treat the return
        value as a secret and must never include it in results or exceptions.
        """
        self._validate_request_target(path)
        return self._cookie_header_for(path)

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"X-Requested-With": "XMLHttpRequest"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        allow_redirect_status = bool(kwargs.pop("_allow_redirect_status", False))
        self._validate_request_target(path)
        if kwargs.get("follow_redirects"):
            raise ApiError("ApiClient 不允许自动跟随重定向，请显式校验目标地址")
        headers = self._headers()
        cookie_header = self._cookie_header_for(path)
        if cookie_header:
            headers["Cookie"] = cookie_header
        headers.update(kwargs.pop("headers", {}))
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            raise NetworkError() from exc
        self._remember_initial_cookie_mutations(response)
        if (
            response.status_code == 403
            and b"<h1>403 Forbidden</h1>" in response.content
            and (b"<center>nginx</center>" in response.content or b"<center>openresty</center>" in response.content)
        ):
            raise ApiError("服务端网关拒绝访问（HTTP 403）", status_code=403)
        if response.status_code in (401, 403):
            raise NotAuthenticatedError("登录已过期或凭据无效")
        if response.status_code == 404:
            raise NotFoundError("资源不存在")
        if 300 <= response.status_code < 400 and not allow_redirect_status:
            location = response.headers.get("location", "").lower()
            if "/user/login" in location or "/hub/login" in location:
                raise NotAuthenticatedError("登录已过期，请重新执行认证命令")
            raise ApiError(f"请求返回了意外重定向（HTTP {response.status_code}）", status_code=response.status_code)
        if response.status_code >= 400:
            raise ApiError(f"请求失败（HTTP {response.status_code}）", status_code=response.status_code)
        return response

    def _cookie_header_for(self, path: str) -> str | None:
        target = httpx.URL(path)
        if target.is_relative_url:
            target = self._client.base_url.join(target)

        probe = httpx.Request("GET", target)
        self._client.cookies.set_cookie_header(probe)
        session_header = probe.headers.get("cookie")
        initial_header = self._initial_cookie_header
        if not initial_header:
            return session_header
        session_names = {name for name, _ in _cookie_segments(session_header)}
        # A browser-exported Cookie header has no Domain/Path metadata.  Model
        # its entries as host-only, Path=/ values.  Once the server accepts an
        # update or deletion for that exact scope, the imported value must never
        # reappear after the session value expires.
        replacement_names = set(self._shadowed_initial_cookie_names)
        if not session_header and not replacement_names:
            # Preserve browser-exported values byte-for-byte, including duplicate
            # names and quoted escape sequences used by existing commands.
            return initial_header

        initial_names = {name for name, _ in _cookie_segments(initial_header)}
        if replacement_names.isdisjoint(initial_names) and session_names.isdisjoint(initial_names):
            if not session_header:
                return initial_header
            return f"{initial_header}; {session_header}"
        initial_pairs = [
            (name, segment)
            for name, segment in _cookie_segments(initial_header)
            if name not in replacement_names
        ]
        if not initial_pairs:
            return session_header
        if not session_header:
            return "; ".join(segment for _, segment in initial_pairs)

        session_pairs = _cookie_segments(session_header)
        initial_by_name: dict[str, list[str]] = {}
        session_by_name: dict[str, list[str]] = {}
        ordered_names: list[str] = []
        for name, segment in initial_pairs:
            initial_by_name.setdefault(name, []).append(segment)
            if name not in ordered_names:
                ordered_names.append(name)
        for name, segment in session_pairs:
            session_by_name.setdefault(name, []).append(segment)
            if name not in ordered_names:
                ordered_names.append(name)

        narrow_counts = self._narrow_session_cookie_counts(target)
        merged: list[str] = []
        for name in ordered_names:
            initial_values = initial_by_name.get(name, [])
            session_values = session_by_name.get(name, [])
            if initial_values and session_values:
                split = min(narrow_counts.get(name, 0), len(session_values))
                merged.extend(session_values[:split])
                merged.extend(initial_values)
                merged.extend(session_values[split:])
            else:
                merged.extend(initial_values or session_values)
        return "; ".join(merged)

    def _narrow_session_cookie_counts(self, target: httpx.URL) -> dict[str, int]:
        host = (target.host or "").lower()
        request_path = target.path or "/"
        secure_request = target.scheme.lower() == "https"
        counts: dict[str, int] = {}
        for item in self._client.cookies.jar:
            cookie_path = item.path or "/"
            if (
                cookie_path != "/"
                and _cookie_domain_matches(host, item.domain, not item.domain_specified)
                and _cookie_path_matches(request_path, cookie_path)
                and (not item.secure or secure_request)
            ):
                counts[item.name] = counts.get(item.name, 0) + 1
        return counts

    def _remember_initial_cookie_mutations(self, response: httpx.Response) -> None:
        initial_names = {name for name, _ in _cookie_segments(self._initial_cookie_header)}
        for name in initial_names.difference(self._shadowed_initial_cookie_names):
            sentinel = secrets.token_hex(16)
            probe = httpx.Cookies()
            probe.jar.set_cookie(_host_root_cookie(name, sentinel, response.url.host or ""))
            probe.extract_cookies(response)
            sentinel_survived = any(
                item.name == name
                and item.value == sentinel
                and not item.domain_specified
                and (item.path or "/") == "/"
                for item in probe.jar
            )
            if not sentinel_survived:
                self._shadowed_initial_cookie_names.add(name)

    def _validate_request_target(self, path: str) -> None:
        try:
            target = urlsplit(str(path))
            if not (target.scheme or target.netloc):
                return
            if target.username is not None or target.password is not None or target.fragment:
                raise ApiError("拒绝包含用户信息或片段的 API 请求地址")
            base = urlsplit(self.api_base)
            if _origin(target) != _origin(base):
                raise ApiError("拒绝向 API 基地址以外的主机发送请求")
        except (UnicodeError, ValueError):
            raise ApiError("API 请求地址无效") from None

    def request_response(
        self,
        method: str,
        path: str,
        *,
        allow_redirect_status: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        return self._send(method, path, _allow_redirect_status=allow_redirect_status, **kwargs)

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._send(method, path, **kwargs)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("服务端返回了无效 JSON") from exc

    def request_text(self, method: str, path: str, **kwargs: Any) -> str:
        response = self._send(method, path, **kwargs)
        return response.text

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def get_text(self, path: str, **kwargs: Any) -> str:
        return self.request_text("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)


def _origin(value: Any) -> tuple[str, str, int | None]:
    scheme = value.scheme.lower()
    port = value.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (value.hostname or "").lower(), port


def _host_root_cookie(name: str, value: str, host: str) -> JarCookie:
    return JarCookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=host.lower(),
        domain_specified=False,
        domain_initial_dot=False,
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _cookie_domain_matches(host: str, domain: str, host_only: bool) -> bool:
    normalized_domain = domain.lstrip(".").lower()
    if host_only:
        return host == normalized_domain
    return host == normalized_domain or host.endswith(f".{normalized_domain}")


def _cookie_path_matches(request_path: str, cookie_path: str) -> bool:
    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    return cookie_path.endswith("/") or request_path[len(cookie_path)] == "/"
