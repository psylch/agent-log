#!/usr/bin/env python3
"""agent-log: read CLI coding-agent sessions as compact text (or JSON) for agents and bots.

Sources (resolved from $HOME at call time; AGENT_LOG_HOME_<NAME> replaces a provider's base dir):
  droid     ~/.factory/sessions/<escaped-cwd>/<id>.jsonl
  codex     ~/.codex/{sessions,archived_sessions}/**/*.jsonl[.zst]   ($CODEX_HOME)
  claude    ~/.claude/projects/<escaped-cwd>/<id>.jsonl              ($CLAUDE_CONFIG_DIR)
  gemini    ~/.gemini/tmp/<project>/chats/session-*.json[l]
  opencode  ~/.local/share/opencode/{opencode*.db,storage/}          ($XDG_DATA_HOME)
  pi        ~/.pi/agent/sessions/--<cwd>--/<ts>_<id>.jsonl           ($PI_CODING_AGENT_DIR)

Times are shown in the local system timezone. --tz or $AGENT_LOG_TZ overrides it with UTC, +HH:MM / -HH:MM
or an IANA name (e.g. Asia/Taipei). JSON timestamps are ISO 8601 with an offset in the same timezone.

Status (JSON "status", list column):
  running  a live agent process was matched to the session (status_reason "proc:session-id" or "proc:cwd"),
           or the file was written in the last 120 s while some process of that agent is alive ("mtime<120s").
           Processes come from /proc, else `ps` (macOS/BSD); with neither, a write in the last 120 s alone counts.
  done     idle, and the latest turn ended with an explicit final message ("final") or on assistant text
           ("ended on assistant text").
  failed   idle, and the last event is an agent/API-level error, not a failed tool call ("ended on agent/API error").
  unknown  idle, anything else ("idle, ended on <kind>", "no events").

Exit codes: 0 ok, 1 nothing found (no sessions, unknown session), 2 usage error (bad flag or value).
list caches per-file summaries keyed by size+mtime in $XDG_CACHE_HOME/agent-log (--no-cache or
AGENT_LOG_NO_CACHE=1 disables it; an unwritable cache directory is ignored).
"""
import argparse, glob, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, time, urllib.parse
from datetime import datetime, timezone, timedelta

__version__ = "0.1.0"
TZ = None  # None = local system timezone; see set_tz()
RECENT_S = 120
SCHEMA_VERSION = 1
USE_COLOR = False
PROC_ROOT = "/proc"

class CliError(Exception):
    """A user-facing error: printed without a traceback, exit code 1 (not found) or 2 (usage)."""
    def __init__(self, msg, code=1):
        super().__init__(msg); self.code = code

def red(s): return f"\033[31m{s}\033[0m" if USE_COLOR else s
def dim(s): return f"\033[2m{s}\033[0m" if USE_COLOR else s

def want_color(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb": return False
    try: return stream.isatty()
    except Exception: return False

# ---------------- time ----------------
def parse_tz(spec):
    """'local' | 'UTC' | '+HH:MM' / '-HHMM' / '+HH' | IANA name -> tzinfo (None = local). Raises ValueError."""
    s = (spec or "").strip()
    if not s or s.lower() == "local": return None
    if s.upper() in ("UTC", "Z", "GMT"): return timezone.utc
    m = re.fullmatch(r"(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d\d))?", s, re.I)
    if m:
        off = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        if off >= timedelta(hours=24): raise ValueError(spec)
        return timezone(-off if m.group(1) == "-" else off)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(s)
    except Exception: raise ValueError(spec) from None

def set_tz(spec):
    global TZ
    TZ = parse_tz(spec)

def to_dt(t):
    """Aware datetime in the display timezone, or None for missing/out-of-range timestamps."""
    if t is None: return None
    try: return datetime.fromtimestamp(t, timezone.utc).astimezone(TZ)
    except (OverflowError, OSError, ValueError): return None

def tz_label():
    off = datetime.now(timezone.utc).astimezone(TZ).utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    if mins == 0: return "UTC"
    sign = "-" if mins < 0 else "+"; h, m = divmod(abs(mins), 60)
    return f"{sign}{h:02d}" + (f":{m:02d}" if m else "")

ISO_TS_RE = re.compile(r"(\d{4}-\d\d-\d\d)(?:[T ](\d\d):(\d\d)(?::(\d\d))?(?:[.,](\d+))?)?\s*(Z|[+-]\d\d(?::?\d\d)?)?", re.I)

def parse_iso(s, default_tz=timezone.utc):
    """ISO 8601 -> epoch seconds, without relying on fromisoformat (which on 3.9/3.10 rejects 'Z',
    'nanosecond' fractions and '+0000'). Naive values are taken in default_tz (None = local)."""
    m = ISO_TS_RE.fullmatch(str(s).strip())
    if not m: return None
    date, hh, mm, ss, frac, off = m.groups()
    try:
        y, mo, d = (int(x) for x in date.split("-"))
        dt = datetime(y, mo, d, int(hh or 0), int(mm or 0), int(ss or 0), int((frac or "0")[:6].ljust(6, "0")))
        if off:
            if off.upper() == "Z": tz = timezone.utc
            else:
                digits = off[1:].replace(":", "")
                delta = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
                tz = timezone(-delta if off[0] == "-" else delta)
            return dt.replace(tzinfo=tz).timestamp()
        if default_tz is None: return dt.timestamp()  # naive = local
        return dt.replace(tzinfo=default_tz).timestamp()
    except (ValueError, OverflowError, OSError): return None

def epoch(v):
    """Numeric epoch in s, ms, us or ns -> seconds."""
    v = float(v)
    if not (0 < v < 1e21): return None
    while v > 1e11: v /= 1000
    return v

def parse_ts(s):
    """Record timestamp (ISO 8601, or epoch s/ms/us/ns as number or digit string) -> epoch seconds or None.
    Naive ISO values are taken as UTC."""
    if s is None or s == "" or isinstance(s, bool): return None
    try:
        if isinstance(s, (int, float)): return epoch(s)
        s = str(s).strip()
        if s.replace(".", "", 1).isdigit(): return epoch(s)
    except (ValueError, OverflowError): return None
    try:  # fast path for the common shapes; parse_iso covers what 3.9's fromisoformat rejects
        d = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, OverflowError, OSError): pass
    return parse_iso(s)

def ms(v):
    try: return float(v) / 1000 if v else None
    except Exception: return None

def iso(t):
    d = to_dt(t)
    return d.isoformat(timespec="seconds") if d else None

def fmt_t(t, full=False):
    d = to_dt(t)
    if d is None: return "--:--:--" if not full else "--"
    return d.strftime("%m-%d %H:%M" if full else "%H:%M:%S")

def fmt_dur(s):
    s = int(s or 0)
    return f"{s//3600}h{s%3600//60:02d}m" if s >= 3600 else f"{s//60}m{s%60:02d}s"

AGE_RE = re.compile(r"(\d+(?:\.\d+)?)([smhd])")

def parse_age(s):
    m = AGE_RE.fullmatch(s.strip())
    if not m: raise CliError(f"bad duration '{s}' (use 30m, 2h, 1d)", 2)
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]

def parse_when(s):
    """'2h' (ago) | 'HH:MM[:SS]' today | ISO 8601 date or datetime (naive = display timezone) -> epoch seconds."""
    s = str(s).strip()
    if AGE_RE.fullmatch(s): return time.time() - parse_age(s)
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        try:
            d = datetime.now(timezone.utc).astimezone(TZ).replace(hour=int(m.group(1)), minute=int(m.group(2)),
                                                                   second=int(m.group(3) or 0), microsecond=0)
            return d.timestamp()
        except ValueError: pass
    else:
        t = parse_iso(s, default_tz=TZ)
        if t is not None: return t
    raise CliError(f"bad time '{s}' (use 30m, 2h, 1d, HH:MM or ISO 8601 like 2026-10-01T14:00)", 2)

WS_RE = re.compile(r"\s+")

