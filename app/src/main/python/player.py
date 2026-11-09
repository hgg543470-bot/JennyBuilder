# -*- coding: utf-8 -*-
import os
import re
import io
import ast
import json
import time
import shutil
import base64
import hashlib
import zipfile
import difflib
import datetime
import traceback
import doctor
import http.client
import urllib.request
import urllib.parse
import urllib.error
import threading as _threading_boot
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8795
EXT_CONFIG_PATH = "/storage/emulated/0/JENNY/JennyBuilder/config.json"
DEFAULT_PROJECT_DIR = "/storage/emulated/0/scripts/JennyPlayer"
DEFAULT_SAVE_DIR = "/storage/emulated/0/Download"
DEFAULT_REPO = "hgg543470-bot/JennyPlayer"

EXCLUDE_DIRS = {".git", "__pycache__", ".gradle", "build", ".idea", ".Temp"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".log"}
EXCLUDE_FILES = {"omni_full_trace.log", "omni_simple.log", "shizuku_window_dump.txt", "SO_LIBRARIES_PASSPORT.txt"}

_MEM_CFG = {}
_CACHED_RUNS_LIST = []
_PROGRESS_LOCK = _threading_boot.Lock()
_CANCEL_DOWNLOAD_EVENT = _threading_boot.Event()
_TEMP_FILES_LOCK = _threading_boot.Lock()
_ACTIVE_TEMP_FILES = set()

_TASK_PROGRESS = {
    "active": False,
    "percent": 0,
    "title": "",
    "detail": "",
    "started_at": 0.0,
    "cancellable": False,
}


class DownloadCancelledError(Exception):
    pass


def register_temp_download_file(path: str):
    if not path:
        return
    with _TEMP_FILES_LOCK:
        _ACTIVE_TEMP_FILES.add(path)


def unregister_temp_download_file(path: str):
    if not path:
        return
    with _TEMP_FILES_LOCK:
        _ACTIVE_TEMP_FILES.discard(path)


def cleanup_cancelled_temp_files() -> list:
    removed = []
    with _TEMP_FILES_LOCK:
        paths = list(_ACTIVE_TEMP_FILES)
        _ACTIVE_TEMP_FILES.clear()
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
                removed.append(p)
        except Exception:
            pass
    return removed


def reset_download_cancel():
    _CANCEL_DOWNLOAD_EVENT.clear()


def trigger_download_cancel() -> list:
    _CANCEL_DOWNLOAD_EVENT.set()
    removed = cleanup_cancelled_temp_files()
    set_progress(False, 0, "⏹ Отменено", "Загрузка отменена пользователем", cancellable=False)
    return removed


def check_download_cancelled():
    if _CANCEL_DOWNLOAD_EVENT.is_set():
        cleanup_cancelled_temp_files()
        raise DownloadCancelledError("Загрузка отменена пользователем")


def set_progress(active: bool, percent: int = 0, title: str = "", detail: str = "", cancellable: bool = None):
    global _TASK_PROGRESS
    with _PROGRESS_LOCK:
        now = time.time()
        started = _TASK_PROGRESS.get("started_at", 0.0)
        prev_cancel = _TASK_PROGRESS.get("cancellable", False)
        if active and not _TASK_PROGRESS.get("active"):
            started = now
        elif not active:
            started = 0.0
            prev_cancel = False
        is_canc = prev_cancel if (cancellable is None) else bool(cancellable)
        if not active:
            is_canc = False
        _TASK_PROGRESS = {
            "active": bool(active),
            "percent": max(0, min(100, int(percent))),
            "title": str(title or ""),
            "detail": str(detail or ""),
            "started_at": started,
            "elapsed_sec": round(now - started, 1) if (active and started > 0) else 0.0,
            "cancellable": is_canc,
        }


def get_progress() -> dict:
    with _PROGRESS_LOCK:
        d = dict(_TASK_PROGRESS)
        if d.get("active") and d.get("started_at", 0) > 0:
            d["elapsed_sec"] = round(time.time() - d["started_at"], 1)
        return d


