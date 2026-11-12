from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx

from jqcli.api.client import ApiClient
from jqcli.errors import ApiError, NetworkError, NotAuthenticatedError, UsageError


LOGIN_PAGE = "/user/login/index"
LOGIN_ENDPOINT = "/user/login/doLogin"
SESSION_STATUS_ENDPOINT = "/user/index/isLogin"
SMS_ORIGIN = "https://www.joinquant.com"


def reusable_session(cookie: str, *, timeout: float) -> str | None:
    with ApiClient(SMS_ORIGIN, cookie=cookie, timeout=timeout) as client:
        try:
            payload = client.get(SESSION_STATUS_ENDPOINT)
        except NotAuthenticatedError:
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or payload.get("code") != "00000":
            raise ApiError("无法确认聚宽会话状态")
        if data.get("isLogin") == 1:
            return client.get_cookie_header("/")
        if data.get("isLogin") == 0:
            return None
        raise ApiError("聚宽会话状态格式异常")


def login_with_sms(phone: str | None, *, timeout: float, on_ready: Callable[[], None]) -> tuple[str, str]:
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError:
        raise UsageError('请先安装 jqcli[sms] 并运行 python -m playwright install chromium') from None

    submitted = None

    def remember_phone(request: Any) -> None:
        nonlocal submitted
        if request.method == "POST" and request.url.startswith(SMS_ORIGIN + "/user/login/"):
            number = parse_qs(request.post_data or "").get("mob", [""])[0]
            if re.fullmatch(r"1[0-9]{10}", number):
                submitted = number

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            try:
                context = browser.new_context()
                page = context.new_page()
                page.goto(SMS_ORIGIN + LOGIN_PAGE, wait_until="domcontentloaded", timeout=timeout * 1000)
                if not page.url.startswith(SMS_ORIGIN + "/"):
                    raise ApiError("聚宽登录页跳转到了其他站点")
                page.on("request", remember_phone)
                tab = page.locator(".tab-nav li").filter(has_text="验证码登录").first
                tab.click(timeout=timeout * 1000)
                if phone is not None:
                    page.locator(".valid-phone:visible").fill(phone, timeout=timeout * 1000)
                on_ready()
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    if page.is_closed():
                        raise ApiError("聚宽登录窗口已关闭，未保存会话")
                    if submitted is not None:
                        cookies = context.cookies(SMS_ORIGIN + "/")
                        cookie = "; ".join(f"{item['name']}={item['value']}" for item in cookies)
                        session = reusable_session(cookie, timeout=timeout) if cookie else None
                        if session:
                            return submitted, session
                    page.wait_for_timeout(2000)
                raise ApiError("等待聚宽短信登录完成超时")
            finally:
                browser.close()
    except TimeoutError:
        raise ApiError("等待聚宽登录页或短信登录完成超时") from None
    except Error:
        raise ApiError("无法使用 Chromium 完成登录，请确认浏览器已安装且登录窗口保持打开") from None


def extract_page_token(html: str) -> str | None:
    match = re.search(r"window\.tokenData\s*=\s*\{\s*name\s*:\s*['\"]?token['\"]?\s*,\s*value\s*:\s*['\"]([^'\"]+)", html)
    if match:
        return match.group(1)
    match = re.search(r"name=[\"']token[\"'][^>]+value=[\"']([^\"']+)", html)
    if match:
        return match.group(1)
    return None


def login_with_password(api_base: str, username: str, password: str, *, timeout: float = 30) -> dict[str, Any]:
    base = api_base.rstrip("/")
    try:
        with httpx.Client(base_url=base, follow_redirects=True, timeout=timeout, trust_env=False) as client:
            page = client.get(LOGIN_PAGE)
            page.raise_for_status()
            token = extract_page_token(page.text)
            if not token:
                raise ApiError("无法从登录页提取 token")
            response = client.post(
                LOGIN_ENDPOINT,
                data={
                    "CyLoginForm[username]": username,
                    "CyLoginForm[pwd]": password,
                    "token": token,
                },
                headers={
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{base}{LOGIN_PAGE}",
                },
            )
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError as exc:
                raise ApiError("登录接口返回了无效 JSON") from exc
            if payload.get("code") != "00000":
                message = payload.get("msg") or "登录失败"
                if payload.get("error"):
                    message = f"{message}: {payload['error']}"
                raise ApiError(str(message), details={"code": payload.get("code")})
            return {
                "payload": payload,
                "cookie": "; ".join(f"{cookie.name}={cookie.value}" for cookie in client.cookies.jar),
            }
    except httpx.RequestError as exc:
        raise NetworkError() from exc
    except httpx.HTTPStatusError as exc:
        raise ApiError(f"登录请求失败（HTTP {exc.response.status_code}）", status_code=exc.response.status_code) from exc