def short(s, n):
    s = str(s or "")
    # whitespace collapsing is linear in len(s); cap the work for huge tool outputs
    cap = max(n * 8, 4096)
    head = s[:cap]
    out = WS_RE.sub(" ", head).strip()
    if len(s) > cap and len(out) < n: out = WS_RE.sub(" ", s).strip()
    elif len(s) > cap: out += "…"
    return out if len(out) <= n else out[: n - 1] + "…"

def short_id(sid):
    """Prefix to show and type back. OpenCode ids (ses_<time-sortable>) share their first 8 chars, so keep more."""
    sid = str(sid)
    return sid[:14] if sid.startswith("ses_") else sid[:8]

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

def open_text(path):
    # explicit utf-8: the locale encoding may be ASCII/latin-1 (LANG=C, Windows); -sig drops a leading BOM
    return open(path, encoding="utf-8-sig", errors="replace")

def read_lines(path, first_only=False):
    """JSONL lines. Split on "\\n" only: str.splitlines() also breaks on U+2028/U+2029/\\x1c.., which JSON
    writers (JSON.stringify, ensure_ascii=False) leave raw inside strings."""
    if path.endswith(".zst"):
        dec = zst_decompressor()
        if not dec: return []
        try: lines = dec(path).decode("utf-8", "replace").split("\n")
        except Exception: return []
        return lines[:1] if first_only else lines
    with open_text(path) as f:
        return [f.readline()] if first_only else f.read().split("\n")

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
        with open_text(path) as f: return json.load(f)
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
    name = name or "?"
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

def tool_ev(t, name, inp, mid=None, cid=None):
    if isinstance(inp, str):
        try: inp = json.loads(inp)
        except Exception: pass
    name = str(name) if name else "?"
    return E(t, "tool_call", tool_oneliner(name, inp), tool=name, paths=edit_paths(name, inp), mid=mid, cid=cid)

def result_text(c):
    if isinstance(c, list):
        return "\n".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
    if isinstance(c, dict): return str(c.get("text") or c.get("output") or json.dumps(c, ensure_ascii=False))
    return str(c if c is not None else "")

def is_injected(text):
    t = str(text or "").lstrip()
    return t.startswith(("<system-reminder>", "<environment_context", "<user_instructions", "# AGENTS.md"))

def blocks_text(content):
    if isinstance(content, str): return content
    if isinstance(content, list):
        return "\n".join(str(x.get("text") or "") for x in content if isinstance(x, dict) and x.get("type") in (None, "text", "input_text", "output_text"))
    return ""

# ---------------- cache ----------------
CACHE_VERSION = 1  # bump when the cached fields or the parsers' output change
FRESH_S = 2  # files written this recently are always re-read: a same-size rewrite within one mtime tick is invisible

def cache_dir():
    v = os.environ.get("AGENT_LOG_CACHE_DIR", "").strip()
    if v: return os.path.expanduser(v)
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "agent-log")

def code_tag():
    try: st = os.stat(os.path.abspath(__file__)); return [CACHE_VERSION, __version__, st.st_size, st.st_mtime_ns]
    except Exception: return [CACHE_VERSION, __version__]

class Cache:
    """Per-file session meta (id, cwd) and list brief, keyed by (size, mtime_ns), so entries are never stale.
    A Cache(None) is a no-op. Every failure (unreadable, corrupt, unwritable) degrades to "no cache"."""
    def __init__(self, dirpath=None):
        self.dir = dirpath
        self.file = os.path.join(dirpath, "sessions.json") if dirpath else None
        self.entries = None; self.dirty = False
        self.scanned, self.seen = set(), set()  # provider names discovered this run, and the paths they returned
    def _data(self):
        if self.entries is None:
            self.entries = {}
            if self.file:
                try:
                    with open(self.file, encoding="utf-8") as f: d = json.load(f)
                    if isinstance(d, dict) and d.get("version") == code_tag() and isinstance(d.get("entries"), dict):
                        self.entries = d["entries"]
                except Exception: pass
        return self.entries
    def usable(self, s):
        return bool(self.file) and s.key is not None and s.mtime is not None and time.time() - s.mtime >= FRESH_S
    def get(self, s):
        if not self.usable(s): return None
        e = self._data().get(s.path)
        return e if isinstance(e, dict) and e.get("key") == list(s.key) else None
    def put(self, s, **fields):
        if not self.usable(s): return
        e = self.get(s) or {}
        e.update(key=list(s.key), src=s.kind, id=s._id, cwd=s._cwd, **fields)
        self._data()[s.path] = e; self.dirty = True
    def save(self):
        if not (self.file and self.dirty): return
        ent = {p: e for p, e in self._data().items()
               if not (isinstance(e, dict) and e.get("src") in self.scanned and p not in self.seen)}
        tmp = None
        try:
            os.makedirs(self.dir, mode=0o700, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".sessions-", suffix=".tmp")  # 0600: holds prompts
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": code_tag(), "entries": ent}, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.file); tmp = None
            self.dirty = False
        except Exception: pass
        finally:
            if tmp:
                try: os.unlink(tmp)
                except OSError: pass

CACHE = Cache(None)

# ---------------- sessions & providers ----------------
class Sess:
    """One session. id and cwd are read lazily (from the cache or the head of the file), so callers can sort
    by mtime and stop early. A session whose meta cannot be read has bad=True and is skipped by callers."""
    def __init__(self, prov, path, sid=None, cwd=None, mtime=None, key=None):
        self.prov, self.kind, self.path, self.mtime, self.key = prov, prov.name, path, mtime, key
        self._id, self._cwd, self._has_meta = (str(sid) if sid is not None else None), cwd, sid is not None
        self.bad, self.err = False, None
        self.running, self.why = False, ""
        self._events = self._brief = None
    @classmethod
    def stub(cls, prov, path):
        st = os.stat(path)
        return cls(prov, path, mtime=st.st_mtime, key=(st.st_size, st.st_mtime_ns) if prov.cacheable else None)
    def _meta(self):
        if self._has_meta: return
        self._has_meta = True
        e = CACHE.get(self)
        if e is not None and e.get("id"):
            self._id, self._cwd = str(e["id"]), e.get("cwd"); return
        try: sid, cwd = self.prov.meta(self.path)
        except Exception as x:
            self.bad, self.err, self._id = True, x, os.path.basename(self.path); return
        self._id, self._cwd = str(sid), (cwd if isinstance(cwd, str) and cwd else None)
        CACHE.put(self)
    @property
    def id(self): self._meta(); return self._id
    @property
    def cwd(self): self._meta(); return self._cwd
    @property
    def ok(self): self._meta(); return not self.bad
    def events(self):
        if self._events is None: self._events = load(self)
        return self._events
    def brief(self):
        """Fields `list` needs, from the cache when the file is unchanged."""
        if self._brief is None:
            self._meta()
            e = CACHE.get(self); b = e.get("brief") if e else None
            if not isinstance(b, dict) or set(b) != BRIEF_KEYS:
                b = brief_of(self.events())
                CACHE.put(self, brief=b)
            self._brief = b
        return self._brief

class Provider:
    name = ""
    procs = ()  # process basenames that mean "this agent is running"
    cacheable = True  # one session per file, and (size, mtime) changes whenever its content does
    def roots(self): return []
    def discover(self): return []
    def meta(self, path):
        """(session_id, cwd) from the head of the file; raises if it is not a session."""
        raise NotImplementedError
    def peek(self, path):
        s = Sess.stub(self, path); s._meta()
        if s.bad: raise s.err
        return s
    def events(self, path): return []
    def stubs(self):
        """Sessions with mtime only; id/cwd load on first use."""
        out = []
        for p in self.discover():
            try: out.append(Sess.stub(self, p))
            except OSError: pass
        return out
    def sessions(self):
        return [s for s in self.stubs() if s.ok]
    def fingerprint(self, path):
        st = os.stat(path); return (st.st_size, st.st_mtime_ns)
    def parse(self, path):
        """Normalized session: dict(id, cwd, source, started, updated, events)."""
        s = self.peek(path); ev = load(s); _, _, t0, _ = stats(ev)
        return dict(id=s.id, cwd=s.cwd, source=self.name, started=t0, updated=s.mtime, events=ev)

