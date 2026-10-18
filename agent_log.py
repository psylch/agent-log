#!/usr/bin/env python3
"""agent-log: read CLI coding-agent sessions as compact text (or JSON) for agents and bots.

Sources (resolved from $HOME at call time; AGENT_LOG_HOME_<NAME> replaces a provider's base dir):
  droid     ~/.factory/sessions/<escaped-cwd>/<id>.jsonl
  codex     ~/.codex/{sessions,archived_sessions}/**/*.jsonl[.zst]   ($CODEX_HOME)
  claude    ~/.claude/projects/<escaped-cwd>/<id>.jsonl              ($CLAUDE_CONFIG_DIR)
  gemini    ~/.gemini/tmp/<hash>/chats/session-*.json
  opencode  ~/.local/share/opencode/{opencode*.db,storage/}          ($XDG_DATA_HOME)
  pi        ~/.pi/agent/sessions/--<cwd>--/<ts>_<id>.jsonl           ($PI_CODING_AGENT_DIR)
Times shown in Asia/Taipei (UTC+8).
"""
import argparse, glob, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.parse
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
RECENT_S = 120
SCHEMA_VERSION = 1
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

def red(s): return f"\033[31m{s}\033[0m" if USE_COLOR else s
def dim(s): return f"\033[2m{s}\033[0m" if USE_COLOR else s

def parse_ts(s):
    if not s: return None
    if isinstance(s, (int, float)): return s / 1000 if s > 1e11 else float(s)
    try: return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception: return None

def ms(v):
    try: return float(v) / 1000 if v else None
    except Exception: return None

def iso(t):
    return datetime.fromtimestamp(t, TZ).isoformat(timespec="seconds") if t is not None else None

def fmt_t(t, full=False):
    if t is None: return "--:--:--"
    d = datetime.fromtimestamp(t, TZ)
    return d.strftime("%m-%d %H:%M" if full else "%H:%M:%S")

def fmt_dur(s):
    s = int(s or 0)
    return f"{s//3600}h{s%3600//60:02d}m" if s >= 3600 else f"{s//60}m{s%60:02d}s"

def parse_age(s):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", s.strip())
    if not m: raise SystemExit(f"bad duration: {s} (use 30m, 2h, 1d)")
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]

def parse_when(s):
    """'2h' (ago) | 'HH:MM' today | ISO."""
    if re.fullmatch(r"\d+(\.\d+)?[smhd]", s): return time.time() - parse_age(s)
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", s):
        p = [int(x) for x in s.split(":")] + [0]
        d = datetime.now(TZ).replace(hour=p[0], minute=p[1], second=p[2], microsecond=0)
        return d.timestamp()
    d = datetime.fromisoformat(s)
    if d.tzinfo is None: d = d.replace(tzinfo=TZ)
    return d.timestamp()

def short(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1] + "…"

def clip(s, n):
    s = str(s or "")
    return s if n is None or len(s) <= n else s[: n - 1] + "…"

# ---------------- file helpers ----------------
def zst_decompressor():
    """bytes-returning decompressor for .zst files, or None when neither zstandard nor zstd is available."""
    try:
        import zstandard
        def dec(p):
            with open(p, "rb") as f: return zstandard.ZstdDecompressor().decompressobj().decompress(f.read())
        return dec
    except ImportError: pass
    if shutil.which("zstd"):
        return lambda p: subprocess.run(["zstd", "-dc", p], capture_output=True, check=True).stdout
    return None

def read_lines(path, first_only=False):
    if path.endswith(".zst"):
        dec = zst_decompressor()
        if not dec: return []
        try: lines = dec(path).decode("utf-8", "replace").splitlines()
        except Exception: return []
        return lines[:1] if first_only else lines
    with open(path, errors="replace") as f:
        return [f.readline()] if first_only else f.read().splitlines()

def iter_jsonl(lines):
    for line in lines:
        line = line.strip()
        if not line: continue
        try: d = json.loads(line)
        except Exception: continue
        if isinstance(d, dict): yield d

def first_json(path):
    for d in iter_jsonl(read_lines(path, first_only=True)): return d
    return {}

def read_json(path):
    try:
        with open(path, errors="replace") as f: return json.load(f)
    except Exception: return None

def base_dir(name, native_env, *default):
    """AGENT_LOG_HOME_<NAME> > native env var > ~/<default>; evaluated per call so tests can swap $HOME."""
    for var in (f"AGENT_LOG_HOME_{name.upper()}", native_env):
        v = var and os.environ.get(var, "").strip()
        if v: return os.path.expanduser(v)
    return os.path.join(os.path.expanduser("~"), *default)

# ---------------- events ----------------
# event: dict(t, k, text, tool?, paths?, err?, mid?) ; k in KINDS. load() adds n (step) and turn.
KINDS = ("user", "say", "think", "tool_call", "tool_result", "error", "compact", "final")
STEP_KINDS = {"say", "think", "tool_call"}

def E(t, k, text="", **kw):
    d = dict(t=t, k=k, text=text if isinstance(text, str) else str(text if text is not None else ""))
    d.update(kw)
    return d

def tool_oneliner(name, inp):
    if not isinstance(inp, dict): return f"{name} {short(inp, 160)}"
    for key in ("command", "cmd"):
        if key in inp:
            c = inp[key]
            if isinstance(c, list):
                c = c[2] if len(c) >= 3 and c[1] in ("-lc", "-c") else " ".join(map(str, c))
            return f"{name} $ {short(c, 160)}"
    path = inp.get("file_path") or inp.get("path") or inp.get("filePath") or inp.get("absolute_path") or inp.get("notebook_path")
    bits = []
    if "pattern" in inp: bits.append(f"/{short(inp['pattern'], 60)}/")
    if path: bits.append(str(path))
    if "url" in inp: bits.append(str(inp["url"]))
    if "query" in inp: bits.append(repr(short(inp["query"], 80)))
    if not bits:
        bits = [f"{k}={short(v, 50)}" for k, v in list(inp.items())[:3]]
    return f"{name} " + " ".join(bits)

