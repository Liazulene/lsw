# 命令行参考

`lsw` 是稳定的命令行接口。约定：

- **长选项是稳定接口**，短选项是别名。
- `--exec` / `--` 之后的程序参数**原样透传**（stdout/stderr/退出码），不被解析。
- `--json` 输出到 stdout；诊断与警告到 stderr。
- 每个可预期错误映射到固定退出码。

## 命令一览

| 命令 | 动作 |
|---|---|
| `lsw --install [NAME]` | 安装/创建新实例（默认名 `Windows-11`） |
| `lsw --list` / `-l` | 列出实例 |
| `lsw --status [--json]` | 状态摘要 |
| `lsw --set-default/-s NAME` | 设置默认实例 |
| `lsw --exec/-e PROG ARGS…` | 执行程序 |
| `lsw -d NAME -- PROG ARGS…` | 在指定实例执行程序 |
| `lsw` | 交互式运行默认程序（需 TTY） |
| `lsw --terminate/-t NAME` | 终止实例 |
| `lsw --shutdown` | 关闭所有实例 |
| `lsw --unregister NAME [--yes]` | 注销/删除实例 |
| `lsw --set-version NAME 1` | 设置实例 schema 版本 |

## 全局选项

| 选项 | 说明 |
|---|---|
| `--version` | 显示 `lsw <版本>` 并退出（退出码 0） |
| `--help` / `-h` | 显示帮助并退出（退出码 0） |
| `-d NAME`, `--distribution NAME` | 安装时为新实例命名；执行时选择实例 |
| `--no-launch` | 安装后不启动（当前为默认行为，保留兼容） |
| `--yes` | 跳过 `--unregister` 确认 |
| `-v`, `--verbose` | `--list` 显示额外列 |
| `-q`, `--quiet` | `--list` 仅输出实例名 |
| `--json` | `--list` / `--status` 输出 JSON |

互斥与约束在冲突时返回用法错误（退出码 2）：

- 动作选项（`--install`/`--list`/`--status`/`--set-default`/`--terminate`/
  `--shutdown`/`--unregister`/`--set-version`/`--exec`）互斥。
- 位置参数 `NAME` 只能与 `--install` 一起用；`-d` 只能用于安装（命名）或执行（选实例）。
- `--no-launch` 只能用于安装；`--yes` 只能用于注销。
- `--` 后的参数只能用于执行。
- `-v`/`-q`/`--json` 只能用于 `--list`/`--status` 且三选一。
- `--exec/-e` 与 `--` 提供的程序参数不能同时使用。

## 命令详解与示例

### 安装 `lsw --install [NAME]`

```console
$ lsw --install Windows-11
正在初始化 Windows-11（Wine prefix）...
已安装 Windows-11。

$ lsw --install Debian-12        # 第二个隔离实例
$ lsw -d my-other-instance --install   # 等价命名方式
```

- 默认实例名来自配置 `default_install_name`（内置 `Windows-11`）。
- 会执行 `wineboot --init` 初始化隔离 prefix（受 `startup_timeout_seconds`
  约束，默认 60s）。
- 无 Wine 时返回退出码 6 且**不创建实例**。
- 已有同名实例返回退出码 5。

### 列出实例 `lsw --list [-l]`

```console
$ lsw --list
  NAME         STATE   VERSION  BACKEND
* Windows-11   Stopped 1        wine
  Debian-12    Running 1        wine

$ lsw --list --verbose
  NAME         STATE   VERSION  BACKEND  CREATED_AT                 INSTALL_STATE
* Windows-11   Stopped 1        wine     2026-08-13T02:10:00Z        Installed
  Debian-12    Running 1        wine     2026-08-13T02:11:30Z        Installed

$ lsw -l --quiet
Windows-11
Debian-12

$ lsw -l --json
{
  "schema_version": 1,
  "default": "Windows-11",
  "instances": [ ... ],
  "corrupt": []
}
```

- `*` 标记默认实例。
- 损坏实例显示为 `Broken`，并在 stderr 打印警告；列表仍正常返回（退出码 0）。

### 状态摘要 `lsw --status [--json]`

```console
$ lsw --status
Backend: wine（可用）
Wine version: 6.0.3
默认实例: Windows-11
实例数量: 2
数据根目录: /home/user/.local/share/lsw

$ lsw --status --json
{
  "schema_version": 1,
  "default_instance": "Windows-11",
  "instance_count": 2,
  "corrupt_count": 0,
  "backend": { "type": "wine", "available": true, "version": "6.0.3", ... },
  "data_root": "/home/user/.local/share/lsw"
}
```

- 无 Wine 时报告 `wine（不可用）`，`--install` 明确报错（退出码 6）。

### 设置默认实例 `lsw --set-default/-s NAME`

