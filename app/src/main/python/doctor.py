# -*- coding: utf-8 -*-
import os, sys, re, ast, json, time, shutil, subprocess, datetime

BACKUP_ROOT = "/storage/emulated/0/JENNY/DoctorBackups"

TRASH_FILE_PATTERNS = [
    r"^FULL_PROJECT_SRC\.txt$",
    r".*_for_AI\.txt$",
    r"^diff_.*\.txt$",
    r"^1\.py$",
    r"^patch_v\d+.*\.py$",
    r"^run_diagnostic.*\.py$",
    r"^run_safe_clean\.py$",
    r"^clean_js_only\.py$",
    r"^build_vcore_termux\.sh$",
    r"^omni_.*\.log$",
    r"^shizuku_window_dump\.txt$",
    r".*\.bak$",
    r".*\.orig$",
    r".*\.tmp_check$",
]

TRASH_DIR_NAMES = {"native_src_backup", "_cleanup_backup_327", "_cleanup_backup", ".Temp"}

def _is_trash_rel_path(rel_path: str) -> str:
    norm = rel_path.replace("\\", "/").lstrip("/")
    parts = norm.split("/")
    for p in parts[:-1]:
        if p in TRASH_DIR_NAMES or p.startswith("_cleanup_backup"):
            return f"Папка старого бэкапа ({p})"
    fname = parts[-1]
    if fname.endswith(".log"):
        return "Забытый лог-файл (.log)"
    for pat in TRASH_FILE_PATTERNS:
        if re.match(pat, fname, re.IGNORECASE):
            if len(parts) == 1 or fname.endswith((".log", ".bak", ".orig", ".tmp_check", "_for_AI.txt")):
                return "Временный дамп / отработавший патчер"
    return ""

_JS_REGEX_KEYWORDS = {
    "return", "typeof", "instanceof", "in", "of", "void",
    "delete", "throw", "new", "case", "yield", "await", "else", "do"
}
_JS_REGEX_PRE_CHARS = set("(,=:[!&|?{};+-*%~^<>")

