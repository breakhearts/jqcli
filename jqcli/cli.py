from __future__ import annotations

from dataclasses import dataclass

import click

from . import __version__
from .config import Config, load_config, load_env_file, resolve_credentials
from .errors import JqcliError
from .output import write_error


@dataclass
class AppContext:
    config: Config
    api_base: str
    token: str | None
    cookie: str | None
    output_format: str
    non_interactive: bool
    quiet: bool
    debug: bool
    timeout: float

    @property
    def json_output(self) -> bool:
        return self.output_format == "json"


class JqcliGroup(click.Group):
    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except JqcliError as exc:
            app = ctx.obj if isinstance(ctx.obj, AppContext) else None
            write_error(exc, json_format=bool(app and app.json_output))
            raise click.exceptions.Exit(exc.exit_code) from exc


@click.group(cls=JqcliGroup)
@click.option("--config", "config_path", type=click.Path(dir_okay=False, path_type=str), help="配置文件路径")
@click.option("--env-file", type=click.Path(dir_okay=False, path_type=str), help="env 文件路径，默认读取当前目录 .env")
@click.option("--api-base", type=str, help="覆盖 API 地址")
@click.option("--token", type=str, help="本次命令使用的 token")
@click.option("--cookie", type=str, help="本次命令使用的 cookie")
@click.option("--format", "output_format", type=click.Choice(["table", "json"]), default=None, help="输出格式")
@click.option("--non-interactive", is_flag=True, help="禁止交互式输入和确认")
@click.option("--quiet", is_flag=True, help="减少成功输出")
@click.option("--debug", is_flag=True, help="输出脱敏调试信息")
@click.option("--timeout", type=float, default=None, help="HTTP 请求超时秒数")
@click.version_option(__version__, prog_name="jqcli")
@click.pass_context
def main(
    ctx: click.Context,
    config_path: str | None,
    env_file: str | None,
    api_base: str | None,
    token: str | None,
    cookie: str | None,
    output_format: str | None,
    non_interactive: bool,
    quiet: bool,
    debug: bool,
    timeout: float | None,
) -> None:
    """聚宽策略、回测、模拟盘与研究工作区管理命令行工具。"""
    load_env_file(env_file)
    config = load_config(config_path)
    api_base = api_base or config.api_base
    resolved_token, resolved_cookie = resolve_credentials(config, token=token, cookie=cookie, api_base=api_base)
    ctx.obj = AppContext(
        config=config,
        api_base=api_base,
        token=resolved_token,
        cookie=resolved_cookie,
        output_format=output_format or str(config.data.get("default_format") or "table"),
        non_interactive=non_interactive,
        quiet=quiet,
        debug=debug,
        timeout=timeout if timeout is not None else config.timeout,
    )


from .commands.auth import auth_group
from .commands.backtest import backtest_group
from .commands.community import community_group
from .commands.research import research_group
from .commands.strategy import strategy_group
from .commands.simulation import simulation_group
from .commands.web import web_group

main.add_command(auth_group, "auth")
main.add_command(backtest_group, "backtest")
main.add_command(community_group, "community")
main.add_command(research_group, "research")
main.add_command(strategy_group, "strategy")
main.add_command(simulation_group, "simulation")
main.add_command(web_group, "web")


if __name__ == "__main__":
    main()
