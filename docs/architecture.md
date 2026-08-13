# 架构概览

> LSW 的分层结构：从 CLI 到 Wine prefix，每个层只依赖它下面的一层。

## 分层

```text
┌──────────────────────────────────────────────────────────────┐
│ CLI  lsw/cli.py                                               │
│  argparse 解析、程序参数拆分、命令分发、退出码映射             │
└───────────────┬──────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│ 应用服务  lsw/services.py                                     │
│  用例：install / list / status / set_default / run /          │
│        terminate / shutdown / unregister / set_version        │
│  组装：仓库 + 后端 + 事件日志 + 配置 + 退出码                  │
└───────────────┬──────────────────────────────────────────────┘
                │
        ┌───────┴───────────┐
        │                   │
┌───────▼───────────┐ ┌─────▼──────────────────────┐
│ 仓库 repository.py │ │ 后端 backends/             │
│ 实例元数据存储     │ │  WineBackend（默认）/      │
│ 原子写、锁、tombstone│ │  FakeBackend（测试）      │
└───────────────────┘ └─────┬──────────────────────┘
                            │
                 ┌──────────▼──────────┐
                 │ process.py 无 shell  │
                 │ exec + 捕获/交互/透传 │
                 └──────────┬──────────┘
                            │
                        Wine prefix
```

依赖方向单向向下：CLI → 服务 → 仓库 + 后端。领域模型（`models.py`）是纯数据对象，
任何层都可引用，但它自身不做 I/O、不感知 Wine。

## 模块职责

| 模块 | 职责 |
|---|---|
| `models.py` | 领域对象：`Instance` / `InstanceState` / `InstallState` / `BackendKind` / `FilesystemPolicy` / `RuntimeConfig` / `RunOptions` / `CommandResult`；日志脱敏辅助。 |
| `paths.py` | XDG 四根路径解析（config/data/state/cache），`LSW_*` 覆盖。 |
| `config.py` | 全局 `config.toml` 原子读改写；内置安全默认值；未知键警告；不支持 schema 拒绝。 |
| `validation.py` | 实例名校验 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`。 |
| `repository.py` | `instances/<name>/{instance.toml,prefix/,locks/}` 布局；元数据原子写（tmp+fsync+rename）；per-instance `flock`；tombstone 删除；路径/符号链接防逃逸；损坏容错。 |
| `locking.py` | `flock` 排他锁上下文管理器；非阻塞返回 `LockUnavailable`。 |
| `backends/base.py` | `Backend` 协议（probe/initialize/status/run/terminate/shutdown_all）。 |
| `backends/fake.py` | 可配置、记录调用的假后端，无需 Wine 跑通全生命周期。 |
| `backends/wine.py` | 真实 Wine 集成：探测、`wineboot --init` 初始化、`/proc` 状态探测、`wine` 执行、`wineserver -k` 终止、shutdown_all、dosdevices 策略、最小环境策略。 |
| `process.py` | 无 shell 的子进程执行器：捕获模式（有界输出、超时、独立进程组）、交互模式（继承 stdio、无超时）、非交互透传（信号转发）。 |
| `errors.py` / `exit_codes.py` | 错误层次与稳定退出码（见 `docs/cli.md`）。 |
| `cli.py` | 命令分发、`--` 参数拆分、输出格式化、退出码返回。 |

## 关键设计决策

### 实例 = Wine prefix

一个实例对应一个隔离的 Wine prefix（`<data>/lsw/instances/<name>/prefix/`）。
所有 Wine 子进程都显式携带实例派生的 `WINEPREFIX` 与 `WINEARCH`，因此 LSW
**绝不触碰宿主 `~/.wine`**，也绝不混用实例间状态。

### 无 shell 执行

`process.py` 用 `exec` 直接构造进程，不经 `/bin/sh`，参数以数组原样传递。
这避免 shell 转义注入，也保证 Windows 风格参数（空格、引号、`&&`）不被改写。

### 状态探测用 /proc 而非 wineserver 子进程

`wineserver` 没有无状态的查询操作（`-p`/`--persistent` 只设置持久化延迟），
调用它会产生副作用。因此 `status()` 改为纯读 `/proc`：按实例精确 WINEPREFIX
查找活着的 `wineserver` 进程。找到 → `Running`，找不到 → `Stopped`，
不可判定 → `Unknown`（探测失败不得自动等于 Stopped）。

### dosdevices 策略在初始化与每次启动前强制执行

`initialize()` 与 `run()`（程序启动前）都会：
1. 校验 dosdevices 目录本身不是符号链接、且解析后仍在 prefix 内；
2. 移除所有解析进 `/mnt` 的盘符链接（宿主挂载，默认拒绝）；
3. `map_root=false`（默认）时移除指向 `/` 的 `z:`；
4. 确保 `c:` 指向 prefix 内的 `drive_c`。

只改动 dosdevices 目录直属符号链接，绝不跟随逃逸链，也绝不改动无关的安全映射。

### 最小安全环境继承

`WineBackend` 启动子进程时以白名单 `_ENV_ALWAYS_PRESERVE` + `LC_*` 构建继承环境，
丢弃 token/密钥/凭据类变量；`XAUTHORITY` 为显示所需被有意保留；
`WINEPREFIX`/`WINEARCH` 由实例派生，调用方无法覆盖。

### 可插拔后端

`config.toml` 的 `[backend] type` 决定后端（`wine` 默认 / `fake` 测试）。
服务层只依赖 `Backend` 协议，测试可以注入记录式假后端与假 process runner。

## 目录结构

```text
src/lsw/
├── __init__.py       # __version__（打包与运行时版本的单一来源）
├── __main__.py       # python -m lsw 入口
├── cli.py            # 命令行
├── config.py         # 全局配置
├── errors.py         # 错误层次
├── exit_codes.py     # 退出码
├── locking.py        # flock 锁
├── models.py         # 领域模型
├── paths.py          # XDG 路径
├── process.py        # 无 shell 执行器
├── repository.py     # 实例仓库
├── services.py       # 应用服务
├── validation.py     # 校验
├── wineserver_probe.py  # /proc 状态探测
└── backends/
    ├── __init__.py   # create_backend(...)
    ├── base.py       # Backend 协议
    ├── fake.py       # 假后端（测试）
    └── wine.py       # 真实 Wine 集成
tests/
├── unit/             # 单元测试（后端契约经 fake runner）
└── integration/      # 真实 Wine 集成（标 wine/integration，无 Wine 跳过）
```

## 测试策略

- **单元层**：领域、仓库、CLI、服务，经 FakeBackend 与 fake process runner 断言
  契约（WINEPREFIX 正确性、初始化命令顺序、argv 数组、dosdevices、环境策略等）。
- **集成层**：`tests/integration/test_wine_integration.py` 标记 `wine`/`integration`，
  在装有 Wine 的主机上真实运行（用临时 XDG 根，绝不触碰 `~/.wine`），
  在无 Wine 的主机上模块级明确跳过。
- 门禁：`pytest`、`ruff check .`、`ruff format --check .`、`mypy src`。