def scan_js_with_stack(text: str) -> dict:
    n = len(text)
    i = 0
    line = 1
    col = 1
    mode_stack = []
    mode = "CODE"
    str_quote = ""
    str_start_line = 1
    regex_in_class = False
    brace_depth = 0
    paren_depth = 0
    bracket_depth = 0
    open_brackets_stack = []
    prev_non_ws_char = ";"
    prev_word = ""
    top_level_brace_closes = {}
    brace_open_pos_stack = []

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if mode == "LINE_COMMENT":
            if ch == "\n":
                mode = "CODE"
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if mode == "BLOCK_COMMENT":
            if ch == "*" and nxt == "/":
                mode = "CODE"
                i += 2
                col += 2
                continue
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if mode == "STRING":
            if ch == "\\":
                if nxt == "\n":
                    line += 1
                    col = 1
                else:
                    col += 2
                i += 2
                continue
            if ch == str_quote:
                mode = "CODE"
                prev_non_ws_char = str_quote
                prev_word = ""
                i += 1
                col += 1
                continue
            if ch == "\n":
                return {"ok": False, "error": f"Незакрытая кавычка {str_quote} на строке {str_start_line}", "line": str_start_line, "top_closes": {}}
            i += 1
            col += 1
            continue

        if mode == "TEMPLATE":
            if ch == "\\":
                if nxt == "\n":
                    line += 1
                    col = 1
                else:
                    col += 2
                i += 2
                continue
            if ch == "$" and nxt == "{":
                mode_stack.append(("EXPR", brace_depth))
                mode = "CODE"
                prev_non_ws_char = "{"
                prev_word = ""
                i += 2
                col += 2
                continue
            if ch == "`":
                if mode_stack and mode_stack[-1][0] == "TEMPLATE":
                    mode_stack.pop()
                mode = "CODE"
                prev_non_ws_char = "`"
                prev_word = ""
                i += 1
                col += 1
                continue
            if ch == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1
            continue

        if mode == "REGEX":
            if ch == "\\":
                i += 2
                col += 2
                continue
            if ch == "[":
                regex_in_class = True
            elif ch == "]":
                regex_in_class = False
            elif ch == "/" and not regex_in_class:
                mode = "CODE"
                prev_non_ws_char = "/"
                prev_word = ""
            i += 1
            col += 1
            continue

        if ch == "\n":
            line += 1
            col = 1
            i += 1
            continue
        if ch.isspace():
            col += 1
            i += 1
            continue
        if ch == "/" and nxt == "/":
            mode = "LINE_COMMENT"
            i += 2
            col += 2
            continue
        if ch == "/" and nxt == "*":
            mode = "BLOCK_COMMENT"
            i += 2
            col += 2
            continue
        if ch in ("'", '"'):
            mode = "STRING"
            str_quote = ch
            str_start_line = line
            i += 1
            col += 1
            continue
        if ch == "`":
            mode_stack.append(("TEMPLATE", line))
            mode = "TEMPLATE"
            i += 1
            col += 1
            continue
        if ch == "/":
            can_be_regex = (prev_non_ws_char in _JS_REGEX_PRE_CHARS) or (prev_word in _JS_REGEX_KEYWORDS)
            if can_be_regex:
                j = i + 1
                in_cls = False
                valid_rx = False
                while j < n and text[j] != "\n":
                    c2 = text[j]
                    if c2 == "\\":
                        j += 2
                        continue
                    if c2 == "[":
                        in_cls = True
                    elif c2 == "]":
                        in_cls = False
                    elif c2 == "/" and not in_cls:
                        valid_rx = True
                        break
                    j += 1
                if valid_rx:
                    mode = "REGEX"
                    regex_in_class = False
                    i += 1
                    col += 1
                    continue
            prev_non_ws_char = "/"
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch == "{":
            brace_open_pos_stack.append((i, len(mode_stack)))
            brace_depth += 1
            open_brackets_stack.append(("{", line, col))
            prev_non_ws_char = "{"
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch == "}":
            if mode_stack and mode_stack[-1][0] == "EXPR" and brace_depth == mode_stack[-1][1]:
                mode_stack.pop()
                mode = "TEMPLATE"
                i += 1
                col += 1
                continue
            if brace_depth <= 0 or not open_brackets_stack or open_brackets_stack[-1][0] != "{":
                return {"ok": False, "error": f"Лишняя закрывающая скобка '}}' на строке {line}:{col}", "line": line, "top_closes": {}}
            brace_depth -= 1
            open_brackets_stack.pop()
            open_pos, stack_len_at_open = brace_open_pos_stack.pop()
            if brace_depth == 0 and stack_len_at_open == 0 and paren_depth == 0:
                top_level_brace_closes[open_pos] = i
            prev_non_ws_char = "}"
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch == "(":
            paren_depth += 1
            open_brackets_stack.append(("(", line, col))
            prev_non_ws_char = "("
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch == ")":
            if paren_depth <= 0 or not open_brackets_stack or open_brackets_stack[-1][0] != "(":
                return {"ok": False, "error": f"Непарная скобка ')' на строке {line}:{col}", "line": line, "top_closes": {}}
            paren_depth -= 1
            open_brackets_stack.pop()
            prev_non_ws_char = ")"
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch == "[":
            bracket_depth += 1
            open_brackets_stack.append(("[", line, col))
            prev_non_ws_char = "["
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch == "]":
            if bracket_depth <= 0 or not open_brackets_stack or open_brackets_stack[-1][0] != "[":
                return {"ok": False, "error": f"Непарная скобка ']' на строке {line}:{col}", "line": line, "top_closes": {}}
            bracket_depth -= 1
            open_brackets_stack.pop()
            prev_non_ws_char = "]"
            prev_word = ""
            i += 1
            col += 1
            continue

        if ch.isalpha() or ch in ("_", "$"):
            start_w = i
            while i < n and (text[i].isalnum() or text[i] in ("_", "$")):
                i += 1
                col += 1
            prev_word = text[start_w:i]
            prev_non_ws_char = prev_word[-1]
            continue

        prev_non_ws_char = ch
        prev_word = ""
        i += 1
        col += 1

    if mode == "TEMPLATE":
        t_line = mode_stack[-1][1] if mode_stack else line
        return {"ok": False, "error": f"Незакрытая шаблонная строка (`) со строки {t_line}", "line": t_line, "top_closes": {}}
    if mode == "STRING":
        return {"ok": False, "error": f"Незакрытая строка ({str_quote}) на строке {str_start_line}", "line": str_start_line, "top_closes": {}}
    if open_brackets_stack:
        b_ch, b_ln, b_col = open_brackets_stack[-1]
        return {"ok": False, "error": f"Незакрытая скобка '{b_ch}' со строки {b_ln}:{b_col}", "line": b_ln, "top_closes": {}}

    return {"ok": True, "error": "", "line": 0, "top_closes": top_level_brace_closes}

def verify_js_syntax(js_text: str, filename_hint: str = "check.js") -> tuple:
    res = scan_js_with_stack(js_text)
    if not res["ok"]:
        return False, res["error"]
    node_bin = shutil.which("node")
    if node_bin:
        tmp_p = os.path.join(os.path.expanduser("~"), f"_jb_syntax_{os.getpid()}.js")
        try:
            with open(tmp_p, "w", encoding="utf-8") as f:
                f.write(js_text)
            proc = subprocess.run([node_bin, "--check", tmp_p], capture_output=True, text=True, timeout=10)
            if proc.returncode != 0:
                err_txt = (proc.stderr or proc.stdout or "").strip()
                if "CANNOT LINK EXECUTABLE" not in err_txt:
                    return False, f"Node V8: {err_txt[:220]}"
        except Exception:
            pass
        finally:
            try:
                if os.path.exists(tmp_p):
                    os.remove(tmp_p)
            except Exception:
                pass
    return True, "OK"

_JS_TOP_FUNC_RE = re.compile(
    r"^[ \t]*(?:window\.([a-zA-Z0-9_]+)\s*=\s*(?:async\s+)?function\s*[a-zA-Z0-9_]*\s*\([^)]*\)\s*\{|((?:async\s+)?function)\s+([a-zA-Z0-9_]+)\s*\([^)]*\)\s*\{)",
    re.MULTILINE,
)

