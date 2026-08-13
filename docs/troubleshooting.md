# 故障排查

按症状查找。多数条目都从 `lsw --status` 开始——它显示后端可用性、Wine 版本、
默认实例、实例数与数据根目录。

## 工具：诊断起点

```console
$ lsw --status
$ lsw --list --verbose
$ ls -la ~/.local/share/lsw/instances/        # 实例数据（XDG 布局见 README）
$ wine --version                              # 宿主导航 Wine
```

## 常见问题

### 1. Wine 未安装或不可用

**症状**：`--status` 显示 `Backend: wine（不可用）`；`--install` 报错退出码 6。

**解决**：

```console
# 确认 Wine 在 PATH 中
$ which wine wineboot wineserver winepath
# 若缺，按发行版安装 Wine 6.0+（Ubuntu/Debian 示例）
$ sudo apt install wine wine32 wine64      # 注意：需要宿主 root，按需自行执行
$ wine --version
```

**说明**：LSW 需要 `wine`、`wineboot`、`wineserver`、`winepath` 四个可执行文件。
Wine 缺失时 `--install` **明确报错**而不创建实例（不会伪造成功）。若只想在没有
Wine 的机器上体验 CLI，可在 `config.toml` 设 `[backend] type = "fake"`。

### 2. GUI 程序不显示 / 黑屏 / 找不到 DISPLAY

**症状**：程序退出时报 X 相关错误（如 `cannot open display`）；或窗口不出现。

**解决**：

- **WSLg（Windows 11 WSL）**：确认 WSLg 启用，`DISPLAY`/`WAYLAND_DISPLAY` 应自动
  设置。检查：`echo $DISPLAY`。
- **WSL1 / 传统 X 转发**：需在宿主安装 X 服务器并设置 `DISPLAY`。LSW 会把
  `DISPLAY`/`XAUTHORITY` 透传给实例。
- **纯命令行/无 X**：Wine 有些程序需要窗口。可加 `wine` 的虚拟桌面，或在
  `config.toml` 中不映射显示变量时程序可能直接失败——请确认为 CLI-only 场景。

**说明**：LSW 只转发已有的 `DISPLAY`/`WAYLAND_DISPLAY`/`XAUTHORITY`，不负责搭建
显示服务器。无 WSLg/无 X 的主机不适合跑 GUI 程序。

### 3. 首次初始化慢 / 弹 Gecko/Mono 对话框

**症状**：`--install` 很慢；Wine 弹出"安装 Gecko/Mono"窗口；或 `winetricks`
类操作要求下载。

**原因**：Wine 需要 Gecko（浏览器引擎）与 Mono（.NET），首次使用会尝试下载。
下载慢或网络受限时卡住。

**解决**：

- 允许下载（等它完成），或
- 预先安装到系统 Wine 目录（`winetricks --no-isolate gecko mono` 之类），
  或
- 在 `config.toml` 里调大 `backend.startup_timeout_seconds`（默认 60s）：
  ```toml
  [backend]
  startup_timeout_seconds = 180
  ```
- 初始化超时（退出码 9）后，prefix 会**保留**供诊断——可直接检查
  `<data>/lsw/instances/<name>/prefix/`。

### 4. 实例元数据损坏 / 实例显示 Broken

**症状**：`--list` 中某实例显示 `Broken`，stderr 出现
`lsw: 警告: 实例 X 损坏：…`。

**原因**：`instance.toml` 被手工编辑坏、磁盘中断写、或版本不兼容。

**解决**：

- 列表/状态**不会**因单个损坏实例崩溃——这是设计（损坏容错）。
- 若该实例不重要：`lsw --unregister NAME --yes` 删除。
- 若重要：检查 `instance.toml` 内容与 `LSW_SPEC.md` 的 schema；修复后应恢复。
  ```console
  $ cat ~/.local/share/lsw/instances/NAME/instance.toml
  ```

### 5. 程序启动超时 / 实例卡在启动

**症状**：`--exec` 或初始化超时（退出码 9）。

**解决**：

- 检查是否有残留 `wineserver`：`pgrep -a wineserver`（或看实例是否被占）。
- `lsw --terminate NAME` 强制停止该实例；必要时 `lsw --shutdown`。
- 调大超时配置（见 §3）。
- 极少数情况需手动清理锁文件（确认没有进程在跑后再删，避免并发写）。

### 6. "实例忙"（退出码 7）

**症状**：操作报实例被锁。

**原因**：另一个 LSW 进程正持有该实例的 `flock`（例如另一个终端在跑同一个实例）。

**解决**：等它结束，或确认没有残留进程后重试。锁文件删除只在确认无并发时进行。

### 7. 删除实例时报安全拒绝（退出码 10）

**症状**：`--unregister` 被拒。

**解决**：默认需要交互输入实例名确认；非 TTY 或不匹配输入会取消。无人值守用
`--yes`。这只删除 LSW 数据（instance.toml + prefix），不碰宿主其他文件。

### 8. 程序行为与真实 Windows 不同

**原因**：Wine 是 Windows API 的用户态实现，兼容性不保证。个别程序需要
`winetricks`、专用 Wine 版本或 DLL 覆盖。

**提示**：LSW 只管理实例生命周期，不提供兼容性魔法。请为具体程序搜索 Wine
AppDB。若需特定 Wine 版本，可用 `config.toml` 的 `backend.wine_binary` 指向
另一个 Wine 构建（例如以新版本预置的 `wine`）。

### 9. Python 环境问题（开发/源码安装）

**症状**：`pip install` 失败、`python3 -m venv` 无 pip、`python -m lsw` 找不到。

**解决**：

```console
# 系统 Python 缺 ensurepip/pip 时引导：
wget https://bootstrap.pypa.io/get-pip.py -O /tmp/get-pip.py
python3 /tmp/get-pip.py
# 然后正常创建 venv 并安装：
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`python -m lsw` 需要在安装了 LSW 的环境里运行；从源码目录可
`PYTHONPATH=src python3 -m lsw --version` 先验证。

### 10. 日志去哪了 / 想开更详细的日志

状态根下 `~/.local/state/lsw/logs/`（可用 `LSW_STATE_HOME` 覆盖）是结构化事件
日志，条目已脱敏。`config.toml` 的 `[logging] level`（默认 INFO）与
`retention_days`（默认 14）控制详细度与保留期。日志写入尽力而为，失败静默忽略。

## 报告问题时应提供

- `lsw --version` 与 `lsw --status --json`
- 宿主：`uname -a`、发行版/版本、`wine --version`
- 触发命令与完整输出（含退出码）
- `~/.local/share/lsw/instances/` 目录列表
- 相关的 `~/.local/state/lsw/logs/` 最近条目