def lossy_cwd(dirname):
    return dirname.replace("-", "/")

def each(records, fn):
    """fn(record) for every record; one with unexpected types is skipped instead of failing the whole session."""
    for d in records:
        try: fn(d)
        except (AttributeError, TypeError, KeyError, ValueError, IndexError): continue

def text_of(x, key="text"):
    v = x.get(key)
    return v if isinstance(v, str) else ""

def dig(d, *keys):
    """d[k1][k2]..., or None when a level is missing or not a dict."""
    for k in keys:
        if not isinstance(d, dict): return None
        d = d.get(k)
    return d

class Droid(Provider):
    name, procs = "droid", ("droid",)
    def root(self): return os.path.join(base_dir("droid", None, ".factory"), "sessions")
    def roots(self): return [self.root()]
    def discover(self): return glob.glob(os.path.join(self.root(), "*", "*.jsonl"))
    def meta(self, path):
        cwd = first_json(path).get("cwd")
        return os.path.basename(path)[:-6], cwd if isinstance(cwd, str) and cwd else lossy_cwd(os.path.basename(os.path.dirname(path)))
    def events(self, path):
        ev, names = [], {}
        def rec(d):
            t = parse_ts(d.get("timestamp")); ty = d.get("type")
            if ty == "compaction_state":
                ev.append(E(t, "compact", d.get("summaryText", ""))); return
            if ty != "message": return
            m = d.get("message") or {}; role = m.get("role"); content = m.get("content")
            if isinstance(content, str): content = [{"type": "text", "text": content}]
            for x in content or []:
                if not isinstance(x, dict): continue
                xt = x.get("type")
                if role == "user" and xt == "text":
                    if not is_injected(text_of(x)): ev.append(E(t, "user", text_of(x)))
                elif role == "user" and xt == "tool_result":
                    ev.append(E(t, "tool_result", result_text(x.get("content")), tool=names.get(x.get("tool_use_id"), "?"), err=bool(x.get("is_error")), cid=x.get("tool_use_id")))
                elif role == "assistant" and xt == "text":
                    ev.append(E(t, "say", text_of(x)))
                elif role == "assistant" and xt == "thinking":
                    ev.append(E(t, "think", text_of(x, "thinking")))
                elif role == "assistant" and xt == "tool_use":
                    names[x.get("id")] = x.get("name")
                    ev.append(tool_ev(t, x.get("name"), x.get("input") or {}, cid=x.get("id")))
        each(iter_jsonl(read_lines(path)), rec)
        return ev

class Codex(Provider):
    name, procs = "codex", ("codex",)
    ID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f-]{27})\.jsonl(?:\.zst)?$")
    EXIT_RE = re.compile(r"exit code:? [1-9]|Process exited with code [1-9]|\"exit_code\":\s*[1-9]")
    def root(self): return base_dir("codex", "CODEX_HOME", ".codex")
    def roots(self): return [os.path.join(self.root(), "sessions"), os.path.join(self.root(), "archived_sessions")]
    def discover(self):
        pats = ["*.jsonl"] + (["*.jsonl.zst"] if zst_decompressor() else [])
        return [p for sub in self.roots() for pat in pats for p in glob.glob(os.path.join(sub, "**", pat), recursive=True)]
    def meta(self, path):
        m = self.ID_RE.search(path)
        sid = m.group(1) if m else re.sub(r"\.jsonl(\.zst)?$", "", os.path.basename(path))
        if path.endswith(".zst") and not read_lines(path, first_only=True): raise ValueError("cannot decompress")
        p = first_json(path).get("payload")
        cwd = p.get("cwd") if isinstance(p, dict) else None
        return sid, cwd
    def events(self, path):
        ev, names = [], {}
        def rec(d):
            t = parse_ts(d.get("timestamp")); p = d.get("payload") or {}
            if not isinstance(p, dict): return
            ty, pt = d.get("type"), p.get("type")
            if ty == "response_item" and pt == "message":
                txt = "".join(text_of(c) for c in p.get("content") or [] if isinstance(c, dict))
                if p.get("role") == "user" and txt.strip() and not is_injected(txt): ev.append(E(t, "user", txt))
                elif p.get("role") == "assistant" and txt.strip(): ev.append(E(t, "say", txt))
            elif ty == "response_item" and pt == "reasoning":
                s = " ".join(text_of(x) for x in p.get("summary") or [] if isinstance(x, dict))
                if s.strip(): ev.append(E(t, "think", s))
            elif ty == "response_item" and pt in ("function_call", "custom_tool_call", "local_shell_call"):
                name = p.get("name", pt); names[p.get("call_id")] = name
                raw = p.get("input") if "input" in p else p.get("arguments", p.get("action"))
                inp = raw
                if isinstance(raw, str):
                    try: inp = json.loads(raw)
                    except Exception:
                        m = re.search(r'cmd:\s*"((?:[^"\\]|\\.)*)"', raw)
                        try: inp = {"command": json.loads('"' + m.group(1) + '"')} if m else raw
                        except ValueError: inp = raw
                ev.append(tool_ev(t, name, inp, cid=p.get("call_id")))
            elif ty == "response_item" and pt in ("function_call_output", "custom_tool_call_output"):
                txt = result_text(p.get("output"))
                err = bool(self.EXIT_RE.search(txt[:2000]))
                ev.append(E(t, "tool_result", txt, tool=names.get(p.get("call_id"), "?"), err=err, cid=p.get("call_id")))
            elif ty == "event_msg" and pt == "task_complete":
                ev.append(E(t, "final", p.get("last_agent_message") or ""))
            elif ty == "event_msg" and pt == "error":
                ev.append(E(t, "error", p.get("message") or "error"))
            elif ty == "event_msg" and pt == "turn_aborted":
                ev.append(E(t, "error", f"turn aborted ({p.get('reason', '?')})"))
            elif ty == "compacted":
                ev.append(E(t, "compact", p.get("message") or ""))
        each(iter_jsonl(read_lines(path)), rec)
        return ev

class Claude(Provider):
    name, procs = "claude", ("claude",)
    def root(self): return os.path.join(base_dir("claude", "CLAUDE_CONFIG_DIR", ".claude"), "projects")
    def roots(self): return [self.root()]
    def discover(self): return glob.glob(os.path.join(self.root(), "*", "*.jsonl"))
    def meta(self, path):
        cwd = None
        with open_text(path) as f:
            for i, line in enumerate(f):
                if i > 50: break
                try: d = json.loads(line)
                except Exception: continue
                if isinstance(d, dict) and isinstance(d.get("cwd"), str) and d["cwd"]: cwd = d["cwd"]; break
        return os.path.basename(path)[:-6], cwd or lossy_cwd(os.path.basename(os.path.dirname(path)))
    @staticmethod
    def user_text(t):
        """Prompt text, or None for harness-injected scaffolding."""
        if not isinstance(t, str): return None
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
        def rec(d):
            if d.get("isSidechain"): return
            t = parse_ts(d.get("timestamp")); ty = d.get("type")
            if ty == "system":
                if d.get("subtype") == "compact_boundary": ev.append(E(t, "compact", d.get("content") or ""))
                return
            if ty not in ("user", "assistant"): return
            m = d.get("message") or {}; content = m.get("content"); mid = m.get("id")
            if isinstance(content, str): content = [{"type": "text", "text": content}]
            if ty == "user":
                if d.get("isCompactSummary"):
                    # follows its compact_boundary record; one compaction, not two
                    if ev and ev[-1]["k"] == "compact" and not ev[-1].get("filled"):
                        ev[-1].update(text=blocks_text(content), filled=True)
                    else: ev.append(E(t, "compact", blocks_text(content), filled=True))
                    return
                if d.get("isMeta"): return
                for x in content or []:
                    if not isinstance(x, dict): continue
                    if x.get("type") == "text":
                        txt = self.user_text(x.get("text"))
                        if txt is None: continue
                        ev.append(E(t, "error" if txt.startswith("[Request interrupted") else "user", txt))
                    elif x.get("type") == "tool_result":
                        ev.append(E(t, "tool_result", result_text(x.get("content")), tool=names.get(x.get("tool_use_id"), "?"), err=bool(x.get("is_error")), cid=x.get("tool_use_id")))
                return
            if d.get("isApiErrorMessage"):
                ev.append(E(t, "error", blocks_text(content))); return
            for x in content or []:
                if not isinstance(x, dict): continue
                xt = x.get("type")
                if xt == "text" and text_of(x).strip(): ev.append(E(t, "say", x["text"], mid=mid))
                elif xt == "thinking" and text_of(x, "thinking"): ev.append(E(t, "think", x["thinking"], mid=mid))
                elif xt == "tool_use":
                    names[x.get("id")] = x.get("name")
                    ev.append(tool_ev(t, x.get("name"), x.get("input") or {}, mid=mid, cid=x.get("id")))
        each(iter_jsonl(read_lines(path)), rec)
        return ev

