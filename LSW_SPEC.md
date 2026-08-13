# LSW（Linux Subsystem for Windows）项目任务书 / Claude Code 实现提示词

> **把本文件完整交给 Claude Code。** 你是本项目的主要实现者。请先阅读全文，再检查当前仓库和运行环境，制定可验证的实施计划，然后分阶段实现。不要只输出示例或设计说明；应在当前仓库中交付可运行、可测试、可打包的首版代码。

## 0. 给 Claude Code 的总指令

请开发 **LSW — Linux Subsystem for Windows**：一个运行在 Linux 上、提供类似 WSL 管理体验的 Windows 用户空间实例管理器。首版以 **Wine prefix** 为运行后端，通过 `lsw` 命令创建、配置、启动、停止、查询和删除彼此隔离的 Windows 用户空间实例，并执行 Windows 程序。

它可以带一点一本正经的行为艺术气质：

> Run Windows userspace on Linux without leaving Linux.

但实现必须诚实、可靠、安全。文档必须明确说明：LSW v1 不是 Windows 内核、不是虚拟机、不是微软 WSL 的官方反向版本；它是 Wine prefix 生命周期和执行环境的管理层。

实施时必须遵守以下工作方式：

1. **先检查环境，不要先改系统。** 检查操作系统、架构、仓库状态、已有文件、可用语言工具链、Wine 版本和测试工具。记录结论，发现缺失依赖时给出提示，不要擅自安装系统包。
2. 阅读仓库内的 `README`、`AGENTS.md`、贡献指南和现有配置；保留用户已有改动，不覆盖无关文件。
3. 在编码前给出简短的阶段计划和关键假设；若仓库已有技术栈，优先沿用。若是空仓库，采用本文的推荐技术方案。
4. **分小步实现。每完成一步，就运行相关格式检查、静态检查和测试；失败时先修复再进入下一步。**
5. 不使用 `sudo`，不修改 `/etc`、引导器、内核模块、systemd 系统服务、防火墙、全局 Wine 配置或其他系统级状态。
6. 不自动下载 Windows ISO、微软 DLL、字体、运行库、注册表内容或其他许可不明的二进制文件；不打包或分发这些内容。
7. 不执行破坏性 Git 操作，不删除用户的既有数据。涉及删除 LSW 实例时必须有明确目标、路径边界验证和确认机制。
8. 不声称执行了没有实际运行的测试。若环境缺少 Wine，应运行所有无需 Wine 的测试，并将 Wine 集成测试明确报告为跳过。
9. 优先完成一个范围克制、行为稳定、接口可扩展的 MVP。不要为了 GUI、虚拟机或“完美兼容 Windows”拖垮首版。
10. 完成后汇报：实现内容、关键文件、实际运行的检查及结果、未完成项、已知限制和手工验证命令。

---

## 1. 项目定位

### 1.1 一句话定义

LSW 是 Linux 上的 Windows 用户空间实例管理器：以命名 Wine prefix 作为“发行版/实例”，用类似 WSL 的 CLI 管理其生命周期和程序执行。

### 1.2 首版用户价值

用户不必手工维护散落的 `WINEPREFIX`、环境变量和 Wine 进程；可以使用一致命令：

```console
lsw --install Windows-11
lsw --list --verbose
lsw --distribution Windows-11 -- cmd.exe
lsw --terminate Windows-11
lsw --unregister Windows-11
```

这里的 `Windows-11` 是用户为 Wine 用户空间配置选择的实例名/模板名，不表示附带 Windows 11、Windows 内核或微软许可证。

### 1.3 产品原则

- **熟悉的管理体验，诚实的技术语义。** 命令风格可以致敬 WSL，帮助文本不能误导。
- **用户态优先。** 默认无需 root，不触碰系统级配置。
- **实例隔离。** 每个实例拥有独立 prefix、元数据、日志和覆盖配置。
- **可预测。** 相同参数产生稳定结果；机器可读输出有固定 schema。
- **后端可替换。** v1 只有 Wine backend，但核心层不应与具体 Wine 命令纠缠。
- **安全失败。** 路径、名称、并发和删除操作均需防护；错误清晰且不泄露敏感值。
- **可测试。** 业务逻辑不依赖真实 Wine；使用 fake backend 覆盖绝大多数测试。

---

## 2. 非目标

首版明确不做：

- 不实现 Windows NT 内核，不提供内核级 Win32 兼容性。
- 不创建或管理 Windows 虚拟机、KVM/QEMU 镜像；未来若有 LSW 2，应另立提案。
- 不承诺所有 `.exe`、驱动、反作弊、内核服务、UWP、Microsoft Store 或 DirectX 游戏兼容。
- 不支持 Windows 内核驱动和需要真实 Windows 服务控制管理器的场景。
- 不提供 GUI、桌面外壳、Explorer 克隆、注册表编辑器克隆或控制面板。
- 不自动安装 Wine、winetricks、Mono、Gecko、GPU 驱动或系统包。
- 不下载、嵌入、重分发 Windows 专有文件或绕过产品激活与许可证。
- 不读取或接管用户现有的任意 Wine prefix；导入能力不属于首版。
- 不做容器级安全隔离。Wine prefix 是配置/数据隔离，不是安全沙箱。
- 不提供多用户守护进程、远程管理 API 或常驻 root daemon。

