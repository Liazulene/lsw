# 安全模型与已知限制

> **LSW 不是安全沙箱。** 它管理 Wine prefix 的生命周期与执行环境，但 Wine 中的
> Windows 程序以**当前 Linux 用户**的权限运行。本节说明 LSW 主动做的防护、
> 防护边界，以及诚实的剩余风险。

## 威胁模型

LSW 运行的 Windows 程序可以：
- 以当前 Linux 用户的权限读写 Linux 文件系统；
- 访问当前用户可访问的网络、D-Bus 会话、X11/Wayland 显示会话（若透传）。

LSW 的职责是把"运行环境"收拾干净，而不是把自己伪装成隔离边界。真正需要隔离时，
应在 LSW 之上再叠加容器/虚拟机（例如把 LSW 放进 Docker/Proxmox）。

## 主动防护

### 1. 元数据视为不可信输入

`instance.toml` 与 `config.toml` 都被当作外部输入处理：

- 布尔值严格解析（字符串 `"true"` 不会当真）；
- `arch` 白名单校验，未知架构拒绝；
- `prefix_directory` 只接受单个、不逃逸的相对段（拒绝 `.`/`..`/`/`/`\`）；
- 元数据与配置文件以 `O_NOFOLLOW` 打开，符号链接被拒绝；
- `instances/` 目录本身为符号链接时拒绝操作；
- 损坏的 `instance.toml` 被标记为 `Broken` 列出，而非崩溃整个 CLI。

### 2. 权限收紧

| 路径 | 模式 |
|---|---|
| 实例目录、`prefix/`、`locks/`、`.locks/`、tombstones/ | `0o700` |
| `instance.toml`、`config.toml`、锁文件 | `0o600` |

### 3. dosdevices 默认拒绝（初始化 + 每次启动前强制）

文件系统暴露策略在**安装初始化**和**每次程序启动前**都强制执行：

- `c:` → prefix 内 `drive_c`（不自动映射任意宿主目录）；
- 移除所有解析进 `/mnt` 的盘符链接（宿主挂载，如 `/mnt/c`、`/mnt/d`）；
- `map_root=false`（默认）时移除指向 `/` 的 `z:`；
- 只改动 dosdevices 目录的**直属符号链接**：`dosdevices` 目录本身为符号链接、
  或解析后逃出 prefix 时，跳过并拒绝跟随外部链；绝不改动无关的安全映射。

这意味着：安装后被重建的宿主盘符映射（无论来自用户、Wine 组件还是 Windows
程序自身）会在程序启动前被移除或拒绝。

### 4. 最小环境继承（秘密暴露边界）

Wine 子进程**只**继承白名单环境变量，其余丢弃：

**保留**：`PATH`、`HOME`、locale（`LANG`/`LANGUAGE`/`LC_ALL`/`LC_*`）、显示会话
（`DISPLAY`/`WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR`/`XAUTHORITY`）、D-Bus
（`DBUS_SESSION_BUS_ADDRESS`）、WSL 互操作（`WSL_INTEROP`/`WSL_DISTRO_NAME`）、
身份（`USER`/`LOGNAME`/`SHELL`/`TERM`/`HOSTNAME`）、临时目录
（`TMP`/`TEMP`/`TMPDIR`）、时区（`TZ`）。

**剔除**：`*_TOKEN`、`*_KEY`、`*_SECRET`、`*_PASSWORD`、`*_CREDENTIAL*`、
云/Provider 凭据（AWS/GCP/Azure 等）、`SSH_AUTH_SOCK` 及任何名字含
`agent`/`auth`/`socket`/`cookie` 的变量。

两个刻意例外：

- `XAUTHORITY` 为 X11 显示所需被**有意保留**（副作用：实例可访问你的 X 会话）；
- `RunOptions.environment`（API/测试调用方显式提供的值）被转发，但**永远不能
  覆盖**受保护的 `WINEPREFIX`/`WINEARCH`——这两个变量始终由实例派生。

### 5. 无 shell 执行

`process.py` 用 `exec` 直接构造进程，不经 `/bin/sh`。参数以数组传递，
无 shell 转义注入面；Windows 风格的参数（空格、引号、`&&`）原样到达程序。

### 6. 不触碰宿主 `~/.wine`

所有 Wine 子进程显式携带实例派生的 `WINEPREFIX`/`WINEARCH`。LSW 管理
`<data>/lsw/instances/<name>/prefix/` 下创建的 prefix，绝不动宿主 `~/.wine`。

### 7. 删除加固

- 删除走 tombstone（原子改名到 `<data>/lsw/tombstones/` 后移除）；
- 删除期间保留锁文件（防新 inode 上的 `flock` 竞争）；
- tombstone 移除对符号链接只 `unlink` 不 `rmtree`（不跟随）。

### 8. 日志脱敏

结构化事件日志对所有条目做脱敏：

- `redact_env`：环境变量值按名字（含 token/password/secret/apikey/credential/
  authorization 等敏感词）替换为 `<redacted>`；
- `redact_text`：文本中的 `key=value` / `key: value`（含引号形式）敏感键脱敏；
- `redact_argument`：`--key=value` 形式的敏感参数脱敏；
- 日志写入尽力而为：任何 I/O 失败静默忽略，日志绝不导致命令失败。

## 剩余风险（诚实声明）

1. **无沙箱**：Windows 程序拥有当前 Linux 用户的全部权限。这是设计使然——
   LSW 是 Wine 兼容管理器，不是安全边界。
2. **X11 会话暴露**：`XAUTHORITY`/`DISPLAY` 透传意味着实例内程序可访问你的
   显示会话（截屏、按键注入的边界取决于你的 X/Wayland 安全模型）。
3. **`map_root`/`map_home` 一旦开启**，实例可读写宿主根目录/宿主 HOME。
   默认关闭；开启前请自行评估。
4. **Wine 自身漏洞**：LSW 不审计 Wine。请对你运行的 Windows 软件设置信任等级；
   不要用 LSW 运行你不信任的代码而不加额外隔离。
5. **状态探测是启发式**：`/proc` 按精确 WINEPREFIX 找 `wineserver`，极端场景
   可能判为 `Unknown`（但不会误判为 Running/Stopped 而掩盖问题）。
6. **继承环境是白名单**：某些 Windows 程序可能依赖未被白名单收录的环境变量
   （如代理设置）。这是有意取舍——宁可缺变量，不可泄秘密。
7. **Alpha 质量**：0.1.0 的接口与安全加固仍在演进；生产使用前请评审你的部署。

## 配置项与安全的关系

| 配置 | 默认 | 说明 |
|---|---|---|
| `filesystem.map_root` | `false` | `true` 时保留 `z:` → `/`（危险） |
| `filesystem.map_home` | `false` | `true` 时映射宿主 HOME 到实例 |
| `filesystem.map_current_directory` | `true` | 启动目录映射 |
| `execution.inherit_environment` | `true` | 是否继承白名单环境 |
| `logging.level` / `retention_days` | INFO / 14 | 日志保留期（脱敏后） |