class Gemini(Provider):
    """chats/session-*.json (one JSON document) or, Gemini CLI >= ~0.60, chats/session-*.jsonl:
    a metadata header line, then message records upserted by id (last write wins) and {"$set": ...} patches."""
    name, procs = "gemini", ("gemini",)
    def root(self): return os.path.join(base_dir("gemini", None, ".gemini"), "tmp")
    def roots(self): return [self.root()]
    def discover(self):
        return [p for ext in ("json", "jsonl") for p in glob.glob(os.path.join(self.root(), "*", "chats", f"session-*.{ext}"))]
    @staticmethod
    def doc(path):
        """(meta, messages) for either layout."""
        if not path.endswith(".jsonl"):
            d = read_json(path)
            if not isinstance(d, dict) or not isinstance(d.get("messages"), list): raise ValueError("not a gemini chat")
            return d, d["messages"]
        meta, order, by_id = {}, [], {}
        for d in iter_jsonl(read_lines(path)):
            if "$set" in d:
                if isinstance(d["$set"], dict): meta.update(d["$set"])
                continue
            if "type" not in d and ("sessionId" in d or "projectHash" in d):
                meta.update(d); continue
            mid = d.get("id") or f"_{len(order)}"
            if mid not in by_id: order.append(mid)
            by_id[mid] = d
        if not meta and not order: raise ValueError("not a gemini chat")
        return meta, [by_id[i] for i in order]
    def meta(self, path):
        try: meta, _ = self.doc(path)
        except ValueError:
            # the CLI rewrites session-*.json in place, so a reader can catch it empty or half-written;
            # keep listing it under the id fragment from its name instead of letting the session vanish
            if path.endswith(".jsonl") or read_json(path) is not None: raise
            meta = {}
        cwd = None
        try:
            with open_text(os.path.join(os.path.dirname(os.path.dirname(path)), ".project_root")) as f: cwd = f.read().strip() or None
        except Exception: pass
        base = os.path.basename(path)
        sid = meta.get("sessionId") or re.sub(r"^session-(?:\d{4}-\d\d-\d\dT\d\d-\d\d-)?|\.jsonl?$", "", base)
        return sid, cwd
    @staticmethod
    def response(r):
        if not isinstance(r, dict): return str(r)
        resp = (r.get("functionResponse") or {}).get("response")
        if not isinstance(resp, dict): return str(resp or "")
        for k in ("output", "error"):
            if isinstance(resp.get(k), str) and resp[k]: return resp[k]
        return json.dumps(resp, ensure_ascii=False)
    EXIT_RE = re.compile(r"^Exit Code: ([1-9]\d*)", re.M)
    @staticmethod
    def clean(out):
        # run_shell_command output is wrapped as <untrusted_context>Output: ...</untrusted_context> in newer CLIs
        out = re.sub(r"^\s*<untrusted_context>\s*|\s*</untrusted_context>\s*$", "", out)
        out = re.sub(r"\n?Process Group PGID: \d+\s*$", "", out)
        return re.sub(r"^Output: ", "", out)
    def events(self, path):
        ev = []
        def rec(m):
            if not isinstance(m, dict): return
            t = parse_ts(m.get("timestamp")); ty = m.get("type"); mid = m.get("id")
            c = m.get("content")
            txt = c if isinstance(c, str) else "\n".join(text_of(x) for x in c or [] if isinstance(x, dict) and text_of(x))
            if ty == "user":
                # functionResponse-only records are tool results (already taken from toolCalls); <session_context> is injected
                if txt.strip() and not txt.lstrip().startswith("<session_context>"): ev.append(E(t, "user", txt))
            elif ty == "gemini":
                for th in m.get("thoughts") or []:
                    if isinstance(th, dict): ev.append(E(t, "think", ": ".join(x for x in (th.get("subject"), th.get("description")) if x), mid=mid))
                if txt.strip(): ev.append(E(t, "say", txt, mid=mid))
                results = []
                for tc in m.get("toolCalls") or []:
                    if not isinstance(tc, dict): continue
                    name = tc.get("name"); cid = tc.get("id") or f"{mid}:{len(results)}"
                    ev.append(tool_ev(t, name, tc.get("args") or {}, mid=mid, cid=cid))
                    rt = parse_ts(tc.get("timestamp")) or t
                    out = self.clean("\n".join(self.response(r) for r in tc.get("result") or []))
                    if tc.get("status") in ("error", "cancelled"): results.append(E(rt, "error", out or tc.get("status"), tool=name, cid=cid))
                    else: results.append(E(rt, "tool_result", out, tool=name, err=bool(self.EXIT_RE.search(out)), cid=cid))
                ev.extend(results)
            elif ty == "error":
                ev.append(E(t, "error", txt))
            elif ty == "info" and txt.strip().lower().startswith(("request cancelled", "operation cancelled")):
                ev.append(E(t, "error", txt))
        each(self.doc(path)[1], rec)
        return ev