EDIT_TOOLS = {"edit", "create", "multiedit", "write", "applypatch", "apply_patch", "notebookedit",
              "write_file", "replace", "patch", "str_replace_based_edit_tool"}
PATH_KEYS = ("file_path", "path", "filePath", "notebook_path", "absolute_path")
# path stops at a backslash/quote too: patches often arrive JSON-escaped inside another tool's code string
PATCH_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\s\\\"']+)")

def edit_paths(name, inp):
    if isinstance(inp, str):
        return PATCH_RE.findall(inp) if "*** " in inp else []
    if str(name).lower() not in EDIT_TOOLS or not isinstance(inp, dict): return []
    for k in PATH_KEYS:
        if isinstance(inp.get(k), str): return [inp[k]]
    for k in ("input", "patch", "patchText"):
        if isinstance(inp.get(k), str): return PATCH_RE.findall(inp[k])
    return []

def tool_ev(t, name, inp, mid=None):
    if isinstance(inp, str):
        try: inp = json.loads(inp)
        except Exception: pass
    return E(t, "tool_call", tool_oneliner(name, inp), tool=name, paths=edit_paths(name, inp), mid=mid)

def result_text(c):
    if isinstance(c, list):
        return "\n".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    if isinstance(c, dict): return str(c.get("text") or c.get("output") or json.dumps(c))
    return str(c if c is not None else "")

def is_injected(text):
    t = text.lstrip()
    return t.startswith("<system-reminder>") or t.startswith("<environment_context") or t.startswith("<user_instructions") or t.startswith("# AGENTS.md")

def blocks_text(content):
    if isinstance(content, str): return content
    if isinstance(content, list):
        return "\n".join(x.get("text", "") for x in content if isinstance(x, dict) and x.get("type") in (None, "text", "input_text", "output_text"))
    return ""

# ---------------- sessions & providers ----------------
class Sess:
    def __init__(self, prov, path, sid, cwd, mtime):
        self.prov, self.kind, self.path, self.id, self.cwd, self.mtime = prov, prov.name, path, sid, cwd, mtime
        self.running, self.why = False, ""

class Provider:
    name = ""
    procs = ()  # process basenames that mean "this agent is running"
    def discover(self): return []
    def peek(self, path): raise NotImplementedError
    def events(self, path): return []
    def sessions(self):
        out = []
        for p in self.discover():
            try: out.append(self.peek(p))
            except Exception: pass
        return out
    def fingerprint(self, path):
        st = os.stat(path); return (st.st_size, st.st_mtime)
    def parse(self, path):
        """Normalized session: dict(id, cwd, source, started, updated, events)."""
        s = self.peek(path); ev = load(s); _, _, t0, _ = stats(ev)
        return dict(id=s.id, cwd=s.cwd, source=self.name, started=t0, updated=s.mtime, events=ev)

def lossy_cwd(dirname):
    return dirname.replace("-", "/")

class Droid(Provider):
    name, procs = "droid", ("droid",)
    def root(self): return os.path.join(base_dir("droid", None, ".factory"), "sessions")
    def discover(self): return glob.glob(os.path.join(self.root(), "*", "*.jsonl"))
    def peek(self, path):
        cwd = None
        try: cwd = first_json(path).get("cwd")
        except Exception: pass
        return Sess(self, path, os.path.basename(path)[:-6], cwd or lossy_cwd(os.path.basename(os.path.dirname(path))), os.path.getmtime(path))
    def events(self, path):
        ev, names = [], {}
        for d in iter_jsonl(read_lines(path)):
            t = parse_ts(d.get("timestamp")); ty = d.get("type")
            if ty == "compaction_state":
                ev.append(E(t, "compact", d.get("summaryText", ""))); continue
            if ty != "message": continue
            m = d.get("message") or {}; role = m.get("role"); content = m.get("content")
            if isinstance(content, str): content = [{"type": "text", "text": content}]
            for x in content or []:
                if not isinstance(x, dict): continue
                xt = x.get("type")
                if role == "user" and xt == "text":
                    if not is_injected(x.get("text", "")): ev.append(E(t, "user", x.get("text", "")))
                elif role == "user" and xt == "tool_result":
                    ev.append(E(t, "tool_result", result_text(x.get("content")), tool=names.get(x.get("tool_use_id"), "?"), err=bool(x.get("is_error"))))
                elif role == "assistant" and xt == "text":
                    ev.append(E(t, "say", x.get("text", "")))
                elif role == "assistant" and xt == "thinking":
                    ev.append(E(t, "think", x.get("thinking", "")))
                elif role == "assistant" and xt == "tool_use":
                    names[x.get("id")] = x.get("name")
                    ev.append(tool_ev(t, x.get("name"), x.get("input") or {}))
        return ev

