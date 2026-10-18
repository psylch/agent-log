#!/usr/bin/env python3
"""Generate a synthetic session history and time agent-log commands against it.

    python3 tests/bench.py [--sessions 400] [--script agent_log.py] [--keep DIR]

Not part of the unit test suite (unittest discovery only picks up test*.py).
"""
import argparse, json, os, random, shutil, statistics, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def iso(t): return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".123Z"

def blob(rng, n):
    return "".join(rng.choice("abcdefghij klmnop\n") for _ in range(n))

def claude_session(path, cwd, sid, t0, rng, records):
    with open(path, "w") as f:
        f.write(json.dumps({"type": "file-history-snapshot", "messageId": "x"}) + "\n")
        f.write(json.dumps({"type": "user", "cwd": cwd, "sessionId": sid, "timestamp": iso(t0),
                            "message": {"role": "user", "content": f"task for {cwd} ü 日本"}}) + "\n")
        for i in range(records):
            t = t0 + i * 3
            f.write(json.dumps({"type": "assistant", "timestamp": iso(t), "message": {"id": f"m{i}", "role": "assistant", "content": [
                {"type": "text", "text": "working " + blob(rng, 80)},
                {"type": "tool_use", "id": f"tu{i}", "name": "Bash", "input": {"command": f"ls {i}"}}]}}) + "\n")
            out = blob(rng, 200_000 if i % 97 == 0 else 1500)
            f.write(json.dumps({"type": "user", "timestamp": iso(t + 1), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"tu{i}", "content": out}]}}) + "\n")

