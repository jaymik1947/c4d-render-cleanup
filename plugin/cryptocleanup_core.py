# -*- coding: utf-8 -*-
"""
渲染输出目录下 mat / obj 文件夹的清理核心逻辑。
被 cryptocleanup.pyp（常驻插件）和 run_now.py（手动测试）共用。

删除方式：Windows 回收站（SHFileOperationW + FOF_ALLOWUNDO），可恢复。
"""

import ctypes
import json
import os
import sys
import time

TOKEN_RENDER_OUTPUT = "<render_output>"

DEFAULTS = {
    "enabled": True,
    "dry_run": True,
    "delay_ms": 4000,
    "quiet_s": 3.0,
    "max_retries": 5,
    "cooldown_s": 10,
    "names": ["mat", "obj"],
    "roots": [TOKEN_RENDER_OUTPUT],
    "recursive": False,
    "min_depth": 3,
    "root_override": None,
    "allow_network": False,
    "allow_permanent_fallback": False,
    "protect": [],
}


def load_config(path):
    cfg = dict(DEFAULTS)
    if not os.path.isfile(path):
        return cfg, "配置文件不存在: %s" % path
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception as exc:
        return cfg, "配置读取失败: %r" % exc
    if isinstance(data, dict):
        cfg.update(data)
    return cfg, ""


