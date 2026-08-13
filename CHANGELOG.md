# Changelog

## 0.1.0 (2026-08-13) — 发布候选（M6）

> 里程碑 M1–M5.1 全部完成，进入 0.1.0 发布候选：文档、打包、版本卫生与发布门禁
> 已就绪。尚未发布到 PyPI。

### M5.1 — 运行期边界加固

- `lsw/backends/wine.py`：`run()` 在程序启动前再次强制执行 dosdevices 文件系统
  策略（`_enforce_filesystem_policy`），移除安装后被重建的 `/mnt` 宿主盘符映射与
  根盘符映射——无论重建来自用户、Wine 组件还是 Windows 程序自身。
- `_safe_dosdevices`：拒绝符号链接 dosdevices 目录、拒绝解析后逃出 prefix 的
  dosdevices，绝不跟随外部符号链接链；只改动 dosdevices 目录的直属符号链接，
  不动无关的安全映射（如用户自定义的 `x:`→`drive_c`）。
- 环境改为**最小安全继承**（`_ENV_ALWAYS_PRESERVE` 白名单 + `LC_*`）：子进程只
  转发 PATH/HOME/locale/显示会话（含 `XAUTHORITY`，显示所需的有意保留）/D-Bus/
  WSL 互操作/TMP/身份/TZ；`*_TOKEN`/`*_KEY`/`*_SECRET`/`*_PASSWORD`/
  `*_CREDENTIAL*`/云与 Provider 凭据/`SSH_AUTH_SOCK` 及名字含 agent/auth/socket/
  cookie 的变量一律不转发（`_is_sensitive_env_var` + `_safe_inherited_env`）。
- `WINEPREFIX`/`WINEARCH` 仍为受保护变量、由实例派生；`RunOptions.environment`
  视为有意的调用方显式值，仍不可覆盖受保护变量。
- 新增 11 项安全测试（dosdevices 运行期执行 + 环境策略）：重建盘符链接启动前
  被移除、链式逃逸不跟随、符号链接 dosdevices 忽略、安全自定义盘符保留、
  无 dosdevices 时不受影响、白名单/剔除类覆盖。
- 门径全绿：276 项 pytest（含 3 项真实 Wine 集成）、`ruff check .`、
  `ruff format --check .`、`mypy src`。

### M5 — 安全加固

- **元数据视为不可信输入**：`_as_bool` 严格布尔解析（字符串 `"true"` 不作真）；
  arch 白名单拒绝未知架构；元数据与配置经 `O_NOFOLLOW` 读取、拒绝符号链接；
  `prefix_directory` 拒绝逃逸；`instances/` 为符号链接时拒绝操作。
- **权限收紧**：实例/prefix/locks/.locks/tombstones 目录 `0o700`；
  `instance.toml`/`config.toml`/锁文件 `0o600`；初始化后 prefix `chmod 0o700`。
- **删除加固**：删除期间保留锁文件（防新 inode 上的 flock 竞争）；tombstone 改名
  失败→`OperationError`；`_remove_tombstone` 对符号链接只 `unlink` 不 `rmtree`
  （不跟随）。
- **环境变量安全合并**：`WINEPREFIX`/`WINEARCH` 为受保护变量，
  `RunOptions.environment` 不可覆盖，冲突→`BackendError`；继承环境先丢弃调用者
  预设的这两个变量。
- **dosdevices 默认拒绝**（初始化时）：移除所有解析进 `/mnt` 的盘符链接；
  `map_root=false`（默认）时移除 `z:`；不自动映射任意宿主目录。
- **日志脱敏**：`redact_env`/`redact_text`（含引号键值 `"api_key": "x"`）/
  `redact_argument`/`is_sensitive_key`；`EventLog` 尽力而为结构化日志、绝不抛错、
  安全实例名校验；Services 安装事件日志。
- 新增 `tests/unit/test_security.py`（28 项），覆盖元数据不可信、权限、删除、
  env 合并、dosdevices、脱敏、日志七类。
- 门径全绿：265 项 pytest（含 3 项真实 Wine 集成）、`ruff check .`、
  `ruff format --check .`、`mypy src`。

### M6 — 文档与打包（本版本）

