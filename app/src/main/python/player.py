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
import http.client
import urllib.request
import urllib.parse
import urllib.error
import threading as _threading_boot
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8795
EXT_CONFIG_PATH = "/storage/emulated/0/Download/.jenny_builder_cfg.json"
DEFAULT_PROJECT_DIR = "/storage/emulated/0/scripts/JennyPlayer"
DEFAULT_SAVE_DIR = "/storage/emulated/0/Download"
DEFAULT_REPO = "hgg543470-bot/JennyPlayer"

EXCLUDE_DIRS = {".git", "__pycache__", ".gradle", "build", ".idea", ".Temp"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".log"}
EXCLUDE_FILES = {"omni_full_trace.log", "omni_simple.log", "shizuku_window_dump.txt"}

_MEM_CFG = {}
_CACHED_RUNS_LIST = []


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
        "manual_installed_build": None,
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
# ТОЧНОЕ ОПРЕДЕЛЕНИЕ УСТАНОВЛЕННОЙ СБОРКИ ЧЕРЕЗ ПАСПОРТ APK (БЕЗ УГАДЫВАНИЯ)
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
    """
    Определяет установленную сборку строго по:
    1. Штампу GitHub Actions в versionName ("#252 (v166...)") или versionCode == run_number.
    2. Если установлен старый APK (versionCode == 1 без штампа # в versionName) — берёт номер сборки,
       который был скачан/выбран пользователем (или последнюю сборку #251, если она только что скачана).
    Никакого сканирования случайных файлов в Download!
    """
    cfg = load_config()
    runs = runs_list if runs_list is not None else _CACHED_RUNS_LIST
    manual_bnum = cfg.get("manual_installed_build")

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
        vtag = ""
        commit_title = ""
        commit_sha = ""
        is_exact_stamp = False

        # 1. Точный штамп в versionName: "#251 (...)"
        m_stamp = re.search(r"#(\d+)\s*(?:\((.*)\))?", ver_name)
        if m_stamp:
            build_num = int(m_stamp.group(1))
            commit_title = (m_stamp.group(2) or "").strip()
            vtag = _extract_version_tag(commit_title) or _extract_version_tag(ver_name)
            is_exact_stamp = True
        elif v_code > 10 and key == "player":
            # Точный штамп в versionCode == GITHUB_RUN_NUMBER
            build_num = v_code
            is_exact_stamp = True

        # 2. Если это старый APK плеера без штампа (versionCode <= 10), используем выбранный/скачанный пользователем номер сборки
        if key == "player" and not build_num and manual_bnum:
            try:
                build_num = int(manual_bnum)
            except Exception:
                pass

        # 3. Обогащаем полными данными из истории GitHub Actions по номеру сборки
        if key == "player" and runs and build_num:
            for r in runs:
                if r.get("run_number") == build_num:
                    commit_title = r.get("title") or commit_title
                    vtag = r.get("version_tag") or vtag
                    commit_sha = r.get("head_sha") or ""
                    break

        if key == "player":
            if build_num:
                parts = [f"Сборка #{build_num}"]
                if vtag:
                    parts.append(vtag)
                if commit_sha:
                    parts.append(f"ID: {commit_sha}")
                summary = " / ".join(parts)
                if commit_title:
                    summary += f" — «{commit_title}»"
            else:
                summary = f"APK без штампа номера (versionName: {ver_name}) — выбери установленную сборку ниже"
        else:
            summary = f"Версия {ver_name}" if ver_name else "Установлен"

        out[key] = {
            "installed": True,
            "package": pkg,
            "versionName": ver_name,
            "versionCode": v_code,
            "updated_at": upd_str,
            "build_number": build_num,
            "version_tag": vtag,
            "commit_sha": commit_sha,
            "title": commit_title,
            "is_exact_stamp": is_exact_stamp,
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
        "User-Agent": "JennyBuilder-Android/1.6",
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


def gh_download_bytes(endpoint: str, token: str = "", repo: str = "") -> bytes:
    cfg = load_config()
    tok = (token or cfg.get("token") or "").strip()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    url = endpoint if endpoint.startswith("http") else f"https://api.github.com/repos/{rep}/{endpoint.lstrip('/')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JennyBuilder-Android/1.6",
    }
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    last_err = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            opener = urllib.request.build_opener(_NoAuthRedirectHandler())
            with opener.open(req, timeout=180) as resp:
                buf = bytearray()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                return bytes(buf)
        except Exception as e:
            last_err = e
            time.sleep(1.5)
    raise RuntimeError(f"Не удалось скачать поток после 4 попыток: {last_err}")