---

## 3. 安全边界与威胁模型

### 3.1 必须在文档和程序中讲清的边界

Wine 中运行的 Windows 程序本质上以当前 Linux 用户权限运行。LSW **不是安全沙箱**。恶意 Windows 程序可能读取当前用户可访问的 Linux 文件、联网、消耗资源或启动子进程。默认文件映射应尽量克制，但不能将其描述成强隔离。

### 3.2 数据根目录

遵循 XDG Base Directory：

- 配置：`${XDG_CONFIG_HOME:-~/.config}/lsw/config.toml`
- 数据：`${XDG_DATA_HOME:-~/.local/share}/lsw/`
- 状态：`${XDG_STATE_HOME:-~/.local/state}/lsw/`
- 缓存：`${XDG_CACHE_HOME:-~/.cache}/lsw/`

测试必须能通过环境变量把四个根目录重定向到临时目录。所有内部写入必须位于解析后的 LSW 根目录中。

推荐数据布局：

```text
~/.local/share/lsw/
├── instances/
│   └── Windows-11/
│       ├── instance.toml
│       ├── prefix/
│       └── locks/
└── version

~/.local/state/lsw/
└── logs/
    └── Windows-11/

~/.cache/lsw/
└── tmp/
```

### 3.3 路径与名称防护

- 实例名建议正则：`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`。
- 拒绝空字符串、`.`、`..`、路径分隔符、控制字符和前后空白。
- 实例目录必须由受验证的名字拼接，并在写入/删除前解析路径，确认仍位于 `instances/` 之下。
- 不跟随导致越界的符号链接。删除前逐层检查目标及数据根目录，绝不对未解析变量、通配符、根目录、家目录或 LSW 总目录做递归删除。
- 外部程序调用一律使用参数数组，不通过 shell 拼接命令；仅用户明确调用 shell 时才运行 shell。
- 配置中的路径进行 `expanduser`/规范化，但不得静默接受越界内部路径。
- 日志不得记录令牌、密码、完整敏感环境变量；默认可记录命令程序名和经过安全转义的参数。

### 3.4 删除与覆盖

- `lsw --unregister NAME` 是破坏性操作，交互终端中要求输入实例名确认，或要求 `--yes`。
- 非交互环境没有 `--yes` 时必须失败，不可挂起等待输入。
- 先将实例原子重命名到同一数据根目录下的临时 tombstone，再删除；若删除失败，给出可恢复位置或明确错误。
- 不允许覆盖已存在实例；后续如需重装，应由用户先注销。
- 不删除实例目录以外的日志/缓存，除非命令契约明确说明。

### 3.5 并发

- 修改同一实例时使用文件锁，至少保护 install、set-version、terminate、unregister 和元数据写入。
- 元数据采用“写临时文件 + `fsync`（可行时）+ 原子替换”。
- 两个并行安装同名实例只能有一个成功，另一个得到确定性冲突错误。
- 只读列表不得因一个损坏实例导致全部不可用；应标记或警告损坏条目。

---

## 4. 推荐实现技术与兼容范围

若仓库没有既定技术栈，推荐：

- Python 3.11+；尽量支持 Python 3.10。
- 包管理与构建：标准 `pyproject.toml`，使用当前环境已有的现代构建后端（优先 hatchling 或 setuptools；不要仅为偏好引入复杂工具）。
- CLI：优先标准库 `argparse`，减少运行时依赖；若仓库已有 Typer/Click 则沿用。
- 配置：TOML；读取使用 Python 3.11 `tomllib`，兼容 3.10 时声明轻量 fallback。写入可使用小型依赖或实现受控序列化。
- 测试：pytest。
- 静态检查/格式化：Ruff；若仓库已有工具，遵循现有规范。
- 类型检查：mypy 或 pyright，至少覆盖核心模块。
- 目标系统：首版支持常见 x86_64 Linux；在非 Linux 上提供清晰错误。其他架构可检测并报告为实验性/不支持。

禁止用“扫描 Wine 进程名称”作为唯一状态来源。尽量使用 Wine 自身的 prefix 管理命令和受控探测，并明确“状态”是瞬时观察结果。

---

## 5. 分层架构

建议采用以下依赖方向：

```text
CLI / presentation
        ↓
Application services / use cases
        ↓
Domain models + repository interfaces + backend interface
        ↓
Filesystem repository / Wine backend / process runner / locks
```

### 5.1 核心组件

1. **CLI 层**：解析 WSL 风格长短参数，生成表格、纯文本或 JSON，映射退出码。
2. **应用服务层**：安装、列举、设置默认、执行、终止、注销、更新/诊断等用例。
3. **实例仓库**：元数据读写、实例名验证、原子操作和目录布局。
4. **Backend 接口**：初始化 prefix、探测状态、执行程序、等待/终止 Wine server、查询版本。
5. **Wine backend**：将抽象操作转换为 `wineboot`、`wine`、`wineserver` 等安全子进程调用。
6. **进程运行器**：环境构造、超时、信号转发、stdout/stderr 和退出码透传。
7. **配置服务**：全局配置、实例覆盖、环境覆盖的合并与校验。
8. **日志服务**：结构化事件和可读日志，支持诊断但默认不污染被执行程序的输出。
9. **平台探测**：Linux、架构、Wine 可执行文件和版本、XDG 路径、TTY。