def _is_ignored_rel_path(rel_path: str) -> bool:
    if not rel_path:
        return True
    norm = rel_path.replace("\\", "/").lstrip("/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        return True
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    fname = parts[-1]
    if fname in EXCLUDE_FILES or os.path.splitext(fname)[1].lower() in EXCLUDE_EXTS:
        return True
    return False


def _get_config_paths():
    paths = []
    internal_dir = (os.environ.get("JENNY_FILES_DIR") or "").strip()
    if not internal_dir:
        internal_dir = os.path.expanduser("~")
    if internal_dir:
        paths.append(os.path.join(internal_dir, ".jenny_builder_cfg.json"))
    paths.append("/data/data/com.jenny.builder/files/.jenny_builder_cfg.json")
    paths.append("/data/user/0/com.jenny.builder/files/.jenny_builder_cfg.json")
    paths.append(EXT_CONFIG_PATH)
    out = []
    for p in paths:
        if p and p not in out:
            out.append(p)
    return out


class _NoAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            orig_host = (urllib.parse.urlparse(req.full_url).hostname or "").lower()
            new_host = (urllib.parse.urlparse(newurl).hostname or "").lower()
            is_signed_url = ("sig=" in newurl) or ("X-Amz-" in newurl) or ("se=" in newurl)
            is_github_domain = new_host == "github.com" or new_host.endswith(".github.com")
            if (orig_host != new_host and not is_github_domain) or is_signed_url:
                for k in list(new_req.headers.keys()):
                    if k.lower() == "authorization":
                        del new_req.headers[k]
                for k in list(new_req.unredirected_hdrs.keys()):
                    if k.lower() == "authorization":
                        del new_req.unredirected_hdrs[k]
        return new_req


class _StopRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _parse_token_from_text(raw: str) -> str:
    if not raw:
        return ""
    m_tok = re.search(r"(gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})", raw)
    if m_tok:
        return m_tok.group(1).strip()
    m_url = re.search(r"https://([^:@/\s]+):([^@/\s]+)@github\.com", raw)
    if m_url:
        u, p = m_url.group(1).strip(), m_url.group(2).strip()
        return p if p else u
    m_single = re.search(r"https://([^:@/\s]+)@github\.com", raw)
    if m_single:
        cand = m_single.group(1).strip()
        if cand.startswith(("gh", "github_pat_")) or len(cand) >= 25:
            return cand
    return ""


def _extract_token_from_zip_bytes(zip_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            for name in zf.namelist():
                norm = name.replace("\\", "/")
                if norm.endswith(".git/config") or norm == ".git/config":
                    raw = zf.read(name).decode("utf-8", errors="ignore")
                    tok = _parse_token_from_text(raw)
                    if tok:
                        return tok
    except Exception:
        pass
    return ""


def _discover_git_token(extra_dirs=None) -> str:
    candidates = []
    if extra_dirs:
        for d in extra_dirs:
            if d:
                d = d.strip()
                if d.endswith(".git/config"):
                    candidates.append(d)
                else:
                    candidates.append(os.path.join(d, ".git", "config"))
                    candidates.append(os.path.join(os.path.dirname(d.rstrip("/")), ".git", "config"))

    base_dirs = [
        "/storage/emulated/0/scripts/JennyPlayer",
        "/storage/emulated/0/scripts/JennyBuilder",
        "/storage/emulated/0/JennyPlayer",
        "/storage/emulated/0/JennyBuilder",
        "/storage/emulated/0/Download/JennyPlayer",
        "/storage/emulated/0/Download/JennyBuilder",
    ]
    for bd in base_dirs:
        candidates.append(os.path.join(bd, ".git", "config"))

    for parent_scan in ("/storage/emulated/0/scripts", "/storage/emulated/0/Download"):
        try:
            if os.path.isdir(parent_scan):
                for entry in os.listdir(parent_scan):
                    sub_git = os.path.join(parent_scan, entry, ".git", "config")
                    if sub_git not in candidates:
                        candidates.append(sub_git)
        except Exception:
            pass

    for cfg_p in candidates:
        try:
            if cfg_p and os.path.isfile(cfg_p):
                raw = open(cfg_p, "r", encoding="utf-8", errors="ignore").read()
                tok = _parse_token_from_text(raw)
                if tok:
                    return tok
        except Exception:
            pass

    try:
        if os.path.isdir(DEFAULT_SAVE_DIR):
            for entry in os.listdir(DEFAULT_SAVE_DIR):
                if entry.lower().endswith(".zip") and ("jenny" in entry.lower()):
                    zp = os.path.join(DEFAULT_SAVE_DIR, entry)
                    if os.path.isfile(zp) and os.path.getsize(zp) < 60 * 1024 * 1024:
                        tok = _extract_token_from_zip_bytes(open(zp, "rb").read())
                        if tok:
                            return tok
    except Exception:
        pass

    return ""


def _persist_cfg_to_disk(cfg: dict):
    for p in _get_config_paths():
        try:
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def load_config() -> dict:
    global _MEM_CFG
    cfg = {
        "repo": DEFAULT_REPO,
        "token": "",
        "save_dir": DEFAULT_SAVE_DIR,
        "source_dir": DEFAULT_PROJECT_DIR,
        "browse_dir": DEFAULT_SAVE_DIR,
    }
    if _MEM_CFG:
        cfg.update(_MEM_CFG)

    for p in _get_config_paths():
        try:
            if os.path.isfile(p):
                saved = json.load(open(p, "r", encoding="utf-8"))
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        if v is not None and (v != "" or not cfg.get(k)):
                            cfg[k] = v
        except Exception:
            pass

    if _MEM_CFG.get("token"):
        cfg["token"] = _MEM_CFG["token"]
    if _MEM_CFG.get("repo"):
        cfg["repo"] = _MEM_CFG["repo"]

    if not cfg.get("token"):
        discovered = _discover_git_token([cfg.get("source_dir"), cfg.get("browse_dir")])
        if discovered:
            cfg["token"] = discovered
            _MEM_CFG.update(cfg)
            _persist_cfg_to_disk(cfg)

    _MEM_CFG.update(cfg)
    return dict(cfg)


def save_config(new_data: dict) -> dict:
    global _MEM_CFG
    cfg = load_config()
    for k, v in (new_data or {}).items():
        if v is not None:
            cfg[k] = v.strip() if isinstance(v, str) else v
    if not cfg.get("token"):
        discovered = _discover_git_token([cfg.get("source_dir"), cfg.get("browse_dir")])
        if discovered:
            cfg["token"] = discovered
    _MEM_CFG.update(cfg)
    _persist_cfg_to_disk(cfg)
    return dict(cfg)


def _extract_version_tag(title: str) -> str:
    if not title:
        return ""
    m = re.search(r"\b(v\d+(?:\.\d+)?)\b", title, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return ""


# ==============================================================================
# ТОЧНОЕ ОПРЕДЕЛЕНИЕ УСТАНОВЛЕННОЙ СБОРКИ ЧЕРЕЗ ПАСПОРТ APK
# ==============================================================================

def _query_android_package_native(pkg_name: str) -> dict:
    try:
        from com.chaquo.python import Python
        ctx = Python.getPlatform().getApplication()
        pm = ctx.getPackageManager()
        pi = pm.getPackageInfo(pkg_name, 0)
        app_info = pi.applicationInfo
        apk_path = ""
        app_name = pkg_name
        if app_info is not None:
            apk_path = str(app_info.publicSourceDir or app_info.sourceDir or "")
            lbl = pm.getApplicationLabel(app_info)
            if lbl is not None:
                app_name = str(lbl)
        try:
            v_code = int(pi.getLongVersionCode())
        except Exception:
            v_code = int(pi.versionCode)
        return {
            "installed": True,
            "package": pkg_name,
            "appName": app_name,
            "versionName": str(pi.versionName or ""),
            "versionCode": v_code,
            "firstInstallTime": int(pi.firstInstallTime),
            "lastUpdateTime": int(pi.lastUpdateTime),
            "apkPath": apk_path,
        }
    except Exception:
        return {"installed": False, "package": pkg_name}


def resolve_installed_apps_status(bridge_hints: dict = None, runs_list: list = None) -> dict:
    runs = runs_list if runs_list is not None else _CACHED_RUNS_LIST
    out = {}
    for key, pkg in (("player", "com.jenny.player"), ("builder", "com.jenny.builder")):
        raw = None
        if bridge_hints and isinstance(bridge_hints.get(key), dict) and bridge_hints[key].get("installed"):
            raw = bridge_hints[key]
        else:
            raw = _query_android_package_native(pkg)

        if not raw or not raw.get("installed"):
            out[key] = {"installed": False, "package": pkg}
            continue

        ver_name = (raw.get("versionName") or "").strip()
        v_code = int(raw.get("versionCode") or 0)
        upd_ms = int(raw.get("lastUpdateTime") or 0)
        upd_str = ""
        if upd_ms > 0:
            try:
                upd_str = datetime.datetime.fromtimestamp(upd_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        build_num = None
        commit_title = ""
        commit_sha = ""
        full_sha = ""

        m_stamp = re.search(r"#(\d+)\s*(?:\((.*)\))?", ver_name)
        if m_stamp:
            build_num = int(m_stamp.group(1))
            commit_title = (m_stamp.group(2) or "").strip()
        elif v_code > 10 and key == "player":
            build_num = v_code

        if key == "player" and runs and build_num:
            for r in runs:
                if r.get("run_number") == build_num:
                    commit_title = r.get("title") or commit_title
                    commit_sha = r.get("head_sha") or ""
                    full_sha = r.get("full_sha") or ""
                    break

        if key == "player":
            if build_num:
                parts = [f"Сборка #{build_num}"]
                if commit_sha:
                    parts.append(f"ID: {commit_sha}")
                summary = " / ".join(parts)
                if commit_title:
                    summary += f" — «{commit_title}»"
            else:
                summary = f"Версия {ver_name} (код: {v_code})"
        else:
            summary = f"Версия {ver_name}" if ver_name else "Установлен"

        out[key] = {
            "installed": True,
            "package": pkg,
            "versionName": ver_name,
            "versionCode": v_code,
            "updated_at": upd_str,
            "build_number": build_num,
            "commit_sha": commit_sha,
            "full_sha": full_sha,
            "title": commit_title,
            "human_summary": summary,
        }

    return out


def gh_api(endpoint: str, method: str = "GET", payload: dict = None, token: str = "", repo: str = ""):
    cfg = load_config()
    tok = (token or cfg.get("token") or "").strip()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    url = endpoint if endpoint.startswith("http") else f"https://api.github.com/repos/{rep}/{endpoint.lstrip('/')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JennyBuilder-Android/1.7",
    }
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoAuthRedirectHandler())
    try:
        with opener.open(req, timeout=60) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as he:
        err_body = ""
        try:
            err_body = he.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if he.code in (401, 403) and not tok:
            raise RuntimeError(
                "Токен GitHub не задан или не прочитан из .git/config (разреши «Доступ ко всем файлам» или вставь токен в ⚙️ Настройки)."
            )
        raise RuntimeError(f"GitHub API HTTP {he.code}: {he.reason} {err_body}".strip())


# ==============================================================================
# СКАЧИВАНИЕ В 32 ПОТОКА С ПРОГРЕСС-БАРОМ В РЕАЛЬНОМ ВРЕМЕНИ
# ==============================================================================

def gh_download_bytes(endpoint: str, token: str = "", repo: str = "", task_title: str = "Скачивание с GitHub", threads: int = 32, cancellable: bool = True) -> bytes:
    cfg = load_config()
    tok = (token or cfg.get("token") or "").strip()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    url = endpoint if endpoint.startswith("http") else f"https://api.github.com/repos/{rep}/{endpoint.lstrip('/')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JennyBuilder-Android/1.8",
    }
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    check_download_cancelled()
    set_progress(True, 2, task_title, "Получение прямой CDN-ссылки у GitHub...", cancellable=cancellable)

    direct_url = url
    need_auth_on_direct = True
    try:
        no_redir_opener = urllib.request.build_opener(_StopRedirectHandler())
        req_init = urllib.request.Request(url, headers=headers, method="GET")
        with no_redir_opener.open(req_init, timeout=30) as r_init:
            direct_url = r_init.geturl() or url
    except urllib.error.HTTPError as he:
        if he.code in (301, 302, 303, 307, 308):
            loc = he.headers.get("Location")
            if loc:
                direct_url = loc
                new_host = (urllib.parse.urlparse(loc).hostname or "").lower()
                is_signed = ("sig=" in loc) or ("X-Amz-" in loc) or ("se=" in loc)
                is_gh_api = (new_host == "api.github.com")
                if not is_gh_api or is_signed:
                    need_auth_on_direct = False
    except Exception:
        pass

    check_download_cancelled()
    dl_headers = {"User-Agent": "JennyBuilder-Android/1.8"}
    if need_auth_on_direct and tok:
        dl_headers["Authorization"] = f"Bearer {tok}"

    total_size = 0
    supports_range = False
    prefetched_stream_bytes = None

    try:
        probe_hdrs = dict(dl_headers)
        probe_hdrs["Range"] = "bytes=0-3"
        req_probe = urllib.request.Request(direct_url, headers=probe_hdrs, method="GET")
        opener = urllib.request.build_opener(_NoAuthRedirectHandler())
        with opener.open(req_probe, timeout=35) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            cr = resp.headers.get("Content-Range") or ""
            if code == 206 and "/" in cr:
                total_size = int(cr.split("/")[-1].strip())
                supports_range = True
            elif code == 200:
                clen = int(resp.headers.get("Content-Length") or 0)
                t0 = time.time()
                buf = bytearray()
                while True:
                    check_download_cancelled()
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    done_mb = round(len(buf) / (1024.0 * 1024.0), 2)
                    el = max(0.1, time.time() - t0)
                    spd = round(done_mb / el, 1)
                    if clen > 0:
                        pct = min(98, max(5, int((len(buf) * 100) / clen)))
                        tot_mb = round(clen / (1024.0 * 1024.0), 2)
                        set_progress(True, pct, task_title, f"⚡ Поток CDN: {done_mb} / {tot_mb} МБ ({pct}%) • {spd} МБ/с", cancellable=cancellable)
                    else:
                        set_progress(True, 60, task_title, f"⚡ Поток CDN: {done_mb} МБ • {spd} МБ/с", cancellable=cancellable)
                prefetched_stream_bytes = bytes(buf)
    except DownloadCancelledError:
        raise
    except Exception:
        pass

    if prefetched_stream_bytes is not None and len(prefetched_stream_bytes) > 0:
        set_progress(True, 99, task_title, "Проверка целостности данных...", cancellable=cancellable)
        return prefetched_stream_bytes

    if supports_range and total_size > 256 * 1024 and threads > 1:
        total_mb = round(total_size / (1024.0 * 1024.0), 2)
        set_progress(True, 5, task_title, f"🚀 Старт загрузки в {threads} потоков ({total_mb} МБ)...", cancellable=cancellable)
        t0 = time.time()
        part_size = (total_size + threads - 1) // threads
        chunks = [None] * threads
        downloaded_bytes = [0] * threads
        prog_lock = _threading_boot.Lock()

        def _update_multi_prog():
            if _CANCEL_DOWNLOAD_EVENT.is_set():
                return
            with prog_lock:
                done_b = sum(downloaded_bytes)
            pct = min(98, max(5, int((done_b * 100) / max(1, total_size))))
            done_mb = round(done_b / (1024.0 * 1024.0), 2)
            el = max(0.1, time.time() - t0)
            spd = round(done_mb / el, 1)
            set_progress(True, pct, task_title, f"⚡ {threads} потоков: {done_mb} / {total_mb} МБ ({pct}%) • {spd} МБ/с", cancellable=cancellable)

        def fetch_part(idx: int):
            check_download_cancelled()
            start_b = idx * part_size
            end_b = min(total_size - 1, start_b + part_size - 1)
            expected_len = (end_b - start_b + 1)
            if start_b >= total_size or expected_len <= 0:
                chunks[idx] = b""
                return
            for attempt in range(4):
                check_download_cancelled()
                try:
                    p_hdrs = dict(dl_headers)
                    p_hdrs["Range"] = f"bytes={start_b}-{end_b}"
                    rq = urllib.request.Request(direct_url, headers=p_hdrs, method="GET")
                    op = urllib.request.build_opener(_NoAuthRedirectHandler())
                    with op.open(rq, timeout=90) as r:
                        r_code = getattr(r, "status", None) or r.getcode()
                        if r_code != 206:
                            raise RuntimeError(f"CDN вернул HTTP {r_code} вместо 206 Partial Content")
                        buf = bytearray()
                        while True:
                            check_download_cancelled()
                            blk = r.read(131072)
                            if not blk:
                                break
                            buf.extend(blk)
                            downloaded_bytes[idx] = len(buf)
                            _update_multi_prog()
                        if len(buf) != expected_len:
                            raise RuntimeError(f"Неполный сегмент #{idx}: {len(buf)} из {expected_len}")
                        chunks[idx] = bytes(buf)
                        return
                except DownloadCancelledError:
                    raise
                except Exception:
                    downloaded_bytes[idx] = 0
                    time.sleep(0.4)
            raise RuntimeError(f"Не удалось скачать сегмент #{idx} в многопоточном режиме")

        try:
            with ThreadPoolExecutor(max_workers=threads) as ex:
                list(ex.map(fetch_part, range(threads)))
            check_download_cancelled()
            set_progress(True, 99, task_title, f"Склейка и проверка ({total_mb} МБ)...", cancellable=cancellable)
            return b"".join(chunks)
        except DownloadCancelledError:
            raise
        except Exception:
            pass

    last_err = None
    for attempt in range(4):
        check_download_cancelled()
        try:
            req = urllib.request.Request(direct_url, headers=dl_headers, method="GET")
            opener = urllib.request.build_opener(_NoAuthRedirectHandler())
            t0 = time.time()
            with opener.open(req, timeout=180) as resp:
                clen = int(resp.headers.get("Content-Length") or total_size or 0)
                buf = bytearray()
                while True:
                    check_download_cancelled()
                    chunk = resp.read(262144)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    done_mb = round(len(buf) / (1024.0 * 1024.0), 2)
                    el = max(0.1, time.time() - t0)
                    spd = round(done_mb / el, 1)
                    if clen > 0:
                        pct = min(98, max(5, int((len(buf) * 100) / clen)))
                        tot_mb = round(clen / (1024.0 * 1024.0), 2)
                        set_progress(True, pct, task_title, f"⚡ Скачано: {done_mb} / {tot_mb} МБ ({pct}%) • {spd} МБ/с", cancellable=cancellable)
                    else:
                        set_progress(True, 55, task_title, f"⚡ Скачано: {done_mb} МБ • {spd} МБ/с", cancellable=cancellable)
                return bytes(buf)
        except DownloadCancelledError:
            raise
        except Exception as e:
            last_err = e
            time.sleep(0.8)
    raise RuntimeError(f"Не удалось скачать файл после 4 попыток: {last_err}")


def _describe_run_or_commit(sha: str = "", run_obj: dict = None) -> dict:
    if run_obj:
        bnum = run_obj.get("run_number") or ""
        title = (run_obj.get("display_title") or run_obj.get("name") or "").strip()
        r_sha = (run_obj.get("head_sha") or sha or "")[:7]
        human = f"Сборка #{bnum}" if bnum else "Сборка"
        if r_sha:
            human += f" (ID: {r_sha})"
        if title:
            human += f" — «{title}»"
        return {
            "build_number": bnum,
            "title": title,
            "human_label": human,
            "sha": r_sha,
            "full_sha": run_obj.get("head_sha") or sha or "",
        }

    for r in _CACHED_RUNS_LIST:
        r_sha = r.get("full_sha") or r.get("head_sha") or ""
        if sha and (r_sha.startswith(sha) or sha.startswith(r_sha)):
            return {
                "build_number": r.get("run_number"),
                "title": r.get("title"),
                "human_label": f"Сборка #{r.get('run_number')} (ID: {r.get('head_sha')}) — «{r.get('title')}»",
                "sha": (r.get("head_sha") or "")[:7],
                "full_sha": r.get("full_sha") or "",
            }

    try:
        runs_data = gh_api("actions/runs?per_page=35", "GET")
        for r in runs_data.get("workflow_runs", []):
            r_sha = r.get("head_sha") or ""
            if sha and (r_sha.startswith(sha) or sha.startswith(r_sha)):
                return _describe_run_or_commit(sha, r)
        if not sha and runs_data.get("workflow_runs"):
            return _describe_run_or_commit("", runs_data["workflow_runs"][0])
    except Exception:
        pass

    title = ""
    if sha:
        try:
            c_info = gh_api(f"commits/{sha}", "GET")
            msg = ((c_info.get("commit") or {}).get("message") or "").splitlines()
            if msg:
                title = msg[0].strip()
        except Exception:
            pass
    human = f"Коммит {sha[:7]}" if sha else "Коммит"
    if title:
        human += f" — «{title}»"
    return {
        "build_number": "",
        "title": title,
        "human_label": human,
        "sha": (sha or "")[:7],
        "full_sha": sha or "",
    }


def _git_blob_sha1(data: bytes) -> str:
    hdr = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(hdr + data).hexdigest()


# ==============================================================================
# УНИВЕРСАЛЬНЫЙ ДВУСТОРОННИЙ GIT-АНАЛИЗАТОР (ЛЮБОЙ ИСТОЧНИК А ↔ ЛЮБОЙ ИСТОЧНИК Б)
# ==============================================================================

def _extract_python_symbols(text: str) -> dict:
    lines = text.splitlines()
    symbols = {}
    try:
        tree = ast.parse(text)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                sig = f"{prefix} {node.name}({', '.join(args)})"
                start = max(0, getattr(node, "lineno", 1) - 1)
                end = getattr(node, "end_lineno", start + 1)
                body = "\n".join(lines[start:end]).strip()
                symbols[node.name] = {"sig": sig, "body": body}
            elif isinstance(node, ast.ClassDef):
                c_start = max(0, getattr(node, "lineno", 1) - 1)
                c_end = getattr(node, "end_lineno", c_start + 1)
                has_methods = False
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        has_methods = True
                        args = [a.arg for a in sub.args.args]
                        m_name = f"{node.name}.{sub.name}"
                        sig = f"def {m_name}({', '.join(args)})"
                        s_start = max(0, getattr(sub, "lineno", 1) - 1)
                        s_end = getattr(sub, "end_lineno", s_start + 1)
                        body = "\n".join(lines[s_start:s_end]).strip()
                        symbols[m_name] = {"sig": sig, "body": body}
                if not has_methods:
                    symbols[f"class {node.name}"] = {
                        "sig": f"class {node.name}",
                        "body": "\n".join(lines[c_start:c_end]).strip(),
                    }
        return symbols
    except Exception:
        cur_name = None
        cur_sig = None
        cur_lines = []
        for ln in lines:
            m = re.match(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z0-9_]+)\s*(\(.*?\))?\s*:", ln)
            if m and not ln.startswith("        "):
                if cur_name:
                    symbols[cur_name] = {"sig": cur_sig, "body": "\n".join(cur_lines).strip()}
                cur_name = m.group(1)
                cur_sig = ln.strip().rstrip(":")
                cur_lines = [ln]
            elif cur_name:
                cur_lines.append(ln)
        if cur_name:
            symbols[cur_name] = {"sig": cur_sig, "body": "\n".join(cur_lines).strip()}
        return symbols