- README 重写为发布质量文档：是什么/不是什么、特性、适用环境（已测试 vs
  预期可用）、安装/卸载、全部命令参考、生命周期示例、退出码、XDG 布局、
  安全模型与已知限制、架构概览、故障排查、FAQ、许可与商标。
- 新增 `docs/architecture.md`、`docs/cli.md`、`docs/security.md`、
  `docs/troubleshooting.md`。
- 打包：`pyproject.toml` 版本改为动态（`lsw.__version__` 单一来源）、license 从
  `LICENSE` 文件嵌入、新增 MIT 分类器；构建 sdist + wheel；全新临时 venv 安装
  产物验证 `lsw --version`/`--help`/`python -m lsw --version`。
- 版本卫生：运行时版本与打包元数据不可分离（构建时从 `__init__.py` 读取）。

### M4 — 真实 Wine 集成

- `lsw/process.py`（新增）：无 shell 的子进程执行器。捕获模式（内部探测）有
  界输出（1 MiB/流）、有限超时、独立进程组，超时 SIGTERM→SIGKILL 并报
  `OperationTimeoutError`；交互模式继承 stdio、留在终端前台进程组（Ctrl-C 直达
  子进程）、无超时；非交互透传在独立会话中按需转发信号。子进程退出码原样透传，
  被信号杀死映射为 `128+signum`，Ctrl-C 返回 130。`CommandRunner` 协议供测试注入
  fake runner。
- `lsw/backends/wine.py`：实现真实 Wine 集成——
  - `probe()`：可注入 executable resolver 查找 wine/wineboot/wineserver/winepath，
    全部存在后运行 `wine --version`（10s 超时、剥离 WINEPREFIX）得到版本号；
  - `initialize()`：`wineboot --init` 后 `wineserver -w`，均显式携带
    `WINEPREFIX=<data>/lsw/instances/<name>/prefix` 与 `WINEARCH`，受
    `startup_timeout_seconds`（默认 60s）约束；失败抛 `BackendError`、超时抛
    `OperationTimeoutError`，prefix 保留供诊断；之后落实 dosdevices 策略
    （`c:`→`drive_c`，`map_root=false` 时移除指向 `/` 的 `z:`，仅改 prefix 内符号链接）；
  - `status()`：不调用任何 wineserver 子进程——`wineserver` 无状态查询操作
    （`-p`/`--persistent` 仅设持久化延迟），改为侧效应为零的 /proc 探测：按实例精确
    WINEPREFIX 查找活着的 `wineserver` 进程，找到→Running / 找不到→Stopped /
    /proc 不可判定→Unknown（探测失败不得自动等于 Stopped）；
  - `run()`：`[wine, *argv]`（永不 shell 转义），环境覆盖调用者预设的 WINEPREFIX，
    stdio 透传，返回退出码；
  - `terminate()`：`wineserver -k`（按 prefix 作用域）后以同款 /proc 探测轮询至停止
    或超时，已停止则幂等；轮询不再用 `wineserver -p`；
  - `shutdown_all()`：逐个终止并聚合 `ShutdownResult`，单实例失败不中断其余。
  - 全程无 `pkill`/`killall`；状态探测仅按精确 WINEPREFIX 读取 /proc，不跨实例混合；
    绝不使用宿主 `~/.wine`。
- `lsw/wineserver_probe.py`（新增）：纯读 /proc 的 wineserver 存在性探测，
  以实例精确 WINEPREFIX 为键（per-prefix 隔离），返回 True/False/None。
- `lsw/backends/__init__.py`：`create_backend(kind, table, *, roots=…)` 把数据根目录与
  winepath/超时参数传入 `WineBackend`，供其解析实例绝对 prefix 路径。
- `lsw/services.py`：`install` 保留 `OperationTimeoutError`（退出码 9）而非包成 8；
  `repo.create` 使用配置 `arch`；`list` 将 PENDING 实例显示为 Installing、FAILED
  显示为 Broken（后端探测失败仍为 Unknown）。