def find_js_overridden_functions(js_text: str) -> dict:
    scan = scan_js_with_stack(js_text)
    if not scan["ok"]:
        return {"ok": False, "error": scan["error"], "safe_cuts": [], "skipped": []}
    top_closes = scan["top_closes"]
    defs_by_name = {}

    for m in _JS_TOP_FUNC_RE.finditer(js_text):
        brace_open = m.end() - 1
        if brace_open not in top_closes:
            continue
        brace_close = top_closes[brace_open]
        fn_name = m.group(1) or m.group(3)
        end_pos = brace_close + 1
        while end_pos < len(js_text) and js_text[end_pos] in (" ", "\t"):
            end_pos += 1
        if end_pos < len(js_text) and js_text[end_pos] == ";":
            end_pos += 1
        start_line = js_text.count("\n", 0, m.start()) + 1
        end_line = js_text.count("\n", 0, end_pos) + 1
        defs_by_name.setdefault(fn_name, []).append({
            "name": fn_name,
            "start": m.start(),
            "brace_open": brace_open,
            "end": end_pos,
            "start_line": start_line,
            "end_line": end_line,
            "bytes": end_pos - m.start(),
        })

    safe_cuts = []
    skipped = []

    for fn_name, items in defs_by_name.items():
        if len(items) < 2:
            continue
        alias_pat = re.compile(rf"(?:=|:)\s*(?:window\.)?{re.escape(fn_name)}\b(?!\s*\()")
        if alias_pat.search(js_text):
            skipped.append({"name": fn_name, "count": len(items), "reason": "Обернута в переходник (wrapper)"})
            continue

        last_item = items[-1]
        dead_spans = [(it["start"], it["end"]) for it in items[:-1]]

        def _is_inside_dead(pos):
            for s, e in dead_spans:
                if s <= pos < e:
                    return True
            return False

        call_pat = re.compile(rf"\b{re.escape(fn_name)}\s*\(")
        called_at_init = False
        for cm in call_pat.finditer(js_text, 0, last_item["start"]):
            cpos = cm.start()
            if _is_inside_dead(cpos):
                continue
            inside_normal_top_func = False
            for o_open, o_close in top_closes.items():
                if o_open < cpos < o_close:
                    hdr = js_text[max(0, o_open - 120):o_open]
                    if not re.search(r"\(\s*(?:async\s+)?function|\(\s*\([^)]*\)\s*=>", hdr):
                        inside_normal_top_func = True
                    break
            if not inside_normal_top_func:
                called_at_init = True
                break

        if called_at_init:
            skipped.append({"name": fn_name, "count": len(items), "reason": "Вызывается при старте до финального патча"})
            continue

        for idx, it in enumerate(items[:-1]):
            if it["bytes"] < 80:
                continue
            safe_cuts.append({
                "name": fn_name,
                "start": it["start"],
                "end": it["end"],
                "start_line": it["start_line"],
                "end_line": it["end_line"],
                "bytes": it["bytes"],
                "keep_stub": (idx == 0),
                "kept_line": last_item["start_line"],
            })

    safe_cuts.sort(key=lambda x: x["start"])
    filtered = []
    last_e = -1
    for c in safe_cuts:
        if c["start"] >= last_e:
            filtered.append(c)
            last_e = c["end"]
    return {"ok": True, "error": "", "safe_cuts": filtered, "skipped": skipped}

def find_py_overridden_functions(py_text: str) -> dict:
    try:
        tree = ast.parse(py_text)
    except SyntaxError as se:
        return {"ok": False, "error": f"Синтаксическая ошибка Python на строке {se.lineno}: {se.msg}", "line": se.lineno or 0, "safe_cuts": []}
    lines = py_text.splitlines(keepends=True)
    top_funcs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            top_funcs.setdefault(node.name, []).append(node)
    safe_cuts = []
    for name, nodes in top_funcs.items():
        if name.endswith("_unused"):
            n = nodes[0]
            start_ln = (n.decorator_list[0].lineno if n.decorator_list else n.lineno)
            end_ln = n.end_lineno
            chunk = "".join(lines[start_ln - 1:end_ln])
            safe_cuts.append({"name": name, "start_line": start_ln, "end_line": end_ln, "bytes": len(chunk.encode("utf-8")), "reason": "Неиспользуемая функция (*_unused)"})
        elif len(nodes) > 1:
            last_n = nodes[-1]
            for idx in range(len(nodes) - 1):
                n_old = nodes[idx]
                n_next = nodes[idx + 1]
                start_ln = (n_old.decorator_list[0].lineno if n_old.decorator_list else n_old.lineno)
                end_ln = n_old.end_lineno
                between = "".join(lines[end_ln : n_next.lineno - 1])
                if not re.search(rf"\b{re.escape(name)}\b", between):
                    chunk = "".join(lines[start_ln - 1:end_ln])
                    safe_cuts.append({"name": name, "start_line": start_ln, "end_line": end_ln, "bytes": len(chunk.encode("utf-8")), "reason": f"Переопределена ниже на строке {last_n.lineno}"})
    return {"ok": True, "error": "", "line": 0, "safe_cuts": safe_cuts}