class OpenCode(Provider):
    """SQLite store (opencode.db, OpenCode >= 1.x) plus the older storage/ JSON tree."""
    name, procs = "opencode", ("opencode",)
    # DB rows change without a reliable per-session file fingerprint, and legacy parts live in other files
    cacheable = False
    def root(self):
        xdg = os.environ.get("XDG_DATA_HOME", "").strip()
        if xdg and not os.environ.get("AGENT_LOG_HOME_OPENCODE"): return os.path.join(xdg, "opencode")
        return base_dir("opencode", None, ".local", "share", "opencode")
    def roots(self): return [self.root()]
    def connect(self, db):
        """(conn, schema): "v1" = session/message/part tables (OpenCode 1.x), "v2" = session_v2/session_message."""
        for q in ("mode=ro", "immutable=1"):
            c = None
            try:
                c = sqlite3.connect(f"file:{urllib.parse.quote(db)}?{q}", uri=True, timeout=2)
                tabs = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "session_v2" in tabs and "session_message" in tabs:
                    # some builds carry both; prefer whichever actually holds messages
                    if not ({"session", "message", "part"} <= tabs) or c.execute("SELECT 1 FROM session_message LIMIT 1").fetchone():
                        return c, "v2"
                if {"session", "message", "part"} <= tabs: return c, "v1"
            except sqlite3.Error: pass
            if c is not None: c.close()
        return None, None
    SESS_SQL = {
        "v2": ("SELECT s.id, s.directory, s.time_updated, "
               "(SELECT IFNULL(max(m.time_updated), 0) FROM session_message m WHERE m.session_id = s.id) FROM session_v2 s "),
        "v1": ("SELECT s.id, s.directory, s.time_updated, "
               "(SELECT IFNULL(max(p.time_updated), 0) FROM part p WHERE p.session_id = s.id) FROM session s "),
    }
    def db_sessions(self, db, sid=None):
        c, schema = self.connect(db)
        if not c: return []
        try:
            rows = c.execute(self.SESS_SQL[schema] + ("WHERE s.id = ?" if sid else "WHERE s.parent_id IS NULL"), (sid,) if sid else ()).fetchall()
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
    def stubs(self):
        out = [s for db in sorted(glob.glob(os.path.join(self.root(), "opencode*.db"))) for s in self.db_sessions(db)]
        for p in self.discover():  # legacy mtime needs the session JSON anyway, so read it now
            try: out.append(self.peek(p))
            except Exception: pass
        return out
    def peek(self, path):
        if ".db#" in path:
            db, _, sid = path.rpartition("#")
            ss = self.db_sessions(db, sid)
            if not ss: raise ValueError(f"no opencode session {sid}")
            return ss[0]
        d = read_json(path) or {}
        mdir = os.path.join(self.legacy_dir(), "message", d["id"])
        mt = max([os.path.getmtime(path), ms(dig(d, "time", "updated")) or 0] + ([os.path.getmtime(mdir)] if os.path.isdir(mdir) else []))
        return Sess(self, path, d["id"], d.get("directory") if isinstance(d.get("directory"), str) else None, mt)
    def fingerprint(self, path): return self.peek(path).mtime
    @staticmethod
    def tool_parts(t, name, st, rt, mid, cid=None):
        st = st if isinstance(st, dict) else {}
        t = ms(dig(st, "time", "start")) or t
        call = tool_ev(t, name, st.get("input"), mid=mid, cid=cid)
        meta = st.get("metadata") if isinstance(st.get("metadata"), dict) else {}
        if st.get("status") == "error":
            e = st.get("error"); msg = e.get("message") if isinstance(e, dict) else e
            return call, E(rt, "error", msg or "error", tool=name, cid=cid)
        if st.get("status") == "completed":
            out = st["output"] if "output" in st else result_text(st.get("content"))
            ex = meta.get("exit")
            return call, E(rt, "tool_result", result_text(out), tool=name, err=(isinstance(ex, int) and ex != 0) or meta.get("error") is True, cid=cid)
        return call, None
    def events(self, path):
        return self.db_events(path) if ".db#" in path else self.legacy_events(path)
    def db_events(self, path):
        db, _, sid = path.rpartition("#"); c, schema = self.connect(db)
        if not c: return []
        if schema == "v1":
            try:
                msgs = c.execute("SELECT id, data, time_created FROM message WHERE session_id = ? ORDER BY time_created, id", (sid,)).fetchall()
                parts = c.execute("SELECT message_id, id, data FROM part WHERE session_id = ? ORDER BY id", (sid,)).fetchall()
            finally: c.close()
            return self.v1_events(msgs, parts)
        try: rows = c.execute("SELECT id, type, data, time_created FROM session_message WHERE session_id = ? ORDER BY seq", (sid,)).fetchall()
        finally: c.close()
        ev = []
        def rec(row):
            mid, ty, data, tc = row
            d = self.jl(data)
            if d is None: return
            t = ms(dig(d, "time", "created")) or ms(tc)
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
                        rt = ms(dig(part, "time", "completed")) or t
                        call, res = self.tool_parts(t, part.get("name"), part.get("state"), rt, mid, part.get("callID") or part.get("id"))
                        ev.append(call)
                        if res: results.append(res)
                ev.extend(results)
                if d.get("error"):
                    e = d["error"]; ev.append(E(t, "error", e.get("message") if isinstance(e, dict) else e))
            elif ty == "compaction":
                if d.get("status") == "completed": ev.append(E(t, "compact", d.get("summary") or ""))
                elif d.get("status") == "failed": ev.append(E(t, "error", f"compaction failed: {d.get('error') or ''}"))
        each(rows, rec)
        return ev
    @staticmethod
    def jl(x):
        try: d = json.loads(x) if isinstance(x, str) else None
        except Exception: return None
        return d if isinstance(d, dict) else None
    def v1_events(self, msg_rows, part_rows):
        """OpenCode 1.x DB: message/part rows whose data JSON has the same shape as the old storage/ files
        (ids live in the columns, not in data)."""
        msgs = []
        for mid, data, tc in msg_rows:
            d = self.jl(data)
            if d is None: continue
            d.setdefault("id", mid)
            if not isinstance(d.get("time"), dict): d["time"] = {}
            d["time"].setdefault("created", tc); msgs.append(d)
        parts = {}
        for mid, pid, data in part_rows:
            d = self.jl(data)
            if d is None: continue
            d.setdefault("id", pid); parts.setdefault(mid, []).append(d)
        return self.msg_events(msgs, parts)
    def legacy_events(self, path):
        info = read_json(path) or {}; st = self.legacy_dir()
        msgs = [m for m in (read_json(p) for p in glob.glob(os.path.join(st, "message", info.get("id", ""), "*.json"))) if isinstance(m, dict)]
        parts = {m.get("id"): [p for p in (read_json(x) for x in glob.glob(os.path.join(st, "part", m.get("id") or "", "*.json"))) if isinstance(p, dict)] for m in msgs}
        return self.msg_events(msgs, parts)
    def msg_events(self, msgs, parts_by_msg):
        ev = []
        msgs.sort(key=lambda m: (ms(dig(m, "time", "created")) or 0, str(m.get("id", ""))))
        def rec(m):
            t = ms(dig(m, "time", "created")); mid = m.get("id")
            parts = sorted(parts_by_msg.get(mid) or [], key=lambda p: str(p.get("id", "")))
            texts = [text_of(p) for p in parts if p.get("type") == "text" and not p.get("synthetic")]
            if m.get("role") == "user":
                txt = "\n".join(texts)
                if txt.strip(): ev.append(E(t, "user", txt))
                return
            if m.get("role") != "assistant": return
            if m.get("summary") is True:
                ev.append(E(t, "compact", "\n".join(texts))); return
            results = []
            for p in parts:
                pt = p.get("type")
                pts = ms(dig(p, "time", "start")) or t
                if pt == "text" and text_of(p).strip(): ev.append(E(pts, "say", p["text"], mid=mid))
                elif pt == "reasoning" and text_of(p).strip(): ev.append(E(pts, "think", p["text"], mid=mid))
                elif pt == "tool":
                    s = p.get("state") if isinstance(p.get("state"), dict) else {}
                    rt = ms(dig(s, "time", "end")) or t
                    call, res = self.tool_parts(t, p.get("tool"), s, rt, mid, p.get("callID") or p.get("id"))
                    ev.append(call)
                    if res: results.append(res)
            ev.extend(results)
            if m.get("error"):
                e = m["error"]
                msg = (dig(e, "data", "message") or e.get("name")) if isinstance(e, dict) else e
                ev.append(E(t, "error", msg or "error"))
        each(msgs, rec)
        return ev