def _extract_brace_blocks(text: str, pattern: re.Pattern, kind_formatter, skip_nested: bool = False) -> dict:
    symbols = {}
    last_end_idx = 0
    for m in pattern.finditer(text):
        if skip_nested and m.start() < last_end_idx:
            continue
        name, sig = kind_formatter(m)
        if not name or name in ("if", "for", "while", "switch", "catch", "synchronized", "function", "return", "sizeof"):
            continue
        brace_open = text.find("{", m.end() - 1)
        if brace_open == -1:
            continue
        depth = 0
        end_idx = brace_open
        n_len = len(text)
        i = brace_open
        while i < n_len:
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break
            i += 1
        if skip_nested and end_idx > last_end_idx:
            last_end_idx = end_idx
        body = text[m.start():end_idx].strip()
        symbols[name] = {"sig": sig, "body": body}
    return symbols


def _extract_js_symbols(text: str) -> dict:
    pat = re.compile(
        r"(?:(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\(([^)]*)\)"
        r"|(?:window\.|const\s+|let\s+|var\s+)([A-Za-z0-9_$]+)\s*=\s*(?:async\s+)?(?:function\s*\(([^)]*)\)|\(([^)]*)\)\s*=>))"
        r"\s*\{"
    )
    def fmt(m):
        if m.group(1):
            name = m.group(1)
            args = m.group(2).strip()
            return name, f"function {name}({args})"
        name = m.group(3)
        args = (m.group(4) or m.group(5) or "").strip()
        return name, f"function {name}({args})"
    return _extract_brace_blocks(text, pat, fmt, skip_nested=False)


def _extract_java_symbols(text: str) -> dict:
    pat = re.compile(
        r"(?:(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?[A-Za-z0-9_<>\[\].,\s]+?\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*(?:throws\s+[A-Za-z0-9_,\s]+)?"
        r"|(?:fun)\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)[^{]*)\{"
    )
    def fmt(m):
        if m.group(1):
            name = m.group(1).strip()
            args = " ".join(m.group(2).split())
            return name, f"{name}({args})"
        name = m.group(3).strip()
        args = " ".join((m.group(4) or "").split())
        return name, f"fun {name}({args})"
    return _extract_brace_blocks(text, pat, fmt, skip_nested=True)


def _extract_go_symbols(text: str) -> dict:
    pat = re.compile(
        r"(?m)^func\s+(?:\(\s*[^)]*?\s+\*?([A-Za-z0-9_]+)\s*\)\s*)?([A-Za-z0-9_]+)\s*\(([^)]*)\)[^{]*\{"
    )
    def fmt(m):
        recv = (m.group(1) or "").strip()
        name = (m.group(2) or "").strip()
        args = " ".join((m.group(3) or "").split())
        full_name = f"{recv}.{name}" if recv else name
        return full_name, f"func {full_name}({args})"
    return _extract_brace_blocks(text, pat, fmt, skip_nested=True)


def _extract_cpp_symbols(text: str) -> dict:
    pat = re.compile(
        r"(?m)^(?:(?:static|inline|extern|virtual|constexpr|pub|fn|unsafe)\s+)*"
        r"(?:[A-Za-z_][A-Za-z0-9_:*&<>\s]*?\s+\*?)?([A-Za-z_][A-Za-z0-9_:]*)\s*\(([^;{}()]*)\)\s*(?:const\s*)?(?:->\s*[^{]+)?\{"
    )
    def fmt(m):
        name = m.group(1).strip()
        args = " ".join(m.group(2).split())
        return name, f"{name}({args})"
    return _extract_brace_blocks(text, pat, fmt, skip_nested=True)


def _extract_elf_so_symbols(data: bytes) -> dict:
    import struct
    symbols = {}
    if not data or len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return symbols
    try:
        e_shoff = struct.unpack_from("<Q", data, 40)[0]
        e_shentsize, e_shnum = struct.unpack_from("<HH", data, 58)
        if e_shoff == 0 or e_shnum == 0 or e_shoff + e_shentsize * e_shnum > len(data):
            return symbols
        sections = []
        for i in range(e_shnum):
            off = e_shoff + i * e_shentsize
            sections.append(struct.unpack_from("<IIQQQQIIQQ", data, off))

        skip_names = {
            "_init", "_fini", "__bss_start", "_edata", "_end", "__cxa_finalize",
            "__cxa_atexit", "deregister_tm_clones", "register_tm_clones",
            "__do_global_dtors_aux", "frame_dummy"
        }
        for sh in sections:
            sh_type, sh_offset, sh_size, sh_link, sh_entsize = sh[1], sh[4], sh[5], sh[6], sh[9]
            if sh_type not in (2, 11) or sh_entsize < 24 or sh_link >= len(sections):
                continue
            str_sh = sections[sh_link]
            str_off, str_sz = str_sh[4], str_sh[5]
            if str_off + str_sz > len(data):
                continue
            strtab = data[str_off:str_off + str_sz]
            count = sh_size // sh_entsize
            for idx in range(count):
                s_off = sh_offset + idx * sh_entsize
                if s_off + 24 > len(data):
                    break
                st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from("<IBBHQQ", data, s_off)
                st_type = st_info & 0x0F
                if st_type != 2 or st_shndx == 0 or st_shndx >= len(sections) or st_name >= len(strtab):
                    continue
                end_n = strtab.find(b"\x00", st_name)
                if end_n == -1:
                    continue
                fn_name = strtab[st_name:end_n].decode("utf-8", errors="ignore").strip()
                if not fn_name or fn_name in skip_names or fn_name.startswith(("$", "__")):
                    continue
                sec = sections[st_shndx]
                sec_addr, sec_off = sec[3], sec[4]
                code_off = sec_off + (st_value - sec_addr)
                if st_size > 0 and 0 <= code_off and code_off + st_size <= len(data):
                    fn_hash = hashlib.sha1(data[code_off:code_off + st_size]).hexdigest()[:10]
                else:
                    fn_hash = f"val:{st_value}:sz:{st_size}"
                symbols[fn_name] = {
                    "sig": f"[.so] {fn_name}() [{st_size} B]",
                    "body": f"{st_size}:{fn_hash}",
                }
    except Exception:
        pass
    return symbols


def _compact_unified_diff(old_str: str, new_str: str) -> list:
    old_lines = old_str.splitlines()
    new_lines = new_str.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, n=2, lineterm=""))
    out = []
    for ln in diff:
        if ln.startswith(("---", "+++")):
            continue
        out.append(ln)
    return out


def _download_zipball_safe_bytes(sha: str, token: str, repo: str, task_title: str) -> bytes:
    """Быстрое и надёжное скачивание zipball/{sha}: прямая CDN-ссылка -> 32 потока (если CDN отдаёт 206) или прямой 256КБ поток + проверка ZIP."""
    last_err = None
    for attempt in range(1, 4):
        check_download_cancelled()
        try:
            threads_cnt = 32 if attempt == 1 else 8
            raw = gh_download_bytes(
                f"zipball/{sha}",
                token=token,
                repo=repo,
                task_title=f"{task_title}" + (f" (попытка {attempt})" if attempt > 1 else ""),
                threads=threads_cnt,
                cancellable=True,
            )
            check_download_cancelled()
            if raw and len(raw) > 22 and raw[:4] == b"PK\x03\x04" and zipfile.is_zipfile(io.BytesIO(raw)):
                return raw
            last_err = f"Сервер вернул неполный ZIP буфер ({len(raw) if raw else 0} байт)"
        except DownloadCancelledError:
            raise
        except Exception as e:
            last_err = str(e)
        time.sleep(0.6 * attempt)
    raise RuntimeError(f"Не удалось скачать корректный .zip с GitHub после 3 попыток: {last_err}")


def download_build_source_zip_custom(
    target_id: str = "",
    skip_so: bool = False,
    save_dir: str = "",
    token: str = "",
    repo: str = "",
) -> dict:
    reset_download_cancel()
    cfg = load_config()
    tok = (token or cfg.get("token") or "").strip()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    out_dir = (save_dir or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
    os.makedirs(out_dir, exist_ok=True)

    set_progress(True, 3, "📦 Подготовка загрузки .zip", f"Поиск сборки #{target_id} на GitHub...", cancellable=True)
    runs_data = gh_api("actions/runs?per_page=60", "GET", token=tok, repo=rep)
    runs = runs_data.get("workflow_runs") or []
    if not runs:
        raise RuntimeError("Сборки в репозитории не найдены.")

    run_obj = None
    t_str = str(target_id or "").strip()
    if t_str and t_str not in ("last_good", "latest"):
        for r in runs:
            if (
                str(r.get("id")) == t_str
                or str(r.get("run_number")) == t_str
                or (r.get("head_sha") or "").startswith(t_str)
            ):
                run_obj = r
                break
        if not run_obj and re.fullmatch(r"[0-9a-fA-F]{7,40}", t_str):
            run_obj = {"head_sha": t_str, "run_number": t_str[:7], "display_title": f"Commit {t_str[:7]}"}
    else:
        for r in runs:
            if r.get("conclusion") == "success":
                run_obj = r
                break
        if not run_obj:
            run_obj = runs[0]

    if not run_obj:
        raise RuntimeError(f"Сборка '{t_str}' не найдена в истории GitHub Actions.")

    sha = run_obj.get("head_sha") or ""
    bnum = run_obj.get("run_number") or sha[:7]
    title = run_obj.get("display_title") or ""

    repo_short = rep.split("/")[-1] if "/" in rep else rep
    suffix = "src_no_so" if skip_so else "full_src"
    fname = f"{repo_short}_#{bnum}_{suffix}.zip"
    out_zip_path = os.path.join(out_dir, fname)
    part_zip_path = out_zip_path + ".part"
    register_temp_download_file(part_zip_path)

    try:
        raw_zip = _download_zipball_safe_bytes(
            sha=sha,
            token=tok,
            repo=rep,
            task_title=f"📦 Исходники #{bnum}" + (" (без .so)" if skip_so else " (полный)"),
        )
        check_download_cancelled()
        set_progress(True, 96, f"📦 Сборка архива #{bnum}", f"Формирование {fname}...", cancellable=True)

        skipped_so = []
        kept_native_sources = []
        native_src_exts = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".go", ".rs", ".mk"}

        with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zin, zipfile.ZipFile(
            part_zip_path, "w", zipfile.ZIP_DEFLATED
        ) as zout:
            for item in zin.infolist():
                check_download_cancelled()
                parts = item.filename.split("/", 1)
                if len(parts) < 2 or not parts[1] or parts[1].endswith("/"):
                    continue
                rel_path = parts[1]
                ext = os.path.splitext(rel_path)[1].lower()
                data_bytes = zin.read(item.filename)

                if ext in native_src_exts:
                    kept_native_sources.append(f"{rel_path} ({len(data_bytes)} B)")

                if skip_so:
                    if ext == ".pyc" or "/__pycache__/" in f"/{rel_path}":
                        continue
                    if ext == ".so":
                        sha1_short = hashlib.sha1(data_bytes).hexdigest()[:10]
                        kb_sz = round(len(data_bytes) / 1024.0, 1)
                        syms = _extract_elf_so_symbols(data_bytes)
                        sym_names = sorted(list(syms.keys()))
                        skipped_so.append({
                            "path": rel_path,
                            "bytes": len(data_bytes),
                            "kb": kb_sz,
                            "sha1": sha1_short,
                            "funcs": sym_names,
                        })
                        continue

                zout.writestr(rel_path, data_bytes)

            if skip_so:
                pass_lines = [
                    "==========================================================",
                    f"📦 ПАСПОРТ НАТИВНЫХ БИБЛИОТЕК (.SO) — СБОРКА #{bnum} (ID: {sha[:7]})",
                    f"Описание сборки: {title}",
                    "==========================================================",
                    "Бинарные .so файлы были пропущены галочкой для уменьшения веса архива,",
                    "но ВСЕ исходники нативных библиотек (.go, .c, .cpp, .h, .hpp) сохранены в архиве!",
                    "",
                    f"✅ Сохраненные исходники .so библиотек ({len(kept_native_sources)}):",
                ]
                for ns in kept_native_sources:
                    pass_lines.append(f"  • {ns}")
                pass_lines.append("")
                pass_lines.append(f"🚫 Пропущенные скомпилированные бинарники .so ({len(skipped_so)}):")
                for s_info in skipped_so:
                    pass_lines.append(
                        f"  • {s_info['path']} — {s_info['kb']} КБ ({s_info['bytes']} B, sha1:{s_info['sha1']}), функций ELF: {len(s_info['funcs'])}"
                    )
                    if s_info["funcs"]:
                        preview_fn = ", ".join(s_info["funcs"][:25])
                        if len(s_info["funcs"]) > 25:
                            preview_fn += f" ... (+{len(s_info['funcs']) - 25})"
                        pass_lines.append(f"    Функции: {preview_fn}")
                zout.writestr("SO_LIBRARIES_PASSPORT.txt", "\n".join(pass_lines).encode("utf-8"))

        check_download_cancelled()
        os.replace(part_zip_path, out_zip_path)
        unregister_temp_download_file(part_zip_path)
    except Exception:
        cleanup_cancelled_temp_files()
        raise

    sz_bytes = os.path.getsize(out_zip_path)
    sz_mb = round(sz_bytes / (1024.0 * 1024.0), 2)
    sz_kb = round(sz_bytes / 1024.0, 1)
    set_progress(False, 100, "Готово", f"Архив {fname} сохранён", cancellable=False)
    return {
        "status": "ok",
        "zip_path": out_zip_path,
        "path": out_zip_path,
        "filename": fname,
        "size_mb": sz_mb,
        "size_kb": sz_kb,
        "commit_sha": sha[:7],
        "sha": sha[:7],
        "run_number": bnum,
        "build_number": bnum,
        "title": title,
        "skip_so": skip_so,
        "skipped_so_count": len(skipped_so),
        "native_src_count": len(kept_native_sources),
        "human_label": f"Сборка #{bnum} (ID: {sha[:7]}) — «{title}»",
    }