def codex_session(path, cwd, sid, t0, rng, records):
    with open(path, "w") as f:
        f.write(json.dumps({"timestamp": iso(t0), "type": "session_meta", "payload": {"id": sid, "cwd": cwd,
                            "base_instructions": {"text": blob(rng, 20000)}}}) + "\n")
        f.write(json.dumps({"timestamp": iso(t0), "type": "response_item", "payload": {"type": "message", "role": "user",
                            "content": [{"type": "input_text", "text": "codex task"}]}}) + "\n")
        for i in range(records):
            t = t0 + i * 3
            f.write(json.dumps({"timestamp": iso(t), "type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                "call_id": f"c{i}", "arguments": json.dumps({"command": ["bash", "-lc", f"echo {i}"]})}}) + "\n")
            f.write(json.dumps({"timestamp": iso(t + 1), "type": "response_item", "payload": {"type": "function_call_output",
                                "call_id": f"c{i}", "output": blob(rng, 1500)}}) + "\n")
        f.write(json.dumps({"timestamp": iso(t0 + records * 3), "type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "done"}}) + "\n")

def droid_session(path, cwd, sid, t0, rng, records):
    with open(path, "w") as f:
        f.write(json.dumps({"type": "session_start", "id": sid, "cwd": cwd}) + "\n")
        f.write(json.dumps({"type": "message", "timestamp": iso(t0), "message": {"role": "user", "content": [{"type": "text", "text": "droid task"}]}}) + "\n")
        for i in range(records):
            t = t0 + i * 3
            f.write(json.dumps({"type": "message", "timestamp": iso(t), "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": f"t{i}", "name": "Execute", "input": {"command": "make"}}]}}) + "\n")
            f.write(json.dumps({"type": "message", "timestamp": iso(t + 1), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{i}", "content": blob(rng, 1500)}]}}) + "\n")

def gemini_session(path, cwd, sid, t0, rng, records):
    msgs = [{"id": "u0", "type": "user", "timestamp": iso(t0), "content": "gemini task"}]
    for i in range(records):
        msgs.append({"id": f"g{i}", "type": "gemini", "timestamp": iso(t0 + i * 3), "content": "ok",
                     "toolCalls": [{"id": f"tc{i}", "name": "run_shell_command", "args": {"command": "ls"}, "status": "success",
                                    "result": [{"functionResponse": {"response": {"output": blob(rng, 1500)}}}]}]})
    with open(path, "w") as f: json.dump({"sessionId": sid, "projectHash": "h", "messages": msgs}, f)

def build(home, n, seed=1):
    rng = random.Random(seed)
    now = time.time() - 3600
    mix = [("claude", 0.45), ("codex", 0.25), ("droid", 0.15), ("gemini", 0.15)]
    for i in range(n):
        r = rng.random(); acc = 0
        for kind, w in mix:
            acc += w
            if r <= acc: break
        sid = f"{i:08x}-0000-4000-8000-{rng.getrandbits(48):012x}"
        cwd = f"/work/proj{i % 25}"
        t0 = now - i * 1800
        records = rng.randint(40, 400)
        if kind == "claude":
            d = os.path.join(home, ".claude/projects", cwd.replace("/", "-")); os.makedirs(d, exist_ok=True)
            p = os.path.join(d, sid + ".jsonl"); claude_session(p, cwd, sid, t0, rng, records)
        elif kind == "codex":
            d = os.path.join(home, ".codex/sessions/2026/10/01"); os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"rollout-2026-10-01T00-00-00-{sid}.jsonl"); codex_session(p, cwd, sid, t0, rng, records)
        elif kind == "droid":
            d = os.path.join(home, ".factory/sessions", cwd.replace("/", "-")); os.makedirs(d, exist_ok=True)
            p = os.path.join(d, sid + ".jsonl"); droid_session(p, cwd, sid, t0, rng, records)
        else:
            d = os.path.join(home, ".gemini/tmp", f"h{i % 25}", "chats"); os.makedirs(d, exist_ok=True)
            with open(os.path.join(home, ".gemini/tmp", f"h{i % 25}", ".project_root"), "w") as f: f.write(cwd)
            p = os.path.join(d, f"session-2026-10-01T00-00-{sid[:8]}.json"); gemini_session(p, cwd, sid, t0, rng, records)
        os.utime(p, (t0 + records * 3, t0 + records * 3))

def du(path):
    return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(path) for f in fs)

def run(script, env, args, reps):
    times = []
    for _ in range(reps):
        t = time.perf_counter()
        r = subprocess.run([sys.executable, script, *args], env=env, capture_output=True, text=True)
        times.append(time.perf_counter() - t)
        if r.returncode != 0: raise SystemExit(f"{args}: exit {r.returncode}: {r.stderr[-500:]}")
    return statistics.median(times)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=400)
    ap.add_argument("--script", default=os.path.join(ROOT, "agent_log.py"))
    ap.add_argument("--keep", help="reuse/keep the generated history in this directory")
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    tmp = a.keep or tempfile.mkdtemp(prefix="agent-log-bench-")
    home = os.path.join(tmp, "home")
    if not os.path.isdir(home): build(home, a.sessions)
    cache = os.path.join(tmp, "cache")
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_LOG_")}
    env.update(HOME=home, XDG_CACHE_HOME=cache, AGENT_LOG_PROC=os.path.join(tmp, "noproc"), AGENT_LOG_TZ="UTC")
    for k in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "XDG_DATA_HOME", "PI_CODING_AGENT_DIR"): env.pop(k, None)
    print(f"history: {a.sessions} sessions, {du(home) / 1e6:.0f} MB in {home}")
    cases = [("list", ["list"]), ("list -n 100", ["list", "-n", "100"]), ("list --json", ["list", "--json"]),
             ("list --project proj7", ["list", "--project", "proj7"]), ("summary (latest)", ["summary"]),
             ("show <id> --last 5", ["show", "00000005", "--last", "5"])]
    for label, args in cases:
        shutil.rmtree(cache, ignore_errors=True)
        cold = run(a.script, env, args, 1)
        warm = run(a.script, env, args, a.reps)
        print(f"  {label:<22} cold {cold * 1000:7.0f} ms   warm {warm * 1000:7.0f} ms")
    if not a.keep: shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