### 5.2 Backend 接口建议

不要让应用层直接构造 Wine 命令。定义类似以下能力（名称可按语言风格调整）：

```python
class RuntimeBackend(Protocol):
    def probe(self) -> BackendInfo: ...
    def initialize(self, instance: Instance) -> None: ...
    def status(self, instance: Instance) -> InstanceState: ...
    def run(self, instance: Instance, argv: Sequence[str], options: RunOptions) -> int: ...
    def terminate(self, instance: Instance, timeout: float) -> None: ...
    def shutdown_all(self, instances: Sequence[Instance], timeout: float) -> ShutdownResult: ...
```

应用层测试使用 `FakeBackend`，不得要求 CI 主机安装 Wine。

---

## 6. 建议目录结构

可以根据现有仓库调整，但职责必须等价且清晰：

```text
lsw/
├── pyproject.toml
├── README.md
├── LICENSE
├── CHANGELOG.md
├── src/
│   └── lsw/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── errors.py
│       ├── exit_codes.py
│       ├── models.py
│       ├── config.py
│       ├── paths.py
│       ├── validation.py
│       ├── services.py
│       ├── repository.py
│       ├── locking.py
│       ├── process.py
│       ├── logging.py
│       └── backends/
│           ├── base.py
│           ├── fake.py
│           └── wine.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── docs/
│   ├── architecture.md
│   ├── security.md
│   ├── cli.md
│   └── troubleshooting.md
├── packaging/
│   ├── completions/
│   └── man/
└── .github/workflows/ci.yml       # 仅在仓库采用 GitHub 时
```

命令入口应同时支持：

```console
lsw --help
python -m lsw --help
```

---

## 7. CLI 命令契约

### 7.1 总体规则

- 应尽量保留 WSL 的熟悉感，但不要为了逐字兼容而制造不合理行为。
- 长参数为稳定接口；常见短参数作为别名。
- 所有命令支持 `--help`；根命令支持 `--version`。
- 对列表/状态类命令提供 `--json`。JSON 输出到 stdout，诊断输出到 stderr。
- 普通执行应透传子进程 stdout、stderr 和退出码，不在 stdout 前后插入 LSW 文案。
- `--` 后的所有参数原样作为 Windows 程序参数，不能再次解析。
- 可支持现代子命令别名（如 `lsw install`），但本文列出的 WSL 风格接口必须工作，且两种入口应调用相同用例。

### 7.2 必须实现的 MVP 命令

#### 帮助与版本

```console
lsw --help
lsw --version
lsw --status
```

`--status` 输出默认实例、实例数量、Wine backend 是否可用、Wine 版本和数据根目录；不得因尚无实例而失败。

#### 安装/创建实例

```console
lsw --install
lsw --install Windows-11
lsw --install --distribution Windows-XP
lsw --install Windows-11 --no-launch
```

语义：

- `--install` 无名称时使用配置中的 `default_install_name`，默认 `Windows-11`。
- 验证环境和名称；Wine 缺失时给出安装提示并退出，不擅自安装。
- 创建目录和初始元数据，调用 `wineboot --init` 初始化 prefix。
- 初始化成功后标记为 installed；失败时不得留下“已安装”的半成品。可保留带失败状态的暂存目录用于诊断，但必须在错误中说明。
- 默认是否启动交互 shell应谨慎：为了脚本可预测性，推荐首版默认等价 `--no-launch`；若为了 WSL 风格选择默认启动，必须只在 TTY 中执行并在 README 说明。验收以行为一致、有测试为准。
- 实例名不是 Windows 镜像名称。可有 `profile = "windows-11-like"` 之类配置，但不得声称安装真实 Windows 11。

#### 列表

```console
lsw --list
lsw -l
lsw --list --verbose
lsw -l -v
lsw --list --quiet
lsw -l -q
lsw --list --json
```

表格示例：

```text
  NAME            STATE           VERSION   BACKEND
* Windows-11      Running         1         wine
  Windows-XP      Stopped         1         wine
```

- `*` 表示默认实例。
- `--quiet` 每行仅打印实例名，适合脚本。
- `--json` 必须包含 `schema_version`、`default`、`instances`；实例至少含 name、state、version、backend、created_at。
- 无实例时成功退出，表格给出简洁提示，quiet 输出为空，JSON 返回空数组。

#### 设置默认实例

```console
lsw --set-default Windows-11
lsw -s Windows-11
```

只接受已完整安装实例，并原子更新全局配置。

#### 执行 Windows 程序

```console
lsw --distribution Windows-11 -- cmd.exe
lsw -d Windows-11 -- notepad.exe C:\\notes.txt
lsw --exec calc.exe
lsw -e cmd.exe /c ver
lsw
```

语义：

- 未给 `-d` 时使用默认实例；没有默认实例时给出可行动错误。
- `--exec/-e` 后面的参数均为程序与参数；还应支持明确的 `--` 分隔形式。
- 裸 `lsw` 在交互终端中启动默认实例的 `cmd.exe`；非交互且无命令时给出用法错误，避免挂起。
- 设置该实例的 `WINEPREFIX`，并调用配置的 Wine 可执行文件。
- 子进程当前目录默认沿用调用者当前目录；如果启用 Windows 工作目录转换，应通过 `winepath` 严格处理，并写测试。
- Unix 信号转发给子进程组；Ctrl-C 不应留下无法解释的 LSW 包装进程。
- LSW 自身启动失败使用 LSW 退出码；Windows 程序成功启动后，尽可能透传其退出码。若退出码超出 POSIX 范围，在文档中定义映射并记录原值。