def scan_project_health(folder_path: str) -> dict:
    if not folder_path or not os.path.isdir(folder_path):
        raise ValueError(f"Папка проекта не найдена: {folder_path}")

    syntax_errors = []
    syntax_checked = {"py": 0, "js": 0, "json": 0, "html": 0}
    trash_files = []
    so_files = []
    js_overrides = {}
    py_overrides = {}
    py_duplicate_routes = []
    frontend_api_calls = {}
    backend_api_routes = {}
    timer_stats = []
    code_text_blobs = []

    for root, dirs, files in os.walk(folder_path):
        for d in list(dirs):
            rel_d = os.path.relpath(os.path.join(root, d), folder_path).replace("\\", "/")
            if d in {".git", "__pycache__", ".gradle", "build", ".idea"}:
                dirs.remove(d)
                continue
            if d in TRASH_DIR_NAMES or d.startswith("_cleanup_backup"):
                dir_full = os.path.join(root, d)
                sz = 0
                cnt = 0
                for r2, _, f2s in os.walk(dir_full):
                    for f2 in f2s:
                        cnt += 1
                        try:
                            sz += os.path.getsize(os.path.join(r2, f2))
                        except Exception:
                            pass
                trash_files.append({"rel_path": rel_d, "full_path": dir_full, "is_dir": True, "bytes": sz, "reason": f"Папка бэкапа/дублей ({cnt} файлов)"})
                dirs.remove(d)

        for fname in files:
            full_p = os.path.join(root, fname)
            rel_p = os.path.relpath(full_p, folder_path).replace("\\", "/")
            ext = os.path.splitext(fname)[1].lower()
            try:
                sz = os.path.getsize(full_p)
            except Exception:
                sz = 0

            trash_reason = _is_trash_rel_path(rel_p)
            if trash_reason:
                trash_files.append({"rel_path": rel_p, "full_path": full_p, "is_dir": False, "bytes": sz, "reason": trash_reason})
                continue

            if ext == ".so":
                so_files.append({"rel_path": rel_p, "full_path": full_p, "name": fname, "bytes": sz})
                continue

            if ext in (".py", ".js", ".html", ".java", ".go", ".c", ".cpp", ".h", ".yml", ".yaml", ".gradle", ".json"):
                try:
                    raw_text = open(full_p, "r", encoding="utf-8", errors="replace").read()
                except Exception:
                    continue
                code_text_blobs.append((rel_p, raw_text))

                if ext == ".py":
                    syntax_checked["py"] += 1
                    py_res = find_py_overridden_functions(raw_text)
                    if not py_res["ok"]:
                        syntax_errors.append({"file": rel_p, "line": py_res["line"], "error": py_res["error"]})
                    elif py_res["safe_cuts"]:
                        py_overrides[rel_p] = py_res["safe_cuts"]
                    for m_rt in re.finditer(r"""(?:path\s*==\s*|@route\(\s*)['"](/api/[a-zA-Z0-9_/\-]+)['"]""", raw_text):
                        rt = m_rt.group(1)
                        ln = raw_text.count("\n", 0, m_rt.start()) + 1
                        backend_api_routes.setdefault(rt, []).append(f"{rel_p}:{ln}")
                    for m_in in re.finditer(r"""path\s+in\s*\(([^)]+)\)""", raw_text):
                        ln = raw_text.count("\n", 0, m_in.start()) + 1
                        for m_s in re.finditer(r"""['"](/api/[a-zA-Z0-9_/\-]+)['"]""", m_in.group(1)):
                            rt = m_s.group(1)
                            backend_api_routes.setdefault(rt, []).append(f"{rel_p}:{ln}")

                elif ext == ".js":
                    syntax_checked["js"] += 1
                    ok_js, err_js = verify_js_syntax(raw_text, rel_p)
                    if not ok_js:
                        syntax_errors.append({"file": rel_p, "line": 0, "error": err_js})
                    else:
                        js_res = find_js_overridden_functions(raw_text)
                        if js_res["safe_cuts"] or js_res["skipped"]:
                            js_overrides[rel_p] = js_res
                    for m_api in re.finditer(r"""fetch\(\s*['"`](/api/[a-zA-Z0-9_/\-]+)""", raw_text):
                        rt = m_api.group(1)
                        ln = raw_text.count("\n", 0, m_api.start()) + 1
                        frontend_api_calls.setdefault(rt, []).append(f"{rel_p}:{ln}")
                    si_cnt = len(re.findall(r"\bsetInterval\s*\(", raw_text))
                    ci_cnt = len(re.findall(r"\bclearInterval\s*\(", raw_text))
                    if si_cnt > 0:
                        timer_stats.append({"file": rel_p, "setInterval": si_cnt, "clearInterval": ci_cnt})

                elif ext == ".json":
                    syntax_checked["json"] += 1
                    try:
                        json.loads(raw_text)
                    except Exception as je:
                        syntax_errors.append({"file": rel_p, "line": 0, "error": f"Ошибка JSON: {je}"})

                elif ext == ".html":
                    syntax_checked["html"] += 1
                    for m_scr in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", raw_text, re.DOTALL | re.IGNORECASE):
                        scr_body = m_scr.group(1)
                        scr_line = raw_text.count("\n", 0, m_scr.start()) + 1
                        ok_s, err_s = verify_js_syntax(scr_body, f"{rel_p}:script@{scr_line}")
                        if not ok_s:
                            syntax_errors.append({"file": f"{rel_p} (<script> со строки {scr_line})", "line": scr_line, "error": err_s})
                    for m_api in re.finditer(r"""fetch\(\s*['"`](/api/[a-zA-Z0-9_/\-]+)""", raw_text):
                        rt = m_api.group(1)
                        ln = raw_text.count("\n", 0, m_api.start()) + 1
                        frontend_api_calls.setdefault(rt, []).append(f"{rel_p}:{ln}")

    all_code_concat = "\n".join(txt for _, txt in code_text_blobs)
    so_unused = []
    so_active = []
    for s in so_files:
        fname = s["name"]
        short_stem = re.sub(r"^lib|\.so$", "", fname)
        is_py_dir_dup = s["rel_path"].startswith("app/src/main/python/") and any(x["name"] == fname and "jniLibs" in x["rel_path"] for x in so_files)
        if is_py_dir_dup:
            s["reason"] = "Дубликат .so в папке python/ (основной уже лежит в jniLibs)"
            so_unused.append(s)
        elif fname in all_code_concat or f'"{short_stem}"' in all_code_concat or f"'{short_stem}'" in all_code_concat:
            so_active.append(s)
        else:
            s["reason"] = "Не упоминается ни в одном исходнике (.py / .java / build.yml)"
            so_unused.append(s)

    missing_backend_routes = []
    for rt, locs in sorted(frontend_api_calls.items()):
        if rt not in backend_api_routes:
            if not any(rt.startswith(br) or br.startswith(rt) for br in backend_api_routes):
                missing_backend_routes.append({"route": rt, "called_at": locs})

    for rt, locs in sorted(backend_api_routes.items()):
        if len(locs) > 1:
            py_duplicate_routes.append({"route": rt, "locations": locs})

    trash_bytes = sum(t["bytes"] for t in trash_files)
    so_unused_bytes = sum(s["bytes"] for s in so_unused)
    overridden_bytes = (
        sum(sum(c["bytes"] for c in cuts) for cuts in py_overrides.values())
        + sum(sum(c["bytes"] for c in info["safe_cuts"]) for info in js_overrides.values())
    )
    overridden_count = (
        sum(len(cuts) for cuts in py_overrides.values())
        + sum(len(info["safe_cuts"]) for info in js_overrides.values())
    )

    rep = [
        "==========================================================",
        f"🩺 ДИАГНОСТИЧЕСКИЙ РЕНТГЕН ПРОЕКТА: {os.path.basename(folder_path.rstrip('/'))}",
        "==========================================================",
        f"📍 Папка: {folder_path}",
        f"🧪 Проверено синтаксисом: Python ({syntax_checked['py']}), JS ({syntax_checked['js']}), HTML ({syntax_checked['html']}), JSON ({syntax_checked['json']})",
    ]

    if syntax_errors:
        rep.append(f"\n🔴 [1] НАЙДЕНЫ СИНТАКСИЧЕСКИЕ ОШИБКИ / КЗ ({len(syntax_errors)}):")
        for se in syntax_errors:
            rep.append(f"  ❌ {se['file']}: {se['error']}")
    else:
        rep.append("\n🟢 [1] СИНТАКСИС И БАЛАНС СКОБОК: ИДЕАЛЬНО (0 ошибок в .py / .js / .html / .json)")

    if trash_files:
        rep.append(f"\n🗑️ [2] ФАЙЛОВЫЙ МУСОР И ЗАБЫТЫЕ ДАМПЫ ({len(trash_files)} шт. — {trash_bytes / 1024.0:.1f} КБ):")
        for t in trash_files:
            rep.append(f"  • {t['rel_path']} ({t['bytes'] / 1024.0:.1f} КБ) — {t['reason']}")
    else:
        rep.append("\n🟢 [2] ФАЙЛОВЫЙ МУСОР: Чисто (забытых дампов и логов нет)")

    if so_unused:
        rep.append(f"\n🧬 [3] ЛИШНИЕ / ДУБЛИРУЮЩИЕСЯ .SO БИБЛИОТЕКИ ({len(so_unused)} шт. — {so_unused_bytes / (1024.0*1024.0):.2f} МБ):")
        for s in so_unused:
            rep.append(f"  • {s['rel_path']} ({s['bytes'] / (1024.0*1024.0):.2f} МБ) — {s['reason']}")
    else:
        rep.append(f"\n🟢 [3] НАТИВНЫЕ БИБЛИОТЕКИ (.SO): Все {len(so_active)} библиотек активны, дублей нет")

    if overridden_count > 0:
        rep.append(f"\n✂️ [4] ПЕРЕОПРЕДЕЛЁННЫЕ ФУНКЦИИ / МЁРТВЫЙ КОД ({overridden_count} шт. — {overridden_bytes / 1024.0:.1f} КБ):")
        for f_py, cuts in py_overrides.items():
            rep.append(f"  🐍 {f_py}: можно безопасно вырезать {len(cuts)} шт. ({sum(c['bytes'] for c in cuts)/1024.0:.1f} КБ)")
            for c in cuts:
                rep.append(f"      - {c['name']} (строки {c['start_line']}..{c['end_line']}) — {c['reason']}")
        for f_js, info in js_overrides.items():
            cuts = info["safe_cuts"]
            skips = info["skipped"]
            if cuts:
                rep.append(f"  🟨 {f_js}: безопасно к вырезанию {len(cuts)} шт. ({sum(c['bytes'] for c in cuts)/1024.0:.1f} КБ)")
                for c in cuts[:25]:
                    rep.append(f"      - {c['name']} (строки {c['start_line']}..{c['end_line']} ➔ актуальна на строке {c['kept_line']})")
                if len(cuts) > 25:
                    rep.append(f"      ... и ещё {len(cuts) - 25} функций")
            if skips:
                rep.append(f"  🛡️ {f_js}: защищено от вырезания (используются в цепочке): {len(skips)} шт.")
                for sk in skips[:10]:
                    rep.append(f"      ⚡ {sk['name']} ({sk['count']}x) — {sk['reason']}")
    else:
        rep.append("\n🟢 [4] ПЕРЕОПРЕДЕЛЁННЫЕ ФУНКЦИИ: Мёртвых дублей функций не найдено")

    rep.append("\n🔌 [5] ПРОЗВОНКА ЦЕПИ ФРОНТЕНД ↔ БЭКЕНД (/api/...):")
    if missing_backend_routes:
        rep.append(f"  🔴 Вызовы из JS на несуществующие роуты Python ({len(missing_backend_routes)}):")
        for mr in missing_backend_routes:
            rep.append(f"    ❌ {mr['route']} (вызывается в {', '.join(mr['called_at'][:3])})")
    else:
        rep.append(f"  🟢 Все вызовы fetch('/api/...') из интерфейса ({len(frontend_api_calls)} маршрутов) подключены к серверу")

    if py_duplicate_routes:
        rep.append(f"  🟡 Дублирующиеся обработчики маршрутов в Python ({len(py_duplicate_routes)}):")
        for dr in py_duplicate_routes:
            rep.append(f"    ⚠️ {dr['route']} объявлен несколько раз: {', '.join(dr['locations'])}")

    if timer_stats:
        rep.append("\n⏱️ [6] ФОНОВЫЕ ТАЙМЕРЫ (setInterval / clearInterval):")
        for ts in timer_stats:
            rep.append(f"  • {ts['file']}: запусков setInterval = {ts['setInterval']}, остановок clearInterval = {ts['clearInterval']}")

    total_reclaim_kb = (trash_bytes + so_unused_bytes + overridden_bytes) / 1024.0
    rep.append("\n==========================================================")
    rep.append(f"💡 ИТОГО МОЖНО БЕЗОПАСНО ОЧИСТИТЬ: {total_reclaim_kb:.1f} КБ ({total_reclaim_kb/1024.0:.2f} МБ)")
    rep.append("==========================================================")

    return {
        "status": "ok",
        "syntax_ok": len(syntax_errors) == 0,
        "syntax_errors": syntax_errors,
        "trash_count": len(trash_files),
        "trash_kb": round(trash_bytes / 1024.0, 1),
        "so_unused_count": len(so_unused),
        "so_unused_mb": round(so_unused_bytes / (1024.0 * 1024.0), 2),
        "overridden_count": overridden_count,
        "overridden_kb": round(overridden_bytes / 1024.0, 1),
        "missing_routes_count": len(missing_backend_routes),
        "duplicate_routes_count": len(py_duplicate_routes),
        "report_text": "\n".join(rep),
        "_internal": {
            "trash_files": trash_files,
            "so_unused": so_unused,
            "py_overrides": py_overrides,
            "js_overrides": js_overrides,
        },
    }