class Pi(Provider):
    name, procs = "pi", ("pi",)
    def root(self):
        flat = os.environ.get("PI_CODING_AGENT_SESSION_DIR", "").strip()
        if flat and not os.environ.get("AGENT_LOG_HOME_PI"): return os.path.expanduser(flat)
        return os.path.join(base_dir("pi", "PI_CODING_AGENT_DIR", ".pi", "agent"), "sessions")
    def roots(self): return [self.root()]
    def discover(self):
        r = self.root()
        return glob.glob(os.path.join(r, "*.jsonl")) + glob.glob(os.path.join(r, "*", "*.jsonl"))
    def meta(self, path):
        h = first_json(path)
        if h.get("type") != "session": h = {}
        sid = h.get("id") or os.path.basename(path)[:-6].split("_", 1)[-1]
        cwd = h.get("cwd") if isinstance(h.get("cwd"), str) else None
        if not cwd:
            dn = os.path.basename(os.path.dirname(path))
            cwd = "/" + dn.strip("-").replace("-", "/") if dn.startswith("--") else None
        return sid, cwd
    @staticmethod
    def active_path(entries):
        """Entries on the branch ending at the last-written leaf (pi stores a tree via parentId)."""
        body = [e for e in entries if e.get("type") != "session"]
        if not any("parentId" in e for e in body): return body
        by_id = {e["id"]: e for e in body if isinstance(e.get("id"), str)}
        leaf = next((e for e in reversed(body) if e.get("id")), None)
        out, seen = [], set()
        while leaf is not None and leaf.get("id") not in seen:
            seen.add(leaf.get("id")); out.append(leaf); leaf = by_id.get(leaf.get("parentId"))
        return out[::-1]
    def events(self, path):
        ev = []
        def rec(d):
            t = parse_ts(d.get("timestamp")); ty = d.get("type")
            if ty == "compaction":
                ev.append(E(t, "compact", d.get("summary") or "")); return
            if ty != "message": return
            m = d.get("message") or {}; role = m.get("role"); mid = d.get("id")
            if role == "user":
                txt = blocks_text(m.get("content"))
                if txt.strip(): ev.append(E(t, "user", txt))
            elif role == "assistant":
                for x in m.get("content") or []:
                    if not isinstance(x, dict): continue
                    xt = x.get("type")
                    if xt == "text" and text_of(x).strip(): ev.append(E(t, "say", x["text"], mid=mid))
                    elif xt == "thinking" and text_of(x, "thinking"): ev.append(E(t, "think", x["thinking"], mid=mid))
                    elif xt == "toolCall": ev.append(tool_ev(t, x.get("name"), x.get("arguments") or {}, mid=mid, cid=x.get("id")))
                if m.get("stopReason") in ("error", "aborted"):
                    ev.append(E(t, "error", m.get("errorMessage") or m["stopReason"]))
            elif role == "toolResult":
                ev.append(E(t, "tool_result", result_text(m.get("content")), tool=m.get("toolName") or "?", err=bool(m.get("isError")), cid=m.get("toolCallId")))
            elif role == "bashExecution":
                ev.append(E(t, "user", f"! {m.get('command', '')}"))
        each(self.active_path(list(iter_jsonl(read_lines(path)))), rec)
        return ev

PROVIDERS = {p.name: p for p in (Droid(), Codex(), Claude(), Gemini(), OpenCode(), Pi())}

def discover(names=None):
    """Sessions of the given providers, newest first. File-based ones are stubs: id/cwd load on first use."""
    out = []
    for p in PROVIDERS.values():
        if names and p.name not in names: continue
        try: ss = p.stubs()
        except Exception: continue
        CACHE.scanned.add(p.name); CACHE.seen.update(s.path for s in ss)
        out.extend(ss)
    out.sort(key=lambda s: (s.mtime or 0, s.path), reverse=True)
    return out

def describe_roots(names):
    """'droid: /x (missing), ...' for error messages."""
    bits = []
    for n in names:
        for r in PROVIDERS[n].roots():
            bits.append(f"{n}: {r}" + ("" if os.path.isdir(r) else " (missing)"))
    return "; ".join(bits)

def sniff(path):
    """Guess the provider of a session file given directly on the command line."""
    if ".db#" in path: return PROVIDERS["opencode"]
    for name, marker in (("codex", "/.codex/"), ("claude", "/.claude/"), ("gemini", "/.gemini/"), ("pi", "/.pi/"), ("droid", "/.factory/"), ("opencode", "/opencode/")):
        if marker in path: return PROVIDERS[name]
    if path.endswith(".json"):
        d = read_json(path)
        return PROVIDERS["gemini" if isinstance(d, dict) and "messages" in d else "opencode"]
    if os.path.basename(path).startswith("session-") and path.endswith(".jsonl"): return PROVIDERS["gemini"]
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
    if not argv: return None
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

def ps_rows():
    """[(pid, argv)] from `ps` (macOS/BSD have no /proc), or None when ps is unavailable.
    ps joins argv with spaces, so arguments that contain spaces come back split."""
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "command="], capture_output=True, text=True, timeout=5)
    except Exception: return None
    if r.returncode != 0: return None
    rows = []
    for line in r.stdout.splitlines():
        bits = line.split()
        if len(bits) >= 2 and bits[0].isdigit(): rows.append((bits[0], bits[1:]))
    return rows

def lsof_cwds(pids):
    """{pid: cwd} via lsof for processes without /proc/<pid>/cwd; {} when lsof is missing or fails."""
    if not pids or not shutil.which("lsof"): return {}
    try:
        r = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fpn", "-p", ",".join(pids)], capture_output=True, text=True, timeout=5)
    except Exception: return {}
    out, pid = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("p"): pid = line[1:]
        elif line.startswith("n") and pid: out[pid] = line[1:]
    return out

def live_procs():
    """([(kind, cwd, session_id, cmdline)], available) for running agent processes.
    Reads $AGENT_LOG_PROC (tests) or /proc, else `ps` + `lsof`. available is False when no process
    table could be read at all; callers then fall back to the mtime heuristic alone."""
    root = os.environ.get("AGENT_LOG_PROC")
    if not root and os.path.isdir(os.path.join(PROC_ROOT, "self")): root = PROC_ROOT
    found = []  # (pid, argv, kind)
    if root:
        try: pids = os.listdir(root)
        except OSError: return [], False
        for pid in pids:
            if not pid.isdigit(): continue
            try:
                with open(os.path.join(root, pid, "cmdline"), "rb") as f:
                    argv = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
            except OSError: continue
            kind = proc_kind(argv)
            if kind: found.append((pid, argv, kind))
    else:
        rows = ps_rows()
        if rows is None: return [], False
        found = [(pid, argv, proc_kind(argv)) for pid, argv in rows]
        found = [f for f in found if f[2]]
    res, need_cwd = [], []
    for pid, argv, kind in found:
        cwd = None
        for i, a in enumerate(argv):
            if a == "--cwd" and i + 1 < len(argv): cwd = argv[i + 1]
            elif a.startswith("--cwd="): cwd = a[6:]
        if not cwd and root:
            try: cwd = os.readlink(os.path.join(root, pid, "cwd"))
            except OSError: pass
        if not cwd and not root: need_cwd.append(pid)
        sid = None
        for i, a in enumerate(argv):
            if a in SID_FLAGS and i + 1 < len(argv) and not argv[i + 1].startswith("-"): sid = argv[i + 1]
        res.append([pid, kind, cwd, sid, " ".join(argv)])
    if need_cwd:
        cwds = lsof_cwds(need_cwd)
        for r in res:
            if not r[2]: r[2] = cwds.get(r[0])
    return [tuple(r[1:]) for r in res], True

def mark_running(sessions):
    """Set running/why on sessions (newest first). See the module docstring for the rules."""
    procs, available = live_procs()
    alive_kinds = {k for k, _, _, _ in procs}
    claimed = set()
    # processes that name their session go first, so a cwd match cannot take a session resumed by id
    for kind, cwd, sid, _ in sorted(procs, key=lambda p: p[2] is None):
        free = lambda s: s.kind == kind and s.path not in claimed and s.ok
        hit, why = None, "proc:session-id"
        if sid: hit = next((s for s in sessions if free(s) and s.id.startswith(sid)), None)
        if hit is None and cwd:
            rc, why = os.path.realpath(cwd), "proc:cwd"
            hit = next((s for s in sessions if free(s) and s.cwd and os.path.realpath(s.cwd) == rc), None)
        if hit is not None:  # the newest unclaimed match
            claimed.add(hit.path)
            hit.running, hit.why = True, why
    now = time.time()
    for s in sessions:
        # recent writes only count while some process of that agent is alive: a finished `claude -p`
        # would otherwise read as running for two minutes after it exited
        if not s.running and s.mtime is not None and now - s.mtime < RECENT_S and (not available or s.kind in alive_kinds):
            s.running, s.why = True, f"mtime<{RECENT_S}s"

def warn(msg): print(f"agent-log: {msg}", file=sys.stderr)