#### 终止实例

```console
lsw --terminate Windows-11
lsw -t Windows-11
```

使用指定 prefix 的 `wineserver -k`，等待至超时，必要时报告仍未退出。不得误杀其他 prefix 或通过模糊进程名杀进程。

#### 全部关闭

```console
lsw --shutdown
```

遍历受管理实例并调用 backend 终止。部分失败时汇总失败实例，返回非零；不得调用全系统级 kill。

#### 注销/删除实例

```console
lsw --unregister Windows-XP
lsw --unregister Windows-XP --yes
```

- 实例运行时默认拒绝，提示先 terminate；可以设计 `--force` 为显式“先终止再删除”，但首版可不实现。
- 执行第 3 节的确认、锁和路径防护。
- 若删除默认实例，清除默认值；不要擅自选择新的默认实例，除非 README 明确且有测试。

#### 设置版本

```console
lsw --set-version Windows-11 1
```

首版只接受 `1`。其他值返回“尚不支持”的明确错误。保留该接口是为了未来的其他 backend，而不是假装已经有 LSW 2。

#### 更新

```console
lsw --update
```

首版不得静默自更新或使用 root。可实现为：检查当前 LSW/Wine 版本、打印由包管理器更新的说明，并成功/以定义好的“不支持”状态退出。若无法提供有意义且可测试的语义，应明确标记为占位接口，不联网。

### 7.3 建议但非 MVP 阻塞项

```console
lsw diagnose [--json]
lsw config get KEY
lsw config set KEY VALUE
lsw logs NAME [--follow]
```

只有完成核心命令及测试后再实现。

---

## 8. 配置格式

### 8.1 全局配置 `config.toml`

建议 schema：

```toml
schema_version = 1
default_instance = "Windows-11"
default_install_name = "Windows-11"

[backend]
type = "wine"
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
map_home = false
map_root = false
map_current_directory = true

[logging]
level = "INFO"
retention_days = 14
```

要求：

- 缺少配置文件时使用安全默认值，不必提前创建。
- 未知键默认给出警告而不是静默拼错；未知 schema 版本必须拒绝。
- 配置优先级：CLI 参数 > `LSW_*` 环境变量 > 实例配置 > 全局配置 > 内置默认值。
- 环境变量只允许白名单，例如 `LSW_DATA_HOME`、`LSW_CONFIG_HOME`、`LSW_LOG_LEVEL`、`LSW_WINE_BINARY`；测试模式可注入根目录。
- 不将任意环境变量持久化到配置中。
- 布尔值、枚举、超时和路径均需严格校验。

### 8.2 实例元数据 `instance.toml`

```toml
schema_version = 1
id = "4c3c79fb-4e37-4a6a-a9ad-114c3439bc6f"
name = "Windows-11"
version = 1
backend = "wine"
state = "installed"
created_at = "2026-08-13T12:00:00Z"
updated_at = "2026-08-13T12:00:00Z"
prefix_directory = "prefix"

[runtime]
arch = "win64"
initialized_with_wine_version = "wine-10.0"

[filesystem]
map_home = false
map_root = false
map_current_directory = true

[labels]
profile = "windows-11-like"
```

- 内部 `prefix_directory` 必须是受控相对路径，不接受逃逸实例目录的值。
- `state` 字段表示持久化安装状态，不等于瞬时 Running/Stopped；运行状态由 backend 探测。
- 时间一律 UTC、RFC 3339。
- 元数据损坏时返回专门错误，并允许列表继续显示其他实例。

---

## 9. 数据模型

至少定义：

### `Instance`

- `id: UUID`
- `name: str`
- `version: int`
- `backend: BackendKind`
- `install_state: pending | installed | failed`
- `created_at / updated_at`
- `root_path / prefix_path`（运行时派生，避免持久化绝对路径）
- `runtime_config`
- `filesystem_policy`
- `labels`

### `InstanceState`

- `Running`
- `Stopped`
- `Installing`
- `Broken`
- `Unknown`

状态探测失败不能自动等同于 Stopped。

### `BackendInfo`

- backend 类型
- 是否可用
- 可执行文件解析结果
- 版本
- 架构能力
- 诊断信息

### `RunOptions`

- argv
- cwd
- environment overrides（白名单/显式值）
- interactive
- timeout（可选）
- capture/stream 模式

### `CommandResult`（内部命令）

- argv（可脱敏）
- exit code
- stdout/stderr
- duration
- timed_out

---

## 10. Wine backend 详细要求

### 10.1 环境探测

- 使用可注入的 executable resolver 查找 `wine`、`wineboot`、`wineserver` 和可选 `winepath`。
- 执行 `wine --version` 并以超时保护；不要依赖版本字符串的单一供应商格式。
- 检查宿主为 Linux。记录架构，但不要仅凭架构猜测所有 Wine 能力。
- 缺失依赖时给出发行版中立的提示，例如“请使用系统包管理器安装 Wine”，可在文档列出常见命令，但程序不自动执行。

### 10.2 Prefix 初始化

初始化环境至少包含：