def clean_project_safe(folder_path: str, clean_trash: bool = True, clean_overrides: bool = True, clean_unused_so: bool = False) -> dict:
    scan = scan_project_health(folder_path)
    internal = scan["_internal"]
    proj_name = os.path.basename(folder_path.rstrip("/")) or "Project"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, f"{proj_name}_{ts}")
    os.makedirs(backup_dir, exist_ok=True)
    manifest = {"project": proj_name, "folder_path": folder_path, "created_at": ts, "restored_items": []}
    actions_log = []
    freed_bytes = 0

    def _backup_item(rel_p: str, full_p: str):
        b_dest = os.path.join(backup_dir, "files", rel_p)
        os.makedirs(os.path.dirname(b_dest), exist_ok=True)
        if os.path.isdir(full_p):
            if os.path.exists(b_dest):
                shutil.rmtree(b_dest)
            shutil.copytree(full_p, b_dest)
            manifest["restored_items"].append({"rel_path": rel_p, "type": "dir"})
        elif os.path.isfile(full_p):
            shutil.copy2(full_p, b_dest)
            manifest["restored_items"].append({"rel_path": rel_p, "type": "file"})

    if clean_trash:
        for t in internal["trash_files"]:
            rel_p, full_p = t["rel_path"], t["full_path"]
            if os.path.exists(full_p):
                _backup_item(rel_p, full_p)
                if os.path.isdir(full_p):
                    shutil.rmtree(full_p)
                else:
                    os.remove(full_p)
                freed_bytes += t["bytes"]
                actions_log.append(f"🗑️ Удалён мусор в бэкап: {rel_p} ({t['bytes']/1024.0:.1f} КБ)")

    if clean_unused_so:
        for s in internal["so_unused"]:
            rel_p, full_p = s["rel_path"], s["full_path"]
            if os.path.isfile(full_p):
                _backup_item(rel_p, full_p)
                os.remove(full_p)
                freed_bytes += s["bytes"]
                actions_log.append(f"🧬 Убран дубль .so в бэкап: {rel_p} ({s['bytes']/(1024.0*1024.0):.2f} МБ)")

    if clean_overrides:
        for rel_p, cuts in internal["py_overrides"].items():
            full_p = os.path.join(folder_path, rel_p)
            if not os.path.isfile(full_p) or not cuts:
                continue
            orig_text = open(full_p, "r", encoding="utf-8").read()
            lines = orig_text.splitlines(keepends=True)
            for c in sorted(cuts, key=lambda x: x["start_line"], reverse=True):
                lines[c["start_line"] - 1:c["end_line"]] = [f"# [DOCTOR CLEANED OVERRIDE: {c['name']}]\n"]
            new_text = "".join(lines)
            try:
                ast.parse(new_text)
                _backup_item(rel_p, full_p)
                with open(full_p, "w", encoding="utf-8") as f:
                    f.write(new_text)
                diff_b = len(orig_text.encode("utf-8")) - len(new_text.encode("utf-8"))
                freed_bytes += max(0, diff_b)
                actions_log.append(f"🐍 [{rel_p}] Вырезано {len(cuts)} мёртвых функций ({diff_b/1024.0:.1f} КБ) — AST OK")
            except Exception as e:
                actions_log.append(f"⚠️ [{rel_p}] Пропущен (защита AST): {e}")

        for rel_p, info in internal["js_overrides"].items():
            cuts = info["safe_cuts"]
            full_p = os.path.join(folder_path, rel_p)
            if not os.path.isfile(full_p) or not cuts:
                continue
            orig_text = open(full_p, "r", encoding="utf-8").read()
            new_text = orig_text
            for c in reversed(cuts):
                stub = f"var {c['name']} = window.{c['name']} = window.{c['name']} || function(){{}};" if c.get("keep_stub") else f"/* [CLEANED: {c['name']}] */"
                new_text = new_text[:c["start"]] + stub + new_text[c["end"]:]
            ok_js, err_js = verify_js_syntax(new_text, rel_p)
            if ok_js:
                _backup_item(rel_p, full_p)
                with open(full_p, "w", encoding="utf-8") as f:
                    f.write(new_text)
                diff_b = len(orig_text.encode("utf-8")) - len(new_text.encode("utf-8"))
                freed_bytes += max(0, diff_b)
                actions_log.append(f"🟨 [{rel_p}] Вырезано {len(cuts)} перекрытых функций ({diff_b/1024.0:.1f} КБ) — Синтаксис V8 OK")
            else:
                actions_log.append(f"🛑 [{rel_p}] Отменено предохранителем V8: {err_js}")

    with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    if not manifest["restored_items"]:
        shutil.rmtree(backup_dir, ignore_errors=True)
        backup_dir = ""

    return {
        "status": "ok",
        "freed_kb": round(freed_bytes / 1024.0, 1),
        "freed_mb": round(freed_bytes / (1024.0 * 1024.0), 2),
        "backup_dir": backup_dir,
        "actions": actions_log,
        "summary": "\n".join(actions_log) if actions_log else "Нечего удалять — проект уже чист!",
    }