def _describe_run_or_commit(sha: str = "", run_obj: dict = None) -> dict:
    if run_obj:
        bnum = run_obj.get("run_number") or ""
        title = (run_obj.get("display_title") or run_obj.get("name") or "").strip()
        vtag = _extract_version_tag(title)
        r_sha = (run_obj.get("head_sha") or sha or "")[:7]
        human = f"Сборка #{bnum}" if bnum else "Сборка"
        if vtag:
            human += f" / {vtag}"
        if r_sha:
            human += f" (ID: {r_sha})"
        if title:
            human += f" — «{title}»"
        return {
            "build_number": bnum,
            "version_tag": vtag,
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
                "version_tag": r.get("version_tag"),
                "title": r.get("title"),
                "human_label": f"Сборка #{r.get('run_number')} / {r.get('version_tag') or ''} (ID: {r.get('head_sha')}) — «{r.get('title')}»",
                "sha": (r.get("head_sha") or "")[:7],
                "full_sha": r.get("full_sha") or "",
            }

    try:
        runs_data = gh_api("actions/runs?per_page=30", "GET")
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
    vtag = _extract_version_tag(title)
    human = f"Версия {vtag}" if vtag else "Коммит"
    if sha:
        human += f" (ID: {sha[:7]})"
    if title:
        human += f" — «{title}»"
    return {
        "build_number": "",
        "version_tag": vtag,
        "title": title,
        "human_label": human,
        "sha": (sha or "")[:7],
        "full_sha": sha or "",
    }


def _git_blob_sha1(data: bytes) -> str:
    hdr = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(hdr + data).hexdigest()


# ==============================================================================
# ПОЛНЫЙ GIT-АНАЛИЗАТОР ИЗМЕНЕНИЙ (МЕЖДУ ЛЮБЫМИ СБОРКАМИ ИЛИ ЛОКАЛЬНОЙ ПАПКОЙ)
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


def _extract_brace_blocks(text: str, pattern: re.Pattern, kind_formatter) -> dict:
    symbols = {}
    for m in pattern.finditer(text):
        name, sig = kind_formatter(m)
        if not name or name in ("if", "for", "while", "switch", "catch", "synchronized", "function"):
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
    return _extract_brace_blocks(text, pat, fmt)


def _extract_java_symbols(text: str) -> dict:
    pat = re.compile(
        r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?"
        r"[A-Za-z0-9_<>\[\].,\s]+?\s+([A-Za-z0-9_]+)\s*\(([^)]*)\)\s*(?:throws\s+[A-Za-z0-9_,\s]+)?\{"
    )
    def fmt(m):
        name = m.group(1).strip()
        args = " ".join(m.group(2).split())
        return name, f"{name}({args})"
    return _extract_brace_blocks(text, pat, fmt)


def _compact_unified_diff(old_str: str, new_str: str, max_lines: int = 0) -> list:
    old_lines = old_str.splitlines()
    new_lines = new_str.splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, n=2, lineterm=""))
    out = []
    for ln in diff:
        if ln.startswith(("---", "+++")):
            continue
        out.append(ln)
    return out