def _analyze_single_file_diff(rel_path: str, old_bytes: bytes, new_bytes: bytes, git_patch_str: str = "") -> dict:
    ext = os.path.splitext(rel_path)[1].lower()
    binary_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".so", ".jar", ".apk", ".zip", ".keystore", ".jks"}
    if ext in binary_exts:
        old_kb = round(len(old_bytes) / 1024.0, 1)
        new_kb = round(len(new_bytes) / 1024.0, 1)
        old_h = hashlib.sha1(old_bytes).hexdigest()[:7] if old_bytes else "none"
        new_h = hashlib.sha1(new_bytes).hexdigest()[:7] if new_bytes else "none"
        added_funcs, removed_funcs, modified_funcs = [], [], []
        if ext == ".so":
            old_syms = _extract_elf_so_symbols(old_bytes)
            new_syms = _extract_elf_so_symbols(new_bytes)
            for k, info in new_syms.items():
                if k not in old_syms:
                    added_funcs.append(info["sig"])
                elif old_syms[k]["body"] != info["body"]:
                    modified_funcs.append(info["sig"])
            for k, info in old_syms.items():
                if k not in new_syms:
                    removed_funcs.append(info["sig"])
        return {
            "path": rel_path,
            "is_binary": True,
            "added_lines": 0,
            "removed_lines": 0,
            "added_funcs": added_funcs,
            "removed_funcs": removed_funcs,
            "modified_funcs": modified_funcs,
            "func_diffs": {},
            "raw_diff_preview": [
                f"Бинарный файл: было {old_kb} КБ ({len(old_bytes)} B, sha1:{old_h}) ➔ стало {new_kb} КБ ({len(new_bytes)} B, sha1:{new_h})"
            ],
        }

    old_text = old_bytes.decode("utf-8", errors="replace") if old_bytes else ""
    new_text = new_bytes.decode("utf-8", errors="replace") if new_bytes else ""

    if old_bytes or new_bytes:
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        u_diff = list(difflib.unified_diff(old_lines, new_lines, n=0, lineterm=""))
        plus_cnt = sum(1 for l in u_diff if l.startswith("+") and not l.startswith("+++"))
        minus_cnt = sum(1 for l in u_diff if l.startswith("-") and not l.startswith("---"))
    else:
        plus_cnt = sum(1 for l in git_patch_str.splitlines() if l.startswith("+") and not l.startswith("+++"))
        minus_cnt = sum(1 for l in git_patch_str.splitlines() if l.startswith("-") and not l.startswith("---"))

    old_syms = {}
    new_syms = {}
    if old_text or new_text:
        if ext == ".py":
            old_syms = _extract_python_symbols(old_text)
            new_syms = _extract_python_symbols(new_text)
        elif ext in (".html", ".js", ".ts", ".jsx", ".tsx"):
            old_syms = _extract_js_symbols(old_text)
            new_syms = _extract_js_symbols(new_text)
        elif ext in (".java", ".kt", ".kts"):
            old_syms = _extract_java_symbols(old_text)
            new_syms = _extract_java_symbols(new_text)
        elif ext == ".go":
            old_syms = _extract_go_symbols(old_text)
            new_syms = _extract_go_symbols(new_text)
        elif ext in (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".rs"):
            old_syms = _extract_cpp_symbols(old_text)
            new_syms = _extract_cpp_symbols(new_text)

    added_funcs = []
    removed_funcs = []
    modified_funcs = []
    func_diffs = {}

    if old_syms or new_syms:
        for k, info in new_syms.items():
            if k not in old_syms:
                added_funcs.append(info["sig"])
            elif old_syms[k]["body"] != info["body"]:
                modified_funcs.append(info["sig"])
        for k, info in old_syms.items():
            if k not in new_syms:
                removed_funcs.append(info["sig"])

    if git_patch_str:
        raw_preview = git_patch_str.splitlines()
    else:
        raw_preview = _compact_unified_diff(old_text, new_text)

    return {
        "path": rel_path,
        "is_binary": False,
        "added_lines": plus_cnt,
        "removed_lines": minus_cnt,
        "added_funcs": added_funcs,
        "removed_funcs": removed_funcs,
        "modified_funcs": modified_funcs,
        "func_diffs": func_diffs,
        "raw_diff_preview": raw_preview,
    }

def _fetch_blob_bytes(blob_sha: str, token: str = "", repo: str = "") -> bytes:
    if not blob_sha:
        return b""
    try:
        blob_obj = gh_api(f"git/blobs/{blob_sha}", "GET", token=token, repo=repo)
        b64_content = (blob_obj.get("content") or "").replace("\n", "").replace("\r", "")
        return base64.b64decode(b64_content) if b64_content else b""
    except Exception:
        return b""


