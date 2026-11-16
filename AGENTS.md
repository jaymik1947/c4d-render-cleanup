# AGENTS.md

给 AI 编程助手（Claude Code / Cursor / Copilot / Codex 等）的项目说明书。人类读者看 [README](README.md) 就够。

---

## 1. 这个项目是什么

Cinema 4D 扩展。渲染结束后，把渲染输出目录下指定名称的子文件夹（默认 `mat`、`obj`）移入 Windows 回收站。

两个插件在同一个 `.pyp` 里注册：

| 插件 | ID | 类型 | 职责 |
|---|---|---|---|
| 渲染后删除文件夹 | `1057722` | `CommandData` | 扩展菜单入口 + 设置对话框 |
| 渲染后删除文件夹 (后台) | `1057721` | `MessageData` | 常驻守护，检测渲染结束并执行 |

## 2. 运行环境

- Cinema 4D **R2024+**，实测 `2024200`
- 内嵌 **Python 3.11.4**（不是系统 Python，装不了 pip 包，只能用标准库 + `ctypes`）
- **仅 Windows**（回收站依赖 `SHFileOperationW`）
- 插件目录：`%APPDATA%\Maxon\Maxon Cinema 4D 2024_XXXX\plugins\` 或 `<安装路径>\plugins\`

## 3. 文件职责

```
plugin/
  cryptocleanup.pyp       扩展入口。两种插件的注册、对话框布局、触发逻辑都在这里
  cryptocleanup_core.py   清理引擎。定位目录、校验护栏、执行删除。不 import c4d
  cryptocleanup.json      配置。对话框保存到这里，后台每次触发都重读
  run_now.py              手动触发入口，在 C4D 脚本管理器里跑
```

**`cryptocleanup_core.py` 不允许 `import c4d`。** 这是硬约束：它是纯标准库实现，可以脱离 C4D 直接用系统 Python 跑测试。想加 C4D 相关逻辑，加在 `.pyp` 里，把结果当参数传进 core。

## 4. 数据流

```
渲染结束
   │
   ├─ 通道 A: EVMSG_RAYTRACER_FINISHED  ─┐
   └─ 通道 B: 1s 心跳, True→False 落沿 ──┤
                                          ▼
                                    _arm(): 读配置, 算 deadline
                                          ▼
                          每 250ms... 不, 每 1s 检查 time >= deadline
                                          ▼
                                    _tick(): 读配置, 冷却检查
                                          ▼
   core.cleanup(cfg, log, output_resolver)
       ├─ resolve_roots()      取扫描根（含 <render_output> 解析 + root_override）
       ├─ find_targets()       枚举根下名字命中的子目录
       ├─ check()              逐条护栏校验
       └─ send_to_recycle_bin() / _purge()
```

## 5. 不能破坏的不变量

改代码前先读这节。这些护栏是踩过坑换来的，删掉会出真事故：

1. **精确名称匹配**。`names` 里的名字必须完全相等（忽略大小写）。不要改成 `startswith` 或通配符——`mat_2`、`obj_1` 这类目录里装的是用户的 Beauty 图，删了就是数据丢失。
2. **根目录本身不可删**。`check()` 里比对 `roots`，相等直接拒绝。
3. **`min_depth` 必须保留**。防止配置写错把盘根目录或工程目录扫进去。
4. **`protect` 语义**：目标路径等于保护路径，或保护路径在目标路径之内 → 拒绝。
5. **网络路径默认拒绝**。`FOF_ALLOWUNDO` 在 UNC / 映射盘上会被忽略，等于永久删除。只有 `allow_network: true` 才放行。
6. **无回收站时默认拒绝**。判断依据是盘根下有没有 `$Recycle.Bin` 目录。不要改回 `GetDriveTypeW`——在虚拟化环境下它会把本地盘误报成 `DRIVE_REMOTE`。
7. **`quiet_s` 写入冷却**。目录里最新文件修改时间距今小于 `quiet_s` 就判定为「还在写」，推迟重试。删掉会导致删到写一半的 EXR。
8. **`CoreMessage` 外面那层 try/except 不能去**。后台插件抛异常会污染 C4D 的消息循环。

## 6. 实战踩过的坑（重要）

这些不是理论，是调试两轮才摸出来的。

### 6.1 `RDATA_PATH` 存的是未解析的 token

用户渲染设置里的路径可能是 `C:\renders\test\$pass\$prj$res`。C4D **不会**在 `RDATA_PATH` 里替换这些占位符，要到保存那一刻才替换。

所以不能直接 `os.path.isdir(raw)`。`core.nearest_existing_root()` 的做法：按 `/` 切段 → 遇到含 `$` 的段截断 → 拼回去 → 从该路径向上回退到第一个真实存在的目录。

**坑中坑**：不能先 `os.path.normpath()` 再按 `/` 切分。`normpath` 在 Windows 上会把 `/` 全部还原成 `\`，导致整条路径变成一个段、第一段就命中 `$`、直接截空退到 `C:\`。必须**先切分再拼**。

### 6.2 `MSG_MULTI_RENDERNOTIFICATION` 收不到

它是 **NodeData 消息**，不是核心消息。`MessageData` 插件监听不到它，只有 Python 标签 / 生成器这类节点实现才有 `message()`。

想监听渲染结束，`MessageData` 只有两条路：`EVMSG_RAYTRACER_FINISHED` 或轮询 `CHECKISRUNNING_EXTERNALRENDERING`。

### 6.3 `EVMSG_RAYTRACER_FINISHED` 实测不发

在 R2024 + Redshift 下这条没有触发过（日志可证）。它是 SDK 标记为 `Private` 的事件。真正生效的是心跳落沿检测。

两条通道都要保留：A 快、B 稳，共用 `cooldown_s` 防止重复执行。

### 6.4 `GetTimer` 是「每条消息之后重新询问」

不是 `SetTimer`。返回非 0 就持续收到 `MSG_TIMER`，返回 0 就停表。这里的实现是恒定返回 1000ms，代价可忽略，换来一个可靠的心跳。

### 6.5 Python 版 `AddMultiLineEditText` 参数顺序与 C++ 相反

```python
# Python:  (id, flags, initw, inith, style)
self.AddMultiLineEditText(ID_NAMES, flags, initw=0, inith=120, style=0)

