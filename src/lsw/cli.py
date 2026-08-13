"""Command-line interface (milestone M3).

WSL-style flags over the application services. The contract is stable:

* long options are the stable interface, short options are aliases;
* list/status commands offer ``--json`` (stdout) and keep diagnostics on stderr;
* ``--exec/-e PROG ARGS…`` and ``-d NAME -- PROG ARGS…`` pass the program's
  stdout/stderr/exit code through untouched;
* every expected error maps to a stable exit code (see ``lsw.exit_codes``).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from . import __version__, exit_codes, paths
from . import services as _services
from .errors import (
    ConfirmationRequiredError,
    InstanceNotFoundError,
    LswError,
    UsageError,
)
from .models import InstanceState, RunOptions
from .services import ListResult, Services, StatusInfo

_DESCRIPTION = (
    "LSW — Linux Subsystem for Windows：管理彼此隔离的 Windows 用户空间实例（Wine prefix）。"
    "注意：它不是 Windows 内核、虚拟机或微软官方 WSL。"
)

_HINTS: dict[type[LswError], str] = {
    InstanceNotFoundError: "提示：运行 `lsw --list` 查看已安装实例。",
    ConfirmationRequiredError: "提示：如需无人值守删除，请使用 `--yes`。",
}


def _split_program_args(argv: Sequence[str]) -> tuple[list[str], list[str] | None]:
    """Split argv at the first ``--``; everything after it is a program argv.

    Returns ``(cli_args, program_args)`` where *program_args* is ``None`` when
    no ``--`` was given and ``[]`` when it was (an empty passthrough).
    """
    tokens = list(argv)
    if "--" in tokens:
        index = tokens.index("--")
        return tokens[:index], tokens[index + 1 :]
    return tokens, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lsw",
        description=_DESCRIPTION,
        add_help=True,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="显示版本号并退出",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true", help="安装/创建新实例")
    action.add_argument("--list", "-l", action="store_true", help="列出实例")
    action.add_argument("--status", action="store_true", help="显示状态摘要")
    action.add_argument("--set-default", "-s", metavar="NAME", help="设置默认实例")
    action.add_argument("--terminate", "-t", metavar="NAME", help="终止实例")
    action.add_argument("--shutdown", action="store_true", help="关闭所有实例")
    action.add_argument("--unregister", metavar="NAME", help="注销/删除实例")
    action.add_argument(
        "--set-version", nargs=2, metavar=("NAME", "VERSION"), help="设置实例版本（仅支持 1）"
    )
    action.add_argument(
        "--exec", "-e", nargs=argparse.REMAINDER, metavar="PROGRAM", help="执行 Windows 程序及参数"
    )
    parser.add_argument(
        "--distribution", "-d", metavar="NAME", help="执行时选择实例；安装时为新实例命名"
    )
    parser.add_argument("--no-launch", action="store_true", help="安装后不启动（当前为默认行为）")
    parser.add_argument("--yes", action="store_true", help="跳过注销确认")
    parser.add_argument("--verbose", "-v", action="store_true", help="列表显示额外列")
    parser.add_argument("--quiet", "-q", action="store_true", help="列表仅输出实例名")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument(
        "name", nargs="?", metavar="NAME", help="安装时的实例名（或使用 --distribution）"
    )
    return parser


def _exit_code_from(system_exit: SystemExit) -> int:
    code = system_exit.code
    if isinstance(code, int):
        return code
    return exit_codes.EXIT_USAGE


def _format_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------- formatting


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        "  "
        + "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)).rstrip()
    ]
    for row in rows:
        lines.append(
            "  " + "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)).rstrip()
        )
    return "\n".join(lines)


def _format_list(result: ListResult, *, verbose: bool) -> str:
    headers = ["NAME", "STATE", "VERSION", "BACKEND"]
    if verbose:
        headers += ["CREATED_AT", "INSTALL_STATE"]
    rows: list[list[str]] = []
    for item in result.instances:
        marker = "*" if item.is_default else " "
        row = [
            f"{marker} {item.instance.name}",
            item.runtime_state.value,
            str(item.instance.version),
            item.instance.backend.value,
        ]
        if verbose:
            row += [_format_dt(item.instance.created_at), item.instance.install_state.value]
        rows.append(row)
    for broken in result.corrupt:
        row = [f"  {broken.name}", InstanceState.BROKEN.value, "-", "-"]
        if verbose:
            row += ["-", "-"]
        rows.append(row)
    if not rows:
        return "（无实例）"
    return _table(headers, rows)


def _list_json(result: ListResult) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "default": result.default,
        "instances": [
            {
                "name": item.instance.name,
                "state": item.runtime_state.value,
                "version": item.instance.version,
                "backend": item.instance.backend.value,
                "created_at": _format_dt(item.instance.created_at),
                "install_state": item.instance.install_state.value,
            }
            for item in result.instances
        ],
        "corrupt": [{"name": broken.name, "reason": broken.reason} for broken in result.corrupt],
    }


def _format_status(info: StatusInfo) -> str:
    if info.backend.available:
        backend_line = f"Backend: {info.backend.backend.value}（可用）"
        version_line = f"Wine version: {info.backend.version or '未知'}"
    else:
        backend_line = f"Backend: {info.backend.backend.value}（不可用）"
        version_line = "Wine version: 未检测到"
    lines = [
        backend_line,
        version_line,
        f"默认实例: {info.default_instance or '（无）'}",
        f"实例数量: {info.instance_count}",
        f"数据根目录: {info.data_root}",
    ]
    if info.corrupt_count:
        lines.append(f"损坏实例: {info.corrupt_count}")
    return "\n".join(lines)


def _status_json(info: StatusInfo) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "default_instance": info.default_instance,
        "instance_count": info.instance_count,
        "corrupt_count": info.corrupt_count,
        "backend": {
            "type": info.backend.backend.value,
            "available": info.backend.available,
            "version": info.backend.version,
            "executable": info.backend.executable,
            "architecture": info.backend.architecture,
            "diagnostics": list(info.backend.diagnostics),
        },
        "data_root": str(info.data_root),
    }


# ------------------------------------------------------------------- actions


def _detect_action(args: argparse.Namespace) -> str:
    if args.install:
        return "install"
    if args.list:
        return "list"
    if args.status:
        return "status"
    if args.set_default:
        return "set_default"
    if args.terminate:
        return "terminate"
    if args.shutdown:
        return "shutdown"
    if args.unregister:
        return "unregister"
    if args.set_version:
        return "set_version"
    return "run"


def _validate_modifiers(
    args: argparse.Namespace, action: str, program_args: list[str] | None
) -> None:
    if args.distribution and action not in ("install", "run"):
        raise UsageError("--distribution/-d 只能用于安装（命名）或执行（选择实例）。")
    if args.name and action != "install":
        raise UsageError("实例名参数只能与 --install 一起使用。")
    if args.no_launch and action != "install":
        raise UsageError("--no-launch 只能与 --install 一起使用。")
    if args.yes and action != "unregister":
        raise UsageError("--yes 只能与 --unregister 一起使用。")
    if program_args is not None and action != "run":
        raise UsageError("`--` 后的程序参数只能用于执行命令。")
    if args.verbose or args.quiet or args.json:
        if action not in ("list", "status"):
            raise UsageError("--verbose/--quiet/--json 只能用于 --list 或 --status。")
        if sum(bool(flag) for flag in (args.verbose, args.quiet, args.json)) > 1:
            raise UsageError("--verbose、--quiet、--json 只能三选一。")
    if args.exec is not None and program_args:
        raise UsageError("不能同时使用 --exec/-e 和 `--` 提供程序参数。")


def _install(args: argparse.Namespace, services: Services) -> int:
    if args.name and args.distribution:
        raise UsageError("不能同时给出实例名和 --distribution。")
    name = args.distribution or args.name or services.default_install_name
    print(f"正在初始化 {name}（Wine prefix）...")
    services.install(name)
    print(f"已安装 {name}。")
    return exit_codes.EXIT_SUCCESS


def _list(args: argparse.Namespace, services: Services) -> int:
    result = services.list_instances()
    for broken in result.corrupt:
        print(f"lsw: 警告: 实例 {broken.name} 损坏：{broken.reason}", file=sys.stderr)
    if args.json:
        print(json.dumps(_list_json(result), ensure_ascii=False, indent=2))
    elif args.quiet:
        names = "\n".join(item.instance.name for item in result.instances)
        if names:
            print(names)
    else:
        print(_format_list(result, verbose=args.verbose))
    return exit_codes.EXIT_SUCCESS


def _status(args: argparse.Namespace, services: Services) -> int:
    info = services.status()
    if args.json:
        print(json.dumps(_status_json(info), ensure_ascii=False, indent=2))
    else:
        print(_format_status(info))
    return exit_codes.EXIT_SUCCESS


def _set_default(args: argparse.Namespace, services: Services) -> int:
    name = args.set_default
    services.set_default(name)
    print(f"已将 {name} 设为默认实例。")
    return exit_codes.EXIT_SUCCESS


def _run(args: argparse.Namespace, program_args: list[str] | None, services: Services) -> int:
    if args.exec is not None and program_args:
        raise UsageError("不能同时使用 --exec/-e 和 `--` 提供程序参数。")
    if args.exec is not None:
        program = list(args.exec)
    elif program_args is not None:
        program = list(program_args)
    else:
        program = []
    interactive = False
    if not program:
        if not sys.stdin.isatty():
            raise UsageError("交互式执行需要 TTY。请显式提供命令，例如 `lsw -d NAME -- cmd.exe`。")
        program = [services.default_program]
        interactive = True
    instance_name = args.distribution or services.repo.get_default()
    if instance_name is None:
        raise InstanceNotFoundError(
            "没有默认实例。请先用 `lsw --set-default NAME` 设置默认实例，或用 `-d NAME` 指定实例。"
        )
    options = RunOptions(argv=tuple(program), interactive=interactive)
    return services.run(instance_name, program, options)


def _terminate(args: argparse.Namespace, services: Services) -> int:
    name = args.terminate
    result = services.terminate(name, services.terminate_timeout)
    if result.previous_state is InstanceState.STOPPED:
        print(f"实例 {name} 已经停止。")
    else:
        print(f"已终止实例 {name}。")
    return exit_codes.EXIT_SUCCESS


def _shutdown(services: Services) -> int:
    result = services.shutdown(services.shutdown_timeout)
    if result.failed:
        details = "，".join(f"{name}（{reason}）" for name, reason in result.failed)
        print(f"lsw: 部分实例关闭失败：{details}", file=sys.stderr)
        return exit_codes.EXIT_OPERATION_FAILED
    if not result.terminated:
        print("没有实例需要关闭。")
    else:
        print(f"已关闭 {len(result.terminated)} 个实例。")
    return exit_codes.EXIT_SUCCESS


def _unregister(args: argparse.Namespace, services: Services) -> int:
    name = args.unregister
    services.ensure_not_running(name)
    confirmed = True
    if not args.yes:
        if sys.stdin.isatty():
            try:
                typed = input(f"确认删除实例 {name}？请输入实例名以确认（其它内容取消）：")
            except EOFError:
                raise ConfirmationRequiredError("已取消删除（无输入）。") from None
            if typed.strip() != name:
                raise ConfirmationRequiredError("输入与实例名不匹配，已取消删除。")
        else:
            confirmed = False
    services.unregister(name, confirmed=confirmed)
    print(f"已注销实例 {name}。")
    return exit_codes.EXIT_SUCCESS


def _set_version(args: argparse.Namespace, services: Services) -> int:
    name, version_text = args.set_version
    try:
        version = int(version_text)
    except ValueError:
        raise UsageError(f"版本必须是整数，得到 {version_text!r}。") from None
    services.set_version(name, version)
    print(f"已将 {name} 的版本设为 {version}。")
    return exit_codes.EXIT_SUCCESS


def _dispatch(
    args: argparse.Namespace,
    program_args: list[str] | None,
    services: Services,
) -> int:
    action = _detect_action(args)
    _validate_modifiers(args, action, program_args)
    if action == "install":
        return _install(args, services)
    if action == "list":
        return _list(args, services)
    if action == "status":
        return _status(args, services)
    if action == "set_default":
        return _set_default(args, services)
    if action == "run":
        return _run(args, program_args, services)
    if action == "terminate":
        return _terminate(args, services)
    if action == "shutdown":
        return _shutdown(services)
    if action == "unregister":
        return _unregister(args, services)
    return _set_version(args, services)


def _print_error(error: LswError) -> None:
    print(f"lsw: {error.message}", file=sys.stderr)
    hint = _HINTS.get(type(error))
    if hint:
        print(hint, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code (never raises ``SystemExit``)."""
    cli_args, program_args = _split_program_args(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    try:
        args = parser.parse_args(cli_args)
    except SystemExit as exc:  # --help / --version / parse errors
        return _exit_code_from(exc)
    try:
        roots = paths.data_roots()
        services = _services.build_services(roots)
        return _dispatch(args, program_args, services)
    except LswError as error:
        _print_error(error)
        return error.exit_code
    except KeyboardInterrupt:
        return 130