def _load_Compare_Side(spec: str, folder_path: str, zip_path: str, token: str = "", repo: str = "") -> dict:
    """
    Загружает состояние любого источника сравнения:
    - 'LOCAL_FOLDER' -> локальная папка
    - 'LOCAL_ZIP' -> выбранный .zip
    - 'GH:<sha>' -> конкретная сборка/коммит на GitHub
    Возвращает:
      {'kind': 'local'|'gh', 'label': str, 'sha': str, 'bytes_map': dict, 'sha_map': dict}
    """
    cfg = load_config()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()

    if spec == "LOCAL_FOLDER":
        fp = (folder_path or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
        if not os.path.isdir(fp):
            raise ValueError(f"Папка не найдена: {fp}")
        bmap = _collect_files_from_folder(fp)
        smap = {k: _git_blob_sha1(v) for k, v in bmap.items()}
        return {
            "kind": "local",
            "label": f"📁 Рабочая папка ({fp})",
            "sha": "",
            "bytes_map": bmap,
            "sha_map": smap,
        }

    if spec == "LOCAL_ZIP":
        zp = (zip_path or "").strip()
        if not zp or not os.path.isfile(zp):
            raise ValueError(f"Выбранный .zip архив не найден: {zp or 'путь не указан'}")
        bmap = _collect_files_from_zip_bytes(open(zp, "rb").read())
        smap = {k: _git_blob_sha1(v) for k, v in bmap.items()}
        return {
            "kind": "local",
            "label": f"📦 Архив .zip ({zp})",
            "sha": "",
            "bytes_map": bmap,
            "sha_map": smap,
        }

    # Иначе это GitHub коммит / сборка ('GH:<sha>' или '<sha>')
    sha = spec[3:].strip() if spec.startswith("GH:") else spec.strip()
    if not sha:
        _, sha = _resolve_target_branch(token=token, repo=rep)
    meta = _describe_run_or_commit(sha, None)
    commit_info = gh_api(f"git/commits/{sha}", "GET", token=token, repo=rep)
    tree_sha = commit_info["tree"]["sha"]
    tree_info = gh_api(f"git/trees/{tree_sha}?recursive=1", "GET", token=token, repo=rep)
    smap = {
        item["path"]: item["sha"]
        for item in tree_info.get("tree", [])
        if item.get("type") == "blob" and not _is_ignored_rel_path(item.get("path") or "")
    }
    return {
        "kind": "gh",
        "label": f"☁️ {meta.get('human_label') or sha[:7]}",
        "sha": sha,
        "bytes_map": None,
        "sha_map": smap,
    }


def compare_any_two_sources(base_spec: str, target_spec: str, base_custom_lbl: str = "", target_custom_lbl: str = "", folder_path: str = "", zip_path: str = "", token: str = "", repo: str = "") -> dict:
    """
    Двустороннее сравнение ЛЮБОГО источника А (База — что было) с ЛЮБЫМ источником Б (Цель — что стало).
    Меняешь местами А и Б — полностью переворачивается знак изменений (+ на -) и генерируемое описание!
    """
    cfg = load_config()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()

    set_progress(True, 10, "🔬 Git-сравнение", "Загрузка структуры Источника А (База)...")
    side_a = _load_Compare_Side(base_spec, folder_path, zip_path, token=token, repo=rep)

    set_progress(True, 30, "🔬 Git-сравнение", "Загрузка структуры Источника Б (Цель)...")
    side_b = _load_Compare_Side(target_spec, folder_path, zip_path, token=token, repo=rep)

    lbl_a = base_custom_lbl or side_a["label"]
    lbl_b = target_custom_lbl or side_b["label"]

    sha_map_a = side_a["sha_map"]
    sha_map_b = side_b["sha_map"]

    added_paths = sorted([p for p in sha_map_b if p not in sha_map_a])
    removed_paths = sorted([p for p in sha_map_a if p not in sha_map_b])
    modified_paths = sorted([p for p in sha_map_b if p in sha_map_a and sha_map_a[p] != sha_map_b[p]])
    unchanged_count = sum(1 for p in sha_map_b if p in sha_map_a and sha_map_a[p] == sha_map_b[p])

    # Если оба источника — сборки GitHub, дополнительно подтягиваем историю коммитов между ними
    commits_between = []
    if side_a["kind"] == "gh" and side_b["kind"] == "gh" and side_a["sha"] != side_b["sha"]:
        try:
            set_progress(True, 45, "🔬 Git-сравнение", "Запрос истории коммитов между сборками...")
            comp = gh_api(f"compare/{side_a['sha']}...{side_b['sha']}", "GET", token=token, repo=rep)
            commits_between = comp.get("commits") or []
        except Exception:
            pass

    report_lines = []
    report_lines.append("==========================================================")
    report_lines.append("📊 ДВУСТОРОННИЙ GIT DIFF (А ➔ Б)")
    report_lines.append("==========================================================")
    report_lines.append(f"• А (База / Старое): {lbl_a}")
    report_lines.append(f"• Б (Цель / Новое):  {lbl_b}")
    report_lines.append(
        f"• Итог (Б относительно А): изменено файлов: {len(modified_paths)} | "
        f"добавлено в Б: {len(added_files := added_paths)} | удалено в Б: {len(removed_paths)} | "
        f"совпадают 1-в-1: {unchanged_count}"
    )
    report_lines.append("----------------------------------------------------------")

    if not modified_paths and not added_paths and not removed_paths:
        set_progress(False, 100, "Готово", "Отличий нет")
        report_lines.append("✅ Код в Источнике А и Источнике Б совпадает на 100% (0 отличий в коде)!")
        return {
            "has_changes": False,
            "short_commit_msg": "",
            "report_text": "\n".join(report_lines),
            "modified_count": 0,
            "added_count": 0,
            "removed_count": 0,
        }

    if commits_between:
        report_lines.append(f"\n📜 КОММИТЫ МЕЖДУ А И Б ({len(commits_between)}):")
        for c in commits_between:
            c_sha = (c.get("sha") or "")[:7]
            c_msg = ((c.get("commit") or {}).get("message") or "").splitlines()[0]
            c_date = (((c.get("commit") or {}).get("author") or {}).get("date") or "").replace("T", " ")[:16]
            report_lines.append(f"  • [{c_sha}] ({c_date}) {c_msg}")

    def _get_side_file_bytes(side: dict, rel_p: str) -> bytes:
        if side["bytes_map"] is not None:
            return side["bytes_map"].get(rel_p, b"")
        b_sha = side["sha_map"].get(rel_p, "")
        return _fetch_blob_bytes(b_sha, token=token, repo=rep)

    commit_summary_parts = []
    total_work = max(1, len(added_paths) + len(removed_paths) + len(modified_paths))
    done_work = 0

    if added_paths:
        report_lines.append(f"\n🟢 НОВЫЕ ФАЙЛЫ В «Б» ({len(added_paths)}):")
        for af in added_paths:
            done_work += 1
            pct = 50 + int((done_work * 45) / total_work)
            set_progress(True, pct, "🔬 Git-сравнение", f"Разбор нового файла {af}...")
            new_b = _get_side_file_bytes(side_b, af)
            info = _analyze_single_file_diff(af, b"", new_b)
            report_lines.append(f"  + {af} (+{info['added_lines']} строк)")
            for fn in info["added_funcs"]:
                report_lines.append(f"      🟢 Новая функция: {fn}")
            commit_summary_parts.append(f"+{os.path.basename(af)}")

    if removed_paths:
        report_lines.append(f"\n🔴 УДАЛЁННЫЕ В «Б» ФАЙЛЫ ({len(removed_paths)}):")
        for rf in removed_paths:
            done_work += 1
            pct = 50 + int((done_work * 45) / total_work)
            set_progress(True, pct, "🔬 Git-сравнение", f"Проверка удалённого файла {rf}...")
            old_b = _get_side_file_bytes(side_a, rf)
            info = _analyze_single_file_diff(rf, old_b, b"")
            report_lines.append(f"  - {rf} (-{info['removed_lines']} строк)")
            for fn in info["removed_funcs"]:
                report_lines.append(f"      🔴 Удалена функция: {fn}")
            commit_summary_parts.append(f"-{os.path.basename(rf)}")

    if modified_paths:
        report_lines.append(f"\n🟡 ИЗМЕНЁННЫЕ ФАЙЛЫ И ФУНКЦИИ ({len(modified_paths)}):")
        for mp in modified_paths:
            done_work += 1
            pct = 50 + int((done_work * 45) / total_work)
            set_progress(True, pct, "🔬 Git-сравнение", f"Посимвольная сверка функций в {mp}...")
            old_b = _get_side_file_bytes(side_a, mp)
            new_b = _get_side_file_bytes(side_b, mp)

            f_diff = _analyze_single_file_diff(mp, old_b, new_b)
            report_lines.append(
                f"\n📄 Файл: {mp} (+{f_diff['added_lines']} / -{f_diff['removed_lines']} строк)"
            )

            if f_diff["added_funcs"]:
                report_lines.append(f"  🟢 Добавлены функции в Б ({len(f_diff['added_funcs'])}):")
                for fn in f_diff["added_funcs"]:
                    report_lines.append(f"    + {fn}")

            if f_diff["removed_funcs"]:
                report_lines.append(f"  🔴 Удалены функции в Б ({len(f_diff['removed_funcs'])}):")
                for fn in f_diff["removed_funcs"]:
                    report_lines.append(f"    - {fn}")

            if f_diff["modified_funcs"]:
                report_lines.append(f"  🟡 Изменены внутри функции ({len(f_diff['modified_funcs'])}):")
                for fn in f_diff["modified_funcs"]:
                    report_lines.append(f"    ~ {fn}")

            if f_diff["raw_diff_preview"]:
                report_lines.append("  🔍 Полный построчный Git Diff по файлу:")
                for rl in f_diff["raw_diff_preview"]:
                    report_lines.append(f"    {rl}")

            short_fn_notes = []
            for fn in f_diff["added_funcs"][:2]:
                short_name = fn.split("(")[0].replace("async def ", "").replace("def ", "").replace("function ", "").strip()
                short_fn_notes.append(f"+{short_name}")
            for fn in f_diff["modified_funcs"][:2]:
                short_name = fn.split("(")[0].replace("async def ", "").replace("def ", "").replace("function ", "").strip()
                short_fn_notes.append(f"~{short_name}")
            for fn in f_diff["removed_funcs"][:2]:
                short_name = fn.split("(")[0].replace("async def ", "").replace("def ", "").replace("function ", "").strip()
                short_fn_notes.append(f"-{short_name}")

            bname = os.path.basename(mp)
            if short_fn_notes:
                commit_summary_parts.append(f"{bname}({', '.join(short_fn_notes)})")
            else:
                commit_summary_parts.append(f"{bname}(+{f_diff['added_lines']}/-{f_diff['removed_lines']})")

    short_commit = "upd: " + "; ".join(commit_summary_parts[:4])
    if len(short_commit) > 100:
        short_commit = short_commit[:97] + "..."

    report_lines.append("\n==========================================================")
    report_lines.append(f"💡 Готовое краткое описание (А ➔ Б): {short_commit}")
    report_lines.append("==========================================================")

    set_progress(False, 100, "Готово", "Сравнение завершено")
    return {
        "has_changes": True,
        "short_commit_msg": short_commit,
        "report_text": "\n".join(report_lines),
        "modified_count": len(modified_paths),
        "added_count": len(added_paths),
        "removed_count": len(removed_paths),
    }


def _collect_files_from_zip_bytes(zip_bytes: bytes) -> dict:
    cfg = load_config()
    if not cfg.get("token"):
        z_tok = _extract_token_from_zip_bytes(zip_bytes)
        if z_tok:
            save_config({"token": z_tok})

    out = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        raw_paths = [i.filename.replace("\\", "/").lstrip("/") for i in infos]
        top_segs = {p.split("/", 1)[0] for p in raw_paths if "/" in p}
        strip_prefix = ""
        if len(top_segs) == 1:
            only_top = list(top_segs)[0]
            if only_top not in ("app", ".github", "res", "src"):
                if all(p.startswith(only_top + "/") for p in raw_paths):
                    strip_prefix = only_top + "/"
        for info in infos:
            rel = info.filename.replace("\\", "/").lstrip("/")
            if strip_prefix and rel.startswith(strip_prefix):
                rel = rel[len(strip_prefix):]
            if not rel or rel.endswith("/"):
                continue
            if _is_ignored_rel_path(rel):
                continue
            out[rel] = zf.read(info.filename)
    return out


def _collect_files_from_folder(folder_path: str) -> dict:
    cfg = load_config()
    if not cfg.get("token"):
        f_tok = _discover_git_token([folder_path])
        if f_tok:
            save_config({"token": f_tok})

    out = {}
    for root, dirs, files in os.walk(folder_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f in EXCLUDE_FILES or os.path.splitext(f)[1].lower() in EXCLUDE_EXTS:
                continue
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, folder_path).replace("\\", "/")
            if _is_ignored_rel_path(rel_p):
                continue
            try:
                out[rel_p] = open(full_p, "rb").read()
            except Exception:
                pass
    return out


def _resolve_target_branch(token: str = "", repo: str = "") -> tuple:
    for br in ("main", "master"):
        try:
            ref_info = gh_api(f"git/ref/heads/{br}", "GET", token=token, repo=repo)
            if ref_info and "object" in ref_info:
                return br, ref_info["object"]["sha"]
        except Exception:
            continue
    repo_info = gh_api("", "GET", token=token, repo=repo)
    def_br = repo_info.get("default_branch") or "main"
    ref_info = gh_api(f"git/ref/heads/{def_br}", "GET", token=token, repo=repo)
    return def_br, ref_info["object"]["sha"]


def _ensure_build_yml_stamps_version(file_map: dict):
    yml_key = ".github/workflows/build.yml"
    if yml_key in file_map:
        try:
            yml_str = file_map[yml_key].decode("utf-8", errors="replace")
            stamp_lines = [
                "    - name: Штамп номера сборки и версии в APK",
                "      run: |",
                "        python3 - << 'STAMP_EOF'",
                "        import os, re, subprocess",
                '        bnum = os.environ.get("GITHUB_RUN_NUMBER", "1")',
                '        msg = subprocess.check_output(["git", "log", "-1", "--pretty=%s"], text=True).strip()',
                '        msg = "".join(c for c in msg if ord(c) >= 32 and c not in (chr(34), chr(39), chr(92), "$"))[:48]',
                '        s = open("app/build.gradle", "r", encoding="utf-8").read()',
                '        s = re.sub("versionCode\\\\s+\\\\d+", lambda _: f"versionCode {bnum}", s)',
                '        s = re.sub("versionName\\\\s+\\"[^\\"]*\\"", lambda _: f"versionName \\"#{bnum} ({msg})\\"", s)',
                '        open("app/build.gradle", "w", encoding="utf-8").write(s)',
                "        STAMP_EOF",
                "",
                "    - name: Сборка APK",
                "      run: gradle assembleDebug",
            ]
            if "STAMP_EOF" not in yml_str and "gradle assembleDebug" in yml_str:
                yml_str = re.sub(
                    r"    - name: Штамп номера сборки и версии в APK.*?    - name: Сборка APK\n      run: gradle assembleDebug",
                    "\n".join(stamp_lines),
                    yml_str,
                    flags=re.DOTALL,
                )
                if "STAMP_EOF" not in yml_str:
                    yml_str = yml_str.replace(
                        "    - name: Сборка APK\n      run: gradle assembleDebug",
                        "\n".join(stamp_lines),
                    )
                file_map[yml_key] = yml_str.encode("utf-8")
        except Exception:
            pass


def push_file_map_to_github(file_map: dict, commit_msg: str, token: str = "", repo: str = "") -> dict:
    if not file_map:
        raise ValueError(
            "Android скрыл файлы в этой папке (0 файлов). Нажми красную кнопку «🔓 Выдать Доступ ко всем файлам» вверху экрана и повтори!"
        )
    set_progress(True, 2, "🛡️ Предполётная проверка кода", "Проверка синтаксиса .py и .js перед отправкой на GitHub...")
    for rel_p, content_b in file_map.items():
        if rel_p.endswith(".py"):
            try:
                ast.parse(content_b.decode("utf-8", errors="replace"))
            except SyntaxError as se:
                set_progress(False, 0)
                raise ValueError(f"🛑 СТОП! Синтаксическая ошибка в {rel_p} на строке {se.lineno}: {se.msg}. Сборка остановлена!")
        elif rel_p.endswith(".js"):
            ok_j, err_j = doctor.verify_js_syntax(content_b.decode("utf-8", errors="replace"), rel_p)
            if not ok_j:
                set_progress(False, 0)
                raise ValueError(f"🛑 СТОП! Синтаксическая ошибка JS в {rel_p}: {err_j}. Сборка остановлена!")

    set_progress(True, 5, "📤 Экспорт на GitHub", "Сверка дерева файлов и поиск удалённого мусора на GitHub...")
    _ensure_build_yml_stamps_version(file_map)
    branch_name, head_sha = _resolve_target_branch(token=token, repo=repo)
    commit_info = gh_api(f"git/commits/{head_sha}", "GET", token=token, repo=repo)
    base_tree_sha = commit_info["tree"]["sha"]
    tree_info = gh_api(f"git/trees/{base_tree_sha}?recursive=1", "GET", token=token, repo=repo)

    remote_blobs = {}
    remote_modes = {}
    for item in tree_info.get("tree", []):
        if item.get("type") == "blob":
            p = item["path"]
            remote_blobs[p] = item["sha"]
            remote_modes[p] = item.get("mode") or "100644"

    to_upload = []
    for rel_path, content_bytes in file_map.items():
        local_sha = _git_blob_sha1(content_bytes)
        if remote_blobs.get(rel_path) != local_sha:
            to_upload.append((rel_path, content_bytes))

    to_delete = []
    for rel_path in sorted(remote_blobs.keys()):
        if rel_path not in file_map:
            norm_low = rel_path.replace("\\", "/").lower()
            # Броня на нативные .so библиотеки в jniLibs и workflow (на случай пуша папки без .so)
            if norm_low.endswith(".so") and "jnilibs/" in norm_low:
                continue
            if norm_low == ".github/workflows/build.yml":
                continue
            to_delete.append(rel_path)

    tree_updates = []
    changed_count = len(to_upload)
    deleted_count = len(to_delete)

    for idx, (rel_path, content_bytes) in enumerate(to_upload, 1):
        pct = 15 + int((idx * 70) / max(1, changed_count))
        set_progress(True, pct, "📤 Экспорт на GitHub", f"Отправка файла {idx} из {changed_count}: {rel_path}...")
        blob_res = gh_api(
            "git/blobs",
            "POST",
            {
                "content": base64.b64encode(content_bytes).decode("ascii"),
                "encoding": "base64",
            },
            token=token,
            repo=repo,
        )
        mode_str = "100755" if rel_path.endswith(("gradlew", ".sh")) else "100644"
        tree_updates.append({
            "path": rel_path,
            "mode": mode_str,
            "type": "blob",
            "sha": blob_res["sha"],
        })

    for del_path in to_delete:
        tree_updates.append({
            "path": del_path,
            "mode": remote_modes.get(del_path, "100644"),
            "type": "blob",
            "sha": None,
        })

    set_progress(True, 90, "📤 Экспорт на GitHub", "Формирование коммита и запуск GitHub Actions...")
    if tree_updates:
        new_tree = gh_api(
            "git/trees",
            "POST",
            {"base_tree": base_tree_sha, "tree": tree_updates},
            token=token,
            repo=repo,
        )
        new_tree_sha = new_tree["sha"]
    else:
        new_tree_sha = base_tree_sha

    new_commit = gh_api(
        "git/commits",
        "POST",
        {
            "message": commit_msg,
            "tree": new_tree_sha,
            "parents": [head_sha],
        },
        token=token,
        repo=repo,
    )
    new_commit_sha = new_commit["sha"]

    gh_api(
        f"git/refs/heads/{branch_name}",
        "PATCH",
        {"sha": new_commit_sha, "force": True},
        token=token,
        repo=repo,
    )
    set_progress(False, 100, "Готово", "Отправлено на GitHub")
    return {
        "commit_sha": new_commit_sha[:7],
        "commit_msg": commit_msg,
        "changed_files": changed_count,
        "deleted_files": deleted_count,
        "total_files": len(file_map),
        "branch": branch_name,
    }



def _unpack_zip_bytes_to_folder(zip_bytes: bytes, target_folder: str) -> int:
    os.makedirs(target_folder, exist_ok=True)
    fmap = _collect_files_from_zip_bytes(zip_bytes)
    if not fmap:
        raise ValueError("В архиве не найдено файлов для распаковки.")
    written = 0
    total = len(fmap)
    for idx, (rel_p, content_b) in enumerate(fmap.items(), 1):
        if rel_p.startswith(".git/") or rel_p == ".git":
            continue
        if idx % 5 == 0 or idx == total:
            pct = 75 + int((idx * 24) / max(1, total))
            set_progress(True, pct, "📥 Распаковка в папку", f"Запись файла {idx} из {total}: {rel_p}")
        dest_p = os.path.join(target_folder, rel_p)
        os.makedirs(os.path.dirname(dest_p), exist_ok=True)
        with open(dest_p, "wb") as f:
            f.write(content_b)
        written += 1
    return written


def extract_failed_step_log(raw_log_text: str, step_name: str, started_at: str = "", completed_at: str = "") -> str:
    lines = raw_log_text.splitlines()
    if not lines:
        return "Лог пуст."

    err_highlights = [
        l for l in lines
        if any(k in l for k in ("##[error]", "FAILURE:", "What went wrong:", "> Task ", "Exception:", "Error:", "error:"))
    ][-25:]
    summary_top = (
        "--- 🎯 КРАТКАЯ СУТЬ ОШИБКИ ---\n" + "\n".join(err_highlights) + "\n------------------------------\n\n"
    ) if err_highlights else ""
    header = f"=== ❌ ЭТАП С ОШИБКОЙ: {step_name} ===\n" + summary_top

    start_prefix = (started_at or "")[:19]
    end_prefix = (completed_at or "")[:19]
    if start_prefix and len(start_prefix) == 19:
        sliced = []
        capturing = False
        for ln in lines:
            ts = ln[:19]
            if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts):
                if ts >= start_prefix:
                    capturing = True
                if end_prefix and ts > end_prefix and len(sliced) > 10:
                    if "##[error]" in ln or "Error:" in ln or "FAILURE:" in ln or "Exception" in ln:
                        sliced.append(ln)
                    continue
            if capturing:
                sliced.append(ln)
        if len(sliced) >= 5:
            return header + "\n".join(sliced[-350:])

    err_indices = [i for i, ln in enumerate(lines) if ("##[error]" in ln or "FAILURE:" in ln or "Execution failed for task" in ln)]
    end_idx = err_indices[-1] + 25 if err_indices else len(lines)
    end_idx = min(len(lines), end_idx)
    start_idx = max(0, end_idx - 220)
    for i in range(end_idx - 1, max(0, end_idx - 400), -1):
        if "##[group]" in lines[i]:
            start_idx = i
            break
    return header + "\n".join(lines[start_idx:end_idx])


