from __future__ import annotations

import re
import sys

import click

from jqcli.api.auth import SMS_ORIGIN, login_with_password, login_with_sms, reusable_session
from jqcli.cli import AppContext
from jqcli.config import resolve_login_credentials
from jqcli.errors import UsageError
from jqcli.output import write_json


@click.group(name="auth")
def auth_group() -> None:
    """认证管理。"""


@auth_group.command("status")
@click.pass_obj
def status(app: AppContext) -> None:
    authenticated = bool(app.token or app.cookie)
    payload = {
        "authenticated": authenticated,
        "api_base": app.api_base,
        "credential_source": "token" if app.token else "cookie" if app.cookie else None,
        "username": app.config.data.get("username"),
    }
    if app.json_output:
        write_json(payload)
    elif authenticated:
        click.echo("已配置认证凭据")
    else:
        click.echo("未登录")


@auth_group.command("logout")
@click.pass_obj
def logout(app: AppContext) -> None:
    app.config.data.pop("token", None)
    app.config.data.pop("cookie", None)
    app.config.data.pop("username", None)
    app.config.data.pop("sms_session_origin", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True})
    elif not app.quiet:
        click.echo("已清除本地认证凭据")


@auth_group.command("import-token")
@click.option("--token", required=True, help="要保存的 token")
@click.pass_obj
def import_token(app: AppContext, token: str) -> None:
    app.config.data["token"] = token
    app.config.data.pop("cookie", None)
    app.config.data.pop("sms_session_origin", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "token"})
    elif not app.quiet:
        click.echo("token 已保存")


@auth_group.command("import-cookie")
@click.option("--cookie", required=True, help="要保存的 cookie")
@click.pass_obj
def import_cookie(app: AppContext, cookie: str) -> None:
    app.config.data["cookie"] = cookie
    app.config.data.pop("token", None)
    app.config.data.pop("sms_session_origin", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "cookie"})
    elif not app.quiet:
        click.echo("cookie 已保存")


@auth_group.command("login")
@click.option("--username", help="用户名；省略时读取 JQCLI_USERNAME")
@click.option("--password-stdin", is_flag=True, help="从 stdin 读取密码")
@click.option("--sms", is_flag=True, help="选择手机验证码登录，默认使用密码登录")
@click.option("--phone", help="指定手机号；默认使用上次成功登录的号码")
@click.pass_obj
def login(app: AppContext, username: str | None, password_stdin: bool, sms: bool, phone: str | None) -> None:
    if sms:
        if username is not None or password_stdin:
            raise UsageError("--sms 不能与 --username 或 --password-stdin 同时使用")
        _login_sms(app, phone)
        return
    if phone is not None:
        raise UsageError("--phone 需要与 --sms 同时使用")
    password = sys.stdin.read() if password_stdin else None
    username, password = resolve_login_credentials(username=username, password=password)
    if not username:
        raise UsageError("缺少用户名，请传入 --username 或在 env 文件中设置 JQCLI_USERNAME")
    if not password:
        raise UsageError("缺少密码，请传入 --password-stdin 或在 env 文件中设置 JQCLI_PASSWORD")
    result = login_with_password(app.api_base, username, password, timeout=app.timeout)
    app.config.data["username"] = username
    app.config.data["cookie"] = result["cookie"]
    app.config.data.pop("sms_session_origin", None)
    app.config.data.pop("password_login_pending", None)
    app.config.save()
    if app.json_output:
        write_json({"ok": True, "username": username, "credential": "cookie"})
    elif not app.quiet:
        click.echo("登录成功，cookie 已保存")


def _login_sms(app: AppContext, phone: str | None) -> None:
    if app.api_base.rstrip("/") != SMS_ORIGIN:
        raise UsageError("短信登录仅支持聚宽官方网站")

    config = app.config
    saved_phone = config.data.get("sms_phone")
    phone = phone if phone is not None else saved_phone
    cookie = config.cookie if config.data.get("sms_session_origin") == SMS_ORIGIN else None
    session = reusable_session(cookie, timeout=app.timeout) if cookie and phone == saved_phone else None
    reused = session is not None

    if not reused:
        if app.non_interactive:
            raise UsageError("短信登录需要在浏览器中完成验证，请取消 --non-interactive")
        if phone is not None and (not isinstance(phone, str) or not re.fullmatch(r"1[0-9]{10}", phone.strip())):
            raise UsageError("请输入 11 位中国大陆手机号")
        phone, session = login_with_sms(
            phone.strip() if phone is not None else None,
            timeout=app.timeout,
            on_ready=lambda: click.echo("请在聚宽浏览器窗口填写或核对手机号，并完成短信验证。", err=True),
        )
        config.data["sms_phone"] = phone
        config.data["sms_session_origin"] = SMS_ORIGIN
        config.data.pop("token", None)
        config.data.pop("username", None)
        config.data.pop("password_login_pending", None)

    if not reused or session != cookie:
        config.data["cookie"] = session
        config.save()
    if app.json_output:
        write_json({"ok": True, "credential": "cookie", "reused": reused})
    elif not app.quiet:
        click.echo("已复用登录会话" if reused else "登录成功，手机号和会话已保存")