def restore_latest_backup(folder_path: str) -> dict:
    if not os.path.isdir(BACKUP_ROOT):
        raise ValueError("Папка бэкапов пуста.")
    proj_name = os.path.basename(folder_path.rstrip("/")) or "Project"
    candidates = [os.path.join(BACKUP_ROOT, e) for e in os.listdir(BACKUP_ROOT) if e.startswith(proj_name + "_") and os.path.isfile(os.path.join(BACKUP_ROOT, e, "manifest.json"))]
    if not candidates:
        raise ValueError(f"Бэкапы для {proj_name} не найдены")
    candidates.sort(reverse=True)
    latest_b = candidates[0]
    man = json.load(open(os.path.join(latest_b, "manifest.json"), "r", encoding="utf-8"))
    target_root = folder_path or man.get("folder_path")
    restored_cnt = 0
    for item in man.get("restored_items", []):
        rel_p = item["rel_path"]
        src_p = os.path.join(latest_b, "files", rel_p)
        dst_p = os.path.join(target_root, rel_p)
        if not os.path.exists(src_p):
            continue
        os.makedirs(os.path.dirname(dst_p), exist_ok=True)
        if item["type"] == "dir":
            if os.path.exists(dst_p):
                shutil.rmtree(dst_p)
            shutil.copytree(src_p, dst_p)
        else:
            shutil.copy2(src_p, dst_p)
        restored_cnt += 1
    return {"status": "ok", "backup_used": latest_b, "restored_count": restored_cnt}