# ==============================================================================
# V1.9: ЭКСПОРТ ВСЕГО ПРОЕКТА В 1 .TXT (ДЛЯ ИИ) И ЭКСПОРТ ТОЛЬКО ИЗМЕНЁННЫХ ФАЙЛОВ
# ==============================================================================
TEXT_EXPORT_MAX_FILE_BYTES = 1500000
BINARY_EXTS = {
    ".so", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".apk", ".aab", ".dex",
    ".zip", ".jar", ".gz", ".7z", ".rar", ".tar", ".mp3", ".flac", ".ogg", ".opus", ".m4a",
    ".wav", ".mp4", ".mkv", ".webm", ".ttf", ".otf", ".woff", ".woff2", ".bin", ".xflac",
    ".class", ".a", ".o", ".torrent", ".db", ".sqlite",
}


def _is_probably_text(rel_path: str, data: bytes) -> bool:
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in BINARY_EXTS:
        return False
    return b"\x00" not in data[:4096]


def _short_src_tag(lbl: str, fallback: str) -> str:
    m_b = re.search(r"#(\d+)", lbl or "")
    if m_b:
        return f"b{m_b.group(1)}"
    if "Рабочая папка" in (lbl or "") or "LOCAL_FOLDER" in (lbl or ""):
        return "folder"
    if ".zip" in (lbl or "").lower():
        return "zip"
    m_id = re.search(r"ID:\s*([0-9a-f]{5,10})", lbl or "", re.I)
    if m_id:
        return m_id.group(1)[:7]
    return fallback


def _build_text_bundle(file_map: dict, title: str, numbered: bool = False, extra_header=None) -> tuple:
    """
    Склеивает файлы в один текст:  шапка + дерево + блоки «FILE: путь (LINES: N)».
    Бинарные и слишком большие файлы не включаются, но перечисляются в шапке.
    Возвращает (text, included_count, skipped_list, total_lines).
    """
    included = []
    skipped = []
    for rel in sorted(file_map.keys()):
        data = file_map[rel]
        if len(data) > TEXT_EXPORT_MAX_FILE_BYTES:
            skipped.append((rel, len(data), "слишком большой"))
        elif not _is_probably_text(rel, data):
            skipped.append((rel, len(data), "бинарный"))
        else:
            included.append(rel)

    bodies = []
    total_lines = 0
    tree_lines = []
    for rel in included:
        text = file_map[rel].decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        total_lines += len(lines)
        tree_lines.append(f"  {rel} ({len(lines)} строк)")
        head = "=" * 80
        block = [head, f"FILE: {rel} (LINES: {len(lines)})", head]
        if numbered:
            block.extend(f"{i}: {ln}" for i, ln in enumerate(lines, 1))
        else:
            block.extend(lines)
        block.append("")
        bodies.append("\n".join(block))

    out = [f"=== {title} ==="]
    out.append("Создано: " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " (Jenny Builder)")
    out.append(
        f"Файлов в дампе: {len(included)} | строк: {total_lines} | "
        f"пропущено (бинарные/большие): {len(skipped)} | "
        f"{'с номерами строк' if numbered else 'без номеров строк (чистый код)'}"
    )
    if extra_header:
        out.append("")
        out.extend(extra_header)
    out.append("")
    out.append("СТРУКТУРА ФАЙЛОВ:")
    out.extend(tree_lines)
    if skipped:
        out.append("")
        out.append("ПРОПУЩЕНО (содержимое не включено):")
        for rel, sz, why in skipped:
            out.append(f"  {rel} — {why}, {round(sz / 1024.0, 1)} КБ")
    out.append("")
    return "\n".join(out) + "\n" + "\n".join(bodies), len(included), skipped, total_lines


def export_project_to_txt(folder_path: str, save_dir: str, numbered: bool = False) -> dict:
    if not os.path.isdir(folder_path):
        raise ValueError(
            f"Папка не найдена или нет доступа: {folder_path}. Нажми «🔓 Выдать Доступ ко всем файлам»!"
        )
    set_progress(True, 10, "📝 Экспорт проекта в .txt", f"Сбор файлов из {folder_path}...")
    fmap = _collect_files_from_folder(folder_path)
    if not fmap:
        set_progress(False, 0)
        raise ValueError(
            "Android скрыл файлы в этой папке (0 файлов). Нажми «🔓 Выдать Доступ ко всем файлам» вверху экрана и повтори!"
        )
    proj = os.path.basename(folder_path.rstrip("/")) or "Project"
    set_progress(True, 50, "📝 Экспорт проекта в .txt", f"Склейка {len(fmap)} файлов в один текст...")
    text, inc_cnt, skipped, total_lines = _build_text_bundle(
        fmap, f"{proj.upper()} FULL SOURCE DUMP", numbered=numbered
    )
    os.makedirs(save_dir, exist_ok=True)
    out_path = os.path.join(save_dir, f"{proj}_for_AI.txt")
    raw = text.encode("utf-8")
    with open(out_path, "wb") as f:
        f.write(raw)
    set_progress(False, 100, "Готово", "Проект сохранён в один .txt")
    return {
        "status": "ok",
        "txt_path": out_path,
        "filename": os.path.basename(out_path),
        "files_count": inc_cnt,
        "skipped_count": len(skipped),
        "lines_count": total_lines,
        "size_kb": round(len(raw) / 1024.0, 1),
    }


def _fetch_blob_bytes_strict(blob_sha: str, token: str = "", repo: str = "") -> bytes:
    """Как _fetch_blob_bytes, но с повторами и ошибкой вместо тихой пустоты."""
    if not blob_sha:
        return b""
    last_err = None
    for attempt in range(3):
        try:
            blob_obj = gh_api(f"git/blobs/{blob_sha}", "GET", token=token, repo=repo)
            b64_content = (blob_obj.get("content") or "").replace("\n", "").replace("\r", "")
            return base64.b64decode(b64_content) if b64_content else b""
        except Exception as e:
            last_err = e
            time.sleep(0.6 * (attempt + 1))
    raise RuntimeError(f"Не удалось скачать файл с GitHub (blob {blob_sha[:7]}): {last_err}")