def resolve(ref, sessions):
    """latest | file path | <db>#<id> | id prefix | project substring -> Sess. Raises CliError (exit 1)."""
    if ref in (None, "", "latest"):
        s = next((s for s in sessions if s.ok), None)
        if s is None: raise CliError("no sessions found")
        return s
    if os.path.isfile(ref) or (".db#" in ref and os.path.isfile(ref.rpartition("#")[0])):
        rp = os.path.abspath(ref)
        for s in sessions:
            if os.path.abspath(s.path) == rp and s.ok: return s
        try: return sniff(rp).peek(rp)
        except Exception as x: raise CliError(f"cannot read session file {ref}: {x}") from None
    ok = [s for s in sessions if s.ok]
    hits = [s for s in ok if s.id == ref] or [s for s in ok if s.id.startswith(ref)]
    if len({s.id for s in hits}) > 1:
        warn(f"'{ref}' matches {len(hits)} sessions; using the newest ({short_id(hits[0].id)}). Give more of the id to pick another.")
    if not hits: hits = [s for s in ok if s.cwd and ref.lower() in s.cwd.lower()]
    if not hits: raise CliError(f"no session matches '{ref}' (id prefix, project substring, path or 'latest')")
    return hits[0]

# ---------------- normalization ----------------
def load(s):
    try: ev = s.prov.events(s.path)
    except Exception as x:
        # tool is set so the session reads as "unknown", not "failed": a half-written file is not an agent error
        ev = [E(None, "error", f"failed to parse {s.path}: {x}", tool="agent-log")]
    turn = 0; last_t = None
    raw_t = [e["t"] for e in ev]
    calls = {e["cid"]: e for e in ev if e["k"] == "tool_call" and e.get("cid")}
    for e in ev:
        if e["k"] == "tool_result" and e.get("err"): e["k"] = "error"
        # an edit whose own result is an error did not change the file
        if e["k"] == "error" and e.get("cid") in calls and calls[e["cid"]].get("paths"):
            calls[e["cid"]]["paths"] = []
        if e["t"] is None: e["t"] = last_t
        last_t = e["t"]
        if e["k"] == "user": turn += 1
        e["turn"] = max(turn, 1)
    # blocks from the same assistant message (same timestamp or message id) share one step number;
    # compare the records' own timestamps: inherited (missing) ones say nothing about grouping
    prev = None; step = 0
    for i, e in enumerate(ev):
        if e["k"] in STEP_KINDS:
            same = prev is not None and ev[prev]["k"] in STEP_KINDS and (
                (raw_t[i] is not None and raw_t[prev] == raw_t[i]) or (e.get("mid") and ev[prev].get("mid") == e.get("mid")))
            if not same: step += 1
        e["n"] = step
        prev = i
    return ev

def stats(ev):
    steps = max([e["n"] for e in ev] or [0])
    turns = max([e["turn"] for e in ev] or [0])
    ts = [e["t"] for e in ev if e["t"]]
    return steps, turns, (min(ts) if ts else None), (max(ts) if ts else None)

def files_changed(ev):
    out, seen = [], set()
    for e in ev:
        for p in e.get("paths") or []:
            if p not in seen: seen.add(p); out.append(p)
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

def idle_status(ev):
    """(status, reason) for a session with no live process."""
    tail = [e for e in ev if e["k"] != "think"]
    if not tail: return "unknown", "no events"
    lu = max((i for i, e in enumerate(tail) if e["k"] == "user"), default=-1)
    if any(e["k"] == "final" for e in tail[lu + 1:]): return "done", "final"
    if tail[-1]["k"] == "say": return "done", "ended on assistant text"
    if tail[-1]["k"] == "error" and not tail[-1].get("tool"): return "failed", "ended on agent/API error"
    return "unknown", f"idle, ended on {tail[-1]['k']}"

def status_of(s, ev):
    return ("running", s.why) if s.running else idle_status(ev)

BRIEF_KEYS = {"started", "steps", "turns", "task", "files_changed", "status", "status_reason"}

def brief_of(ev):
    """The event-derived part of a list item (what the cache stores)."""
    steps, turns, t0, _ = stats(ev)
    st, why = idle_status(ev)
    users = [e for e in ev if e["k"] == "user"]
    return dict(started=t0, steps=steps, turns=turns, task=short(users[0]["text"], 600) if users else None,
                files_changed=files_changed(ev), status=st, status_reason=why)

def session_item(s, ev=None, full=True):
    """JSON session object. Every key is always present (null or [] when unknown)."""
    b = brief_of(ev) if ev is not None else s.brief()
    st, why = ("running", s.why) if s.running else (b["status"], b["status_reason"])
    d = dict(schema_version=SCHEMA_VERSION, source=s.kind, session_id=s.id, path=s.path, cwd=s.cwd,
             status=st, status_reason=why, started=iso(b["started"]), updated=iso(s.mtime),
             steps=b["steps"], turns=b["turns"], task=b["task"], files_changed=list(b["files_changed"]))
    if full:
        if ev is None: ev = s.events()
        errs = [e for e in ev if e["k"] == "error"]
        d["error_count"] = len(errs)
        d["errors"] = [dict(ts=iso(e["t"]), tool=e.get("tool"), text=short(e["text"], 300)) for e in errs[-10:]]
        d["last_actions"] = [dict(ts=iso(e["t"]), kind=e["k"], text=short(e["text"], 200)) for e in ev if e["k"] in ("tool_call", "say")][-6:]
        d["final_answer"] = final_answer(s, ev)[0]
    return d

def event_json(e, a):
    lim = None if a.full else a.max_chars * 4
    return {"ts": iso(e["t"]), "kind": e["k"], "tool": e.get("tool") or None,
            "text": clip(e["text"], lim) if e["k"] in ("tool_result", "error", "compact") else e["text"],
            "paths": list(e.get("paths") or []), "step": e["n"], "turn": e["turn"]}

def dump(obj): print(json.dumps(obj, ensure_ascii=False, indent=2))

# ---------------- rendering ----------------
def render(ev, a, out=None):
    out = out or sys.stdout
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
    """a.since / a.until are epoch seconds (main() parses them)."""
    if a.turn: ev = [e for e in ev if e["turn"] == a.turn]
    if a.since is not None: ev = [e for e in ev if e["t"] and e["t"] >= a.since]
    if a.until is not None: ev = [e for e in ev if e["t"] and e["t"] <= a.until]
    if a.last:
        ns = sorted({e["n"] for e in ev if e["k"] in STEP_KINDS})
        if ns:
            cut = ns[-a.last] if len(ns) >= a.last else ns[0]
            ev = [e for e in ev if e["n"] >= cut]
    return ev

def header(s, ev):
    steps, turns, t0, t1 = stats(ev)
    run = f"RUNNING ({s.why})" if s.running else "idle"
    return (f"[{s.kind}] {s.cwd}  {short_id(s.id)}  {run}\n"
            f"  steps {steps}, turns {turns}, {fmt_t(t0, True)} → {fmt_t(t1, True)} ({tz_label()}), elapsed {fmt_dur((t1 or 0) - (t0 or 0))}")

def rel(path, cwd):
    pre = (cwd or "").rstrip("/") + "/"
    return path[len(pre):] if cwd and path.startswith(pre) else path

# ---------------- commands ----------------
def source_names(a):
    names = [a.source] if getattr(a, "source", None) else list(PROVIDERS)
    if getattr(a, "no_codex", False): names = [n for n in names if n != "codex"]
    return names

def in_project(s, a):
    return not a.project or bool(s.cwd and a.project.lower() in s.cwd.lower())

def note_missing(names):
    missing = [f"{n}: {r}" for n in names for r in PROVIDERS[n].roots() if not os.path.isdir(r)]
    if missing: warn("note: not found: " + "; ".join(missing))

def cmd_list(a):
    names = source_names(a)
    ss = discover(names)
    if a.since is not None: ss = [s for s in ss if s.mtime is not None and s.mtime >= a.since]
    mark_running(ss)
    out = []
    for s in ss:  # newest first: stop reading files once -n rows are found
        if a.active and not s.running: continue
        if not s.ok or not in_project(s, a): continue
        out.append(s)
        if a.n and len(out) >= a.n: break
    if not out and not ss: note_missing(names)
    if a.json:
        dump({"schema_version": SCHEMA_VERSION, "sessions": [session_item(s, full=False) for s in out]}); return
    if not out: print("(no sessions)"); return
    idw = max([8] + [len(short_id(s.id)) for s in out])
    print(f"{'last(' + tz_label() + ')':<12} {'kind':<8} {'id':<{idw}} {'steps':>5} {'turns':>5}  {'status':<20} project")
    for s in out:
        b = s.brief()
        st = f"RUN {s.why}" if s.running else "-"
        print(f"{fmt_t(s.mtime, True):<12} {s.kind:<8} {short_id(s.id):<{idw}} {b['steps']:>5} {b['turns']:>5}  {st:<20} {s.cwd}")
    print(dim(f"status: proc:cwd/proc:session-id = live agent process matched (newest session in that cwd); mtime<{RECENT_S}s = file just written"))