def _analyze_single_file_diff(rel_path: str, old_bytes: bytes, new_bytes: bytes, git_patch_str: str = "") -> dict:
    ext = os.path.splitext(rel_path)[1].lower()
    binary_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".so", ".jar", ".apk", ".zip", ".keystore", ".jks"}
    if ext in binary_exts:
        old_kb = round(len(old_bytes) / 1024.0, 1)
        new_kb = round(len(new_bytes) / 1024.0, 1)
        return {
            "path": rel_path,
            "is_binary": True,
            "added_lines": 0,
            "removed_lines": 0,
            "added_funcs": [],
            "removed_funcs": [],
            "modified_funcs": [],
            "func_diffs": {},
            "raw_diff_preview": [f"Бинарный файл: было {old_kb} КБ ➔ стало {new_kb} КБ"],
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
        elif ext in (".html", ".js"):
            old_syms = _extract_js_symbols(old_text)
            new_syms = _extract_js_symbols(new_text)
        elif ext == ".java":
            old_syms = _extract_java_symbols(old_text)
            new_syms = _extract_java_symbols(new_text)

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
                func_diffs[info["sig"]] = _compact_unified_diff(old_syms[k]["body"], info["body"], max_lines=14)
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


def compare_two_github_commits(base_sha: str, head_sha: str, base_label: str = "", head_label: str = "", token: str = "", repo: str = "") -> dict:
    """
    Использует родной Git Compare API GitHub (/compare/{base}...{head}):
    выдаёт список всех коммитов между двумя сборками, список изменённых функций и точный git diff!
    """
    cfg = load_config()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    base_meta = _describe_run_or_commit(base_sha, None)
    head_meta = _describe_run_or_commit(head_sha, None)
    b_lbl = base_label or base_meta.get("human_label") or base_sha[:7]
    h_lbl = head_label or head_meta.get("human_label") or head_sha[:7]

    if base_sha == head_sha or (base_sha and head_sha and base_sha[:7] == head_sha[:7]):
        txt = (
            "==========================================================\n"
            "📊 GIT DIFF: СРАВНЕНИЕ СБОРОК\n"
            "==========================================================\n"
            f"• База (Откуда): {b_lbl}\n"
            f"• Цель (Куда):   {h_lbl}\n"
            "----------------------------------------------------------\n"
            "✅ Выбрана одна и та же сборка (0 отличий)."
        )
        return {
            "has_changes": False,
            "short_commit_msg": "",
            "report_text": txt,
            "modified_count": 0,
            "added_count": 0,
            "removed_count": 0,
        }

    comp = gh_api(f"compare/{base_sha}...{head_sha}", "GET", token=token, repo=rep)
    commits = comp.get("commits") or []
    files = comp.get("files") or []

    # Получаем деревья base и head для AST-сравнения функций в изменённых исходниках
    base_tree_map = {}
    head_tree_map = {}
    try:
        bc = gh_api(f"git/commits/{base_sha}", "GET", token=token, repo=rep)
        bt = gh_api(f"git/trees/{bc['tree']['sha']}?recursive=1", "GET", token=token, repo=rep)
        base_tree_map = {it["path"]: it["sha"] for it in bt.get("tree", []) if it.get("type") == "blob"}
    except Exception:
        pass
    try:
        hc = gh_api(f"git/commits/{head_sha}", "GET", token=token, repo=rep)
        ht = gh_api(f"git/trees/{hc['tree']['sha']}?recursive=1", "GET", token=token, repo=rep)
        head_tree_map = {it["path"]: it["sha"] for it in ht.get("tree", []) if it.get("type") == "blob"}
    except Exception:
        pass

    clean_files = [f for f in files if not _is_ignored_rel_path(f.get("filename") or "")]
    added_files = [f for f in clean_files if f.get("status") == "added"]
    removed_files = [f for f in clean_files if f.get("status") == "removed"]
    modified_files = [f for f in clean_files if f.get("status") not in ("added", "removed")]

    report_lines = []
    report_lines.append("==========================================================")
    report_lines.append("📊 ТОЧНЫЙ GIT DIFF МЕЖДУ СБОРКАМИ НА GITHUB")
    report_lines.append("==========================================================")
    report_lines.append(f"• Старая сборка (База): {b_lbl}")
    report_lines.append(f"• Новая сборка (Цель):  {h_lbl}")
    report_lines.append(
        f"• Коммитов между ними: {len(commits)} | Файлов изменено: {len(modified_files)} | "
        f"добавлено: {len(added_files)} | удалено: {len(removed_files)}"
    )
    report_lines.append("----------------------------------------------------------")

    if commits:
        report_lines.append(f"\n📜 ИСТОРИЯ КОММИТОВ МЕЖДУ СБОРКАМИ ({len(commits)}):")
        for c in commits:
            c_sha = (c.get("sha") or "")[:7]
            c_msg = ((c.get("commit") or {}).get("message") or "").splitlines()[0]
            c_date = (((c.get("commit") or {}).get("author") or {}).get("date") or "").replace("T", " ")[:16]
            report_lines.append(f"  • [{c_sha}] ({c_date}) {c_msg}")

    if added_files:
        report_lines.append(f"\n🟢 ДОБАВЛЕННЫЕ ФАЙЛЫ ({len(added_files)}):")
        for af in added_files:
            fn = af["filename"]
            report_lines.append(f"  + {fn} (+{af.get('additions', 0)} строк)")

    if removed_files:
        report_lines.append(f"\n🔴 УДАЛЁННЫЕ ФАЙЛЫ ({len(removed_files)}):")
        for rf in removed_files:
            fn = rf["filename"]
            report_lines.append(f"  - {fn} (-{rf.get('deletions', 0)} строк)")

    if modified_files:
        report_lines.append(f"\n🟡 ИЗМЕНЁННЫЕ ФАЙЛЫ, ФУНКЦИИ И GIT PATCH ({len(modified_files)}):")
        for mf in modified_files:
            mp = mf["filename"]
            patch_str = mf.get("patch") or ""
            ext = os.path.splitext(mp)[1].lower()
            old_b = b""
            new_b = b""
            if ext in (".py", ".html", ".js", ".java") and base_tree_map.get(mp) and head_tree_map.get(mp):
                old_b = _fetch_blob_bytes(base_tree_map[mp], token=token, repo=rep)
                new_b = _fetch_blob_bytes(head_tree_map[mp], token=token, repo=rep)

            f_diff = _analyze_single_file_diff(mp, old_b, new_b, git_patch_str=patch_str)
            report_lines.append(
                f"\n📄 Файл: {mp} (+{mf.get('additions', f_diff['added_lines'])} / -{mf.get('deletions', f_diff['removed_lines'])} строк)"
            )
            if f_diff["added_funcs"]:
                report_lines.append(f"  🟢 Добавлены функции ({len(f_diff['added_funcs'])}):")
                for fn in f_diff["added_funcs"]:
                    report_lines.append(f"    + {fn}")
            if f_diff["removed_funcs"]:
                report_lines.append(f"  🔴 Удалены функции ({len(f_diff['removed_funcs'])}):")
                for fn in f_diff["removed_funcs"]:
                    report_lines.append(f"    - {fn}")
            if f_diff["modified_funcs"]:
                report_lines.append(f"  🟡 Изменены внутри функции ({len(f_diff['modified_funcs'])}):")
                for fn in f_diff["modified_funcs"]:
                    report_lines.append(f"    ~ {fn}")
                    for sl in (f_diff["func_diffs"].get(fn) or []):
                        report_lines.append(f"        {sl}")

            if patch_str:
                report_lines.append("  🔍 Точный Git Patch по строкам:")
                for pl in f_diff["raw_diff_preview"]:
                    report_lines.append(f"    {pl}")

    return {
        "has_changes": bool(clean_files or commits),
        "short_commit_msg": "",
        "report_text": "\n".join(report_lines),
        "modified_count": len(modified_files),
        "added_count": len(added_files),
        "removed_count": len(removed_files),
    }


def compare_local_map_with_github(local_map: dict, source_label: str, base_sha: str = "", base_label: str = "", token: str = "", repo: str = "") -> dict:
    if not local_map:
        raise ValueError("Нет доступных файлов для сравнения (0 файлов). Проверь «Доступ ко всем файлам»!")

    cfg = load_config()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    if not base_sha:
        _, base_sha = _resolve_target_branch(token=token, repo=rep)
    meta = _describe_run_or_commit(base_sha, None)
    b_lbl = base_label or meta.get("human_label") or base_sha[:7]

    commit_info = gh_api(f"git/commits/{base_sha}", "GET", token=token, repo=rep)
    base_tree_sha = commit_info["tree"]["sha"]
    tree_info = gh_api(f"git/trees/{base_tree_sha}?recursive=1", "GET", token=token, repo=rep)

    # ВАЖНО: отфильтровываем мусор (__pycache__, .pyc, .log) и на стороне GitHub тоже!
    remote_blobs = {
        item["path"]: item["sha"]
        for item in tree_info.get("tree", [])
        if item.get("type") == "blob" and not _is_ignored_rel_path(item.get("path") or "")
    }

    added_files = []
    removed_files = []
    modified_paths = []
    unchanged_count = 0

    for rel_p, loc_bytes in sorted(local_map.items()):
        if _is_ignored_rel_path(rel_p):
            continue
        loc_sha = _git_blob_sha1(loc_bytes)
        rem_sha = remote_blobs.get(rel_p)
        if rem_sha is None:
            added_files.append(rel_p)
        elif rem_sha != loc_sha:
            modified_paths.append(rel_p)
        else:
            unchanged_count += 1

    for rem_p in sorted(remote_blobs.keys()):
        if rem_p not in local_map:
            removed_files.append(rem_p)

    report_lines = []
    report_lines.append("==========================================================")
    report_lines.append("📊 ОТЧЁТ ОБ ИЗМЕНЕНИЯХ (GIT DIFF: СБОРКА vs ЛОКАЛЬНО)")
    report_lines.append("==========================================================")
    report_lines.append(f"• Базовая сборка на GitHub: {b_lbl}")
    report_lines.append(f"• Сравниваем с:             {source_label}")
    report_lines.append(
        f"• Итог по чистым файлам кода: изменено: {len(modified_paths)} | добавлено: {len(added_files)} | "
        f"удалено: {len(removed_files)} | совпадают 1-в-1: {unchanged_count}"
    )
    report_lines.append("----------------------------------------------------------")

    if not modified_paths and not added_files and not removed_files:
        report_lines.append(
            f"✅ Код в «{source_label}» на 100% совпадает с «{b_lbl}» (0 отличий в коде)!\n"
            "💡 Подсказка: если хочешь увидеть разницу со старой сборкой (например #245 или #247), "
            "просто выбери её в верхнем выпадающем списке «База (С какой сборкой сравнивать)» и нажми кнопку сравнения!"
        )
        return {
            "has_changes": False,
            "short_commit_msg": "",
            "report_text": "\n".join(report_lines),
            "modified_count": 0,
            "added_count": 0,
            "removed_count": 0,
        }

    commit_summary_parts = []

    if added_files:
        report_lines.append(f"\n🟢 НОВЫЕ ФАЙЛЫ ({len(added_files)}):")
        for af in added_files:
            info = _analyze_single_file_diff(af, b"", local_map[af])
            report_lines.append(f"  + {af} (+{info['added_lines']} строк)")
            for fn in info["added_funcs"]:
                report_lines.append(f"      🟢 Новая функция: {fn}")
            commit_summary_parts.append(f"+{os.path.basename(af)}")

    if removed_files:
        report_lines.append(f"\n🔴 УДАЛЁННЫЕ ФАЙЛЫ ({len(removed_files)}):")
        for rf in removed_files:
            report_lines.append(f"  - {rf}")
            commit_summary_parts.append(f"-{os.path.basename(rf)}")

    if modified_paths:
        report_lines.append(f"\n🟡 ИЗМЕНЁННЫЕ ФАЙЛЫ И ФУНКЦИИ ({len(modified_paths)}):")
        for mp in modified_paths:
            old_bytes = _fetch_blob_bytes(remote_blobs[mp], token=token, repo=rep)
            f_diff = _analyze_single_file_diff(mp, old_bytes, local_map[mp])
            report_lines.append(
                f"\n📄 Файл: {mp} (+{f_diff['added_lines']} / -{f_diff['removed_lines']} строк)"
            )

            if f_diff["added_funcs"]:
                report_lines.append(f"  🟢 Добавлены функции ({len(f_diff['added_funcs'])}):")
                for fn in f_diff["added_funcs"]:
                    report_lines.append(f"    + {fn}")

            if f_diff["removed_funcs"]:
                report_lines.append(f"  🔴 Удалены функции ({len(f_diff['removed_funcs'])}):")
                for fn in f_diff["removed_funcs"]:
                    report_lines.append(f"    - {fn}")

            if f_diff["modified_funcs"]:
                report_lines.append(f"  🟡 Изменены внутри функции ({len(f_diff['modified_funcs'])}):")
                for fn in f_diff["modified_funcs"]:
                    report_lines.append(f"    ~ {fn}")
                    sub_lines = f_diff["func_diffs"].get(fn) or []
                    for sl in sub_lines:
                        report_lines.append(f"        {sl}")

            if f_diff["raw_diff_preview"]:
                report_lines.append("  🔍 Построчный diff:")
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
    report_lines.append(f"💡 Готовое краткое описание для сборки: {short_commit}")
    report_lines.append("==========================================================")

    return {
        "has_changes": True,
        "short_commit_msg": short_commit,
        "report_text": "\n".join(report_lines),
        "modified_count": len(modified_paths),
        "added_count": len(added_files),
        "removed_count": len(removed_files),
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
    """Автоматически добавляет шаг штамповки versionCode = GITHUB_RUN_NUMBER и versionName = #GITHUB_RUN_NUMBER (...) в .github/workflows/build.yml."""
    yml_key = ".github/workflows/build.yml"
    if yml_key in file_map:
        try:
            yml_str = file_map[yml_key].decode("utf-8", errors="replace")
            if "Штамп номера сборки" not in yml_str and "gradle assembleDebug" in yml_str:
                target = "    - name: Сборка APK\n      run: gradle assembleDebug"
                stamp_lines = [
                    "    - name: Штамп номера сборки и версии в APK",
                    "      run: |",
                    "        SHORT_MSG=$(git log -1 --pretty=%s | tr -d '\"\\\\' | cut -c1-48)",
                    '        sed -i "s/versionCode .*/versionCode ${GITHUB_RUN_NUMBER}/" app/build.gradle',
                    '        sed -i "s/versionName \\".*\\"/versionName \\"#${GITHUB_RUN_NUMBER} (${SHORT_MSG})\\"/" app/build.gradle',
                    "",
                    "    - name: Сборка APK",
                    "      run: gradle assembleDebug",
                ]
                yml_str = yml_str.replace(target, "\n".join(stamp_lines))
                file_map[yml_key] = yml_str.encode("utf-8")
        except Exception:
            pass


def push_file_map_to_github(file_map: dict, commit_msg: str, token: str = "", repo: str = "") -> dict:
    if not file_map:
        raise ValueError(
            "Android скрыл файлы в этой папке (0 файлов). Нажми красную кнопку «🔓 Выдать Доступ ко всем файлам» вверху экрана и повтори!"
        )
    _ensure_build_yml_stamps_version(file_map)
    branch_name, head_sha = _resolve_target_branch(token=token, repo=repo)
    commit_info = gh_api(f"git/commits/{head_sha}", "GET", token=token, repo=repo)
    base_tree_sha = commit_info["tree"]["sha"]
    tree_info = gh_api(f"git/trees/{base_tree_sha}?recursive=1", "GET", token=token, repo=repo)
    remote_blobs = {
        item["path"]: item["sha"]
        for item in tree_info.get("tree", [])
        if item.get("type") == "blob"
    }

    tree_updates = []
    changed_count = 0

    for rel_path, content_bytes in file_map.items():
        local_sha = _git_blob_sha1(content_bytes)
        if remote_blobs.get(rel_path) != local_sha:
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
            changed_count += 1

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
    return {
        "commit_sha": new_commit_sha[:7],
        "commit_msg": commit_msg,
        "changed_files": changed_count,
        "total_files": len(file_map),
        "branch": branch_name,
    }


def _unpack_zip_bytes_to_folder(zip_bytes: bytes, target_folder: str) -> int:
    os.makedirs(target_folder, exist_ok=True)
    fmap = _collect_files_from_zip_bytes(zip_bytes)
    if not fmap:
        raise ValueError("В архиве не найдено файлов для распаковки.")
    written = 0
    for rel_p, content_b in fmap.items():
        if rel_p.startswith(".git/") or rel_p == ".git":
            continue
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
            if not os.path.isdir(req_dir):
                req_dir = DEFAULT_SAVE_DIR if os.path.isdir(DEFAULT_SAVE_DIR) else "/storage/emulated/0"
            req_dir = os.path.abspath(req_dir)
            save_config({"browse_dir": req_dir})

            dirs_out = []
            zips_out = []
            try:
                for entry in sorted(os.listdir(req_dir), key=lambda x: x.lower()):
                    if entry.startswith("."):
                        continue
                    fp = os.path.join(req_dir, entry)
                    if os.path.isdir(fp):
                        dirs_out.append(entry)
                    elif os.path.isfile(fp) and entry.lower().endswith(".zip"):
                        sz_mb = round(os.path.getsize(fp) / (1024.0 * 1024.0), 2)
                        zips_out.append({"name": entry, "path": fp, "mb": sz_mb})
            except Exception as e:
                return self._send_json({"status": "error", "error": str(e)})

            parent_dir = os.path.dirname(req_dir) if req_dir != "/" else "/"
            self._send_json({
                "status": "ok",
                "current_dir": req_dir,
                "parent_dir": parent_dir,
                "dirs": dirs_out,
                "zips": zips_out,
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
                raw_bytes = gh_download_bytes(f"actions/jobs/{job_id}/logs")
                raw_text = raw_bytes.decode("utf-8", errors="replace")
                step_log = extract_failed_step_log(raw_text, step_name, started_at, completed_at)
                self._send_json({
                    "status": "ok",
                    "step_name": step_name,
                    "error_log": step_log,
                })
            except Exception as e:
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
            if path == "/api/config":
                cfg = save_config(data)
                return self._send_json({"status": "ok", "config": cfg})

            if path == "/api/installed_info":
                if data.get("manual_installed_build") is not None:
                    save_config({"manual_installed_build": int(data["manual_installed_build"])})
                hints = data.get("bridge_hints") or {}
                inst = resolve_installed_apps_status(hints, _CACHED_RUNS_LIST)
                return self._send_json({"status": "ok", "installed": inst})

            if path == "/api/compare":
                cfg = load_config()
                base_sha = (data.get("base_sha") or "").strip()
                base_label = (data.get("base_label") or "").strip()
                target_type = (data.get("target_type") or "folder").strip()
                target_sha = (data.get("target_sha") or "").strip()
                target_label = (data.get("target_label") or "").strip()

                if not base_sha:
                    _, base_sha = _resolve_target_branch()

                if target_type == "github_run":
                    if not target_sha:
                        _, target_sha = _resolve_target_branch()
                    res = compare_two_github_commits(base_sha, target_sha, base_label=base_label, head_label=target_label)
                    return self._send_json({"status": "ok", **res})

                elif target_type == "zip":
                    zip_path = (data.get("zip_path") or "").strip()
                    if not zip_path or not os.path.isfile(zip_path):
                        return self._send_json({"status": "error", "error": f"Файл .zip не найден: {zip_path}"})
                    fmap = _collect_files_from_zip_bytes(open(zip_path, "rb").read())
                    res = compare_local_map_with_github(fmap, f"Архив {zip_path}", base_sha=base_sha, base_label=base_label)
                    return self._send_json({"status": "ok", **res})

                else:
                    folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                    if not os.path.isdir(folder_p):
                        return self._send_json({"status": "error", "error": f"Папка не найдена: {folder_p}"})
                    save_config({"source_dir": folder_p})
                    fmap = _collect_files_from_folder(folder_p)
                    res = compare_local_map_with_github(fmap, f"Рабочая папка {folder_p}", base_sha=base_sha, base_label=base_label)
                    return self._send_json({"status": "ok", **res})

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
                file_map = _collect_files_from_zip_bytes(open(zip_path, "rb").read())
                res = push_file_map_to_github(file_map, commit_msg)
                return self._send_json({"status": "ok", **res})

            if path == "/api/push_uploaded_zip":
                b64_str = data.get("zip_base64") or ""
                fname = data.get("filename") or "uploaded.zip"
                commit_msg = (data.get("message") or "").strip() or f"build(upload): push {fname} via JennyBuilder"
                if not b64_str:
                    return self._send_json({"status": "error", "error": "Пустой архив"})
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
                file_map = _collect_files_from_folder(folder_p)
                res = push_file_map_to_github(file_map, commit_msg)
                return self._send_json({"status": "ok", **res})

            if path == "/api/download_apk":
                cfg = load_config()
                run_id = data.get("run_id")
                run_number = data.get("run_number") or run_id
                version_tag = (data.get("version_tag") or "").strip()
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                os.makedirs(save_dir, exist_ok=True)
                try:
                    save_config({"save_dir": save_dir, "manual_installed_build": int(run_number)})
                except Exception:
                    save_config({"save_dir": save_dir})

                arts = gh_api(f"actions/runs/{run_id}/artifacts", "GET")
                art_list = arts.get("artifacts", [])
                if not art_list:
                    return self._send_json({"status": "error", "error": "Артефакт APK ещё не сформирован в этой сборке"})
                art_id = art_list[0]["id"]
                zip_bytes = gh_download_bytes(f"actions/artifacts/{art_id}/zip")

                repo_short = cfg["repo"].split("/")[-1]
                ver_suffix = f"_{version_tag}" if version_tag else ""
                out_apk_name = f"{repo_short}_#{run_number}{ver_suffix}.apk"
                out_apk_path = os.path.join(save_dir, out_apk_name)

                with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                    apk_members = [m for m in zf.namelist() if m.lower().endswith(".apk")]
                    if not apk_members:
                        return self._send_json({"status": "error", "error": "Внутри артефакта не найден .apk файл"})
                    with open(out_apk_path, "wb") as out_f:
                        out_f.write(zf.read(apk_members[0]))

                sz_mb = round(os.path.getsize(out_apk_path) / (1024.0 * 1024.0), 2)
                return self._send_json({
                    "status": "ok",
                    "apk_path": out_apk_path,
                    "size_mb": sz_mb,
                    "run_number": run_number,
                    "version_tag": version_tag,
                })

            if path == "/api/download_last_good_zip":
                cfg = load_config()
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                os.makedirs(save_dir, exist_ok=True)
                save_config({"save_dir": save_dir})

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

                raw_zip_bytes = gh_download_bytes(f"zipball/{sha}")

                repo_short = cfg["repo"].split("/")[-1]
                bnum = meta.get("build_number") or "latest"
                vtag = meta.get("version_tag")
                ver_part = f"_{vtag}" if vtag else ""
                out_zip_name = f"{repo_short}_#{bnum}{ver_part}_clean.zip"
                out_zip_path = os.path.join(save_dir, out_zip_name)

                with zipfile.ZipFile(io.BytesIO(raw_zip_bytes), "r") as zin, zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.infolist():
                        parts = item.filename.split("/", 1)
                        clean_rel = parts[1] if len(parts) > 1 else ""
                        if not clean_rel or clean_rel.endswith("/"):
                            continue
                        zout.writestr(clean_rel, zin.read(item.filename))

                sz_mb = round(os.path.getsize(out_zip_path) / (1024.0 * 1024.0), 2)
                return self._send_json({
                    "status": "ok",
                    "zip_path": out_zip_path,
                    "size_mb": sz_mb,
                    "build_number": bnum,
                    "version_tag": vtag,
                    "title": meta.get("title"),
                    "human_label": meta.get("human_label"),
                })

            if path == "/api/pull_to_folder":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                save_config({"source_dir": folder_p})

                runs_data = gh_api("actions/runs?conclusion=success&per_page=5", "GET")
                good_runs = [r for r in runs_data.get("workflow_runs", []) if r.get("conclusion") == "success"]
                if good_runs:
                    sha = good_runs[0]["head_sha"]
                    meta = _describe_run_or_commit(sha, good_runs[0])
                else:
                    _, sha = _resolve_target_branch()
                    meta = _describe_run_or_commit(sha, None)

                raw_zip_bytes = gh_download_bytes(f"zipball/{sha}")
                written = _unpack_zip_bytes_to_folder(raw_zip_bytes, folder_p)
                return self._send_json({
                    "status": "ok",
                    "folder_path": folder_p,
                    "written_files": written,
                    "build_number": meta.get("build_number"),
                    "version_tag": meta.get("version_tag"),
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
                written = _unpack_zip_bytes_to_folder(open(zip_path, "rb").read(), folder_p)
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
                fmap = _collect_files_from_folder(folder_p)
                if not fmap:
                    return self._send_json({
                        "status": "error",
                        "error": "Android скрыл файлы в этой папке (0 файлов). Нажми «🔓 Выдать Доступ ко всем файлам» вверху экрана и повтори!"
                    })
                with zipfile.ZipFile(out_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for rel_p, content_b in fmap.items():
                        zf.writestr(rel_p, content_b)
                sz_mb = round(os.path.getsize(out_zip_path) / (1024.0 * 1024.0), 2)
                return self._send_json({
                    "status": "ok",
                    "folder_path": folder_p,
                    "zip_path": out_zip_path,
                    "files_count": len(fmap),
                    "size_mb": sz_mb,
                })

            self._send_json({"status": "error", "error": "Unknown endpoint"}, 404)
        except Exception as e:
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