def export_changed_files(base_spec: str, target_spec: str, base_lbl: str = "", target_lbl: str = "",
                         folder_path: str = "", zip_path: str = "", save_dir: str = "",
                         fmt: str = "zip", numbered: bool = False) -> dict:
    """
    Берёт ИЗМЕНЁННЫЕ и НОВЫЕ файлы Источника Б относительно Источника А
    и сохраняет их ЦЕЛИКОМ: либо в .zip (с той же структурой папок, можно накладывать на проект),
    либо в один .txt для отправки ИИ. Удалённые файлы только перечисляются.
    """
    cfg = load_config()
    rep = (cfg.get("repo") or DEFAULT_REPO).strip()
    save_dir = (save_dir or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()

    set_progress(True, 8, "🔬 Изменённые файлы", "Загрузка структуры Источника А (База)...")
    side_a = _load_Compare_Side(base_spec, folder_path, zip_path, repo=rep)
    set_progress(True, 20, "🔬 Изменённые файлы", "Загрузка структуры Источника Б (Цель)...")
    side_b = _load_Compare_Side(target_spec, folder_path, zip_path, repo=rep)

    lbl_a = base_lbl or side_a["label"]
    lbl_b = target_lbl or side_b["label"]
    map_a, map_b = side_a["sha_map"], side_b["sha_map"]

    added = sorted(p for p in map_b if p not in map_a)
    modified = sorted(p for p in map_b if p in map_a and map_a[p] != map_b[p])
    removed = sorted(p for p in map_a if p not in map_b)
    to_export = sorted(added + modified)

    if not to_export:
        set_progress(False, 100, "Готово", "Изменённых файлов нет")
        return {
            "status": "ok", "has_changes": False, "files_count": 0,
            "added_count": 0, "modified_count": 0, "removed_count": len(removed),
            "removed": removed,
        }

    file_bytes = {}
    for idx, rel in enumerate(to_export, 1):
        set_progress(True, 25 + int((idx * 65) / max(1, len(to_export))), "🔬 Изменённые файлы",
                     f"Получение файла {idx} из {len(to_export)}: {rel}")
        if side_b["bytes_map"] is not None:
            file_bytes[rel] = side_b["bytes_map"].get(rel, b"")
        else:
            file_bytes[rel] = _fetch_blob_bytes_strict(map_b.get(rel, ""), repo=rep)

    proj = os.path.basename((folder_path or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).rstrip("/")) or "Project"
    tag_a = _short_src_tag(lbl_a, "A")
    tag_b = _short_src_tag(lbl_b, "B")
    ts = datetime.datetime.now().strftime("%H%M%S")
    base_name = f"{proj}_changed_{tag_a}_to_{tag_b}_{ts}"
    os.makedirs(save_dir, exist_ok=True)

    manifest = [f"А (было):  {lbl_a}", f"Б (стало): {lbl_b}",
                f"Новых: {len(added)} | изменённых: {len(modified)} | удалено в Б: {len(removed)}"]
    for p in added:
        manifest.append(f"  + НОВЫЙ     {p}")
    for p in modified:
        manifest.append(f"  ~ ИЗМЕНЁН   {p}")
    for p in removed:
        manifest.append(f"  - УДАЛЁН    {p}  (в архив/дамп не входит — удали у себя вручную)")

    set_progress(True, 94, "🔬 Изменённые файлы", "Запись результата...")
    if fmt == "txt":
        text, inc_cnt, skipped, total_lines = _build_text_bundle(
            file_bytes, f"{proj.upper()} — ТОЛЬКО ИЗМЕНЁННЫЕ ФАЙЛЫ (ЦЕЛИКОМ)", numbered=numbered, extra_header=manifest
        )
        out_path = os.path.join(save_dir, base_name + ".txt")
        raw = text.encode("utf-8")
        with open(out_path, "wb") as f:
            f.write(raw)
        size_kb = round(len(raw) / 1024.0, 1)
    else:
        out_path = os.path.join(save_dir, base_name + ".zip")
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel in to_export:
                zf.writestr(rel, file_bytes[rel])
            # Список изменений кладём в комментарий архива, а не файлом, чтобы
            # он не попал в проект при распаковке «поверх» и не улетел на GitHub.
            zf.comment = "\n".join(manifest).encode("utf-8")[:65000]
        size_kb = round(os.path.getsize(out_path) / 1024.0, 1)

    set_progress(False, 100, "Готово", "Изменённые файлы сохранены")
    return {
        "status": "ok",
        "has_changes": True,
        "fmt": fmt,
        "out_path": out_path,
        "filename": os.path.basename(out_path),
        "files_count": len(to_export),
        "added_count": len(added),
        "modified_count": len(modified),
        "removed_count": len(removed),
        "removed": removed,
        "size_kb": size_kb,
    }


class BuilderHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, data: dict, status: int = 200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        global _CACHED_RUNS_LIST
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            html_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            raw = open(html_p, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == "/api/progress":
            return self._send_json({"status": "ok", "progress": get_progress()})

        if path == "/api/config":
            cfg = load_config()
            self._send_json({"status": "ok", "config": cfg})
            return

        if path == "/api/rediscover_token":
            cfg = load_config()
            tok = _discover_git_token([qs.get("dir", [""])[0], cfg.get("source_dir"), cfg.get("browse_dir")])
            if tok:
                cfg = save_config({"token": tok})
                return self._send_json({"status": "ok", "found": True, "config": cfg})
            return self._send_json({"status": "ok", "found": False, "config": cfg})

        if path == "/api/fs/list":
            cfg = load_config()
            req_dir = (qs.get("dir", [""])[0] or cfg.get("browse_dir") or DEFAULT_SAVE_DIR).strip()
            mode = (qs.get("mode", [""])[0] or "").strip()
            if not os.path.isdir(req_dir):
                req_dir = DEFAULT_SAVE_DIR if os.path.isdir(DEFAULT_SAVE_DIR) else "/storage/emulated/0"
            req_dir = os.path.abspath(req_dir)
            save_config({"browse_dir": req_dir})

            def _fmt_size(sz_b: int) -> str:
                if sz_b < 1024:
                    return f"{sz_b} Б"
                if sz_b < 1024 * 1024:
                    return f"{round(sz_b / 1024.0, 1)} КБ"
                return f"{round(sz_b / (1024.0 * 1024.0), 2)} МБ"

            def _fmt_dt(ts: float) -> str:
                try:
                    return datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M:%S")
                except Exception:
                    return ""

            dirs_detail = []
            files_detail = []
            zips_out = []
            apks_out = []
            try:
                for entry in os.listdir(req_dir):
                    if entry.startswith("."):
                        continue
                    fp = os.path.join(req_dir, entry)
                    try:
                        st = os.stat(fp)
                        mtime = float(st.st_mtime)
                        sz_b = int(st.st_size)
                    except Exception:
                        mtime = 0.0
                        sz_b = 0

                    dt_str = _fmt_dt(mtime)
                    if os.path.isdir(fp):
                        try:
                            cnt = len([x for x in os.listdir(fp) if not x.startswith(".")])
                        except Exception:
                            cnt = 0
                        dirs_detail.append({
                            "name": entry,
                            "path": fp,
                            "mtime": mtime,
                            "date": dt_str,
                            "count": cnt,
                        })
                    elif os.path.isfile(fp):
                        low = entry.lower()
                        ext = os.path.splitext(low)[1]
                        sz_mb = round(sz_b / (1024.0 * 1024.0), 2)
                        sz_str = _fmt_size(sz_b)
                        f_item = {
                            "name": entry,
                            "path": fp,
                            "ext": ext,
                            "bytes": sz_b,
                            "mb": sz_mb,
                            "size_str": sz_str,
                            "mtime": mtime,
                            "date": dt_str,
                        }
                        files_detail.append(f_item)
                        if low.endswith(".zip"):
                            zips_out.append(f_item)
                        elif low.endswith(".apk"):
                            apks_out.append(f_item)
            except Exception as e:
                return self._send_json({"status": "error", "error": str(e)})

            # Сортируем все списки: самые новые вверху (mtime DESC)
            dirs_detail.sort(key=lambda x: (x["mtime"], x["name"].lower()), reverse=True)
            files_detail.sort(key=lambda x: (x["mtime"], x["name"].lower()), reverse=True)
            zips_out.sort(key=lambda x: (x["mtime"], x["name"].lower()), reverse=True)
            apks_out.sort(key=lambda x: (x["mtime"], x["name"].lower()), reverse=True)

            dirs_out = [d["name"] for d in dirs_detail]
            parent_dir = os.path.dirname(req_dir) if req_dir != "/" else "/"
            self._send_json({
                "status": "ok",
                "current_dir": req_dir,
                "parent_dir": parent_dir,
                "mode": mode,
                "dirs": dirs_out,
                "dirs_detail": dirs_detail,
                "files_detail": files_detail,
                "zips": zips_out,
                "apks": apks_out,
            })
            return

        if path == "/api/runs":
            try:
                cfg = load_config()
                runs_data = gh_api("actions/runs?per_page=35", "GET", token=cfg["token"], repo=cfg["repo"])
                runs_list = []
                latest_good_summary = None
                for r in runs_data.get("workflow_runs", []):
                    title = r.get("display_title") or r.get("name") or "Build"
                    vtag = _extract_version_tag(title)
                    item_dict = {
                        "id": r["id"],
                        "run_number": r.get("run_number"),
                        "version_tag": vtag,
                        "status": r.get("status"),
                        "conclusion": r.get("conclusion"),
                        "head_sha": (r.get("head_sha") or "")[:7],
                        "full_sha": r.get("head_sha") or "",
                        "title": title,
                        "created_at": (r.get("created_at") or "").replace("T", " ")[:19],
                    }
                    runs_list.append(item_dict)
                    if r.get("conclusion") == "success" and latest_good_summary is None:
                        latest_good_summary = item_dict

                _CACHED_RUNS_LIST = runs_list

                target_run_id = qs.get("run_id", [""])[0]
                if not target_run_id and runs_list:
                    target_run_id = str(runs_list[0]["id"])

                selected_run_meta = None
                for item in runs_list:
                    if str(item["id"]) == str(target_run_id):
                        selected_run_meta = item
                        break

                active_details = None
                if target_run_id:
                    jobs_data = gh_api(f"actions/runs/{target_run_id}/jobs", "GET", token=cfg["token"], repo=cfg["repo"])
                    steps_out = []
                    failed_job_id = None
                    failed_step_info = None
                    for jb in jobs_data.get("jobs", []):
                        for st in jb.get("steps", []):
                            st_conc = st.get("conclusion")
                            st_stat = st.get("status")
                            steps_out.append({
                                "number": st.get("number"),
                                "name": st.get("name"),
                                "status": st_stat,
                                "conclusion": st_conc,
                            })
                            if st_conc == "failure" and not failed_job_id:
                                failed_job_id = jb["id"]
                                failed_step_info = st

                    active_details = {
                        "run_id": int(target_run_id),
                        "run_number": selected_run_meta.get("run_number") if selected_run_meta else None,
                        "version_tag": selected_run_meta.get("version_tag") if selected_run_meta else "",
                        "title": selected_run_meta.get("title") if selected_run_meta else "",
                        "steps": steps_out,
                        "failed_job_id": failed_job_id,
                        "failed_step_name": failed_step_info.get("name") if failed_step_info else "",
                        "failed_started_at": failed_step_info.get("started_at") if failed_step_info else "",
                        "failed_completed_at": failed_step_info.get("completed_at") if failed_step_info else "",
                    }

                installed_status = resolve_installed_apps_status(None, runs_list)

                self._send_json({
                    "status": "ok",
                    "repo": cfg["repo"],
                    "runs": runs_list,
                    "latest_good": latest_good_summary,
                    "active": active_details,
                    "installed": installed_status,
                })
            except Exception as e:
                self._send_json({"status": "error", "error": str(e)})
            return

        if path == "/api/job_error_log":
            try:
                job_id = qs.get("job_id", [""])[0]
                step_name = qs.get("step_name", ["Build Step"])[0]
                started_at = qs.get("started_at", [""])[0]
                completed_at = qs.get("completed_at", [""])[0]
                if not job_id:
                    return self._send_json({"status": "error", "error": "Не указан job_id"})
                raw_bytes = gh_download_bytes(f"actions/jobs/{job_id}/logs", task_title="Загрузка лога ошибки")
                set_progress(False, 100)
                raw_text = raw_bytes.decode("utf-8", errors="replace")
                step_log = extract_failed_step_log(raw_text, step_name, started_at, completed_at)
                self._send_json({
                    "status": "ok",
                    "step_name": step_name,
                    "error_log": step_log,
                })
            except Exception as e:
                set_progress(False, 0)
                self._send_json({"status": "error", "error": str(e)})
            return

        self._send_json({"status": "error", "error": "Not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(raw_body.decode("utf-8", errors="replace"))
        except Exception:
            data = {}

        try:
            if path == "/api/cancel_download":
                removed = trigger_download_cancel()
                return self._send_json({
                    "status": "ok",
                    "cancelled": True,
                    "removed_files": removed,
                })

            if path == "/api/config":
                cfg = save_config(data)
                return self._send_json({"status": "ok", "config": cfg})

            if path == "/api/installed_info":
                hints = data.get("bridge_hints") or {}
                inst = resolve_installed_apps_status(hints, _CACHED_RUNS_LIST)
                return self._send_json({"status": "ok", "installed": inst})

            if path in ("/api/download_build_zip", "/api/download_last_good_zip", "/api/download_clean_zip"):
                cfg = load_config()
                target_id = str(data.get("target_id") or data.get("run_id") or data.get("build_number") or "last_good")
                skip_so = bool(data.get("skip_so", False))
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                res = download_build_source_zip_custom(
                    target_id=target_id,
                    skip_so=skip_so,
                    save_dir=save_dir,
                    token=data.get("token") or cfg.get("token") or "",
                    repo=data.get("repo") or cfg.get("repo") or DEFAULT_REPO,
                )
                return self._send_json(res)

            if path == "/api/save_diff_txt":
                cfg = load_config()
                report_text = data.get("report_text") or ""
                if not report_text.strip():
                    return self._send_json({"status": "error", "error": "Текст отчёта пуст"})
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                os.makedirs(save_dir, exist_ok=True)
                base_lbl = data.get("base_label") or ""
                target_lbl = data.get("target_label") or ""

                def _short_tag(lbl: str, fb: str) -> str:
                    m_b = re.search(r"#(\d+)", lbl)
                    if m_b:
                        return f"b{m_b.group(1)}"
                    if "Рабочая папка" in lbl or "LOCAL_FOLDER" in lbl:
                        return "folder"
                    if ".zip" in lbl.lower():
                        return "zip"
                    m_id = re.search(r"ID:\s*([0-9a-f]{5,10})", lbl, re.I)
                    if m_id:
                        return m_id.group(1)[:7]
                    return fb

                tag_a = _short_tag(base_lbl, "A")
                tag_b = _short_tag(target_lbl, "B")
                ts_str = datetime.datetime.now().strftime("%H%M%S")
                fname = f"diff_{tag_a}_to_{tag_b}_{ts_str}.txt"
                out_path = os.path.join(save_dir, fname)
                raw_bytes = report_text.encode("utf-8")
                with open(out_path, "wb") as f:
                    f.write(raw_bytes)
                sz_kb = round(len(raw_bytes) / 1024.0, 1)
                return self._send_json({
                    "status": "ok",
                    "txt_path": out_path,
                    "filename": fname,
                    "size_kb": sz_kb,
                })

            if path == "/api/doctor/scan":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                set_progress(True, 25, "🩺 Рентген проекта", f"Проверка синтаксиса, дублей и API в {folder_p}...")
                res = doctor.scan_project_health(folder_p)
                res.pop("_internal", None)
                set_progress(False, 100, "Готово", "Диагностика завершена")
                return self._send_json(res)

            if path == "/api/doctor/clean":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                set_progress(True, 20, "🧹 Безопасная чистка проекта", "Создание бэкапа и очистка с проверкой V8/AST...")
                res = doctor.clean_project_safe(
                    folder_p,
                    clean_trash=bool(data.get("clean_trash", True)),
                    clean_overrides=bool(data.get("clean_overrides", True)),
                    clean_unused_so=bool(data.get("clean_unused_so", False)),
                )
                set_progress(False, 100, "Готово", f"Очищено {res['freed_kb']} КБ")
                return self._send_json(res)

            if path == "/api/doctor/restore":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                res = doctor.restore_latest_backup(folder_p)
                return self._send_json(res)

            if path == "/api/doctor/hud":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                res = doctor.toggle_blackbox_hud(folder_p, enable=bool(data.get("enable", True)))
                return self._send_json(res)

            if path == "/api/compare":
                base_spec = (data.get("base_spec") or "LOCAL_FOLDER").strip()
                target_spec = (data.get("target_spec") or "LOCAL_FOLDER").strip()
                base_lbl = (data.get("base_label") or "").strip()
                target_lbl = (data.get("target_label") or "").strip()
                folder_p = (data.get("folder_path") or "").strip()
                zip_p = (data.get("zip_path") or "").strip()

                res = compare_any_two_sources(
                    base_spec=base_spec,
                    target_spec=target_spec,
                    base_custom_lbl=base_lbl,
                    target_custom_lbl=target_lbl,
                    folder_path=folder_p,
                    zip_path=zip_p,
                )
                return self._send_json({"status": "ok", **res})

            if path == "/api/export_project_txt":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                res = export_project_to_txt(folder_p, save_dir, numbered=bool(data.get("numbered", False)))
                return self._send_json(res)

            if path == "/api/export_changed":
                res = export_changed_files(
                    base_spec=(data.get("base_spec") or "LOCAL_FOLDER").strip(),
                    target_spec=(data.get("target_spec") or "LOCAL_FOLDER").strip(),
                    base_lbl=(data.get("base_label") or "").strip(),
                    target_lbl=(data.get("target_label") or "").strip(),
                    folder_path=(data.get("folder_path") or "").strip(),
                    zip_path=(data.get("zip_path") or "").strip(),
                    save_dir=(data.get("save_dir") or "").strip(),
                    fmt=("txt" if (data.get("fmt") == "txt") else "zip"),
                    numbered=bool(data.get("numbered", False)),
                )
                return self._send_json(res)

            if path == "/api/fs/mkdir":
                parent = (data.get("parent") or DEFAULT_SAVE_DIR).strip()
                new_name = os.path.basename((data.get("name") or "").strip())
                if not new_name:
                    return self._send_json({"status": "error", "error": "Пустое имя папки"})
                full_new = os.path.join(parent, new_name)
                os.makedirs(full_new, exist_ok=True)
                save_config({"save_dir": full_new, "browse_dir": full_new})
                return self._send_json({"status": "ok", "created": full_new})

            if path == "/api/push_local_zip":
                zip_path = (data.get("zip_path") or "").strip()
                commit_msg = (data.get("message") or "").strip() or f"build(zip): push {os.path.basename(zip_path)} via JennyBuilder"
                if not zip_path or not os.path.isfile(zip_path):
                    return self._send_json({
                        "status": "error",
                        "error": f"Файл архива не найден или нет доступа: {zip_path}. Проверь «Доступ ко всем файлам»!"
                    })
                set_progress(True, 3, "📤 Отправка .zip на GitHub", f"Чтение архива {os.path.basename(zip_path)}...")
                file_map = _collect_files_from_zip_bytes(open(zip_path, "rb").read())
                res = push_file_map_to_github(file_map, commit_msg)
                return self._send_json({"status": "ok", **res})

            if path == "/api/push_uploaded_zip":
                b64_str = data.get("zip_base64") or ""
                fname = data.get("filename") or "uploaded.zip"
                commit_msg = (data.get("message") or "").strip() or f"build(upload): push {fname} via JennyBuilder"
                if not b64_str:
                    return self._send_json({"status": "error", "error": "Пустой архив"})
                set_progress(True, 3, "📤 Отправка .zip на GitHub", f"Распаковка {fname}...")
                zip_bytes = base64.b64decode(b64_str)
                file_map = _collect_files_from_zip_bytes(zip_bytes)
                res = push_file_map_to_github(file_map, commit_msg)
                return self._send_json({"status": "ok", **res})

            if path == "/api/push_folder":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                commit_msg = (data.get("message") or "").strip() or f"build(folder): push {os.path.basename(folder_p)} via JennyBuilder"
                if not os.path.isdir(folder_p):
                    return self._send_json({
                        "status": "error",
                        "error": f"Папка не найдена или скрыта Android: {folder_p}. Разреши «Доступ ко всем файлам»!"
                    })
                save_config({"source_dir": folder_p})
                set_progress(True, 3, "📤 Экспорт папки на GitHub", f"Сканирование файлов в {folder_p}...")
                file_map = _collect_files_from_folder(folder_p)
                res = push_file_map_to_github(file_map, commit_msg)
                return self._send_json({"status": "ok", **res})

            if path == "/api/download_apk":
                reset_download_cancel()
                cfg = load_config()
                run_id = data.get("run_id")
                run_number = data.get("run_number") or run_id
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                os.makedirs(save_dir, exist_ok=True)
                save_config({"save_dir": save_dir})

                repo_short = cfg["repo"].split("/")[-1]
                out_apk_name = f"{repo_short}_#{run_number}.apk"
                out_apk_path = os.path.join(save_dir, out_apk_name)
                part_apk_path = out_apk_path + ".part"
                register_temp_download_file(part_apk_path)

                set_progress(True, 2, f"📥 Скачивание APK #{run_number}", "Получение ссылки на артефакт сборки...", cancellable=True)
                arts = gh_api(f"actions/runs/{run_id}/artifacts", "GET")
                art_list = arts.get("artifacts", [])
                if not art_list:
                    unregister_temp_download_file(part_apk_path)
                    set_progress(False, 0)
                    return self._send_json({"status": "error", "error": "Артефакт APK ещё не сформирован в этой сборке"})
                art_id = art_list[0]["id"]
                zip_bytes = gh_download_bytes(
                    f"actions/artifacts/{art_id}/zip",
                    task_title=f"📥 Скачивание APK #{run_number} (32 потока)",
                    threads=32,
                    cancellable=True,
                )
                check_download_cancelled()

                set_progress(True, 98, f"📥 Распаковка APK #{run_number}", f"Сохранение в {out_apk_path}...", cancellable=True)
                with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                    apk_members = [m for m in zf.namelist() if m.lower().endswith(".apk")]
                    if not apk_members:
                        cleanup_cancelled_temp_files()
                        set_progress(False, 0)
                        return self._send_json({"status": "error", "error": "Внутри артефакта не найден .apk файл"})
                    with open(part_apk_path, "wb") as out_f:
                        out_f.write(zf.read(apk_members[0]))

                check_download_cancelled()
                os.replace(part_apk_path, out_apk_path)
                unregister_temp_download_file(part_apk_path)

                sz_mb = round(os.path.getsize(out_apk_path) / (1024.0 * 1024.0), 2)
                set_progress(False, 100, "Готово", f"APK #{run_number} сохранён", cancellable=False)
                return self._send_json({
                    "status": "ok",
                    "apk_path": out_apk_path,
                    "size_mb": sz_mb,
                    "run_number": run_number,
                })

            if path == "/api/download_last_good_zip":
                cfg = load_config()
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                os.makedirs(save_dir, exist_ok=True)
                save_config({"save_dir": save_dir})

                set_progress(True, 5, "📦 Скачивание .zip с GitHub", "Поиск последней успешной сборки...")
                runs_data = gh_api("actions/runs?conclusion=success&per_page=5", "GET")
                good_run = None
                for r in runs_data.get("workflow_runs", []):
                    if r.get("conclusion") == "success":
                        good_run = r
                        break

                if good_run:
                    sha = good_run["head_sha"]
                    meta = _describe_run_or_commit(sha, good_run)
                else:
                    br, sha = _resolve_target_branch()
                    meta = _describe_run_or_commit(sha, None)

                bnum = meta.get("build_number") or "latest"
                raw_zip_bytes = gh_download_bytes(f"zipball/{sha}",
                    task_title=f"📦 Скачивание исходников Сборки #{bnum}",
                    threads=1,
                )

                repo_short = cfg["repo"].split("/")[-1]
                out_zip_name = f"{repo_short}_#{bnum}_clean.zip"
                out_zip_path = os.path.join(save_dir, out_zip_name)

                set_progress(True, 95, "📦 Упаковка чистого .zip", f"Сохранение {out_zip_name}...")
                with zipfile.ZipFile(io.BytesIO(raw_zip_bytes), "r") as zin, zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.infolist():
                        parts = item.filename.split("/", 1)
                        clean_rel = parts[1] if len(parts) > 1 else ""
                        if not clean_rel or clean_rel.endswith("/"):
                            continue
                        zout.writestr(clean_rel, zin.read(item.filename))

                sz_mb = round(os.path.getsize(out_zip_path) / (1024.0 * 1024.0), 2)
                set_progress(False, 100, "Готово", "Архив сохранён")
                return self._send_json({
                    "status": "ok",
                    "zip_path": out_zip_path,
                    "size_mb": sz_mb,
                    "build_number": bnum,
                    "title": meta.get("title"),
                    "human_label": meta.get("human_label"),
                })

            if path == "/api/pull_to_folder":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                save_config({"source_dir": folder_p})

                set_progress(True, 5, "⬇️ Импорт с GitHub в папку", "Поиск последней успешной сборки...")
                runs_data = gh_api("actions/runs?conclusion=success&per_page=5", "GET")
                good_runs = [r for r in runs_data.get("workflow_runs", []) if r.get("conclusion") == "success"]
                if good_runs:
                    sha = good_runs[0]["head_sha"]
                    meta = _describe_run_or_commit(sha, good_runs[0])
                else:
                    _, sha = _resolve_target_branch()
                    meta = _describe_run_or_commit(sha, None)

                reset_download_cancel()
                bnum = meta.get("build_number") or "latest"
                raw_zip_bytes = _download_zipball_safe_bytes(
                    sha=sha,
                    token=cfg.get("token") or "",
                    repo=cfg.get("repo") or DEFAULT_REPO,
                    task_title=f"⬇️ Скачивание Сборки #{bnum} с GitHub",
                )
                written = _unpack_zip_bytes_to_folder(raw_zip_bytes, folder_p)
                set_progress(False, 100, "Готово", f"Записано {written} файлов")
                return self._send_json({
                    "status": "ok",
                    "folder_path": folder_p,
                    "written_files": written,
                    "build_number": meta.get("build_number"),
                    "title": meta.get("title"),
                    "human_label": meta.get("human_label"),
                })

            if path == "/api/unpack_zip_to_folder":
                cfg = load_config()
                zip_path = (data.get("zip_path") or "").strip()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                if not zip_path or not os.path.isfile(zip_path):
                    return self._send_json({"status": "error", "error": f"Архив не найден: {zip_path}"})
                save_config({"source_dir": folder_p})
                set_progress(True, 20, "📥 Распаковка .zip в папку", f"Чтение {os.path.basename(zip_path)}...")
                written = _unpack_zip_bytes_to_folder(open(zip_path, "rb").read(), folder_p)
                set_progress(False, 100, "Готово", f"Записано {written} файлов")
                return self._send_json({
                    "status": "ok",
                    "zip_path": zip_path,
                    "folder_path": folder_p,
                    "written_files": written,
                })

            if path == "/api/pack_local_folder_zip":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                if not os.path.isdir(folder_p):
                    return self._send_json({
                        "status": "error",
                        "error": f"Папка не найдена или нет доступа: {folder_p}. Нажми «🔓 Выдать Доступ ко всем файлам»!"
                    })
                os.makedirs(save_dir, exist_ok=True)
                proj_name = os.path.basename(folder_p.rstrip("/")) or "Project"
                out_zip_path = os.path.join(save_dir, f"{proj_name}_clean_local.zip")
                set_progress(True, 15, "📦 Упаковка папки в .zip", f"Сбор файлов из {folder_p}...")
                fmap = _collect_files_from_folder(folder_p)
                if not fmap:
                    set_progress(False, 0)
                    return self._send_json({
                        "status": "error",
                        "error": "Android скрыл файлы в этой папке (0 файлов). Нажми «🔓 Выдать Доступ ко всем файлам» вверху экрана и повтори!"
                    })
                total_f = len(fmap)
                with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for idx, (rel_p, content_b) in enumerate(fmap.items(), 1):
                        if idx % 5 == 0 or idx == total_f:
                            set_progress(True, 20 + int((idx * 75) / max(1, total_f)), "📦 Упаковка папки в .zip", f"Сжатие {idx} из {total_f}: {rel_p}")
                        zf.writestr(rel_p, content_b)
                sz_mb = round(os.path.getsize(out_zip_path) / (1024.0 * 1024.0), 2)
                set_progress(False, 100, "Готово", "Архив упакован")
                return self._send_json({
                    "status": "ok",
                    "folder_path": folder_p,
                    "zip_path": out_zip_path,
                    "files_count": len(fmap),
                    "size_mb": sz_mb,
                })

            self._send_json({"status": "error", "error": "Unknown endpoint"}, 404)
        except DownloadCancelledError:
            removed = cleanup_cancelled_temp_files()
            set_progress(False, 0, "⏹ Отменено", "Недокачанные файлы удалены", cancellable=False)
            self._send_json({
                "status": "cancelled",
                "error": "Загрузка отменена пользователем. Недокачанный файл удалён.",
                "removed_files": removed,
            })
        except Exception as e:
            cleanup_cancelled_temp_files()
            set_progress(False, 0)
            self._send_json({"status": "error", "error": f"{e}"})


def start_server():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), BuilderHandler)
        print(f"🚀 JennyBuilder server listening on http://127.0.0.1:{PORT}")
        srv.serve_forever()
    except OSError as e:
        print("Server bind info:", e)


_server_started_flag = False
_server_lock = _threading_boot.Lock()


def ensure_builder_server_running():
    global _server_started_flag
    with _server_lock:
        if _server_started_flag:
            return
        _server_started_flag = True
    t = _threading_boot.Thread(target=start_server, daemon=True)
    t.start()


ensure_builder_server_running()