class Codex(Provider):
    name, procs = "codex", ("codex",)
    ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl(?:\.zst)?$")
    def root(self): return base_dir("codex", "CODEX_HOME", ".codex")
    def discover(self):
        pats = ["*.jsonl"] + (["*.jsonl.zst"] if zst_decompressor() else [])
        return [p for sub in ("sessions", "archived_sessions") for pat in pats
                for p in glob.glob(os.path.join(self.root(), sub, "**", pat), recursive=True)]
    def peek(self, path):
        m = self.ID_RE.search(path)
        sid = m.group(1) if m else re.sub(r"\.jsonl(\.zst)?$", "", os.path.basename(path))
        if path.endswith(".zst") and not read_lines(path, first_only=True): raise ValueError("cannot decompress")
        cwd = None
        try: cwd = (first_json(path).get("payload") or {}).get("cwd")
        except Exception: pass
        return Sess(self, path, sid, cwd, os.path.getmtime(path))
    def events(self, path):
        ev, names = [], {}
        for d in iter_jsonl(read_lines(path)):
            t = parse_ts(d.get("timestamp")); p = d.get("payload") or {}
            if not isinstance(p, dict): continue
            ty, pt = d.get("type"), p.get("type")
            if ty == "response_item" and pt == "message":
                txt = "".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
                if p.get("role") == "user" and not is_injected(txt): ev.append(E(t, "user", txt))
                elif p.get("role") == "assistant": ev.append(E(t, "say", txt))
            elif ty == "response_item" and pt == "reasoning":
                s = " ".join(x.get("text", "") for x in p.get("summary") or [] if isinstance(x, dict))
                if s: ev.append(E(t, "think", s))
            elif ty == "response_item" and pt in ("function_call", "custom_tool_call", "local_shell_call"):
                name = p.get("name", pt); names[p.get("call_id")] = name
                raw = p.get("input") if "input" in p else p.get("arguments", p.get("action"))
                inp = raw
                if isinstance(raw, str):
                    try: inp = json.loads(raw)
                    except Exception:
                        m = re.search(r'cmd:\s*"((?:[^"\\]|\\.)*)"', raw)
                        inp = {"command": json.loads('"' + m.group(1) + '"')} if m else raw
                ev.append(tool_ev(t, name, inp))
            elif ty == "response_item" and pt in ("function_call_output", "custom_tool_call_output"):
                out = p.get("output"); txt = result_text(out) if isinstance(out, list) else str(out)
                err = bool(re.search(r"exit code:? [1-9]|Process exited with code [1-9]|\"exit_code\":\s*[1-9]", txt[:2000]))
                ev.append(E(t, "tool_result", txt, tool=names.get(p.get("call_id"), "?"), err=err))
            elif ty == "event_msg" and pt == "task_complete":
                ev.append(E(t, "final", p.get("last_agent_message") or ""))
            elif ty == "event_msg" and pt == "error":
                ev.append(E(t, "error", p.get("message") or "error"))
            elif ty == "event_msg" and pt == "turn_aborted":
                ev.append(E(t, "error", f"turn aborted ({p.get('reason', '?')})"))
            elif ty == "compacted":
                ev.append(E(t, "compact", p.get("message") or ""))
        return ev

class Claude(Provider):
    name, procs = "claude", ("claude",)
    def root(self): return os.path.join(base_dir("claude", "CLAUDE_CONFIG_DIR", ".claude"), "projects")
    def discover(self): return glob.glob(os.path.join(self.root(), "*", "*.jsonl"))
    def peek(self, path):
        cwd = None
        with open(path, errors="replace") as f:
            for i, line in enumerate(f):
                if i > 50: break
                try: d = json.loads(line)
                except Exception: continue
                if isinstance(d, dict) and d.get("cwd"): cwd = d["cwd"]; break
        return Sess(self, path, os.path.basename(path)[:-6], cwd or lossy_cwd(os.path.basename(os.path.dirname(path))), os.path.getmtime(path))
    @staticmethod
    def user_text(t):
        """Prompt text, or None for harness-injected scaffolding."""
        s = t.lstrip()
        if is_injected(s) or s.startswith(("<local-command-std", "<bash-stdout", "<bash-stderr", "Caveat: The messages below")): return None
        m = re.search(r"<command-name>(.*?)</command-name>", s, re.S)
        if m:
            a = re.search(r"<command-args>(.*?)</command-args>", s, re.S)
            return (m.group(1).strip() + " " + (a.group(1).strip() if a else "")).strip()
        m = re.search(r"<bash-input>(.*?)</bash-input>", s, re.S)
        if m: return "! " + m.group(1).strip()
        return t
    def events(self, path):
        ev, names = [], {}
        for d in iter_jsonl(read_lines(path)):
            if d.get("isSidechain"): continue
            t = parse_ts(d.get("timestamp")); ty = d.get("type")
            if ty == "system":
                if d.get("subtype") == "compact_boundary": ev.append(E(t, "compact", d.get("content") or ""))
                continue
            if ty not in ("user", "assistant"): continue
            m = d.get("message") or {}; content = m.get("content"); mid = m.get("id")
            if isinstance(content, str): content = [{"type": "text", "text": content}]
            if ty == "user":
                if d.get("isCompactSummary"):
                    # follows its compact_boundary record; one compaction, not two
                    if ev and ev[-1]["k"] == "compact" and not ev[-1].get("filled"):
                        ev[-1].update(text=blocks_text(content), filled=True)
                    else: ev.append(E(t, "compact", blocks_text(content), filled=True))
                    continue
                if d.get("isMeta"): continue
                for x in content or []:
                    if not isinstance(x, dict): continue
                    if x.get("type") == "text":
                        txt = self.user_text(x.get("text", ""))
                        if txt is None: continue
                        ev.append(E(t, "error" if txt.startswith("[Request interrupted") else "user", txt))
                    elif x.get("type") == "tool_result":
                        ev.append(E(t, "tool_result", result_text(x.get("content")), tool=names.get(x.get("tool_use_id"), "?"), err=bool(x.get("is_error"))))
                continue
            if d.get("isApiErrorMessage"):
                ev.append(E(t, "error", blocks_text(content))); continue
            for x in content or []:
                if not isinstance(x, dict): continue
                xt = x.get("type")
                if xt == "text" and x.get("text", "").strip(): ev.append(E(t, "say", x["text"], mid=mid))
                elif xt == "thinking" and x.get("thinking"): ev.append(E(t, "think", x["thinking"], mid=mid))
                elif xt == "tool_use":
                    names[x.get("id")] = x.get("name")
                    ev.append(tool_ev(t, x.get("name"), x.get("input") or {}, mid=mid))
        return ev