HUD_SNIPPET = """<!-- [JENNY_BLACKBOX_HUD_V20_START] -->
<script>
(function(){
  if (window.__jennyBlackBoxInstalledV20) return;
  window.__jennyBlackBoxInstalledV20 = true;
  window.__jennyErrorLogsV20 = [];
  var badge = null, modal = null, pre = null;
  function ensureUi() {
    if (badge || !document.body) return;
    badge = document.createElement('div');
    badge.style.cssText = 'position:fixed;bottom:74px;right:10px;z-index:999998;background:rgba(220,38,38,0.92);color:#fff;font-family:monospace;font-size:11px;font-weight:bold;padding:5px 9px;border-radius:14px;border:1.5px solid #fca5a5;box-shadow:0 4px 14px rgba(0,0,0,0.7);display:none;cursor:pointer;user-select:none;';
    badge.onclick = function() { if (modal) modal.style.display = 'flex'; };
    document.body.appendChild(badge);
    modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;inset:0;z-index:999999;background:rgba(0,0,0,0.85);display:none;align-items:center;justify-content:center;padding:12px;';
    modal.innerHTML = '<div style="width:100%;max-width:440px;background:#161b22;border:1.5px solid #ff7b72;border-radius:12px;padding:12px;color:#e6edf3;font-family:sans-serif;"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;"><b style="color:#ff7b72;font-size:13px;">🐞 Бортовой самописец ошибок</b><button id="jbHudCloseBtn" style="background:#21262d;color:#fff;border:1px solid #30363d;border-radius:6px;padding:3px 8px;">✕</button></div><pre id="jbHudPre" style="max-height:55vh;overflow-y:auto;background:#090d13;padding:8px;border-radius:6px;font-family:monospace;font-size:10.5px;color:#ffa198;white-space:pre-wrap;word-break:break-all;border:1px solid #30363d;margin:6px 0;"></pre><div style="display:flex;gap:6px;"><button id="jbHudCopyBtn" style="flex:1;background:#238636;color:#fff;border:none;border-radius:7px;padding:8px;font-weight:bold;font-size:12px;">📋 Скопировать для ИИ</button><button id="jbHudClearBtn" style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:7px;padding:8px;font-size:12px;">🗑 Очистить</button></div></div>';
    document.body.appendChild(modal);
    pre = modal.querySelector('#jbHudPre');
    modal.querySelector('#jbHudCloseBtn').onclick = function() { modal.style.display = 'none'; };
    modal.querySelector('#jbHudClearBtn').onclick = function() { window.__jennyErrorLogsV20 = []; badge.style.display = 'none'; modal.style.display = 'none'; };
    modal.querySelector('#jbHudCopyBtn').onclick = function() {
      var txt = window.__jennyErrorLogsV20.join('\\n\\n');
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt);
      this.textContent = '✅ Скопировано!';
      var self = this;
      setTimeout(function(){ self.textContent = '📋 Скопировать для ИИ'; }, 1800);
    };
  }
  function recordErr(kind, msg, src, line, col, stack) {
    var ts = new Date().toTimeString().split(' ')[0];
    var loc = src ? (src.split('/').pop() + ':' + (line || 0) + (col ? ':' + col : '')) : '';
    var entry = '[' + ts + '] ' + kind + ': ' + msg + (loc ? '\\n  📍 Файл: ' + loc : '') + (stack ? '\\n  🧵 Стек: ' + String(stack).split('\\n').slice(0, 4).join(' | ') : '');
    window.__jennyErrorLogsV20.push(entry);
    if (window.__jennyErrorLogsV20.length > 50) window.__jennyErrorLogsV20.shift();
    ensureUi();
    if (badge) { badge.textContent = '🐞 Ошибок: ' + window.__jennyErrorLogsV20.length; badge.style.display = 'block'; }
    if (pre) pre.textContent = window.__jennyErrorLogsV20.join('\\n----------------------------------\\n');
  }
  window.addEventListener('error', function(e) { recordErr('JS Error', e.message || String(e), e.filename, e.lineno, e.colno, e.error && e.error.stack); });
  window.addEventListener('unhandledrejection', function(e) { var r = e.reason || {}; recordErr('Promise Reject', r.message || String(r), '', 0, 0, r.stack || ''); });
})();
</script>
<!-- [JENNY_BLACKBOX_HUD_V20_END] -->"""

def toggle_blackbox_hud(folder_path: str, enable: bool = True) -> dict:
    html_p = os.path.join(folder_path, "app/src/main/python/index.html")
    if not os.path.isfile(html_p):
        raise ValueError(f"Файл index.html не найден в {html_p}")
    raw = open(html_p, "r", encoding="utf-8").read()
    raw_clean = re.sub(r"<!-- \[JENNY_BLACKBOX_HUD_V20_START\] -->.*?<!-- \[JENNY_BLACKBOX_HUD_V20_END\] -->\s*", "", raw, flags=re.DOTALL)
    if enable:
        new_raw = raw_clean.replace("<head>", "<head>\n" + HUD_SNIPPET, 1) if "<head>" in raw_clean else (HUD_SNIPPET + "\n" + raw_clean)
        open(html_p, "w", encoding="utf-8").write(new_raw)
        return {"status": "ok", "enabled": True, "msg": "Бортовой самописец встроен в index.html!"}
    open(html_p, "w", encoding="utf-8").write(raw_clean)
    return {"status": "ok", "enabled": False, "msg": "Бортовой самописец отключён и убран из index.html."}
