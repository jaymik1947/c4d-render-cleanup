# -*- coding: utf-8 -*-
"""
Cinema 4D 扩展：渲染后删除文件夹

安装：把整个 cryptocleanup 文件夹放进下面两个目录之一，然后重启 C4D
  用户目录  %APPDATA%\\Maxon\\Maxon Cinema 4D 2024_XXXX\\plugins
  安装目录  <C4D 安装路径>\\plugins

菜单：扩展 -> 渲染后删除文件夹
日志：cryptocleanup.log（与本文件同目录）

组成
  SettingsCommand / SettingsDialog : 扩展菜单里的设置界面（写 JSON 配置）
  CleanupRunner (MessageData)      : 后台守护，渲染结束后按配置执行
触发双通道
  A) EVMSG_RAYTRACER_FINISHED（快，SDK 私有事件，不保证必发）
  B) 1 秒心跳轮询 CHECKISRUNNING_EXTERNALRENDERING，检测 True->False 落沿
"""

import os
import sys
import time

import c4d

CMD_PLUGIN_ID = 1057722
MSG_PLUGIN_ID = 1057721
PLUGIN_TITLE = "渲染后删除文件夹"

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _HERE = c4d.storage.GeGetStartupPath()

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cryptocleanup_core as core  # noqa: E402

CONFIG_PATH = os.path.join(_HERE, "cryptocleanup.json")
LOG_PATH = os.path.join(_HERE, "cryptocleanup.log")

_LOG = core.make_logger(LOG_PATH)
_EVMSG_DONE = getattr(c4d, "EVMSG_RAYTRACER_FINISHED", None)


def raw_output_path():
    """渲染设置里的原始输出路径（可能带未解析的 $token）。"""
    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        return None
    rdata = doc.GetActiveRenderData()
    if rdata is None:
        return None
    raw = rdata[c4d.RDATA_PATH] or ""
    if not raw:
        return None
    raw = str(raw).replace("\\", "/")
    if not os.path.isabs(raw):
        base = doc.GetDocumentPath() or ""
        if not base:
            return None
        raw = os.path.join(base, raw)
    return raw


def render_output_dir():
    raw = raw_output_path()
    if not raw:
        return None
    return core.nearest_existing_root(raw)


# ---------------------------------------------------------------- 设置界面

ID_ENABLED = 1001
ID_NAMES = 1002
ID_DELAY = 1003
ID_GROUP_MAIN = 1100
ID_GROUP_DELAY = 1101
ID_LBL_NAMES = 1200
ID_LBL_WAIT = 1201
ID_LBL_SEC = 1202