class Gemini(Provider):
    name, procs = "gemini", ("gemini",)
    def root(self): return os.path.join(base_dir("gemini", None, ".gemini"), "tmp")
    def discover(self): return glob.glob(os.path.join(self.root(), "*", "chats", "session-*.json"))
    def peek(self, path):
        d = read_json(path)
        if not isinstance(d, dict) or not isinstance(d.get("messages"), list): raise ValueError("not a gemini chat")
        cwd = None
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(path)), ".project_root")) as f: cwd = f.read().strip() or None
        except Exception: pass
        sid = d.get("sessionId") or os.path.basename(path)[8:-5]
        return Sess(self, path, sid, cwd, os.path.getmtime(path))
    @staticmethod
    def response(r):
        if not isinstance(r, dict): return str(r)
        resp = (r.get("functionResponse") or {}).get("response")
        if not isinstance(resp, dict): return str(resp or "")
        for k in ("output", "error"):
            if isinstance(resp.get(k), str) and resp[k]: return resp[k]
        return json.dumps(resp, ensure_ascii=False)
    def events(self, path):
        ev = []
        for m in (read_json(path) or {}).get("messages") or []:
            if not isinstance(m, dict): continue
            t = parse_ts(m.get("timestamp")); ty = m.get("type"); mid = m.get("id")
            c = m.get("content")
            txt = c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("text"))
            if ty == "user":
                if txt.strip(): ev.append(E(t, "user", txt))
            elif ty == "gemini":
                for th in m.get("thoughts") or []:
                    if isinstance(th, dict): ev.append(E(t, "think", ": ".join(x for x in (th.get("subject"), th.get("description")) if x), mid=mid))
                if txt.strip(): ev.append(E(t, "say", txt, mid=mid))
                results = []
                for tc in m.get("toolCalls") or []:
                    if not isinstance(tc, dict): continue
                    name = tc.get("name"); ev.append(tool_ev(t, name, tc.get("args") or {}, mid=mid))
                    rt = parse_ts(tc.get("timestamp")) or t
                    out = "\n".join(self.response(r) for r in tc.get("result") or [])
                    if tc.get("status") in ("error", "cancelled"): results.append(E(rt, "error", out or tc.get("status"), tool=name))
                    else: results.append(E(rt, "tool_result", out, tool=name))
                ev.extend(results)
            elif ty == "error":
                ev.append(E(t, "error", txt))
        return ev

class OpenCode(Provider):
    """SQLite store (opencode.db, OpenCode >= 1.x) plus the older storage/ JSON tree."""
    name, procs = "opencode", ("opencode",)
    def root(self):
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        if xdg and not os.environ.get("AGENT_LOG_HOME_OPENCODE"): return os.path.join(xdg, "opencode")
        return base_dir("opencode", None, ".local", "share", "opencode")
    def connect(self, db):
        for q in ("mode=ro", "immutable=1"):
            try:
                c = sqlite3.connect(f"file:{urllib.parse.quote(db)}?{q}", uri=True, timeout=2)
                c.execute("SELECT 1 FROM session_v2 LIMIT 1").fetchall()
                return c
            except sqlite3.Error: continue
        return None
    SESS_SQL = ("SELECT s.id, s.directory, s.time_updated, "
                "(SELECT IFNULL(max(m.time_updated), 0) FROM session_message m WHERE m.session_id = s.id) FROM session_v2 s ")
    def db_sessions(self, db, sid=None):
        c = self.connect(db)
        if not c: return []
        try:
            rows = c.execute(self.SESS_SQL + ("WHERE s.id = ?" if sid else "WHERE s.parent_id IS NULL"), (sid,) if sid else ()).fetchall()
        except sqlite3.Error: rows = []
        finally: c.close()
        return [Sess(self, f"{db}#{r[0]}", r[0], r[1], ms(max(r[2] or 0, r[3] or 0)) or os.path.getmtime(db)) for r in rows]
    def legacy_dir(self): return os.path.join(self.root(), "storage")
    def discover(self):
        out = []
        for p in glob.glob(os.path.join(self.legacy_dir(), "session", "*", "*.json")):
            d = read_json(p)
            if isinstance(d, dict) and d.get("id") and not d.get("parentID"): out.append(p)
        return out
    def sessions(self):
        out = [s for db in sorted(glob.glob(os.path.join(self.root(), "opencode*.db"))) for s in self.db_sessions(db)]
        return out + super().sessions()
    def peek(self, path):
        if ".db#" in path:
            db, _, sid = path.rpartition("#")
            ss = self.db_sessions(db, sid)
            if not ss: raise ValueError(f"no opencode session {sid}")
            return ss[0]
        d = read_json(path) or {}
        mdir = os.path.join(self.legacy_dir(), "message", d["id"])
        mt = max([os.path.getmtime(path), ms((d.get("time") or {}).get("updated")) or 0] + ([os.path.getmtime(mdir)] if os.path.isdir(mdir) else []))
        return Sess(self, path, d["id"], d.get("directory"), mt)
    def fingerprint(self, path): return self.peek(path).mtime
    @staticmethod
    def tool_parts(t, name, st, rt, mid):
        st = st if isinstance(st, dict) else {}
        call = tool_ev(t, name, st.get("input"), mid=mid)
        meta = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
        if st.get("status") == "error":
            e = st.get("error"); msg = e.get("message") if isinstance(e, dict) else e
            return call, E(rt, "error", msg or "error", tool=name)
        if st.get("status") == "completed":
            out = st["output"] if "output" in st else result_text(st.get("content"))
            ex = meta.get("exit")
            return call, E(rt, "tool_result", result_text(out), tool=name, err=(isinstance(ex, int) and ex != 0) or meta.get("error") is True)
        return call, None
    def events(self, path):
        return self.db_events(path) if ".db#" in path else self.legacy_events(path)
    def db_events(self, path):
        db, _, sid = path.rpartition("#"); c = self.connect(db)
        if not c: return []
        try: rows = c.execute("SELECT id, type, data, time_created FROM session_message WHERE session_id = ? ORDER BY seq", (sid,)).fetchall()
        finally: c.close()
        ev = []
        for mid, ty, data, tc in rows:
            try: d = json.loads(data) if isinstance(data, str) else {}
            except Exception: continue
            if not isinstance(d, dict): continue
            t = ms((d.get("time") or {}).get("created")) or ms(tc)
            if ty == "user":
                if str(d.get("text") or "").strip(): ev.append(E(t, "user", d["text"]))
            elif ty == "shell":
                ev.append(E(t, "user", f"! {d.get('command', '')}"))
            elif ty == "assistant":
                results = []
                for part in d.get("content") or []:
                    if not isinstance(part, dict): continue
                    pt = part.get("type")
                    if pt == "text" and str(part.get("text") or "").strip(): ev.append(E(t, "say", part["text"], mid=mid))
                    elif pt == "reasoning" and str(part.get("text") or "").strip(): ev.append(E(t, "think", part["text"], mid=mid))
                    elif pt == "tool":
                        rt = ms((part.get("time") or {}).get("completed")) or t
                        call, res = self.tool_parts(t, part.get("name"), part.get("state"), rt, mid)
                        ev.append(call)
                        if res: results.append(res)
                ev.extend(results)
                if d.get("error"):
                    e = d["error"]; ev.append(E(t, "error", e.get("message") if isinstance(e, dict) else e))
            elif ty == "compaction":
                if d.get("status") == "completed": ev.append(E(t, "compact", d.get("summary") or ""))
                elif d.get("status") == "failed": ev.append(E(t, "error", f"compaction failed: {d.get('error') or ''}"))
        return ev
    def legacy_events(self, path):
        info = read_json(path) or {}; st = self.legacy_dir(); ev = []
        msgs = [m for m in (read_json(p) for p in glob.glob(os.path.join(st, "message", info.get("id", ""), "*.json"))) if isinstance(m, dict)]
        msgs.sort(key=lambda m: ((m.get("time") or {}).get("created") or 0, m.get("id", "")))
        for m in msgs:
            t = ms((m.get("time") or {}).get("created")); mid = m.get("id")
            parts = [p for p in (read_json(x) for x in glob.glob(os.path.join(st, "part", mid or "", "*.json"))) if isinstance(p, dict)]
            parts.sort(key=lambda p: p.get("id", ""))
            texts = [p.get("text", "") for p in parts if p.get("type") == "text" and not p.get("synthetic")]
            if m.get("role") == "user":
                txt = "\n".join(texts)
                if txt.strip(): ev.append(E(t, "user", txt))
                continue
            if m.get("role") != "assistant": continue
            if m.get("summary") is True:
                ev.append(E(t, "compact", "\n".join(texts))); continue
            results = []
            for p in parts:
                pt = p.get("type")
                if pt == "text" and str(p.get("text") or "").strip(): ev.append(E(t, "say", p["text"], mid=mid))
                elif pt == "reasoning" and str(p.get("text") or "").strip(): ev.append(E(t, "think", p["text"], mid=mid))
                elif pt == "tool":
                    s = p.get("state") if isinstance(p.get("state"), dict) else {}
                    rt = ms((s.get("time") or {}).get("end")) or t
                    call, res = self.tool_parts(t, p.get("tool"), s, rt, mid)
                    ev.append(call)
                    if res: results.append(res)
            ev.extend(results)
            if m.get("error"):
                e = m["error"]
                msg = ((e.get("data") or {}).get("message") or e.get("name")) if isinstance(e, dict) else e
                ev.append(E(t, "error", msg or "error"))
        return ev

