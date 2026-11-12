from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import FileError


APP_NAME = "jqcli"


def default_config_path() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME / "config.json"
        return Path.home() / "AppData" / "Roaming" / APP_NAME / "config.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_NAME / "config.json"
    return Path.home() / ".config" / APP_NAME / "config.json"


def legacy_config_path() -> Path:
    return Path.home() / ".jqcli" / "config.json"


def default_env_path() -> Path:
    return Path.cwd() / ".env"


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if value:
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError:
            parsed = [value]
        if len(parsed) == 1:
            value = parsed[0]
    return key, value


def load_env_file(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    env_path = Path(path).expanduser() if path else default_env_path()
    if not env_path.exists():
        return None
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FileError(f"无法读取 env 文件 {env_path}") from exc
    for line in lines:
        item = parse_env_line(line)
        if item is None:
            continue
        key, value = item
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path


@dataclass
class Config:
    path: Path
    data: dict[str, Any]

    @property
    def api_base(self) -> str:
        return str(self.data.get("api_base") or "https://www.joinquant.com")

    @property
    def timeout(self) -> float:
        return float(self.data.get("timeout") or 30)

    @property
    def token(self) -> str | None:
        token = self.data.get("token")
        return str(token) if token else None

    @property
    def cookie(self) -> str | None:
        cookie = self.data.get("cookie")
        return str(cookie) if cookie else None

    def cookie_for(self, api_base: str) -> str | None:
        origin = self.data.get("sms_session_origin")
        if origin and api_base.rstrip("/") != origin:
            return None
        return self.cookie

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise FileError(f"无法写入配置文件 {self.path}") from exc


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path).expanduser() if path else default_config_path()
    read_path = config_path
    if path is None and not config_path.exists() and legacy_config_path().exists():
        read_path = legacy_config_path()
    if not read_path.exists():
        return Config(path=config_path, data={})
    try:
        data = json.loads(read_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileError(f"无法读取配置文件 {read_path}") from exc
    if not isinstance(data, dict):
        raise FileError(f"配置文件 {read_path} 格式错误")
    return Config(path=config_path, data=data)


def resolve_credentials(
    config: Config, token: str | None = None, cookie: str | None = None, *, api_base: str | None = None
) -> tuple[str | None, str | None]:
    resolved_token = os.environ.get("JQCLI_TOKEN") or token or config.token
    resolved_cookie = os.environ.get("JQCLI_COOKIE") or cookie or config.cookie_for(api_base or config.api_base)
    return resolved_token, resolved_cookie


def resolve_login_credentials(username: str | None = None, password: str | None = None) -> tuple[str | None, str | None]:
    resolved_username = username or os.environ.get("JQCLI_USERNAME")
    resolved_password = password or os.environ.get("JQCLI_PASSWORD")
    return resolved_username, resolved_password
