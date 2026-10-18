#!/usr/bin/env python3
"""Regenerate docs/assets/demo.svg from real agent-log output on the demo home (examples/demo_home.py).

    python3 docs/assets/make_demo_svg.py

The SVG is a static terminal window: no fonts or scripts are embedded, so GitHub renders it as an image.
"""
import os, re, subprocess, sys, tempfile
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "docs", "assets", "demo.svg")
COMMANDS = [["agent-log"], ["agent-log", "summary", "134298f6"]]
COLS = 104
FONT, LINE, CHAR_W = 14, 20, 8.43  # px; CHAR_W is Menlo/DejaVu Sans Mono at 14px
PAD_X, PAD_TOP, BAR = 28, 22, 40

STYLE = """
  .win { fill: #161b22; stroke: #30363d; } .bar { fill: #21262d; }
  text { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', 'Liberation Mono', monospace;
         font-size: 14px; fill: #c9d1d9; white-space: pre; }
  .t { fill: #8b949e; font-size: 13px; } .p { fill: #3fb950; font-weight: bold; } .c { fill: #f0f6fc; font-weight: bold; }
  .d { fill: #8b949e; } .r { fill: #f85149; } .ok { fill: #3fb950; } .run { fill: #e3b341; font-weight: bold; }
  .l { fill: #58a6ff; font-weight: bold; } .h { fill: #d2a8ff; }
"""
STATUS_CLS = {"done": "ok", "failed": "r", "running": "run"}
LABEL_RE = re.compile(r"^(TASK|LATEST PROMPT \(turn \d+\)|FILES CHANGED \(\d+\)|LAST ACTIONS|FINAL(?: \(last assistant text\))?|STATUS):")


def run(home, argv):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_LOG_")}
    env.update(HOME=home, AGENT_LOG_PROC=os.path.join(home, ".proc"), AGENT_LOG_NO_CACHE="1", AGENT_LOG_TZ="UTC", NO_COLOR="1")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "agent_log.py")] + argv[1:], env=env, capture_output=True, text=True, check=True)
    return r.stdout.rstrip("\n").split("\n")


def colorize(line, state):
    """[(text, css class or None)] for one output line; state tracks the ERRORS block."""
    if line.startswith("$ "): return [("$ ", "p"), (line[2:], "c")]
    if line.startswith("status: "): return [(line, "d")]
    if line.startswith("ERRORS"): state["err"] = True; return [(line, "r")]
    if state.get("err") and line.startswith("  "): return [(line, "r")]
    state["err"] = False
    m = LABEL_RE.match(line)
    if m: return [(m.group(0), "l"), (line[m.end():], None)]
    if line.startswith("last("): return [(line, "d")]
    if line.startswith("[") or re.match(r"^\d\d-\d\d \d\d:\d\d ", line):
        out, pos = [], 0
        for w in re.finditer(r"\b(done|failed|running)\b", line):
            out += [(line[pos:w.start()], "h" if line.startswith("[") and pos == 0 else None), (w.group(0), STATUS_CLS[w.group(0)])]
            pos = w.end()
            if line.startswith("["): break  # only the header's status word, not words in the reason
        return out + [(line[pos:], None)]
    return [(line, None)]


def wrap(segs, cols, indent="  "):
    """Word-wrap colored segments into display lines of at most cols characters."""
    chars = [(ch, cls) for text, cls in segs for ch in text]
    lines = []
    while len(chars) > cols:
        cut = max((i for i in range(len(indent) + 1, cols + 1) if chars[i][0] == " "), default=cols)
        lines.append(chars[:cut])
        rest = chars[cut + 1:] if chars[cut][0] == " " else chars[cut:]
        chars = [(ch, None) for ch in indent] + rest
    lines.append(chars)
    out = []
    for line in lines:  # merge runs of the same class back into segments
        segs = []
        for ch, cls in line:
            if segs and segs[-1][1] == cls: segs[-1][0] += ch
            else: segs.append([ch, cls])
        out.append([tuple(s) for s in segs])
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = os.path.join(tmp, "agent-log-demo")
        subprocess.run([sys.executable, os.path.join(ROOT, "examples", "demo_home.py"), home], check=True, capture_output=True)
        raw = []
        for i, argv in enumerate(COMMANDS):
            if i: raw.append("")
            raw.append("$ " + " ".join(argv))
            raw += run(home, argv)
    state, rows = {}, []
    for line in raw: rows += wrap(colorize(line, state), COLS)
    w = int(PAD_X * 2 + COLS * CHAR_W) + 8
    h = BAR + PAD_TOP + LINE * len(rows) + 18
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" '
           f'aria-label="agent-log listing sessions of six coding agents, then a one-screen summary of a Claude Code session">',
           f"<style>{STYLE}</style>",
           f'<rect class="win" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="10"/>',
           f'<path class="bar" d="M0.5 10.5a10 10 0 0 1 10-10h{w - 21}a10 10 0 0 1 10 10v{BAR - 10}h-{w - 1}z"/>',
           '<circle cx="22" cy="20" r="6" fill="#ff5f57"/><circle cx="42" cy="20" r="6" fill="#febc2e"/><circle cx="62" cy="20" r="6" fill="#28c840"/>',
           f'<text class="t" x="{w / 2:.0f}" y="25" text-anchor="middle">agent-log — check on the coding agents you launched</text>']
    y = BAR + PAD_TOP + FONT - 2
    for segs in rows:
        body = "".join(f'<tspan class="{c}">{escape(t)}</tspan>' if c else escape(t) for t, c in segs if t)
        out.append(f'<text x="{PAD_X}" y="{y}" xml:space="preserve">{body}</text>')
        y += LINE
    out.append("</svg>")
    with open(OUT, "w", encoding="utf-8") as f: f.write("\n".join(out) + "\n")
    print(f"wrote {os.path.relpath(OUT, ROOT)} ({w}x{h}, {len(rows)} lines)")


if __name__ == "__main__":
    main()