class Pi(Provider):
    name, procs = "pi", ("pi",)
    def root(self):
        flat = os.environ.get("PI_CODING_AGENT_SESSION_DIR", "").strip()
        if flat and not os.environ.get("AGENT_LOG_HOME_PI"): return os.path.expanduser(flat)
        return os.path.join(base_dir("pi", "PI_CODING_AGENT_DIR", ".pi", "agent"), "sessions")
    def discover(self):
        r = self.root()
        return glob.glob(os.path.join(r, "*.jsonl")) + glob.glob(os.path.join(r, "*", "*.jsonl"))
    def peek(self, path):
        h = first_json(path)
        if h.get("type") != "session": h = {}
        sid = h.get("id") or os.path.basename(path)[:-6].split("_", 1)[-1]
        cwd = h.get("cwd")
        if not cwd:
            dn = os.path.basename(os.path.dirname(path))
            cwd = "/" + dn.strip("-").replace("-", "/") if dn.startswith("--") else None
        return Sess(self, path, sid, cwd, os.path.getmtime(path))
    @staticmethod
    def active_path(entries):
        """Entries on the branch ending at the last-written leaf (pi stores a tree via parentId)."""
        body = [e for e in entries if e.get("type") != "session"]
        if not any("parentId" in e for e in body): return body
        by_id = {e["id"]: e for e in body if e.get("id")}
        leaf = next((e for e in reversed(body) if e.get("id")), None)
        out, seen = [], set()
        while leaf is not None and leaf.get("id") not in seen:
            seen.add(leaf.get("id")); out.append(leaf); leaf = by_id.get(leaf.get("parentId"))
        return out[::-1]
    def events(self, path):
        ev = []
        for d in self.active_path(list(iter_jsonl(read_lines(path)))):
            t = parse_ts(d.get("timestamp")); ty = d.get("type")
            if ty == "compaction":
                ev.append(E(t, "compact", d.get("summary") or "")); continue
            if ty != "message": continue
            m = d.get("message") or {}; role = m.get("role"); mid = d.get("id")
            if role == "user":
                txt = blocks_text(m.get("content"))
                if txt.strip(): ev.append(E(t, "user", txt))
            elif role == "assistant":
                for x in m.get("content") or []:
                    if not isinstance(x, dict): continue
                    xt = x.get("type")
                    if xt == "text" and x.get("text", "").strip(): ev.append(E(t, "say", x["text"], mid=mid))
                    elif xt == "thinking" and x.get("thinking"): ev.append(E(t, "think", x["thinking"], mid=mid))
                    elif xt == "toolCall": ev.append(tool_ev(t, x.get("name"), x.get("arguments") or {}, mid=mid))
                if m.get("stopReason") in ("error", "aborted"):
                    ev.append(E(t, "error", m.get("errorMessage") or m["stopReason"]))
            elif role == "toolResult":
                ev.append(E(t, "tool_result", result_text(m.get("content")), tool=m.get("toolName") or "?", err=bool(m.get("isError"))))
            elif role == "bashExecution":
                ev.append(E(t, "user", f"! {m.get('command', '')}"))
        return ev

