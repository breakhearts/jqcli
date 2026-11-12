from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Flask

from jqcli.config import load_config, load_env_file, resolve_credentials

from .db import connect, init_db
from .routes import bp
from .services.posts import fill_missing_logical_keys, import_posts, mark_duplicate_posts, post_count, rebuild_post_index_if_needed


def create_app(config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    root = Path.cwd()
    data_dir = root / "local" / "data"
    manager_dir = data_dir / "strategy_manager"
    app.config.update(
        SECRET_KEY="jqcli-local",
        JQCLI_ROOT=root,
        JQCLI_DATA_DIR=data_dir,
        JQCLI_MANAGER_DIR=manager_dir,
        JQCLI_DB_PATH=manager_dir / "manager.sqlite3",
        JQCLI_ARCHIVE_PATH=data_dir / "community_posts_archive.jsonl",
        JQCLI_CANDIDATES_PATH=data_dir / "original_strategy_candidates_period_gt_1y.csv",
        JQCLI_ENV_FILE=root / ".env",
        JQCLI_BACKTEST_START="2021-01-01",
        JQCLI_BACKTEST_END="2025-12-12",
        JQCLI_BACKTEST_CAPITAL=500000,
        JQCLI_BACKTEST_FREQUENCY="day",
    )
    if config:
        app.config.update(config)

    manager_dir.mkdir(parents=True, exist_ok=True)
    load_env_file(app.config.get("JQCLI_ENV_FILE"))
    jq_config = load_config(app.config.get("JQCLI_CONFIG_PATH"))
    app.config["JQCLI_API_BASE"] = app.config.get("JQCLI_API_BASE") or jq_config.api_base
    token, cookie = resolve_credentials(jq_config, api_base=app.config["JQCLI_API_BASE"])
    app.config["JQCLI_TIMEOUT"] = float(app.config.get("JQCLI_TIMEOUT") or jq_config.timeout)
    app.config["JQCLI_TOKEN"] = app.config.get("JQCLI_TOKEN") or token
    app.config["JQCLI_COOKIE"] = app.config.get("JQCLI_COOKIE") or cookie

    init_db(Path(app.config["JQCLI_DB_PATH"]))
    if post_count(Path(app.config["JQCLI_DB_PATH"])) == 0 and Path(app.config["JQCLI_ARCHIVE_PATH"]).exists():
        import_posts(
            Path(app.config["JQCLI_DB_PATH"]),
            Path(app.config["JQCLI_ARCHIVE_PATH"]),
            Path(app.config["JQCLI_CANDIDATES_PATH"]),
        )
    else:
        rebuild_post_index_if_needed(Path(app.config["JQCLI_DB_PATH"]))
        with connect(Path(app.config["JQCLI_DB_PATH"])) as conn:
            missing_keys = conn.execute(
                "SELECT COUNT(*) AS c FROM post_index WHERE logical_key IS NULL OR logical_key = ''"
            ).fetchone()["c"]
            if missing_keys:
                fill_missing_logical_keys(conn)
                mark_duplicate_posts(conn)
    app.register_blueprint(bp)
    return app
