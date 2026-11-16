# -*- coding: utf-8 -*-
"""
手动测试入口。在 C4D「脚本管理器」里打开执行。

FORCE_DRY = True   只列清单，不动文件
ROOT_OVERRIDE      填绝对路径可绕过「当前文档渲染设置」，指定别的输出目录来测试
"""

import os
import sys

FORCE_DRY = False
ROOT_OVERRIDE = None

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _HERE = r"把 cryptocleanup 插件目录填这里"

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import cryptocleanup_core as _core  # noqa: E402
import importlib  # noqa: E402

# 手动跑时强制重载核心模块，这样改了 core.py 不用重启 C4D 也能测到新逻辑
core = importlib.reload(_core) if "cryptocleanup_core" in sys.modules else _core

CONFIG_PATH = os.path.join(_HERE, "cryptocleanup.json")
LOG_PATH = os.path.join(_HERE, "cryptocleanup.log")


def render_output_dir():
    if ROOT_OVERRIDE:
        return ROOT_OVERRIDE
    import c4d
    doc = c4d.documents.GetActiveDocument()
    if doc is None:
        return None
    rdata = doc.GetActiveRenderData()
    if rdata is None:
        return None
    raw = str(rdata[c4d.RDATA_PATH] or "").replace("\\", "/")
    if not raw:
        return None
    if not os.path.isabs(raw):
        base = doc.GetDocumentPath() or ""
        if not base:
            return None
        raw = os.path.join(base, raw)
    return core.nearest_existing_root(raw)


def main():
    cfg, err = core.load_config(CONFIG_PATH)
    log = core.make_logger(LOG_PATH)
    log("== 手动触发 ==")
    if err:
        log(err)
    if FORCE_DRY:
        cfg["dry_run"] = True
    log("dry_run=%s  names=%r  输出目录=%s" % (cfg.get("dry_run"), cfg.get("names"), render_output_dir()))
    core.cleanup(cfg, log, output_resolver=render_output_dir)


if __name__ == "__main__":
    main()