PROVIDERS = {p.name: p for p in (Droid(), Codex(), Claude(), Gemini(), OpenCode(), Pi())}

def discover(names=None):
    out = []
    for p in PROVIDERS.values():
        if names and p.name not in names: continue
        try: out.extend(p.sessions())
        except Exception: pass
    out.sort(key=lambda s: s.mtime, reverse=True)
    return out

def sniff(path):
    """Guess the provider of a session file given directly on the command line."""
    if ".db#" in path: return PROVIDERS["opencode"]
    for name, marker in (("codex", "/.codex/"), ("claude", "/.claude/"), ("gemini", "/.gemini/"), ("pi", "/.pi/"), ("droid", "/.factory/"), ("opencode", "/opencode/")):
        if marker in path: return PROVIDERS[name]
    if path.endswith(".json"):
        return PROVIDERS["gemini" if "messages" in (read_json(path) or {}) else "opencode"]
    d = first_json(path); ty = d.get("type")
    if ty == "session_start": return PROVIDERS["droid"]
    if ty == "session_meta" or "payload" in d: return PROVIDERS["codex"]
    if ty == "session" and "version" in d: return PROVIDERS["pi"]
    if "sessionId" in d or ty in ("summary", "user", "assistant", "system", "file-history-snapshot"): return PROVIDERS["claude"]
    return PROVIDERS["droid"]

# ---------------- running detection ----------------
WRAPPERS = ("node", "nodejs", "bun", "deno")
SCRIPT_HINTS = (("claude-code", "claude"), ("gemini-cli", "gemini"), ("pi-coding-agent", "pi"),
                ("opencode", "opencode"), ("@openai/codex", "codex"), ("droid", "droid"))
SID_FLAGS = ("-s", "--session-id", "resume", "--resume", "-r", "--session")

def proc_kind(argv):
    names = {n: p.name for p in PROVIDERS.values() for n in p.procs}
    exe = os.path.basename(argv[0])
    cands = [exe]
    script = argv[1] if exe in WRAPPERS and len(argv) > 1 else None
    if script: cands.append(os.path.basename(script))
    for c in cands:
        c = re.sub(r"\.(js|mjs|cjs|exe)$", "", c)
        if c in names: return names[c]
    if script:
        for hint, kind in SCRIPT_HINTS:
            if hint in script: return kind
    return None

def live_procs():
    """[(kind, cwd, session_id, cmdline)] for running agent processes. AGENT_LOG_PROC overrides /proc (tests)."""
    root = os.environ.get("AGENT_LOG_PROC", "/proc")
    try: pids = os.listdir(root)
    except Exception: return []
    res = []
    for pid in pids:
        if not pid.isdigit(): continue
        try:
            with open(os.path.join(root, pid, "cmdline"), "rb") as f:
                argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
        except Exception: continue
        if not argv: continue
        kind = proc_kind(argv)
        if not kind: continue
        cwd = None
        for i, a in enumerate(argv):
            if a == "--cwd" and i + 1 < len(argv): cwd = argv[i + 1]
            elif a.startswith("--cwd="): cwd = a[6:]
        if not cwd:
            try: cwd = os.readlink(os.path.join(root, pid, "cwd"))
            except Exception: pass
        sid = None
        for i, a in enumerate(argv):
            if a in SID_FLAGS and i + 1 < len(argv) and not argv[i + 1].startswith("-"): sid = argv[i + 1]
        res.append((kind, cwd, sid, " ".join(argv)))
    return res

def mark_running(sessions):
    claimed = set()
    for kind, cwd, sid, _ in live_procs():
        mine = [s for s in sessions if s.kind == kind]
        cands = [s for s in mine if sid and s.id.startswith(sid)]
        why = "proc:session-id"
        if not cands:
            cands = [s for s in mine if cwd and s.cwd and os.path.realpath(s.cwd) == os.path.realpath(cwd)]
            why = "proc:cwd"
        if cands:
            s = cands[0]  # newest mtime
            if s.path in claimed: continue
            claimed.add(s.path)
            s.running, s.why = True, why
    now = time.time()
    for s in sessions:
        if not s.running and now - s.mtime < RECENT_S:
            s.running, s.why = True, f"mtime<{RECENT_S}s"

def resolve(ref, sessions):
    if ref in (None, "", "latest"):
        if not sessions: raise SystemExit("no sessions found")
        return sessions[0]
    if os.path.isfile(ref) or (".db#" in ref and os.path.isfile(ref.rpartition("#")[0])):
        rp = os.path.abspath(ref)
        for s in sessions:
            if os.path.abspath(s.path) == rp: return s
        try: return sniff(rp).peek(rp)
        except Exception as x: raise SystemExit(f"cannot read session file {ref}: {x}")
    hits = [s for s in sessions if s.id.startswith(ref)]
    if not hits: hits = [s for s in sessions if s.cwd and ref.lower() in s.cwd.lower()]
    if not hits: raise SystemExit(f"no session matches '{ref}' (id prefix or project substring)")
    return hits[0]

# ---------------- normalization ----------------
def load(s):
    try: ev = s.prov.events(s.path)
    except Exception as x: ev = [E(None, "error", f"agent-log: failed to parse {s.path}: {x}")]
    turn = 0; last_t = None
    for e in ev:
        if e["k"] == "tool_result" and e.get("err"): e["k"] = "error"
        if e["t"] is None: e["t"] = last_t
        last_t = e["t"]
        if e["k"] == "user": turn += 1
        e["turn"] = max(turn, 1)
    # blocks from the same assistant message (same timestamp or message id) share one step number
    prev = None; step = 0
    for e in ev:
        if e["k"] in STEP_KINDS:
            same = prev and prev["k"] in STEP_KINDS and (prev["t"] == e["t"] or (e.get("mid") and prev.get("mid") == e.get("mid")))
            if not same: step += 1
        e["n"] = step
        prev = e
    return ev