```text
WINEPREFIX=<实例 prefix 的绝对路径>
WINEARCH=win64 或经验证的配置值
```

调用语义：

```console
wineboot --init
wineserver -w
```

- 给初始化设置可配置超时。
- Wine 首次运行可能请求安装 Mono/Gecko 或显示窗口。首版应在 README 说明；不要自动接受下载。
- 无图形会话时应尽可能正常支持 console 程序；若 Wine 初始化需要 DISPLAY/WAYLAND_DISPLAY，给出诊断。
- 初始化中断时将元数据标为 failed 或清理安全暂存目录，不可冒充成功。

### 10.3 运行程序

基本调用：

```text
[wine_binary, program, *args]
```

配合实例 `WINEPREFIX`。不要构造 shell 字符串。保留用户参数中的空格、反斜杠和 Unicode。

环境继承策略：

- 默认继承当前用户环境，覆盖 LSW 管理的 `WINEPREFIX`/必要变量。
- 默认忽略调用者预设的 `WINEPREFIX`，防止越过目标实例；可在 debug 日志说明已覆盖。
- 不继承明确列入敏感阻止清单的测试/内部变量到日志，但是否传给子进程应保持兼容并记录策略。
- 可设置 `WINEDLLOVERRIDES` 等高级项时，只从受验证的实例配置读取；MVP 不必暴露。

### 10.4 状态与终止

- 对单一 prefix 使用设置了 `WINEPREFIX` 的 `wineserver -p` 或可靠等价机制探测；先通过实际 Wine 行为验证返回码，再固化实现和测试。
- 终止使用同 prefix 的 `wineserver -k`，随后轮询等待到超时。
- 不使用 `pkill wine`、`killall wine` 或扫描并杀死所有 Wine 进程。
- `--shutdown` 只针对 LSW 仓库登记的 prefix。

### 10.5 Prefix 文件系统默认策略

Wine 通常创建 `dosdevices` 映射。LSW 初始化后必须审计并落实配置策略：

- `c:` 指向实例内 `drive_c`。
- 默认不主动创建 Linux `/` 的映射；若 Wine 自动创建 `z: -> /`，在 `map_root = false` 时安全移除该实例 prefix 内的链接。
- 默认不将整个 home 映射为盘符。
- `map_current_directory = true` 只表示运行时允许从当前目录访问/转换路径，不应永久暴露整个根目录。
- 操作 `dosdevices` 时仅允许修改实例 prefix 内的符号链接，检查链接与父目录边界。
- 在 README 中明确：隐藏盘符映射不构成强沙箱，程序仍继承 Linux 用户权限。

---

## 11. 实例生命周期

### 11.1 状态机

```text
Absent
  │ install
  ▼
Installing ──失败──> Failed/Broken
  │ 成功
  ▼
Installed + Stopped
  │ run
  ▼
Installed + Running
  │ terminate
  ▼
Installed + Stopped
  │ unregister + confirm
  ▼
Absent
```

- Running/Stopped 是瞬时 runtime state，不直接覆写持久化 install state。
- 安装状态转换必须可恢复，并且崩溃后能够诊断 `.pending`/暂存目录。
- `unregister` 只有在未运行且锁成功时执行。
- 默认实例只是全局引用，不改变实例自身状态。

### 11.2 幂等性

- 查询、帮助和状态命令无副作用。
- terminate 已停止实例可成功并提示“已经停止”，或返回定义好的非错误结果；行为必须有测试。
- shutdown 在没有实例时成功。
- install 同名实例必须明确冲突，不静默重用。
- set-default 设置为当前默认值应成功且不做无意义破坏。

---

## 12. 文件系统映射

### 12.1 路径语义

- LSW 自己的路径使用 Linux `Path` 语义。
- 传给 Windows 程序的参数默认原样保留，不猜测每个参数是否为路径。
- 若提供显式转换功能，设计为 `lsw path --windows /path` 或内部使用 `winepath -w`，不能自动误改类似 `/c` 的程序开关。
- 当前工作目录默认继承；在 Wine 无法正确表达时给出明确限制。

### 12.2 安全映射建议

MVP 仅承诺：

- 独立 `C:`。
- 按策略移除 `Z:` 根映射。
- 不主动创建 home 映射。
- 文档说明 Linux 文件访问不是安全隔离。

未来可设计显式映射：

```toml
[[filesystem.mounts]]
drive = "D:"
source = "/home/user/Documents"
read_only = false
```

但 Wine 符号链接不能真正强制只读，故首版不要暴露虚假的 `read_only` 安全承诺。

---

## 13. 进程执行与终端行为

- 使用无 shell 的 subprocess API。
- 交互执行继承 stdin/stdout/stderr；捕获模式用于内部探测。
- 内部探测设置有限超时和最大捕获输出，避免无限输出占用内存。
- Unix 上为交互程序建立合适的进程组/会话；处理 SIGINT、SIGTERM，并将信号传给子进程。
- CLI 包装器退出前清理临时资源和锁，但不要误杀已经按 Wine 语义脱离的后台程序。
- 程序退出码透传规则写入 `docs/cli.md`。
- 所有显示字符串支持 UTF-8；遇到不可解码的子进程输出时采用稳健的替换或字节透传策略。
- `cmd.exe /c` 的参数不得由 LSW 自行进行 Windows shell 转义；传入 Wine argv，由调用者负责 cmd 语义。

