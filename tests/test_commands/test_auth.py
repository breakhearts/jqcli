import json

from click.testing import CliRunner

from jqcli.cli import main


def test_auth_status_json_without_credentials(tmp_path):
    result = CliRunner().invoke(main, ["--config", str(tmp_path / "c.json"), "--format", "json", "auth", "status"])

    assert result.exit_code == 0
    assert json.loads(result.output)["authenticated"] is False


def test_import_token_and_status(tmp_path):
    config = tmp_path / "config.json"
    runner = CliRunner()

    imported = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "import-token", "--token", "abc"])
    status = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "status"])

    assert imported.exit_code == 0
    assert json.loads(imported.output) == {"ok": True, "credential": "token"}
    assert json.loads(status.output)["authenticated"] is True
    assert json.loads(status.output)["credential_source"] == "token"


def test_logout_clears_config_credentials(tmp_path):
    config = tmp_path / "config.json"
    runner = CliRunner()
    runner.invoke(main, ["--config", str(config), "auth", "import-token", "--token", "abc"])

    result = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "logout"])
    status = runner.invoke(main, ["--config", str(config), "--format", "json", "auth", "status"])

    assert result.exit_code == 0
    assert json.loads(status.output)["authenticated"] is False


def test_login_requires_password_or_env(tmp_path):
    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--env-file",
            str(tmp_path / "missing.env"),
            "--format",
            "json",
            "auth",
            "login",
            "--username",
            "u",
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.stderr)["error"]["code"] == "usage_error"


def test_login_reads_password_stdin(monkeypatch, tmp_path):
    def fake_login(api_base, username, password, timeout=30):
        return {"payload": {"code": "00000"}, "cookie": "sid=abc"}

    monkeypatch.setattr("jqcli.commands.auth.login_with_password", fake_login)

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(tmp_path / "c.json"),
            "--format",
            "json",
            "auth",
            "login",
            "--username",
            "u",
            "--password-stdin",
        ],
        input="secret",
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["ok"] is True


def test_login_reads_username_and_password_from_env_file(monkeypatch, tmp_path):
    def fake_login(api_base, username, password, timeout=30):
        return {"payload": {"code": "00000"}, "cookie": "sid=abc"}

    monkeypatch.setattr("jqcli.commands.auth.login_with_password", fake_login)
    env_file = tmp_path / ".env"
    config = tmp_path / "c.json"
    env_file.write_text('JQCLI_USERNAME="u@example.com"\nJQCLI_PASSWORD="secret"\n', encoding="utf-8")

    result = CliRunner().invoke(
        main,
        [
            "--config",
            str(config),
            "--env-file",
            str(env_file),
            "--format",
            "json",
            "auth",
            "login",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["username"] == "u@example.com"


def test_auth_status_reads_token_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("JQCLI_TOKEN=from-env-file\n", encoding="utf-8")

    result = CliRunner().invoke(
        main,
        ["--config", str(tmp_path / "c.json"), "--env-file", str(env_file), "--format", "json", "auth", "status"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["authenticated"] is True


def test_sms_login_remembers_phone_and_reuses_session_until_expiry(monkeypatch, tmp_path):
    phone = "13800000000"
    login_phones = []
    session_checks = iter(["sid=first", None])

    def fake_login(number, *, timeout, on_ready):
        login_phones.append(number)
        on_ready()
        return phone, "sid=first" if len(login_phones) == 1 else "sid=renewed"

    def fake_reuse(cookie, *, timeout):
        assert cookie == "sid=first"
        return next(session_checks)

    monkeypatch.setattr("jqcli.commands.auth.login_with_sms", fake_login)
    monkeypatch.setattr("jqcli.commands.auth.reusable_session", fake_reuse)
    monkeypatch.delenv("JQCLI_TOKEN", raising=False)
    monkeypatch.delenv("JQCLI_COOKIE", raising=False)
    config = tmp_path / "config.json"
    runner = CliRunner()
    args = ["--config", str(config), "--env-file", str(tmp_path / "missing.env"), "--format", "json"]

    first = runner.invoke(main, args + ["auth", "login", "--sms"])
    reused = runner.invoke(main, args + ["--non-interactive", "auth", "login", "--sms"])
    expired = runner.invoke(main, args + ["auth", "login", "--sms"])
    for result, expected in [(first, False), (reused, True), (expired, False)]:
        assert result.exit_code == 0, result.exception
        assert json.loads(result.stdout) == {"ok": True, "credential": "cookie", "reused": expected}
        assert phone not in result.stdout
        assert "sid=" not in result.output
    assert login_phones == [None, phone]
    assert json.loads(config.read_text(encoding="utf-8"))["cookie"] == "sid=renewed"
    status = runner.invoke(main, args + ["auth", "status"])
    assert json.loads(status.stdout)["credential_source"] == "cookie"
    other_site = runner.invoke(main, args + ["--api-base", "https://example.test", "auth", "status"])
    assert json.loads(other_site.stdout)["authenticated"] is False
    assert runner.invoke(main, args + ["auth", "logout"]).exit_code == 0
    assert json.loads(config.read_text(encoding="utf-8")) == {"sms_phone": phone}