class SettingsDialog(c4d.gui.GeDialog):

    def CreateLayout(self):
        self.SetTitle(PLUGIN_TITLE)

        self.GroupBegin(ID_GROUP_MAIN, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupSpace(4, 8)

        self.AddCheckbox(ID_ENABLED, c4d.BFH_LEFT, 0, 0, "启用（渲染结束后自动删除）")

        self.AddStaticText(ID_LBL_NAMES, c4d.BFH_LEFT, 0, 0,
                           "要删除的文件夹名（每行一个，加一行就是新增）：", 0)
        # 注意：Python 版顺序是 (initw, inith)，跟 C++ SDK 相反
        self.AddMultiLineEditText(ID_NAMES, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
                                  initw=0, inith=120, style=0)

        self.GroupBegin(ID_GROUP_DELAY, c4d.BFH_LEFT, 3, 1, "")
        self.AddStaticText(ID_LBL_WAIT, c4d.BFH_LEFT, 0, 0, "渲染结束后等待", 0)
        self.AddEditText(ID_DELAY, c4d.BFH_LEFT, 60, 0)
        self.AddStaticText(ID_LBL_SEC, c4d.BFH_LEFT, 0, 0, "秒再删除", 0)
        self.GroupEnd()

        self.GroupEnd()

        self.AddDlgGroup(c4d.DLG_OK | c4d.DLG_CANCEL)
        return True

    def InitValues(self):
        cfg, err = core.load_config(CONFIG_PATH)
        if err:
            _LOG(err)
        self.SetBool(ID_ENABLED, bool(cfg.get("enabled", True)))
        self.SetString(ID_NAMES, "\n".join(cfg.get("names") or ["mat", "obj"]))
        self.SetString(ID_DELAY, "%g" % (float(cfg.get("delay_ms", 4000)) / 1000.0))
        return True

    def Command(self, cid, msg):
        if cid == c4d.DLG_OK:
            cfg, err = core.load_config(CONFIG_PATH)
            if err:
                _LOG(err)

            cfg["enabled"] = bool(self.GetBool(ID_ENABLED))

            names = [line.strip() for line in (self.GetString(ID_NAMES) or "").splitlines() if line.strip()]
            # 去掉可能的引号/斜杠，只保留文件夹名
            cleaned = []
            for n in names:
                n = n.strip().strip('"').strip("'").rstrip("/\\")
                if n and os.sep not in n and "/" not in n:
                    cleaned.append(n)
            cfg["names"] = cleaned or ["mat", "obj"]

            try:
                seconds = float((self.GetString(ID_DELAY) or "").strip())
            except Exception:
                c4d.gui.MessageDialog("延迟时间填的数字看不懂，已按 4 秒处理。")
                seconds = 4.0
            cfg["delay_ms"] = int(max(0.5, min(600.0, seconds)) * 1000)

            try:
                core.save_config(CONFIG_PATH, cfg)
                _LOG("设置已保存: 启用=%s 名称=%r 延迟=%dms"
                     % (cfg["enabled"], cfg["names"], cfg["delay_ms"]))
            except Exception as exc:
                _LOG("设置保存失败: %r" % exc)
                c4d.gui.MessageDialog("设置保存失败：%r" % exc)
            return True

        if cid == c4d.DLG_CANCEL:
            return True

        return False


class SettingsCommand(c4d.plugins.CommandData):

    def Execute(self, doc):
        dlg = SettingsDialog()
        dlg.Open(c4d.DLG_TYPE_MODAL, CMD_PLUGIN_ID)
        return True

    def GetState(self, doc):
        return c4d.CMD_ENABLED


# ---------------------------------------------------------------- 后台守护

class CleanupRunner(c4d.plugins.MessageData):
    TICK_MS = 1000

    def __init__(self):
        self._active = False
        self._deadline = 0.0
        self._retry = 0
        self._last_run = 0.0
        self._was_rendering = False

    # 常驻 1s 心跳：既是兜底触发源，也是延迟执行的计时器
    def GetTimer(self):
        return self.TICK_MS

    def CoreMessage(self, mid, bc):
        try:
            if _EVMSG_DONE is not None and mid == _EVMSG_DONE:
                self._arm("EVMSG_RAYTRACER_FINISHED")
                return True

            if mid == c4d.MSG_TIMER:
                self._watch()
                if self._active and time.time() >= self._deadline:
                    self._tick()
                return True
        except Exception as exc:
            # 绝不让后台插件把异常抛进 C4D 的消息循环
            _LOG("CoreMessage 异常: %r" % exc)
            self._active = False
        return False

    def _watch(self):
        try:
            running = bool(c4d.CheckIsRunning(c4d.CHECKISRUNNING_EXTERNALRENDERING))
        except Exception:
            running = False
        if running:
            self._was_rendering = True
        elif self._was_rendering:
            self._was_rendering = False
            self._arm("渲染状态落沿")

    def _arm(self, source):
        if self._active:
            return
        cfg, _err = core.load_config(CONFIG_PATH)
        if not cfg.get("enabled", True):
            return
        delay = float(cfg.get("delay_ms", 4000)) / 1000.0
        self._active = True
        self._retry = 0
        self._deadline = time.time() + delay
        _LOG("检测到渲染结束（%s），%.1f 秒后处理" % (source, delay))

    def _tick(self):
        cfg, err = core.load_config(CONFIG_PATH)
        if err:
            _LOG(err)

        if time.time() - self._last_run < float(cfg.get("cooldown_s", 10)):
            _LOG("冷却中，本轮忽略")
            self._active = False
            return

        self._last_run = time.time()
        _LOG("开始处理，原始路径=%s  解析后=%s" % (raw_output_path(), render_output_dir()))
        res = core.cleanup(cfg, _LOG, output_resolver=render_output_dir)

        if res.get("busy") and self._retry < int(cfg.get("max_retries", 5)):
            self._retry += 1
            self._deadline = time.time() + 3.0
            _LOG("仍有文件在写，3 秒后重试（第 %d 次）" % self._retry)
            return

        self._active = False


if __name__ == "__main__":
    ok_cmd = c4d.plugins.RegisterCommandPlugin(
        id=CMD_PLUGIN_ID,
        str=PLUGIN_TITLE,
        info=0,
        icon=None,
        help="渲染结束后，把输出目录下指定名称的文件夹移入回收站",
        dat=SettingsCommand(),
    )
    ok_msg = c4d.plugins.RegisterMessagePlugin(
        id=MSG_PLUGIN_ID,
        str=PLUGIN_TITLE + " (后台)",
        info=0,
        dat=CleanupRunner(),
    )
    cfg, _err = core.load_config(CONFIG_PATH)
    _LOG("插件注册：菜单=%s 后台=%s  版本=C4D %s  Python=%s"
         % (ok_cmd, ok_msg, c4d.GetC4DVersion(), sys.version.split()[0]))
    _LOG("配置路径=%s" % CONFIG_PATH)
    _LOG("当前配置=%r" % (cfg,))