---

## 14. 日志与诊断

### 14.1 日志目标

- 用户可读错误短而可行动。
- debug 日志足以还原 LSW 的决策与 backend 调用，但不泄漏秘密。
- 被执行程序的 stdout/stderr 默认直接归程序所有，不混入日志。

### 14.2 建议事件字段

```text
timestamp, level, event, instance_id, instance_name,
backend, operation_id, duration_ms, exit_code, message
```

- 日志落在 XDG state 目录，可按实例分文件；默认 INFO。
- `--verbose`/`--debug` 可提升当前命令日志等级，debug 内容走 stderr 或日志文件。
- argv 日志需逐参数安全表示；对命名含 password/token/secret/key 的环境项和配置项脱敏。
- 提供合理轮转/保留策略；MVP 可以简单限制文件大小和备份数量，不必实现后台清理服务。
- 错误消息附 operation ID 便于关联日志。

### 14.3 `diagnose`（若实现）

只读检查：平台、XDG 路径、目录权限、Wine 命令及版本、实例元数据完整性和 backend 可用性。不得运行未知 Windows 程序或修改 prefix。JSON 中不得输出完整环境。

---

## 15. 错误模型与退出码

定义稳定异常层次，例如：

- `LswError`：预期的用户可处理错误
- `UsageError`
- `EnvironmentError`
- `DependencyMissingError`
- `ConfigurationError`
- `InvalidInstanceNameError`
- `InstanceNotFoundError`
- `InstanceAlreadyExistsError`
- `InstanceBusyError`
- `InstanceCorruptError`
- `BackendError`
- `OperationTimeoutError`
- `UnsafePathError`
- `ConfirmationRequiredError`

建议 LSW 自身退出码：

```text
0   成功
2   CLI 用法错误
3   配置错误
4   实例不存在
5   实例已存在/冲突
6   backend 或依赖不可用
7   实例忙/锁冲突
8   操作失败
9   超时
10  安全检查或确认失败
```

Windows 子程序已成功启动后原则上透传其 0–255 退出码。为避免与 LSW 错误混淆，文档应说明：是否启动成功可通过 debug/JSON 控制命令判断；普通 exec 以程序退出码为主。不要输出 Python traceback 给普通用户；`--debug` 时才展示开发信息。

错误格式示例：

```text
lsw: 实例“Windows-XP”不存在。
提示：运行 `lsw --list` 查看已安装实例。
```

---

## 16. 测试要求

### 16.1 单元测试（必须，无 Wine）

至少覆盖：

- 实例名称合法/非法边界，包括 Unicode、分隔符、`..`、超长、控制字符。
- XDG 路径解析和环境覆盖。
- 路径越界、符号链接和删除防护。
- 配置默认值、优先级、未知键、错误类型、schema 版本。
- 元数据序列化/反序列化、原子写入和损坏文件。
- CLI 参数组合、别名、`--` 透传和互斥参数。
- 列表三种输出及稳定 JSON schema。
- FakeBackend 下完整生命周期。
- 并行同名安装、实例锁和 busy 错误。
- 默认实例设置/删除行为。
- terminate/shutdown 幂等性和部分失败汇总。
- unregister 的 TTY、非 TTY、`--yes` 和运行中拒绝。
- 进程参数不经 shell，空格、引号、反斜杠和 Unicode 保持。
- 子进程退出码、超时和信号处理（在 CI 可控范围内）。
- 日志脱敏。

### 16.2 Wine backend 契约测试（必须，无 Wine）

通过 fake process runner 精确断言：

- `WINEPREFIX` 指向正确实例。
- 初始化命令和执行顺序正确。
- 程序参数为数组且未被 shell 拼接。
- terminate 只操作目标 prefix。
- Wine 缺失、非零退出、超时和异常输出被正确转换为领域错误。
- `dosdevices` 策略只修改实例内部安全目标。

### 16.3 真实 Wine 集成测试（条件执行）

标记为 `wine`/`integration`，Wine 不存在时自动跳过并说明原因。至少验证：

- 在临时 XDG 根目录初始化一个测试 prefix。
- 执行轻量命令（例如 `cmd /c exit 7`）并验证退出码。
- 列表状态可读取。
- terminate 后变为 stopped。
- 清理只发生在测试临时目录。

集成测试不得使用用户真实 `~/.wine`，不得在 home 中残留 prefix。

### 16.4 CLI 端到端测试

在隔离环境使用 fake backend 或测试 backend，从可执行入口验证 stdout、stderr 和退出码。对表格避免脆弱的全字符串断言；JSON 应严格验证 schema。

### 16.5 质量门槛

每个里程碑至少运行：

```console
pytest
ruff check .
ruff format --check .
```

以及项目配置的类型检查。目标核心模块覆盖率不低于 85%；不要为了数字排除困难代码。最终报告真实结果。

---

## 17. 打包与发布

### 17.1 Python 包

- `pyproject.toml` 声明 `lsw` console script。
- 生成 wheel 和 sdist，并在干净临时虚拟环境进行安装冒烟测试。
- 包内不含 Wine、微软二进制或预创建 prefix。
- 版本遵循 SemVer；MVP 可为 `0.1.0`。
- 明确 Python 与 Linux 平台要求。

### 17.2 Linux 发行包