def stats(ev):
    steps = max([e["n"] for e in ev] or [0])
    turns = max([e["turn"] for e in ev] or [0])
    ts = [e["t"] for e in ev if e["t"]]
    return steps, turns, (min(ts) if ts else None), (max(ts) if ts else None)

def files_changed(ev):
    out = []
    for e in ev:
        for p in e.get("paths") or []:
            if p not in out: out.append(p)
    return out

def final_answer(s, ev):
    """(text, explicit) for the latest turn, or (None, False)."""
    lu = max((i for i, e in enumerate(ev) if e["k"] == "user"), default=-1)
    tail = [e for e in ev[lu + 1:] if e["k"] != "think"]
    fin = [e for e in tail if e["k"] == "final"]
    if fin and fin[-1]["text"].strip(): return fin[-1]["text"], True
    rest = [e for e in tail if e["k"] != "final"]
    if (fin or not s.running) and rest and rest[-1]["k"] == "say": return rest[-1]["text"], False
    return None, False

def status_of(s, ev):
    if s.running: return "running", s.why
    tail = [e for e in ev if e["k"] != "think"]
    if not tail: return "unknown", "no events"
    lu = max((i for i, e in enumerate(tail) if e["k"] == "user"), default=-1)
    if any(e["k"] == "final" for e in tail[lu + 1:]): return "done", "final"
    if tail[-1]["k"] == "say": return "done", "ended on assistant text"
    return "unknown", f"idle, ended on {tail[-1]['k']}"

def session_item(s, ev, full=True):
    steps, turns, t0, _ = stats(ev)
    st, why = status_of(s, ev)
    users = [e for e in ev if e["k"] == "user"]
    d = dict(schema_version=SCHEMA_VERSION, source=s.kind, session_id=s.id, path=s.path, cwd=s.cwd,
             status=st, status_reason=why, started=iso(t0), updated=iso(s.mtime), steps=steps, turns=turns,
             task=short(users[0]["text"], 600) if users else None, files_changed=files_changed(ev))
    if full:
        errs = [e for e in ev if e["k"] == "error"]
        d["error_count"] = len(errs)
        d["errors"] = [dict(ts=iso(e["t"]), tool=e.get("tool"), text=short(e["text"], 300)) for e in errs[-10:]]
        d["last_actions"] = [dict(ts=iso(e["t"]), kind=e["k"], text=short(e["text"], 200)) for e in ev if e["k"] in ("tool_call", "say")][-6:]
        d["final_answer"] = final_answer(s, ev)[0]
    return d

def event_json(e, a):
    d = {"ts": iso(e["t"]), "kind": e["k"]}
    if e.get("tool"): d["tool"] = e["tool"]
    lim = None if a.full else a.max_chars * 4
    d["text"] = clip(e["text"], lim) if e["k"] in ("tool_result", "error", "compact") else e["text"]
    if e.get("paths"): d["paths"] = e["paths"]
    d["step"], d["turn"] = e["n"], e["turn"]
    return d

def dump(obj): print(json.dumps(obj, ensure_ascii=False, indent=2))

# ---------------- rendering ----------------
def render(ev, a, out=sys.stdout):
    mc = a.max_chars
    for e in ev:
        k = e["k"]; hdr = f"{fmt_t(e['t'])} #{e['n']:<3}"
        if k == "user":
            print(f"{hdr} USER  (turn {e['turn']}): {short(e['text'], mc * 2 if a.full else mc)}", file=out)
        elif k == "say":
            print(f"{hdr} SAY   {e['text'].strip() if a.full else short(e['text'], mc)}", file=out)
        elif k == "think":
            if not a.no_thinking: print(dim(f"{hdr} think {short(e['text'], 160 if not a.full else mc)}"), file=out)
        elif k == "tool_call":
            print(f"{hdr} ▶ {e['text']}", file=out)
        elif k == "error":
            label = f"✗ ERROR {e['tool']}:" if e.get("tool") else "✗ ERROR:"
            print(red(f"{hdr} {label} {short(e['text'], mc)}"), file=out)
        elif k == "tool_result":
            if a.with_output: print(dim(f"{hdr}   ↳ {short(e['text'], mc)}"), file=out)
        elif k == "compact":
            print(dim(f"{hdr} [context compacted] {short(e['text'], 200)}"), file=out)
        elif k == "final":
            print(f"{hdr} DONE  {short(e['text'], mc * 3)}", file=out)

def window(ev, a):
    if a.turn: ev = [e for e in ev if e["turn"] == a.turn]
    if a.since: t0 = parse_when(a.since); ev = [e for e in ev if e["t"] and e["t"] >= t0]
    if a.until: t1 = parse_when(a.until); ev = [e for e in ev if e["t"] and e["t"] <= t1]
    if a.last:
        ns = sorted({e["n"] for e in ev if e["k"] in STEP_KINDS})
        if ns:
            cut = ns[-a.last] if len(ns) >= a.last else ns[0]
            ev = [e for e in ev if e["n"] >= cut]
    return ev

def header(s, ev):
    steps, turns, t0, t1 = stats(ev)
    run = f"RUNNING ({s.why})" if s.running else "idle"
    return (f"[{s.kind}] {s.cwd}  {s.id[:8]}  {run}\n"
            f"  steps {steps}, turns {turns}, {fmt_t(t0, True)} → {fmt_t(t1, True)} (+08), elapsed {fmt_dur((t1 or 0) - (t0 or 0))}")

# ---------------- commands ----------------
def source_names(a):
    names = [a.source] if getattr(a, "source", None) else list(PROVIDERS)
    if getattr(a, "no_codex", False): names = [n for n in names if n != "codex"]
    return names