- 契约测试（fake process runner，无需 Wine）：WINEPREFIX 正确性、初始化命令顺序、
  argv 数组、终止按 prefix 作用域、dosdevices 仅安全目标、Wine 缺失/非零退出/超时
  到领域错误的转换；`tests/unit/test_process.py` 用真实 `python`/`sleep` 验证
  退出码/捕获/argv/超时/信号映射；`tests/integration/test_wine_integration.py`
  标 `wine`/`integration`，无 Wine 时模块级明确跳过（`cmd /c exit 7`、list/status、
  terminate→stopped，均用临时 XDG 根、绝不触碰 `~/.wine`）。
- 质量门禁：219 项单元测试全绿、1 项集成测试在无 Wine 主机上明确跳过；
  `ruff check .`、`ruff format --check .`、`mypy src` 全干净。

### M3 — CLI MVP + FakeBackend

- WSL 风格 CLI：`--install [NAME] [--distribution] [--no-launch]`、
  `--list/-l [--verbose/-v|--quiet/-q|--json]`、`--status [--json]`、
  `--set-default/-s`、`--exec/-e PROG ARGS…`、`-d NAME -- PROG ARGS…`、
  `--terminate/-t`、`--shutdown`、`--unregister NAME [--yes]`、
  `--set-version NAME 1`。
- 分层：CLI → 应用服务 `lsw/services.py` → 仓库 + Backend 协议
  `lsw/backends/base.py`。Backend 通过 config `[backend] type` 选择
  （`wine`/`fake`）。
- `lsw/backends/fake.py`：可配置、记录调用的 FakeBackend，无需 Wine 即可
  跑完整个生命周期测试。`lsw/backends/wine.py`：M3 仅探测 Wine 可用性
  （`--status` 上报不可用、`--install` 无 Wine 时退出码 6 且不创建实例），
  真实集成留待 M4。
- 输出契约：JSON 到 stdout、诊断到 stderr；`--exec` 透传子进程
  stdout/stderr/退出码；`--` 后参数原样不解析；列表 `*` 标记默认实例，
  损坏实例显示 Broken 并警告到 stderr。
- 退出码：用法冲突 2、配置 3、实例不存在 4、冲突 5、后端/依赖不可用 6、
  实例忙 7、操作失败 8、安全确认 10；`KeyboardInterrupt` 130。
- 仓库 `delete` 改为 tombstone（原子改名到 `<data>/lsw/tombstones/` 后删除，
  清理失败时报告可恢复路径）。
- 生命周期端到端测试 188 项全绿（无需 Wine）。

### M2 — 实例仓库与配置

- `lsw/repository.py`：`instances/<name>/{instance.toml,prefix/,locks/}` 布局，
  create/get/list/delete/update_install_state，路径与符号链接越界防护，
  损坏元数据以 `InstanceListing` 标记而非中断列表。
- `lsw/locking.py`：`flock` 排他锁上下文管理器，非阻塞模式返回 `LockUnavailable`
  （仓库映射为 `InstanceBusyError`）。
- 元数据原子写：临时文件 + `fsync` + `os.replace`；并行同名创建仅一个成功。
- `lsw/config.py`：原子读改写 `config.toml`，`get/set/clear_default_instance`，
  保留用户已有键；写入经 `tomli-w`。
- `lsw/models.py`：`Instance.prefix_directory`（受控相对路径，拒绝逃逸）。
- 依赖新增 `tomli-w`。

### M1 — 工程骨架与领域模型

- 包结构与入口：`src/lsw`，`lsw` console script 与 `python -m lsw`。
- 错误层次 `lsw/errors.py` 与稳定退出码 `lsw/exit_codes.py`。
- 领域模型 `lsw/models.py`：Instance / InstanceState / InstallState / BackendKind /
  FilesystemPolicy / RuntimeConfig / BackendInfo / RunOptions / CommandResult，
  含日志脱敏辅助 `redact_argument`。
- XDG 路径解析 `lsw/paths.py`：config/data/state/cache 四个根，支持 `LSW_*`
  环境变量覆盖（测试注入）。
- 实例名校验 `lsw/validation.py`：`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`。
- 全局配置 `lsw/config.py`：内置安全默认值、未知键警告、不支持 schema 拒绝，
  Python 3.10 经 `tomli` 读取 TOML。
- CLI `lsw/cli.py`：`--help` / `--version`，其余调用返回用法错误退出码 2。
- 工具链配置：pytest、ruff、mypy、setuptools 构建（`pyproject.toml`）。