MVP 必须提供发行版中立的 Python 安装说明。可额外提供：

- `pipx install ...`
- Debian/RPM/Arch 打包说明或模板（非首版阻塞项）。
- Bash/Zsh/Fish 补全和 man page（核心稳定后再做）。

不得在安装脚本里静默 `sudo` 或修改系统 Wine。若编写安装脚本，默认仅执行用户目录安装，并在每个系统级动作前显式说明和征得用户操作。

### 17.3 CI

如果仓库托管平台明确，配置 Linux CI：

- 支持的 Python 版本矩阵。
- lint、format check、type check、unit tests、build。
- 默认无需 Wine；可选单独 job 安装 Wine 运行集成测试，但不要把不稳定 GUI 依赖变成核心门槛。
- 上传构建产物前先验证 wheel 安装。

---

## 18. README 必须包含

README 面向普通 Linux 用户，至少包含：

1. 项目一句话简介和终端演示。
2. “LSW 是什么 / 不是什么”。首屏附近明确：Wine 管理器，不是真 Windows，不是安全沙箱。
3. 当前状态和兼容范围。
4. 依赖：Linux、Python、Wine；如何自行检查 Wine。
5. 安装方式。
6. 5 分钟快速开始：install、list、run、terminate、unregister。
7. 完整 CLI 摘要及进一步文档链接。
8. 数据存放位置和备份/删除含义。
9. 文件系统映射与安全警告。
10. 配置示例和优先级。
11. 常见问题：Wine 缺失、无 DISPLAY、Mono/Gecko 提示、32/64 位、实例损坏、程序兼容性。
12. 卸载 LSW 程序与删除实例数据是两件事；给出安全命令，不诱导宽泛递归删除。
13. 开发、测试和贡献说明。
14. 许可证与商标声明：项目与 Microsoft/WineHQ 无隶属关系；Windows、WSL 等商标归各自所有。
15. 路线图：LSW v1 = Wine backend；所谓 LSW 2/KVM 仅是未来概念，不承诺、不混入 MVP。

可以保留一句幽默：

> 正在安装 Windows-11……实际上我们正在非常认真地初始化一个 Wine prefix。

但错误信息、风险提示和安装说明不要玩梗。

---

## 19. 分阶段里程碑

严格按阶段推进；每阶段结束运行对应测试并简要记录结果。

### M0：环境与仓库审计

- 检查仓库、Git 状态、项目指令和现有技术栈。
- 检查 Linux、架构、Python、Wine、构建/测试工具。
- 不安装系统包，不修改系统。
- 输出实施计划、风险和假设。

**完成条件：** 已知工具链和约束，已有用户文件未被覆盖。

### M1：工程骨架与领域模型

- 创建包、入口、错误/退出码、模型、XDG 路径和名称验证。
- 配置 lint、测试、类型检查和构建。
- 完成路径、验证、配置基础测试。

**完成条件：** `lsw --help`、`lsw --version` 可运行；基础质量检查通过。

### M2：实例仓库与配置

- 实现目录布局、TOML schema、原子写、锁、默认实例。
- 使用临时 XDG 目录完成仓库单元测试。

**完成条件：** 可在 FakeBackend 下安全创建、读取、列出和删除元数据；路径越界测试通过。

### M3：CLI MVP + FakeBackend

- 实现 install/list/set-default/run/terminate/shutdown/unregister/set-version/status 的应用用例和 CLI。
- 完成 JSON、quiet、verbose 和退出码。
- 完成整个生命周期的端到端测试。

**完成条件：** 不安装 Wine也能跑完核心测试；CLI 契约稳定。

### M4：Wine backend

- 实现 probe、prefix 初始化、执行、状态、terminate 和文件映射策略。
- 完成 process runner 契约测试。
- 如环境有 Wine，运行真实集成测试；否则明确跳过。

**完成条件：** 在有 Wine 的 Linux 上能初始化隔离实例并执行 `cmd.exe /c`；在无 Wine 时错误清楚且无残留。

### M5：安全加固与可观测性

- 完善锁、符号链接防护、删除确认、超时、信号、日志脱敏和损坏实例恢复提示。
- 添加并发/故障注入测试。

**完成条件：** 所有已列安全用例有自动测试；无宽泛 kill/删除行为。

### M6：文档、打包与发布候选

- 完成 README、架构、安全、CLI、故障排除文档。
- 构建 wheel/sdist，在干净环境安装并冒烟测试。
- 运行全部 lint、format、types、unit、可用的 integration tests。

**完成条件：** 可从构建产物安装并完成快速开始；最终报告准确列出限制。

### M7（可选）：增强项

- diagnose、logs、配置命令、shell completion、man page。
- 仅在 M0–M6 全部完成后进行，不得牺牲核心质量。

---

## 20. MVP 验收标准

只有同时满足以下条件，才能称为完成：

### 功能

