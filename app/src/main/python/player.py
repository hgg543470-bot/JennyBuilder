# -*- coding: utf-8 -*-
import os
import re
import io
import json
import time
import shutil
import base64
import hashlib
import zipfile
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

    # Также проверяем .zip архивы в Download, если внутри них есть .git/config
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
                        if v or not cfg.get(k):
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


def gh_api(endpoint: str, method: str = "GET", payload: dict = None, token: str = "", repo: str = ""):
    cfg = load_config()
    tok = (token or cfg.get("token") or "").strip()
    rep = (repo or cfg.get("repo") or DEFAULT_REPO).strip()
    url = endpoint if endpoint.startswith("http") else f"https://api.github.com/repos/{rep}/{endpoint.lstrip('/')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "JennyBuilder-Android/1.2",
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
        "User-Agent": "JennyBuilder-Android/1.2",
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


def _git_blob_sha1(data: bytes) -> str:
    hdr = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(hdr + data).hexdigest()


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
            parts = rel.split("/")
            if any(p in EXCLUDE_DIRS for p in parts):
                continue
            fname = parts[-1]
            if fname in EXCLUDE_FILES or os.path.splitext(fname)[1].lower() in EXCLUDE_EXTS:
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


def push_file_map_to_github(file_map: dict, commit_msg: str, token: str = "", repo: str = "") -> dict:
    if not file_map:
        raise ValueError(
            "Android скрыл файлы в этой папке (0 файлов). Нажми красную кнопку «🔓 Выдать Доступ ко всем файлам» вверху экрана и повтори!"
        )
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
        "commit_sha": new_commit_sha[:8],
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
                runs_data = gh_api("actions/runs?per_page=8", "GET", token=cfg["token"], repo=cfg["repo"])
                runs_list = []
                for r in runs_data.get("workflow_runs", []):
                    runs_list.append({
                        "id": r["id"],
                        "run_number": r.get("run_number"),
                        "status": r.get("status"),
                        "conclusion": r.get("conclusion"),
                        "head_sha": (r.get("head_sha") or "")[:8],
                        "full_sha": r.get("head_sha") or "",
                        "title": r.get("display_title") or r.get("name") or "Build",
                        "created_at": (r.get("created_at") or "").replace("T", " ")[:19],
                    })

                target_run_id = qs.get("run_id", [""])[0]
                if not target_run_id and runs_list:
                    target_run_id = str(runs_list[0]["id"])

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
                        "steps": steps_out,
                        "failed_job_id": failed_job_id,
                        "failed_step_name": failed_step_info.get("name") if failed_step_info else "",
                        "failed_started_at": failed_step_info.get("started_at") if failed_step_info else "",
                        "failed_completed_at": failed_step_info.get("completed_at") if failed_step_info else "",
                    }

                self._send_json({
                    "status": "ok",
                    "repo": cfg["repo"],
                    "runs": runs_list,
                    "active": active_details,
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
                save_dir = (data.get("save_dir") or cfg.get("save_dir") or DEFAULT_SAVE_DIR).strip()
                os.makedirs(save_dir, exist_ok=True)
                save_config({"save_dir": save_dir})

                arts = gh_api(f"actions/runs/{run_id}/artifacts", "GET")
                art_list = arts.get("artifacts", [])
                if not art_list:
                    return self._send_json({"status": "error", "error": "Артефакт APK ещё не сформирован в этой сборке"})
                art_id = art_list[0]["id"]
                zip_bytes = gh_download_bytes(f"actions/artifacts/{art_id}/zip")

                repo_short = cfg["repo"].split("/")[-1]
                out_apk_name = f"{repo_short}_b{run_number}.apk"
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
                    bnum = good_run.get("run_number") or sha[:7]
                else:
                    br, sha = _resolve_target_branch()
                    bnum = br

                raw_zip_bytes = gh_download_bytes(f"zipball/{sha}")

                repo_short = cfg["repo"].split("/")[-1]
                out_zip_name = f"{repo_short}_clean_b{bnum}_{sha[:7]}.zip"
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
                    "sha": sha[:7],
                })

            if path == "/api/pull_to_folder":
                cfg = load_config()
                folder_p = (data.get("folder_path") or cfg.get("source_dir") or DEFAULT_PROJECT_DIR).strip()
                mode = (data.get("mode") or "latest").strip()
                save_config({"source_dir": folder_p})

                if mode == "last_good":
                    runs_data = gh_api("actions/runs?conclusion=success&per_page=5", "GET")
                    good_runs = [r for r in runs_data.get("workflow_runs", []) if r.get("conclusion") == "success"]
                    if good_runs:
                        sha = good_runs[0]["head_sha"]
                    else:
                        _, sha = _resolve_target_branch()
                else:
                    _, sha = _resolve_target_branch()

                raw_zip_bytes = gh_download_bytes(f"zipball/{sha}")
                written = _unpack_zip_bytes_to_folder(raw_zip_bytes, folder_p)
                return self._send_json({
                    "status": "ok",
                    "folder_path": folder_p,
                    "written_files": written,
                    "sha": sha[:7],
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
                    "zip_path": out_zip_path,
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