def get_session(a):
    names = source_names(a)
    ss = discover(names); mark_running(ss)
    if a.project: ss = [s for s in ss if s.ok and in_project(s, a)]
    if a.session in (None, "", "latest") and not any(s.ok for s in ss):
        where = f" in projects matching '{a.project}'" if a.project else ""
        raise CliError(f"no sessions found{where} ({describe_roots(names)})")
    return resolve(a.session, ss)

def cmd_show(a):
    s = get_session(a); ev = s.events()
    w = window(ev, a); note = None
    if a.default_last and not (a.last or a.since is not None or a.until is not None or a.turn):
        a.last = a.default_last; w = window(ev, a)
        hidden = len({e['n'] for e in ev}) - len({e['n'] for e in w})
        if hidden > 0: note = f"  (showing last {a.last} steps; use --last/--since/--turn/--all for more)"
    if a.json:
        dump({"schema_version": SCHEMA_VERSION, "session": session_item(s, ev, full=False),
              "events": [event_json(e, a) for e in w if not (a.no_thinking and e["k"] == "think")]})
        return
    print(header(s, ev))
    if note: print(dim(note))
    print("-" * 60)
    render(w, a)
    if a.follow:
        sys.stdout.flush()
        try:
            pos = s.prov.fingerprint(s.path); seen = len(ev)
            while True:
                time.sleep(2)
                fp = s.prov.fingerprint(s.path)
                if fp != pos:
                    pos = fp; ev = load(s)
                    render(ev[seen:], a); seen = len(ev); sys.stdout.flush()
        except KeyboardInterrupt: pass
        except (OSError, ValueError) as x: raise CliError(f"stopped following {s.path}: {x}")

def cmd_summary(a):
    s = get_session(a); ev = s.events()
    if a.json: dump(session_item(s, ev)); return
    print(header(s, ev))
    users = [e for e in ev if e["k"] == "user"]
    if users:
        print(f"TASK: {short(users[0]['text'], 600)}")
        if len(users) > 1: print(f"LATEST PROMPT (turn {users[-1]['turn']}): {short(users[-1]['text'], 300)}")
    files = files_changed(ev)
    if files:
        print(f"FILES CHANGED ({len(files)}): " + ", ".join(rel(f, s.cwd) for f in files[-25:]))
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

# ---------------- CLI ----------------
def int_arg(lo):
    def conv(v):
        try: n = int(v)
        except ValueError: raise argparse.ArgumentTypeError(f"expected an integer, got '{v}'") from None
        if n < lo: raise argparse.ArgumentTypeError(f"must be >= {lo}, got {n}")
        return n
    return conv

def build_parser():
    p = argparse.ArgumentParser(prog="agent-log", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"agent-log {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="{list,show,tail,summary}")
    sub.required = True
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("common options")
    g.add_argument("--source", choices=list(PROVIDERS), help="only this agent")
    g.add_argument("--no-codex", action="store_true", help="hide Codex sessions")
    g.add_argument("--project", metavar="SUBSTR", help="only sessions whose cwd contains SUBSTR")
    g.add_argument("--json", action="store_true", help=f"machine-readable output (schema_version {SCHEMA_VERSION})")
    g.add_argument("--tz", metavar="TZ", help="display timezone: local (default), UTC, +HH:MM or an IANA name; default $AGENT_LOG_TZ")
    g.add_argument("--no-cache", action="store_true", help="do not read or write the list cache")
    sess_help = "id prefix | latest (default) | project substring | session file path"
    when_help = "30m, 2h, 1d (ago), HH:MM (today) or ISO 8601"
    l = sub.add_parser("list", parents=[common], help="list sessions, newest first")
    l.add_argument("--active", action="store_true", help="only running sessions")
    l.add_argument("--since", metavar="WHEN", help=f"updated since: {when_help}")
    l.add_argument("-n", "--limit", dest="n", type=int_arg(0), default=15, metavar="N", help="max rows (default 15, 0 = all)")
    l.set_defaults(fn=cmd_list)
    for name, dl, hlp in (("show", 0, "transcript"), ("tail", 20, "transcript, last 20 steps by default")):
        sp = sub.add_parser(name, parents=[common], help=hlp)
        sp.add_argument("session", nargs="?", default="latest", help=sess_help)
        sp.add_argument("--last", type=int_arg(1), metavar="N", help="last N steps")
        sp.add_argument("--since", metavar="WHEN", help=f"events since: {when_help}")
        sp.add_argument("--until", metavar="WHEN", help=f"events until: {when_help}")
        sp.add_argument("--turn", type=int_arg(1), metavar="N", help="only turn N")
        sp.add_argument("--with-output", action="store_true", help="show tool output")
        sp.add_argument("--max-chars", type=int_arg(10), default=300, metavar="N", help="truncate text to N characters (default 300)")
        sp.add_argument("--no-thinking", action="store_true", help="hide thinking")
        sp.add_argument("--full", action="store_true", help="don't truncate assistant text")
        sp.add_argument("--all", action="store_true", help="no default window (tail)")
        sp.add_argument("-f", "--follow", action="store_true", help="keep printing new events (text only)")
        sp.set_defaults(fn=cmd_show, default_last=dl)
    s = sub.add_parser("summary", parents=[common], help="one-screen status")
    s.add_argument("session", nargs="?", default="latest", help=sess_help)
    s.set_defaults(fn=cmd_summary)
    return p

def truthy(v): return str(v or "").strip().lower() not in ("", "0", "false", "no", "off")

def prepare(a):
    """Validate values that depend on the timezone or each other; raises CliError(code 2)."""
    spec = a.tz if a.tz is not None else os.environ.get("AGENT_LOG_TZ", "")
    try: set_tz(spec)
    except ValueError:
        src = "--tz" if a.tz is not None else "AGENT_LOG_TZ"
        raise CliError(f"bad timezone {src}='{spec}' (use local, UTC, +HH:MM or an IANA name like Asia/Taipei)", 2) from None
    for k in ("since", "until"):
        if getattr(a, k, None) is not None: setattr(a, k, parse_when(getattr(a, k)))
    if not hasattr(a, "until"): a.until = None
    if getattr(a, "follow", False) and a.json: raise CliError("--follow is not supported with --json", 2)
    if getattr(a, "all", False): a.default_last = 0

def main(argv=None):
    """CLI entry point; returns the exit code (0 ok, 1 not found, 2 usage)."""
    global USE_COLOR, CACHE, TZ
    for stream in (sys.stdout, sys.stderr):
        try: stream.reconfigure(errors="replace")  # never die on a non-UTF-8 terminal
        except Exception: pass
    try: a = build_parser().parse_args(argv)
    except SystemExit as x:
        return x.code if isinstance(x.code, int) else (0 if x.code is None else 2)
    saved = USE_COLOR, CACHE, TZ
    try:
        USE_COLOR = want_color(sys.stdout)
        prepare(a)
        CACHE = Cache(None if a.no_cache or truthy(os.environ.get("AGENT_LOG_NO_CACHE")) else cache_dir())
        a.fn(a)
        sys.stdout.flush()
        return 0
    except CliError as x:
        warn(f"error: {x}")
        return x.code
    except BrokenPipeError:
        # reader went away (`| head`); point stdout at devnull so the interpreter's final flush stays quiet
        try: os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception: pass
        return 0
    except KeyboardInterrupt:
        return 130
    finally:
        CACHE.save()
        USE_COLOR, CACHE, TZ = saved

try: set_tz(os.environ.get("AGENT_LOG_TZ", ""))
except ValueError: pass

if __name__ == "__main__":
    sys.exit(main())