```console
$ lsw -s Windows-11
已将 Windows-11 设为默认实例。
```

### 执行程序 `lsw --exec/-e PROG ARGS…` 或 `lsw -d NAME -- PROG ARGS…`

```console
$ lsw --exec cmd.exe /c "echo hi"
hi

$ lsw -d Debian-12 -- notepad.exe                 # `--` 后全部透传
$ lsw -d Debian-12 -- cmd.exe /c "echo a && echo b"   # && 不会被 LSW 解析
$ lsw --exec curl.exe https://example.com
```

- 未指定 `-d` 时用默认实例；没有默认实例则报错（退出码 4，提示先
  `lsw --set-default NAME` 或 `-d NAME`）。
- stdout/stderr/退出码原样透传；程序被信号杀死映射为 `128+signum`。

### 交互式运行 `lsw`（裸命令）

```console
$ lsw
```

- 需要 TTY；无 TTY 且未给程序时返回用法错误（退出码 2）。
- 在默认实例中启动默认程序 `cmd.exe` 并保持交互（Ctrl-C 直达子进程）。

### 终止实例 `lsw --terminate/-t NAME`

```console
$ lsw --terminate Windows-11
已终止实例 Windows-11。
$ lsw --terminate Windows-11    # 幂等
实例 Windows-11 已经停止。
```

### 关闭所有实例 `lsw --shutdown`

```console
$ lsw --shutdown
已关闭 2 个实例。
```

- 部分实例失败时在 stderr 列出细节并返回退出码 8（成功关闭的实例不中断）。

### 注销/删除实例 `lsw --unregister NAME [--yes]`

```console
$ lsw --unregister Debian-12
确认删除实例 Debian-12？请输入实例名以确认（其它内容取消）：Debian-12
已注销实例 Debian-12。

$ lsw --unregister Windows-11 --yes
```

- 默认在 TTY 上要求输入实例名确认；输入不匹配或非 TTY 时取消（退出码 10）。
- 正在运行的实例会先被拒绝（`ensure_not_running`）。
- 删除不可逆：整个实例目录（含 prefix）被移除。

### 设置版本 `lsw --set-version NAME 1`

```console
$ lsw --set-version Windows-11 1
已将 Windows-11 的版本设为 1。
```

- 当前仅支持 `1`；非整数版本返回用法错误（退出码 2），整数非 `1` 返回操作失败
  （退出码 8）。

## 退出码

| 码 | 常量 | 含义 | 典型场景 |
|---|---|---|---|
| 0 | `EXIT_SUCCESS` | 成功 | — |
| 2 | `EXIT_USAGE` | 用法错误 | 非法组合、非法实例名、非 TTY 裸 `lsw` |
| 3 | `EXIT_CONFIGURATION` | 配置错误 | `config.toml` 损坏、不支持的 schema |
| 4 | `EXIT_INSTANCE_NOT_FOUND` | 实例不存在 | 找不到实例、无默认实例 |
| 5 | `EXIT_INSTANCE_CONFLICT` | 实例冲突 | 同名实例已存在 |
| 6 | `EXIT_BACKEND_UNAVAILABLE` | 后端不可用 | 缺 Wine、Wine 命令失败 |
| 7 | `EXIT_INSTANCE_BUSY` | 实例忙 | 实例被锁 |
| 8 | `EXIT_OPERATION_FAILED` | 操作失败 | 后端操作失败、元数据损坏 |
| 9 | `EXIT_TIMEOUT` | 超时 | 初始化/终止超时 |
| 10 | `EXIT_SECURITY` | 安全拒绝 | 未确认删除、不可信路径/元数据 |
| 130 | — | 用户中断 | Ctrl-C |

## 全局配置 `config.toml`

位置：`<config root>/config.toml`（默认 `~/.config/lsw/config.toml`，可用
`LSW_CONFIG_HOME` 覆盖）。可选——缺省即内置安全默认值。未知键警告；不支持的
`schema_version` 拒绝。

```toml
schema_version = 1
default_install_name = "Windows-11"

[backend]
type = "wine"                      # "wine" 或 "fake"（测试）
wine_binary = "wine"
wineboot_binary = "wineboot"
wineserver_binary = "wineserver"
winepath_binary = "winepath"
arch = "win64"
startup_timeout_seconds = 60
shutdown_timeout_seconds = 15

[execution]
default_program = "cmd.exe"
inherit_environment = true
working_directory = "inherit"

[filesystem]
map_home = false                   # 映射宿主 HOME 到 Wine 用户目录（默认关闭）
map_root = false                   # 保留 z: → /（默认关闭）
map_current_directory = true

[logging]
level = "INFO"
retention_days = 14
```

`default_instance` 键由 `lsw --set-default` 原子维护；手动编辑 `config.toml`
时也会被保留。
