# LSW — Linux Subsystem for Windows

> 在 Linux 上跑 Windows 用户空间，不离开 Linux。

LSW 是一个运行在 Linux 上的 **Windows 用户空间实例管理器**：它把命名 Wine
prefix 当作"发行版/实例"，用一套 WSL 风格的命令行来创建、初始化、查询、启动、
停止、删除彼此隔离的实例，并在其中执行 Windows 程序。

```console
$ lsw --install Windows-11
正在初始化 Windows-11（Wine prefix）...
已安装 Windows-11。
$ lsw -d Windows-11 -- cmd.exe /c "echo hello from Windows userspace"
hello from Windows userspace
```

## LSW 是什么 / 不是什么

| | |
|---|---|
| **是** | 管理 Wine prefix 生命周期与执行环境的命令行工具（类 WSL 体验）。 |
| **不是 Windows 内核** | 没有 Windows NT 内核、没有 win32.sys、没有内核驱动。Windows 程序通过 Wine 在 Linux 上以用户态运行。 |
| **不是虚拟机** | 不包含 KVM/QEMU/VirtualBox，不存在客户机内核或硬件虚拟化。 |
| **不是微软官方 WSL** | 与 Microsoft 的 WSL/WSLg 无关，也非其"反向实现"。 |
| **不是安全沙箱** | 见下文[安全模型](#安全模型与已知限制)：Wine 中的 Windows 程序以**当前 Linux 用户**的权限运行，LSW 不提供沙箱/容器隔离。 |

`Windows-11` 只是你为 Wine 用户空间配置选的**实例名**，不代表随附 Windows 11、
Windows 内核或微软许可证。

## 特性一览

- **实例即 prefix**：每个实例是一个隔离的 Wine prefix，创建/初始化/查询/启动/
  停止/删除生命周期完整。
- **WSL 风格 CLI**：`--install` / `--list` / `--status` / `--set-default` /
  `--exec` / `--terminate` / `--shutdown` / `--unregister` / `--set-version`。
- **稳定退出码**：每个可预期错误映射到固定退出码，便于脚本使用。
- **无 shell 执行**：程序通过 `exec` 直接启动，不经 shell，参数原样透传。
- **默认拒绝的文件系统暴露**：初始化与每次启动前强制 dosdevices 策略，拒绝
  `/mnt` 宿主盘符映射，`map_root` 关闭时移除 `z:`（默认关闭）。
- **最小安全环境继承**：子进程只继承白名单环境变量，不转发 token/密钥/凭据。
- **元数据视为不可信输入**：损坏的 `instance.toml` 被标记为 Broken 而非崩溃。
- **可插拔后端**：默认 `wine` 后端；`fake` 后端无需 Wine 即可跑通全生命周期（测试用）。

## 适用环境

### 已测试

- **主机**：Ubuntu 22.04 on WSL2（内核 5.x/6.x，x86_64）
- **Python**：3.10.12
- **Wine**：6.0.3（M4 起真实 Wine 集成测试在此版本上运行）
- **LSW 版本**：0.1.0（本版本）

### 预期可用（未经本仓库测试）

- Python 3.11 / 3.12（包元数据声明 `>=3.10`；3.10 需要 `tomli` 依赖）
- 其他 x86_64 桌面 Linux（Debian/Arch 等）：核心逻辑不依赖 WSL 专有特性，
  但**仅 WSL2/Ubuntu 22.04 经过验证**。
- 较新版本 Wine（7/8/9/10 系列）：本仓库只测过 6.0.3。Wine 内部变化可能影响
  行为；如遇到问题请报告。

### 明确不支持

- 非 Linux 主机（macOS/Windows 原生/BSD）：依赖 `/proc`、`flock`、Linux 进程语义。
- 无图形环境的纯命令行使用可能受限：Wine 程序若要 GUI 需要显示服务器
  （WSLg / X11 / Wayland），见[故障排查](docs/troubleshooting.md)。

## 安装 / 卸载

LSW 未发布到 PyPI。当前三种安装方式：

**从源码开发安装**（推荐）：

```console
python3 -m venv .venv
# 若系统 Python 缺 ensurepip/pip，先引导（例如 Debian/Ubuntu 缺 python3-venv）：
#   wget https://bootstrap.pypa.io/get-pip.py -O /tmp/get-pip.py
#   .venv/bin/python /tmp/get-pip.py
.venv/bin/pip install -e ".[dev]"
.venv/bin/lsw --help
```

**从构建产物安装**（见[从源码构建](#从源码构建)）：

```console
pip install dist/lsw-0.1.0-py3-none-any.whl
```

**从源码目录直接运行**（需已安装运行时依赖，见上方开发安装；Python 3.10
需要 `tomli`/`tomli-w`）：

```console
PYTHONPATH=src .venv/bin/python -m lsw --version
```

**卸载**：

```console
pip uninstall lsw
```

### 从源码构建

```console
pip install -e ".[dev]"        # 一次性，提供 build 工具
python -m build
# 产物：dist/lsw-0.1.0.tar.gz 与 dist/lsw-0.1.0-py3-none-any.whl
```

## 快速上手

```console
$ lsw --version
lsw 0.1.0

$ lsw --status                 # 后端可用性、Wine 版本、默认实例、实例数、数据根
Backend: wine（可用）
Wine version: 6.0.3
默认实例: （无）
实例数量: 0
数据根目录: /home/user/.local/share/lsw

$ lsw --install Windows-11
正在初始化 Windows-11（Wine prefix）...
已安装 Windows-11。

$ lsw --list
  NAME         STATE   VERSION  BACKEND
* Windows-11   Stopped 1        wine

$ lsw -s Windows-11            # 设为默认实例
已将 Windows-11 设为默认实例。

$ lsw --exec cmd.exe /c "echo hi"
hi

$ lsw --terminate Windows-11   # 停止该实例的所有 Wine 进程
已终止实例 Windows-11。
```

没有 Wine 时：`--status` 上报 `wine（不可用）`；`--install` 明确报错（退出码 6）
且**不**创建实例。

## 命令行参考

完整用法见 [`docs/cli.md`](docs/cli.md)。所有命令一览：

| 命令 | 说明 |
|---|---|
| `lsw --install [NAME]` | 安装/创建新实例（默认名 `Windows-11`）。初始化 Wine prefix。 |
| `lsw --list` / `-l` | 列出实例；`--verbose/-v` 更多列，`--quiet/-q` 仅名字，`--json` JSON。 |
| `lsw --status [--json]` | 显示后端可用性、Wine 版本、默认实例、实例数、数据根目录。 |
| `lsw --set-default/-s NAME` | 设置默认实例。 |
| `lsw --exec/-e PROG ARGS…` | 在默认实例（或 `-d NAME` 指定）中执行程序，透传 stdio 与退出码。 |
| `lsw -d NAME -- PROG ARGS…` | 同上，用 `--` 分隔 CLI 参数与程序参数。 |
| `lsw`（交互式） | 有 TTY 时在默认实例中启动默认程序（`cmd.exe`）并保持交互。 |
| `lsw --terminate/-t NAME` | 终止实例（停止其所有 Wine 进程）。 |
| `lsw --shutdown` | 关闭所有实例；部分失败时退出码 8。 |
| `lsw --unregister NAME [--yes]` | 注销/删除实例。默认需交互输入实例名确认；`--yes` 跳过。 |
| `lsw --set-version NAME 1` | 设置实例 schema 版本（当前仅支持 `1`）。 |

常用修饰：`-d/--distribution NAME`（安装时命名 / 执行时选实例）、`--no-launch`
（安装后不启动，当前即默认行为）、`--yes`、`--verbose/-v`、`--quiet/-q`、
`--json`。互斥与约束（如 `--` 仅用于执行、`-d` 仅用于安装/执行）在冲突时报
用法错误（退出码 2）。

## 实例生命周期示例

```console
# 创建并确认
$ lsw --install Windows-11
$ lsw --install Debian-12            # 第二个隔离实例（名字随意，只要合法）
$ lsw --list

# 运行 Windows 程序
$ lsw -d Windows-11 -- cmd.exe /c "echo a && echo b"
$ lsw -d Debian-12 -- notepad.exe    # 每个实例独立 prefix，互不干扰

# 停止与关闭
$ lsw --terminate Debian-12
$ lsw --shutdown

# 删除（需确认）
$ lsw --unregister Debian-12
确认删除实例 Debian-12？请输入实例名以确认（其它内容取消）：Debian-12
已注销实例 Debian-12。
$ lsw --unregister Windows-11 --yes    # 无人值守删除
```

`--exec` / `--` 之后的一切都原样传给程序，不会被 LSW 解析。程序退出码原样透传
（被信号杀死映射为 `128+signum`；Ctrl-C 为 130）。

## 退出码

| 码 | 含义 | 典型场景 |
|---|---|---|
| 0 | 成功 | — |
| 2 | 用法错误 | 非法组合/非法实例名/非 TTY 的裸 `lsw` 交互 |
| 3 | 配置错误 | `config.toml` 损坏、不支持的 schema_version |
| 4 | 实例不存在 | 找不到实例 / 没有默认实例 |
| 5 | 实例冲突 | 已存在的同名实例 |
| 6 | 后端不可用 | 缺少 Wine、Wine 命令失败 |
| 7 | 实例忙 | 实例被其他进程锁住 |
| 8 | 操作失败 | 后端操作失败、实例元数据损坏 |
| 9 | 超时 | 初始化/终止超时 |
| 10 | 安全拒绝 | 未确认删除、不可信路径/元数据被拒绝 |
| 130 | 用户中断 | Ctrl-C |

## 文件布局（XDG）

LSW 遵循 XDG 基目录约定，四个根目录下统一使用 `lsw` 子目录，均可用
`LSW_*` 环境变量覆盖：

| 用途 | 默认路径 | 覆盖变量 |
|---|---|---|
| 配置 | `~/.config/lsw/` | `LSW_CONFIG_HOME`（← `XDG_CONFIG_HOME`） |
| 数据 | `~/.local/share/lsw/` | `LSW_DATA_HOME`（← `XDG_DATA_HOME`） |
| 状态 | `~/.local/state/lsw/` | `LSW_STATE_HOME`（← `XDG_STATE_HOME`） |
| 缓存 | `~/.cache/lsw/` | `LSW_CACHE_HOME`（← `XDG_CACHE_HOME`） |

四个根各自布局：

```text
~/.config/lsw/config.toml          # 全局配置（可选；缺省用内置安全默认值）

~/.local/share/lsw/                # 数据
├── instances/
│   ├── .locks/                    # 全局锁文件
│   └── <name>/
│       ├── instance.toml          # 实例元数据（0o600）
│       ├── prefix/                # Wine prefix（0o700）
│       └── locks/                 # 实例锁
└── tombstones/                    # 删除实例的原子改名暂存区

~/.local/state/lsw/logs/           # 结构化事件日志（脱敏）
~/.cache/lsw/                      # 缓存
```

全局配置 `config.toml` 可选，缺省即安全默认值。主要键（完整见
`docs/cli.md`）：

```toml
schema_version = 1
default_install_name = "Windows-11"

[backend]
type = "wine"              # 或 "fake"（测试，无需 Wine）
arch = "win64"

[execution]
default_program = "cmd.exe"

[filesystem]
map_home = false           # 是否映射宿主 HOME 到 Wine 用户目录
map_root = false           # 是否保留 z: → /（默认拒绝）
map_current_directory = true
```

## 安全模型与已知限制

完整讨论见 [`docs/security.md`](docs/security.md)。要点：

- **不是沙箱**。Wine 中的程序以当前 Linux 用户权限运行；`config.toml` 中的
  `filesystem.map_root` / `map_home` 一旦开启就把宿主目录暴露给实例。
- **dosdevices 默认拒绝**：初始化时与**每次程序启动前**都强制移除解析进 `/mnt`
  的宿主盘符映射；`map_root=false`（默认）时移除 `z:`；只动 prefix 内 dosdevices
  直属符号链接，绝不跟随逃逸链。
- **最小环境继承**：子进程只继承白名单环境变量（PATH/HOME/locale/显示会话/D-Bus/
  WSL 互操作/TMP/TZ 等），不转发 `*_TOKEN`/`*_KEY`/`*_SECRET`/`*_PASSWORD`/
  `*_CREDENTIAL*`/云凭据/`SSH_AUTH_SOCK`/agent·socket 变量。`XAUTHORITY` 为显示
  所需被有意保留。`WINEPREFIX`/`WINEARCH` 由实例派生，外部无法覆盖。
- **元数据不可信**：`instance.toml` 视为输入——严格类型校验、arch 白名单、
  拒绝符号链接、路径逃逸拒绝；损坏元数据标记为 Broken 而非崩溃。
- **权限收紧**：实例/prefix/锁/tombstone 目录 `0o700`，`instance.toml`、
  `config.toml`、锁文件 `0o600`。
- **日志脱敏**：所有日志经 `redact_*` 处理，敏感键值以 `<redacted>` 呈现。

**已知限制 / 诚实声明**：

- 它不是安全边界：恶意 Windows 程序可以像当前用户一样读写 Linux 侧文件。
- `XAUTHORITY` 透传给实例意味着 X11 客户端可访问你的 X 会话。
- `/proc` 状态探测是启发式（按精确 WINEPREFIX 找 `wineserver`）；极端场景可能
  判定为 Unknown。
- LSW 不审计 Wine 自身的漏洞；请为 Wine 设置你信任的等级。

## 架构概览

```
CLI (lsw/cli.py) → 应用服务 (lsw/services.py) → 仓库 (repository.py) + 后端 (backends/)
                                                    │
                                        wine (默认) / fake (测试)
                                                    │
                              lsw/process.py：无 shell exec、捕获/交互/透传
                                                    │
                                                Wine prefix
```

- `models.py`：纯领域对象（不接触 I/O）。
- `paths.py`：XDG 路径解析；`config.py`：全局配置（原子读改写）。
- `repository.py`：实例元数据原子写、flock、tombstone、路径防逃逸。
- `backends/wine.py`：probe / initialize（`wineboot --init`）/ run / terminate /
  shutdown_all，全部子进程显式携带实例派生 WINEPREFIX/WINEARCH，绝不触碰宿主
  `~/.wine`；dosdevices 与最小环境策略在初始化与每次启动时强制执行。
- 详见 [`docs/architecture.md`](docs/architecture.md)。

## 故障排查

Wine 安装、WSLg/显示、Gecko/Mono、损坏 prefix、超时、缺失依赖等常见问题与
解决步骤见 [`docs/troubleshooting.md`](docs/troubleshooting.md)。

## 常见问题（FAQ）

**LSW 能用它跑 Windows 游戏吗？**
也许，取决于游戏与 Wine 兼容性。LSW 是实例管理器，不是兼容性保证；性能/兼容性
取决于 Wine 本身。

**"Windows-11" 名字暗示装了 Windows 11？**
不。那只是默认实例名；实例跑的是 Wine 用户空间。

**需要微软许可证吗？**
不需要。LSW 只管理 Wine；不包含任何微软专有代码或二进制。

**为什么删实例还要输名字确认？**
防止误删整个 prefix（`unregister` 会删除实例数据）。需要无人值守可用 `--yes`。

**能管理 `~/.wine` 吗？**
不能也不打算。LSW 只管理自己 `data/lsw/instances/` 下创建的隔离 prefix。

**和 WSL 有什么区别？**
微软 WSL 运行完整的 Windows 内核兼容层（WSLg/WSL2）；LSW 运行的是 Wine 用户态
Windows API 实现。二者目标、依赖、许可完全不同。

## 项目状态与路线图

- 里程碑 M1（骨架/模型）、M2（实例仓库）、M3（CLI MVP）、M4（真实 Wine 集成）、
  M5（安全加固）、M5.1（运行期边界加固）已完成；M6（文档/打包）进行中。
- 版本 `0.1.0`，Alpha 质量：接口可能在 1.0 前调整。
- 完整任务书见 `LSW_SPEC.md`；变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。

## 许可与商标

MIT License，见 [`LICENSE`](LICENSE)。本项目与 Microsoft / WineHQ 无隶属关系；
Windows、WSL、WSLg 等商标归各自所有者。
