"""CLI end-to-end tests (M3), driven by the fake backend — no Wine required.

``cli.main`` is the executable entry point; the ``cli_env`` fixture injects an
isolated repository plus a configurable :class:`FakeBackend`, so stdout,
stderr and exit codes are exercised through the full parsing → services → repo
path without touching Wine or the real home directory.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from lsw import __version__, cli, exit_codes
from lsw.backends import FakeBackend
from lsw.repository import InstanceRepository
from lsw.services import Services


@pytest.fixture
def cli_env(isolated_roots, monkeypatch):
    """Wire the CLI to an isolated repo + configurable FakeBackend."""
    fake = FakeBackend()
    svc = Services(
        repo=InstanceRepository(isolated_roots),
        backend=fake,
        roots=isolated_roots,
    )
    monkeypatch.setattr("lsw.services.build_services", lambda roots: svc)
    return svc, fake


def _install(cli_env, name="Windows-11"):
    svc, fake = cli_env
    assert cli.main(["--install", name, "--no-launch"]) == exit_codes.EXIT_SUCCESS
    return svc, fake


# ------------------------------------------------------------- parsing basics


def test_version_exits_zero_and_prints_version(capsys):
    assert cli.main(["--version"]) == 0
    captured = capsys.readouterr()
    assert f"lsw {__version__}" in captured.out


def test_help_exits_zero_and_lists_commands(capsys):
    assert cli.main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out
    assert "--install" in captured.out
    assert "--list" in captured.out
    assert "--status" in captured.out


def test_no_command_non_tty_is_usage_error(cli_env, capsys, monkeypatch):
    # 非交互终端且无命令时，裸 `lsw` 必须给出用法错误而不是挂起。
    monkeypatch.setattr("lsw.cli.sys.stdin.isatty", lambda: False)
    assert cli.main([]) == exit_codes.EXIT_USAGE
    captured = capsys.readouterr()
    assert "TTY" in captured.err


def test_unknown_flag_is_usage_error(capsys):
    assert cli.main(["--definitely-not-a-flag"]) == exit_codes.EXIT_USAGE


def test_conflicting_actions_are_usage_error(cli_env, capsys):
    assert cli.main(["--install", "X", "--list"]) == exit_codes.EXIT_USAGE


def test_conflicting_output_modes_are_usage_error(cli_env, capsys):
    assert cli.main(["--list", "-q", "-v"]) == exit_codes.EXIT_USAGE
    assert cli.main(["--list", "--json", "-q"]) == exit_codes.EXIT_USAGE


def test_distribution_only_with_install_or_run(cli_env, capsys):
    assert cli.main(["-d", "Windows-11", "--list"]) == exit_codes.EXIT_USAGE


def test_positional_name_only_with_install(cli_env, capsys):
    assert cli.main(["Windows-11"]) == exit_codes.EXIT_USAGE


def test_yes_only_with_unregister(cli_env, capsys):
    assert cli.main(["--yes", "--list"]) == exit_codes.EXIT_USAGE


def test_program_args_only_for_run(cli_env, capsys):
    assert cli.main(["--install", "X", "--", "cmd.exe"]) == exit_codes.EXIT_USAGE


def test_exec_and_dashdash_conflict(cli_env, capsys):
    assert cli.main(["-d", "X", "-e", "cmd.exe", "--", "ver"]) == exit_codes.EXIT_USAGE


# ------------------------------------------------------------------ install


def test_install_success(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert "initialize:Windows-11" in fake.calls
    captured = capsys.readouterr()
    assert "正在初始化 Windows-11（Wine prefix）..." in captured.out
    assert "已安装 Windows-11。" in captured.out
    assert svc.repo.get("Windows-11").install_state.value == "installed"


def test_install_uses_default_name(cli_env, capsys):
    svc, fake = cli_env
    # 无名称时使用 default_install_name = "Windows-11"
    assert cli.main(["--install", "--no-launch"]) == exit_codes.EXIT_SUCCESS
    assert svc.repo.exists("Windows-11")


def test_install_uses_distribution_as_name(cli_env, capsys):
    svc, _ = cli_env
    assert cli.main(["--install", "--distribution", "Windows-XP", "--no-launch"]) == 0
    assert svc.repo.exists("Windows-XP")


def test_install_both_name_and_distribution_conflict(cli_env, capsys):
    assert cli.main(["--install", "X", "--distribution", "Y"]) == exit_codes.EXIT_USAGE


def test_install_existing_conflicts(cli_env, capsys):
    _install(cli_env)
    assert cli.main(["--install", "Windows-11", "--no-launch"]) == exit_codes.EXIT_INSTANCE_CONFLICT


def test_install_invalid_name(cli_env, capsys):
    assert cli.main(["--install", "bad/name"]) == exit_codes.EXIT_USAGE
    captured = capsys.readouterr()
    assert "实例名" in captured.err or "lsw:" in captured.err


def test_install_backend_unavailable(cli_env, capsys):
    svc, fake = cli_env
    fake.available = False
    assert (
        cli.main(["--install", "Windows-11", "--no-launch"]) == exit_codes.EXIT_BACKEND_UNAVAILABLE
    )
    assert not svc.repo.exists("Windows-11")  # 未创建半成品


def test_install_backend_initialize_failure_marks_failed(cli_env, capsys):
    svc, fake = cli_env
    fake.initialize_error = "wineboot 崩溃"
    assert cli.main(["--install", "Windows-11", "--no-launch"]) == exit_codes.EXIT_OPERATION_FAILED
    captured = capsys.readouterr()
    assert "初始化实例 Windows-11 失败" in captured.err
    inst = svc.repo.get("Windows-11")
    assert inst.install_state.value == "failed"  # 不留下"已安装"半成品
    assert svc.repo.instance_root("Windows-11").is_dir()  # 保留诊断目录


# --------------------------------------------------------------------- list


def test_list_empty(cli_env, capsys):
    assert cli.main(["--list"]) == exit_codes.EXIT_SUCCESS
    assert "（无实例）" in capsys.readouterr().out


def test_list_table_marks_default(cli_env, capsys):
    _install(cli_env)
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert cli.main(["--list"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "NAME" in out and "STATE" in out and "VERSION" in out
    assert "* Windows-11" in out


def test_list_shows_running_state(cli_env, capsys):
    svc, fake = _install(cli_env)
    fake.running.add("Windows-11")
    assert cli.main(["--list"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "Running" in out


def test_list_probe_failure_is_unknown_not_stopped(cli_env, capsys):
    svc, fake = _install(cli_env)
    fake.available = False  # 状态探测失败 → Unknown，不能等同 Stopped
    assert cli.main(["--list"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "Unknown" in out


def test_list_quiet_prints_names(cli_env, capsys):
    _install(cli_env)
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert cli.main(["--list", "-q"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "Windows-11" in out
    assert "NAME" not in out  # 无表头


def test_list_verbose_adds_columns(cli_env, capsys):
    _install(cli_env)
    assert cli.main(["--list", "-v"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "CREATED_AT" in out
    assert "INSTALL_STATE" in out
    assert "installed" in out


def test_list_json_schema(cli_env, capsys):
    svc, fake = _install(cli_env)
    capsys.readouterr()  # 丢弃安装输出，保持 stdout 只含 JSON
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    capsys.readouterr()
    fake.running.add("Windows-11")
    assert cli.main(["--list", "--json"]) == exit_codes.EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["default"] == "Windows-11"
    assert len(payload["instances"]) == 1
    entry = payload["instances"][0]
    assert entry["name"] == "Windows-11"
    assert entry["state"] == "Running"
    assert entry["version"] == 1
    assert entry["created_at"].endswith("Z")
    assert payload["corrupt"] == []


def test_list_corrupt_instance_is_broken_not_fatal(cli_env, capsys):
    svc, fake = _install(cli_env)
    (svc.repo.instances_dir / "Windows-11" / "instance.toml").write_text(
        "not [ valid toml", encoding="utf-8"
    )
    assert cli.main(["--list"]) == exit_codes.EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "Broken" in captured.out
    assert "Windows-11" in captured.err  # 警告到 stderr
    assert cli.main(["--list", "--json"]) == exit_codes.EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["corrupt"]) == 1
    assert payload["corrupt"][0]["name"] == "Windows-11"


# ------------------------------------------------------------------- status


def test_status_no_instances_succeeds(cli_env, capsys):
    assert cli.main(["--status"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "实例数量: 0" in out


def test_status_reports_backend_and_default(cli_env, capsys):
    _install(cli_env)
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert cli.main(["--status"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "fake-wine-9.0" in out
    assert "Windows-11" in out
    assert "1" in out


def test_status_json(cli_env, capsys):
    _install(cli_env)
    capsys.readouterr()  # 丢弃安装输出，保持 stdout 只含 JSON
    assert cli.main(["--status", "--json"]) == exit_codes.EXIT_SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["instance_count"] == 1
    assert payload["backend"]["type"] == "fake"
    assert payload["backend"]["available"] is True
    assert payload["data_root"].endswith("lsw")


def test_status_reports_wine_unavailable_without_config(isolated_roots, capsys, monkeypatch):
    # 真实（未打补丁）路径：无论宿主是否装有 Wine，都强制上报 wine 不可用，且不失败。
    monkeypatch.setattr("shutil.which", lambda command: None)
    assert cli.main(["--status"]) == exit_codes.EXIT_SUCCESS
    out = capsys.readouterr().out
    assert "wine" in out
    assert "不可用" in out


# ------------------------------------------------------------- set-default


def test_set_default_success(cli_env, capsys):
    svc, _ = _install(cli_env)
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert svc.repo.get_default() == "Windows-11"


def test_set_default_missing_instance(cli_env, capsys):
    assert cli.main(["--set-default", "Nope"]) == exit_codes.EXIT_INSTANCE_NOT_FOUND


def test_set_default_not_installed(cli_env, capsys):
    svc, _ = cli_env
    svc.repo.create("Pending")  # PENDING
    assert cli.main(["--set-default", "Pending"]) == exit_codes.EXIT_CONFIGURATION


# --------------------------------------------------------------------- run


def test_run_with_explicit_instance(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["-d", "Windows-11", "--", "cmd.exe", "/c", "ver"]) == exit_codes.EXIT_SUCCESS
    assert any(call.startswith("run:Windows-11:cmd.exe /c ver") for call in fake.calls)


def test_run_uses_default_instance(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert cli.main(["-e", "calc.exe"]) == exit_codes.EXIT_SUCCESS
    assert any(call == "run:Windows-11:calc.exe" for call in fake.calls)


def test_run_no_default_instance_errors(cli_env, capsys):
    assert cli.main(["-e", "calc.exe"]) == exit_codes.EXIT_INSTANCE_NOT_FOUND
    captured = capsys.readouterr()
    assert "默认实例" in captured.err


def test_run_missing_instance(cli_env, capsys):
    assert cli.main(["-d", "Nope", "--", "cmd.exe"]) == exit_codes.EXIT_INSTANCE_NOT_FOUND


def test_run_passes_through_program_exit_code(cli_env, capsys):
    svc, fake = _install(cli_env)
    fake.run_exit_code = 7
    assert cli.main(["-d", "Windows-11", "--", "cmd.exe", "/c", "exit 7"]) == 7


def test_run_backend_failure(cli_env, capsys):
    svc, fake = _install(cli_env)
    fake.run_error = "无法启动程序"
    assert cli.main(["-d", "Windows-11", "--", "cmd.exe"]) == exit_codes.EXIT_BACKEND_UNAVAILABLE


def test_run_dashdash_args_passthrough_unparsed(cli_env, capsys):
    svc, fake = _install(cli_env)
    # `--` 后的参数原样传给程序，不能被再次解析成 CLI 选项
    assert cli.main(["-d", "Windows-11", "--", "--help", "--list"]) == exit_codes.EXIT_SUCCESS
    assert any(call == "run:Windows-11:--help --list" for call in fake.calls)


def test_bare_interactive_run_uses_default_instance_and_program(cli_env, monkeypatch):
    # 裸 `lsw`（stdin 是 TTY）必须解析到配置的默认实例、启动配置的默认程序，
    # 以 interactive=True 通过服务/后端层，并原样透传程序退出码。
    svc, fake = _install(cli_env)
    # 第二个实例确保是"默认实例解析"而非"唯一实例"被选中。
    assert cli.main(["--install", "Other", "--no-launch"]) == exit_codes.EXIT_SUCCESS
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    monkeypatch.setattr("lsw.cli.sys.stdin.isatty", lambda: True)
    fake.run_exit_code = 7
    assert cli.main([]) == 7  # 退出码原样透传（services.run -> backend.run）
    name, argv, options = fake.run_calls[-1]
    assert name == "Windows-11"  # 解析到配置的默认实例
    assert argv == (svc.default_program,)  # 启动配置的默认程序
    assert options.interactive is True  # interactive=True 按预期传递
    assert fake.calls[-1].startswith("run:Windows-11:cmd.exe")


# ----------------------------------------------------------------- terminate


def test_terminate_running_instance(cli_env, capsys):
    svc, fake = _install(cli_env)
    fake.running.add("Windows-11")
    assert cli.main(["--terminate", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert "已终止实例 Windows-11。" in capsys.readouterr().out
    assert "Windows-11" not in fake.running


def test_terminate_already_stopped_is_idempotent(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--terminate", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert "已经停止" in capsys.readouterr().out


def test_terminate_missing_instance(cli_env, capsys):
    assert cli.main(["--terminate", "Nope"]) == exit_codes.EXIT_INSTANCE_NOT_FOUND


# ----------------------------------------------------------------- shutdown


def test_shutdown_with_no_instances(cli_env, capsys):
    assert cli.main(["--shutdown"]) == exit_codes.EXIT_SUCCESS
    assert "没有实例需要关闭。" in capsys.readouterr().out


def test_shutdown_success(cli_env, capsys):
    _install(cli_env, "Windows-11")
    _install(cli_env, "Windows-XP")
    assert cli.main(["--shutdown"]) == exit_codes.EXIT_SUCCESS
    assert "已关闭 2 个实例。" in capsys.readouterr().out


def test_shutdown_partial_failure_returns_nonzero(cli_env, capsys):
    svc, fake = _install(cli_env, "Windows-11")
    _install(cli_env, "Windows-XP")
    fake.shutdown_failures = {"Windows-XP"}
    assert cli.main(["--shutdown"]) == exit_codes.EXIT_OPERATION_FAILED
    captured = capsys.readouterr()
    assert "部分实例关闭失败" in captured.err
    assert "Windows-XP" in captured.err


# ---------------------------------------------------------------- unregister


def test_unregister_with_yes(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--set-default", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert cli.main(["--unregister", "Windows-11", "--yes"]) == exit_codes.EXIT_SUCCESS
    assert "已注销实例 Windows-11。" in capsys.readouterr().out
    assert not svc.repo.exists("Windows-11")
    assert svc.repo.get_default() is None  # 删除默认实例时清除默认值


def test_unregister_running_instance_refused(cli_env, capsys):
    svc, fake = _install(cli_env)
    fake.running.add("Windows-11")
    assert cli.main(["--unregister", "Windows-11", "--yes"]) == exit_codes.EXIT_INSTANCE_BUSY
    assert svc.repo.exists("Windows-11")  # 未被删除


def test_unregister_non_tty_without_yes_fails(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--unregister", "Windows-11"]) == exit_codes.EXIT_SECURITY
    assert svc.repo.exists("Windows-11")
    assert "确认" in capsys.readouterr().err


def test_unregister_tty_prompt_confirmed(cli_env, capsys, monkeypatch):
    svc, fake = _install(cli_env)
    monkeypatch.setattr("lsw.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "Windows-11")
    assert cli.main(["--unregister", "Windows-11"]) == exit_codes.EXIT_SUCCESS
    assert not svc.repo.exists("Windows-11")


def test_unregister_tty_prompt_mismatch_aborts(cli_env, capsys, monkeypatch):
    svc, fake = _install(cli_env)
    monkeypatch.setattr("lsw.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "别删")
    assert cli.main(["--unregister", "Windows-11"]) == exit_codes.EXIT_SECURITY
    assert svc.repo.exists("Windows-11")


def test_unregister_missing_instance(cli_env, capsys):
    assert cli.main(["--unregister", "Nope", "--yes"]) == exit_codes.EXIT_INSTANCE_NOT_FOUND


# --------------------------------------------------------------- set-version


def test_set_version_one_succeeds(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--set-version", "Windows-11", "1"]) == exit_codes.EXIT_SUCCESS
    assert "已将 Windows-11 的版本设为 1。" in capsys.readouterr().out


def test_set_version_unsupported(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--set-version", "Windows-11", "2"]) == exit_codes.EXIT_OPERATION_FAILED
    assert "尚不支持版本 2" in capsys.readouterr().err


def test_set_version_non_integer(cli_env, capsys):
    svc, fake = _install(cli_env)
    assert cli.main(["--set-version", "Windows-11", "latest"]) == exit_codes.EXIT_USAGE


def test_set_version_missing_instance(cli_env, capsys):
    assert cli.main(["--set-version", "Nope", "1"]) == exit_codes.EXIT_INSTANCE_NOT_FOUND


# ------------------------------------------------------------ module entry


def test_module_entrypoint_runs():
    result = subprocess.run(
        [sys.executable, "-m", "lsw", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert f"lsw {__version__}" in result.stdout