# C++ SDK: (id, flags, inith, initw, style)
```

按 C++ 的顺序写，Python 里高度会变成 0，控件直接塌掉看不见。

### 6.6 数值输入控件有类型问题

`AddEditNumber` + `SetFloat` 在整数/浮点之间行为不稳定，所以延迟用的是 `AddEditText` + 手动 `float()` 解析。改这块别退回数值控件。

### 6.7 回收站要用 `SHFileOperationW`

`send2trash` 在 C4D 内置 Python 里装不了。可用的是：

```python
ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
# fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
# pFrom 必须以 \0 结尾（ctypes 再补一个，构成双 null）
```

返回 0 且目标已消失才算成功。`IFileOperation` 的 COM 方案在 C4D 里权限和初始化都更麻烦，不要换。

## 7. 常见改造任务

### 加一个默认文件夹名

改 `cryptocleanup.json` 的 `names`，以及 `.pyp` 里两处兜底默认值（`SettingsDialog.InitValues`、`SettingsDialog.Command`）。三处要一致。

### 支持通配符匹配

改 `core.find_targets()` 里的 `wanted` 集合匹配逻辑，改用 `fnmatch.fnmatch(name.lower(), pattern)`。**同时**要收紧 `check()`：至少加上「目录内所有文件必须是 `.exr`」这类校验，否则 `mat*` 会连 `mat_2`（Beauty PNG）一起吃掉。

### 改成永久删除

把 `core.cleanup()` 里的 `send_to_recycle_bin(path)` 换成 `_purge(path)`。**不建议**：`_purge` 只在「磁盘无回收站 + `allow_permanent_fallback`」这一条路径上使用。

### 加一个「恢复上次删除」按钮

做不到。回收站还原需要 Shell 命名空间操作，`SHFileOperation` 不支持反向操作，得走 `IFileOperation` 或 `Shell.Application` COM，C4D 内嵌 Python 里不现实。

### 支持 macOS

`send_to_recycle_bin` 换平台实现，macOS 用 `NSFileManager trashItemAtURL:`（`pyobjc`，C4D 里没有）或调 `osascript`。`is_network_path` / `has_recycle_bin` 也要改。

### 加日志轮转

`core.make_logger()` 里加：写入前判断文件大小，超过阈值就重命名成 `.log.1`。目前日志是无限追加的。

## 8. 测试

`cryptocleanup_core.py` 可以脱离 C4D 直接测：

```python
import sys, tempfile, os
sys.path.insert(0, "plugin")
import cryptocleanup_core as core

out = tempfile.mkdtemp()
for n in ("mat", "obj", "rgba"):
    os.makedirs(os.path.join(out, n))

cfg = dict(core.DEFAULTS)
cfg.update({"dry_run": True, "quiet_s": 0.0, "min_depth": 3})
core.cleanup(cfg, print, output_resolver=lambda: out)
```

改完 core 后想在 C4D 里立刻验证，不用重启：脚本管理器里跑 `run_now.py`，它用 `importlib.reload` 强制重载 core 模块。

改 `.pyp` 则**必须重启 C4D**，插件注册只发生在启动时。

## 9. 代码约定

- 只用标准库。不要 `pip install` 任何东西。
- 中文注释和中文日志，与现有风格一致。
- 日志走 `core.make_logger()`，它同时写控制台和 `cryptocleanup.log`。
- 新增配置字段时：`core.DEFAULTS` 补默认值 → README 表格补一行 → 需要的话在对话框加控件。
- 插件 ID（`1057721` / `1057722`）不要改，硬编码在用户机器上。

## 10. 发布流程

1. 改 `plugin/` 下的文件
2. `python -m py_compile plugin/*.pyp plugin/*.py` 过一遍语法
3. 复制到 C4D 插件目录
4. 重启 C4D，看 `cryptocleanup.log` 里的注册行
5. `Shift+R` 渲染验证