def save_config(path, cfg):
    """原子写入，避免写一半把配置写坏。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return True


def make_logger(logfile):
    def _log(msg):
        line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print("[CryptoCleanup] " + msg)
        try:
            with open(logfile, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    return _log


def _norm(path):
    return os.path.normpath(os.path.abspath(path)).replace("\\", "/").lower()


def is_network_path(path):
    p = str(path).replace("/", "\\")
    return p.startswith("\\\\")


def nearest_existing_root(raw):
    """
    渲染设置里的路径可能带未解析的 $token（$pass / $prj / $res ...），
    也可能指向还没创建的目录。这里剥掉含 $ 的片段，再回退到最近一个真实存在的目录。
    C:\\Users\\x\\Desktop\\test\\$pass\\$prj$res  ->  C:\\Users\\x\\Desktop\\test
    """
    p = str(raw).replace("\\", "/")
    drive, rest = os.path.splitdrive(p)
    keep = []
    for seg in [s for s in rest.split("/") if s]:
        if "$" in seg:
            break
        keep.append(seg)

    base = os.path.join(drive + "\\", *keep) if keep else drive + "\\"
    base = os.path.normpath(base)

    while base and not os.path.isdir(base):
        parent = os.path.dirname(base)
        if parent == base:
            break
        base = parent
    return base


def has_recycle_bin(path):
    """该磁盘卷上有没有回收站。没有的话 FOF_ALLOWUNDO 会变成永久删除。"""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if not drive:
        return False
    return os.path.isdir(os.path.join(drive + "\\", "$Recycle.Bin"))


def resolve_roots(cfg, output_resolver=None, log=None):
    """展开 roots。[<render_output>] 交给调用方的解析函数。"""
    override = cfg.get("root_override")
    if override:
        p = os.path.expandvars(os.path.expanduser(str(override)))
        if os.path.isdir(p):
            return [os.path.normpath(os.path.abspath(p))]
        if log:
            log("root_override 不存在，已忽略: %s" % p)

    roots = []
    for item in cfg.get("roots") or []:
        item = str(item)
        if item.strip() == TOKEN_RENDER_OUTPUT:
            if output_resolver is None:
                if log:
                    log("roots 含 <render_output>，但没有可用的渲染输出路径，跳过")
                continue
            resolved = output_resolver()
            if not resolved:
                if log:
                    log("<render_output> 解析失败（渲染设置未指定输出路径？）")
                continue
            roots.append(resolved)
        else:
            roots.append(os.path.expandvars(os.path.expanduser(item)))
    return [os.path.normpath(os.path.abspath(r)) for r in roots if r]


def find_targets(roots, names, recursive=False, log=None):
    """在 roots 下查找名字命中 names 的文件夹。"""
    wanted = {str(n).strip().lower() for n in (names or []) if str(n).strip()}
    hits = []
    for root in roots:
        if not os.path.isdir(root):
            if log:
                log("输出目录不存在，跳过: %s" % root)
            continue
        if recursive:
            for cur, dirs, _files in os.walk(root):
                for d in dirs:
                    if d.strip().lower() in wanted:
                        hits.append(os.path.join(cur, d))
        else:
            try:
                entries = os.listdir(root)
            except OSError as exc:
                if log:
                    log("读取目录失败 %s —— %r" % (root, exc))
                continue
            for d in entries:
                full = os.path.join(root, d)
                if d.strip().lower() in wanted and os.path.isdir(full):
                    hits.append(full)
    uniq = {}
    for h in hits:
        uniq[_norm(h)] = os.path.normpath(os.path.abspath(h))
    return sorted(uniq.values(), key=lambda p: len(p), reverse=True)


def check(path, cfg, roots, now=None):
    """返回 (可删?, 原因)。"""
    now = now if now is not None else time.time()

    if not os.path.isdir(path):
        return False, "不存在或不是文件夹"

    if is_network_path(path) and not cfg.get("allow_network", False):
        return False, "网络路径默认跳过：回收站不可用，会变成永久删除"

    if not has_recycle_bin(path) and not cfg.get("allow_permanent_fallback", False):
        return False, "该磁盘没有回收站($Recycle.Bin)，无法保证可恢复"

    depth = len([seg for seg in _norm(path).split("/") if seg])
    if depth < int(cfg.get("min_depth", 3)):
        return False, "层级过浅(min_depth=%s)" % cfg.get("min_depth")

    npath = _norm(path)
    for root in roots:
        if npath == _norm(root):
            return False, "这是输出根目录本身，拒绝删除"

    for prot in cfg.get("protect") or []:
        np = _norm(os.path.expandvars(str(prot)))
        if npath == np or np.startswith(npath + "/"):
            return False, "命中保护路径: %s" % prot

    newest = 0.0
    for cur, _dirs, files in os.walk(path):
        for name in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(cur, name)))
            except OSError:
                pass
    quiet = float(cfg.get("quiet_s", 3.0))
    if newest and (now - newest) < quiet:
        return False, "文件仍在写入(%.1fs 内有改动)" % (now - newest)

    return True, ""


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400


def send_to_recycle_bin(path):
    """把文件夹移进回收站。失败抛异常。"""
    if sys.platform != "win32":
        raise RuntimeError("仅支持 Windows")

    abs_path = os.path.abspath(path)
    if is_network_path(abs_path):
        raise RuntimeError("网络路径不支持回收站")

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = abs_path + "\0"  # ctypes 再补一个 \0，构成双 null 结尾
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI

    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError("SHFileOperationW 返回 %d" % result)
    if op.fAnyOperationsAborted:
        raise OSError("删除被系统中断")
    if os.path.exists(abs_path):
        raise OSError("调用返回成功但目录仍然存在")


def _purge(path):
    """永久删除（仅在 allow_permanent_fallback 且无回收站时使用）。"""
    import shutil

    def _onerror(func, target, _exc):
        try:
            os.chmod(target, 0o777)
            func(target)
        except Exception:
            raise
    shutil.rmtree(path, onerror=_onerror)


def cleanup(cfg, log=print, output_resolver=None):
    result = {"total": 0, "deleted": 0, "skipped": 0, "busy": False, "dry_run": bool(cfg.get("dry_run", True))}

    if not cfg.get("enabled", True):
        log("已停用(enabled=false)，本轮不处理")
        return result

    roots = resolve_roots(cfg, output_resolver, log)
    if not roots:
        log("没有可用的输出根目录")
        return result

    targets = find_targets(roots, cfg.get("names"), bool(cfg.get("recursive", False)), log)
    result["total"] = len(targets)
    if not targets:
        log("输出目录下没有 mat/obj 文件夹: %s" % " | ".join(roots))
        return result

    if result["dry_run"]:
        log("== DRY RUN：只列清单，不动文件 ==")

    now = time.time()
    for path in targets:
        ok, reason = check(path, cfg, roots, now)
        if not ok:
            if "仍在写入" in reason:
                result["busy"] = True
            log("跳过 %s —— %s" % (path, reason))
            result["skipped"] += 1
            continue
        if result["dry_run"]:
            if has_recycle_bin(path):
                log("将移入回收站 %s" % path)
            else:
                log("将永久删除（该磁盘无回收站） %s" % path)
            continue
        try:
            if has_recycle_bin(path):
                send_to_recycle_bin(path)
                log("已移入回收站 %s" % path)
            else:
                _purge(path)
                log("!! 该磁盘无回收站，已永久删除 %s" % path)
            result["deleted"] += 1
        except Exception as exc:
            log("删除失败 %s —— %r" % (path, exc))
            result["skipped"] += 1

    log("本轮结束：命中 %d，移入回收站 %d，跳过 %d" % (result["total"], result["deleted"], result["skipped"]))
    return result