def cmd_list(a):
    ss = discover(source_names(a))
    if a.project: ss = [s for s in ss if s.cwd and a.project.lower() in s.cwd.lower()]
    if a.since: t0 = time.time() - parse_age(a.since); ss = [s for s in ss if s.mtime >= t0]
    mark_running(ss)
    if a.active: ss = [s for s in ss if s.running]
    ss = ss[: a.n]
    if a.json:
        dump({"schema_version": SCHEMA_VERSION, "sessions": [session_item(s, load(s), full=False) for s in ss]}); return
    if not ss: print("(no sessions)"); return
    print(f"{'last(+08)':<11} {'kind':<8} {'id':<8} {'steps':>5} {'turns':>5}  {'status':<20} project")
    for s in ss:
        steps, turns, _, _ = stats(load(s))
        st = f"RUN {s.why}" if s.running else "-"
        print(f"{fmt_t(s.mtime, True):<11} {s.kind:<8} {s.id[:8]:<8} {steps:>5} {turns:>5}  {st:<20} {s.cwd}")
    print(dim(f"status: proc:cwd/proc:session-id = live agent process matched (newest session in that cwd); mtime<{RECENT_S}s = file just written"))

def get_session(a):
    ss = discover(source_names(a)); mark_running(ss)
    return resolve(a.session, ss)

def cmd_show(a):
    s = get_session(a); ev = load(s)
    w = window(ev, a); note = None
    if a.default_last and not (a.last or a.since or a.until or a.turn):
        a.last = a.default_last; w = window(ev, a)
        hidden = len({e['n'] for e in ev}) - len({e['n'] for e in w})
        if hidden > 0: note = f"  (showing last {a.last} steps; use --last/--since/--turn/--all for more)"
    if a.json:
        if a.follow: raise SystemExit("--follow is not supported with --json")
        dump({"schema_version": SCHEMA_VERSION, "session": session_item(s, ev, full=False),
              "events": [event_json(e, a) for e in w if not (a.no_thinking and e["k"] == "think")]})
        return
    print(header(s, ev))
    if note: print(dim(note))
    print("-" * 60)
    render(w, a)
    if a.follow:
        pos = s.prov.fingerprint(s.path); seen = len(ev)
        try:
            while True:
                time.sleep(2)
                fp = s.prov.fingerprint(s.path)
                if fp != pos:
                    pos = fp; ev = load(s)
                    render(ev[seen:], a); seen = len(ev); sys.stdout.flush()
        except KeyboardInterrupt: pass

def cmd_summary(a):
    s = get_session(a); ev = load(s)
    if a.json: dump(session_item(s, ev)); return
    print(header(s, ev))
    users = [e for e in ev if e["k"] == "user"]
    if users:
        print(f"TASK: {short(users[0]['text'], 600)}")
        if len(users) > 1: print(f"LATEST PROMPT (turn {users[-1]['turn']}): {short(users[-1]['text'], 300)}")
    files = files_changed(ev)
    if files:
        cw = (s.cwd or "").rstrip("/") + "/"
        print(f"FILES CHANGED ({len(files)}): " + ", ".join(f.replace(cw, "") for f in files[-25:]))
    errs = [e for e in ev if e["k"] == "error"]
    if errs:
        print(red(f"ERRORS ({len(errs)}), last 3:"))
        for e in errs[-3:]: print(red(f"  {fmt_t(e['t'])} #{e['n']} {e.get('tool') or 'agent'}: {short(e['text'], 200)}"))
    comp = [e for e in ev if e["k"] == "compact"]
    if comp: print(dim(f"context compacted {len(comp)}x"))
    print("LAST ACTIONS:")
    acts = [e for e in ev if e["k"] in ("tool_call", "say")][-6:]
    for e in acts:
        print(f"  {fmt_t(e['t'])} #{e['n']} " + (f"▶ {e['text']}" if e["k"] == "tool_call" else f"SAY {short(e['text'], 200)}"))
    fa, explicit = final_answer(s, ev)
    if fa is not None: print(f"{'FINAL' if explicit else 'FINAL (last assistant text)'}: {short(fa, 1200)}")
    elif s.running: print("STATUS: still running — no final answer yet")
    else: print("STATUS: idle, ended without a final text (crashed/killed or ended on a tool call?)")

def main(argv=None):
    p = argparse.ArgumentParser(prog="agent-log", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    srcs = list(PROVIDERS)
    def common(sp):
        sp.add_argument("--no-codex", action="store_true"); sp.add_argument("--source", choices=srcs)
        sp.add_argument("--json", action="store_true", help="machine-readable output (schema_version 1)")
    l = sub.add_parser("list", help="list sessions")
    l.add_argument("--project"); l.add_argument("--active", action="store_true")
    l.add_argument("--since", help="e.g. 2h, 30m, 1d"); l.add_argument("-n", type=int, default=15)
    common(l); l.set_defaults(fn=cmd_list)
    for name, dl in (("show", 0), ("tail", 20)):
        sp = sub.add_parser(name, help=("full transcript" if name == "show" else "last 20 steps by default"))
        sp.add_argument("session", nargs="?", default="latest", help="id prefix | latest | project substring | path")
        sp.add_argument("--last", type=int, help="last N steps"); sp.add_argument("--since"); sp.add_argument("--until")
        sp.add_argument("--turn", type=int); sp.add_argument("--with-output", action="store_true")
        sp.add_argument("--max-chars", type=int, default=300); sp.add_argument("--no-thinking", action="store_true")
        sp.add_argument("--full", action="store_true", help="don't truncate assistant text")
        sp.add_argument("--all", action="store_true", help="tail: no default window")
        sp.add_argument("-f", "--follow", action="store_true"); common(sp)
        sp.set_defaults(fn=cmd_show, default_last=dl)
    s = sub.add_parser("summary", help="one-screen status"); s.add_argument("session", nargs="?", default="latest")
    common(s); s.set_defaults(fn=cmd_summary)
    a = p.parse_args(argv)
    if getattr(a, "all", False): a.default_last = 0
    try: a.fn(a)
    except BrokenPipeError: pass

if __name__ == "__main__":
    main()