- [ ] `lsw --help` 和 `lsw --version` 正常。
- [ ] Wine 存在时，`lsw --install NAME --no-launch` 创建独立 prefix 和有效元数据。
- [ ] Wine 缺失时，install 安全失败、提示可行动、不自动安装、不留下伪成功实例。
- [ ] `lsw -l`、`-l -v`、`-l -q`、`--list --json` 输出正确。
- [ ] 默认实例能设置并被裸 `lsw`/`--exec` 使用。
- [ ] `lsw -d NAME -- cmd.exe /c ...` 参数保持并透传程序退出码。
- [ ] terminate 只终止目标 prefix，shutdown 只处理已登记实例。
- [ ] unregister 要求确认，运行中拒绝，路径验证有效，删除默认实例会修复全局引用。
- [ ] `--set-version NAME 1` 成功，其他版本明确报告不支持。
- [ ] `--status` 能报告 backend、Wine 版本、默认实例和数据目录。

### 安全

- [ ] 正常运行不要求 root，不写 `/etc` 或其他系统级目录。
- [ ] 所有子进程调用不使用 shell 拼接。
- [ ] 实例名无法路径穿越。
- [ ] 符号链接无法将内部写入或删除引向 LSW 根目录之外。
- [ ] 不使用全局 `pkill`/`killall` 终止 Wine。
- [ ] 默认不主动映射 Linux `/` 或整个 home；文档不虚假承诺沙箱。
- [ ] 不下载或分发微软专有组件。
- [ ] 日志对敏感配置和环境值脱敏。

### 工程质量

- [ ] 核心逻辑与 Wine backend 解耦，有 FakeBackend。
- [ ] 单元、CLI、backend 契约测试通过。
- [ ] 有 Wine 时真实集成测试通过；无 Wine 时合理跳过。
- [ ] lint、format 和类型检查通过。
- [ ] wheel/sdist 构建成功，wheel 在干净环境可安装并运行。
- [ ] README 和安全/架构/CLI/故障排除文档完整。
- [ ] 最终工作树不包含临时 prefix、测试缓存、用户数据或许可不明二进制。

---

## 21. 必测示例场景

### 场景 A：干净环境，无 Wine

```console
$ lsw --status
Backend: wine (unavailable)
...
$ lsw --install Windows-11 --no-launch
lsw: 未找到 Wine runtime。
提示：请先通过系统包管理器安装 Wine，然后运行 `lsw --status` 检查。
```

预期：非零退出，无系统修改，无 installed 实例。

### 场景 B：创建与执行

```console
$ lsw --install Windows-11 --no-launch
正在初始化 Windows-11（Wine prefix）...
已安装 Windows-11。

$ lsw -d Windows-11 -- cmd.exe /c ver
...
```

预期：仅使用该实例 prefix，命令退出码正确透传。

### 场景 C：两个实例隔离

创建 `Windows-11` 与 `Windows-XP`，分别写入独立 `C:` 文件，验证互不可见；终止其一不影响另一个。

### 场景 D：恶意名称与路径

```console
lsw --install ../escape
lsw --install /tmp/escape
lsw --unregister .. --yes
```

预期：全部在触及文件系统前拒绝。

### 场景 E：删除确认

非 TTY 执行 `lsw --unregister Windows-XP` 且无 `--yes`，应立即失败；加 `--yes` 后只删除精确实例。

### 场景 F：损坏元数据

一个实例的 TOML 损坏时，`--list` 仍列出健康实例，并对损坏项显示 Broken/警告；任何破坏性修复都需用户明确请求。

---

## 22. 实现决策要求

遇到本文未规定的细节时，按以下优先级决策：

1. 数据和系统安全。
2. CLI 可预测性与向后兼容。
3. 可测试性与清晰分层。
4. Linux/Wine 的真实语义。
5. 与 WSL 界面风格的相似度。
6. 行为艺术效果。

如果 WSL 的某个行为在 Wine 场景中不安全或不诚实，不要照抄；请选择安全语义，并在 README 的“与 WSL 的差异”中记录。

对于重要但存在不确定性的 Wine 行为（例如 `wineserver -p` 返回码、首次初始化交互、Wayland/无头环境），先在当前环境做最小只读或临时目录实验；将结论封装在 backend 内并添加测试。实验只能使用项目临时目录，不能污染用户真实 Wine prefix。

---

## 23. 最终交付报告模板

实现结束时，请输出简洁但完整的报告：

```markdown
## 已完成
- ...

## 关键设计
- ...

## 验证结果
- `pytest`: ...
- `ruff check .`: ...
- 类型检查: ...
- 构建与安装冒烟测试: ...
- Wine 集成测试: 通过 / 因未安装 Wine 跳过（原因）

## 安全确认
- 未使用 sudo
- 未修改系统级配置
- 未触碰用户现有 Wine prefix
- 未下载专有组件

## 已知限制
- ...

## 快速验证
```console
...
```
```

不要把“代码已生成”等同于“项目已完成”。只有实际运行检查、修复失败并满足验收标准后，才报告完成。

---

## 24. 现在开始

现在请按以下顺序行动：

1. 阅读仓库内指令与已有文件，检查 Git 状态。
2. 检查平台、架构、Python、Wine、构建和测试工具版本；不得自动安装系统依赖。
3. 根据现状给出 5–8 步实施计划，说明沿用或选择的技术栈。
4. 从 M1 开始小步实现；每个阶段运行测试并修复。
5. 完成 M0–M6 和 MVP 验收清单；可选项不要阻塞核心。
6. 构建可安装产物，做干净环境冒烟验证。
7. 按第 23 节提交最终报告。

请保持幽默感，但把它留在项目文案里；在权限、路径、进程、错误、测试和许可证问题上保持绝对严谨。
