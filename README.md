# 渲染后删除文件夹 · C4D Render Cleanup

> Cinema 4D 扩展。渲染结束后，自动把输出目录下指定名称的文件夹（默认 `mat` / `obj`）移入回收站。

**在线介绍页 → https://jaymik1947.github.io/c4d-render-cleanup/**

渲染一次，Redshift 就给你甩出一堆按 AOV 命名的文件夹：`mat`、`obj`、`crypto`……一条序列跑完，硬盘里躺着几十个只装了一两张 EXR 的目录。这个扩展干一件事：**渲染一结束，把它们扫进回收站**。

删错了能还原——走的是 Windows 回收站，不是永久删除。

![C4D](https://img.shields.io/badge/Cinema%204D-R2024%2B-1a1a1a?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-3776ab?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-2ea44f?style=flat-square)

---

## 功能

- **扩展菜单入口**：`扩展 → 渲染后删除文件夹`，有完整设置面板
- **自定义文件夹名**：默认 `mat`、`obj`，每行一个，随时增删
- **总开关**：不想用了取消勾选，立即生效，不用重启
- **可调延迟**：渲染结束后等多少秒再动手，默认 4 秒，可填小数
- **入回收站**：调用 Windows Shell API，可还原；检测到磁盘没有回收站则拒绝执行
- **安全护栏**：白名单匹配、层级检查、输出根目录保护、写入冷却、自动重试

## 安装

把 `plugin` 文件夹整个复制到 C4D 插件目录，任选其一：

```
%APPDATA%\Maxon\Maxon Cinema 4D 2024_XXXX\plugins\      ← 用户目录，推荐（升级不丢）
<C4D 安装路径>\plugins\                                  ← 程序目录（需写权限）
```

重启 Cinema 4D，日志出现下面这行就成功了：

```
插件注册：菜单=True 后台=True  版本=C4D 2024200  Python=3.11.4
```

日志在插件同目录的 `cryptocleanup.log`。

## 使用

1. `扩展 → 渲染后删除文件夹`
2. 勾选「启用」
3. 「要删除的文件夹名」每行一个，加一行就是新增
4. 填延迟秒数
5. 确定 → 按 `Shift+R` 渲染 → 等几秒 → 文件夹进回收站

名称匹配**精确且不区分大小写**：`mat` 能匹配 `MAT`，但匹配不到 `mat_2`。这是故意的——`mat_2` 那类带后缀的目录里往往是你的 Beauty 图。

## 配置

设置面板写的是 `plugin/cryptocleanup.json`，也可以直接改文件：

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 总开关 |
| `names` | `["mat","obj"]` | 要删除的文件夹名，精确匹配、不区分大小写 |
| `delay_ms` | `4000` | 渲染结束后延迟毫秒数 |
| `quiet_s` | `3.0` | 目录内文件多久没改动才算「写完」 |
| `max_retries` | `5` | 仍在写入时的重试次数，每次间隔 3 秒 |
| `cooldown_s` | `10` | 两次执行的冷却时间，防止重复触发 |
| `roots` | `["<render_output>"]` | 扫描根目录；`<render_output>` 取当前文档渲染设置 |
| `root_override` | `null` | 填绝对路径可跳过自动解析，强制指定扫描目录 |
| `recursive` | `false` | 是否递归扫描子目录 |
| `min_depth` | `3` | 路径层级下限，太浅的目录拒绝删除 |
| `protect` | `[]` | 保护路径，命中即跳过 |
| `allow_network` | `false` | 是否允许网络路径（回收站不可用，等于永久删除） |
| `allow_permanent_fallback` | `false` | 磁盘无回收站时是否退化为永久删除 |

## 它是怎么工作的

**触发**走双通道，因为 C4D 的渲染结束事件并不完全可靠：

1. `EVMSG_RAYTRACER_FINISHED` —— SDK 私有事件，快，但不保证必发
2. 1 秒心跳轮询 `CHECKISRUNNING_EXTERNALRENDERING`，检测 `True → False` 的落沿

第二条在实测中才是真正起作用的那条，第一条在 R2024 + Redshift 下没有触发。

**路径解析**是唯一有坑的地方：C4D 的 `RDATA_PATH` 存的是**未解析的原始串**，比如 `C:\renders\test\$pass\$prj$res`。插件会剥掉含 `$` 的片段，再回退到最近一个真实存在的目录，得到 `C:\renders\test`。

**删除**用 `ctypes` 调 `SHFileOperationW`（`FO_DELETE | FOF_ALLOWUNDO`），不依赖任何第三方库——C4D 内置的 Python 装不了 `send2trash`。

## 已知限制

- 只支持 **Windows**（回收站是系统 API）
- 只处理**外部渲染**结束，视口快速预览也可能触发清理，靠 `quiet_s` 和护栏兜底
- **Render Queue 多个任务共享同一个输出目录时不要用**，每个任务结束都会触发一次
- 网络盘默认跳过，因为回收站在网络盘上失效，`FOF_ALLOWUNDO` 会被忽略
- 放在程序目录时，**重装 / 升级 C4D 会丢插件**
- Team Render 下每个节点都会触发，`roots` 要用共享的绝对路径

## 用 AI 改造它

仓库根目录有 [`AGENTS.md`](AGENTS.md)，写清了模块职责、关键 API、已知坑和常见改造路径。Claude Code、Cursor、Copilot Workspace 之类的工具会直接读它。

最省事的起手式：

```text
读 AGENTS.md，然后帮我把文件夹名匹配改成支持通配符 mat* 和 obj*，
保持现有的所有护栏不变，改完告诉我动了哪些文件。
```

## 结构

```
plugin/
  cryptocleanup.pyp      # 扩展入口：菜单 + 设置面板 + 后台守护
  cryptocleanup_core.py  # 清理引擎：定位、校验、删除（无 c4d 依赖，可独立测试）
  cryptocleanup.json     # 配置
  run_now.py             # 手动触发，脚本管理器里跑
docs/
  index.html             # 介绍页
```

`cryptocleanup_core.py` 刻意不 import `c4d`，所以你可以用普通 Python 直接跑单元测试，不用开 C4D。

## License

MIT。随便改，随便发。
