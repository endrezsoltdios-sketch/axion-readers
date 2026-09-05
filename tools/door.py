#!/usr/bin/env python3
"""door.py -- one front door to every site Axion touches (Fig Tree primitive, 6 Sep 2026).

Why: fourteen sites, fourteen tools, fourteen ways to find out whether a rail is
alive. The user asked for "connectors that connect to any site". The honest version
is not a universal scraper -- it is a REGISTRY of the rail we own to each site
(knowledge/doors.json), a probe that says which rails answer right now, a dispatcher
that runs the owning tool, and a hard rule that no post rail runs unless a human
fired it in words. Nothing here works around a site's defences: a shut door is
reported with the exact human step that opens it.

  py scripts/door.py list                         every door, rail kinds, human step
  py scripts/door.py probe [door ...]             run each door's canary; table + exit 1 on FAIL
  py scripts/door.py open <door> <rail> [args]    run the rail's tool (read / search / upload)
  py scripts/door.py post <door> --human-fired "<the user's words>" [args]
                                                  post rail; refused without the quote; logged to
                                                  business/content/POSTS.ledger.jsonl
  py scripts/door.py --json ...                   machine output

Exit 0 ok / 1 a probe failed or a tool failed / 2 usage or refused.
Stdlib only. Grow knowledge/doors.json, not this file.
"""
import argparse, json, os, shlex, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "knowledge" / "doors.json"
LEDGER = ROOT / "business" / "content" / "POSTS.ledger.jsonl"
PY = "py" if os.name == "nt" else "python3"


def load():
    return json.loads(REG.read_text(encoding="utf-8"))


def env_key(name):
    v = os.environ.get(name)
    if v:
        return v
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import sales
        return sales.env_key(name)
    except Exception:
        return None


def run(argv, timeout):
    argv = [PY if a == "py" else a for a in argv]
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] or [""], time.time() - t0
    except subprocess.TimeoutExpired:
        return None, ["timeout after %ss" % timeout], time.time() - t0
    except FileNotFoundError as e:
        return None, [str(e)], time.time() - t0


def probe_one(name, door):
    p = door.get("probe")
    if not p:
        return {"door": name, "state": "NO-PROBE", "detail": door.get("human_step") or "dormant", "secs": 0}
    if p["type"] == "cmd":
        code, tail, secs = run(p["argv"], p.get("timeout", 60))
        ok = code in p.get("ok_codes", [0])
        detail = (p.get("means") or "exit %s" % code) if ok else ((tail[0] if tail and tail[0] else "exit %s" % code))
        return {"door": name, "state": "OK" if ok else "FAIL", "detail": detail[:90], "secs": round(secs, 1)}
    if p["type"] == "http":
        H = {"User-Agent": "AxionDoorProbe/1.0 (+https://getaxionlabs.com)"}
        if p.get("auth_env"):
            k = env_key(p["auth_env"])
            if not k:
                return {"door": name, "state": "CONFIGURED-OUT", "detail": "%s absent" % p["auth_env"], "secs": 0}
            H["Authorization"] = "Bearer " + k
        t0 = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(p["url"], headers=H), timeout=p.get("timeout", 30)) as r:
                st = r.status
        except urllib.error.HTTPError as e:
            st = e.code
        except Exception as e:
            return {"door": name, "state": "FAIL", "detail": str(e)[:90], "secs": round(time.time() - t0, 1)}
        ok = st == p.get("expect", 200)
        return {"door": name, "state": "OK" if ok else "FAIL", "detail": (p.get("means") or "http %s" % st) if ok else "http %s" % st, "secs": round(time.time() - t0, 1)}
    return {"door": name, "state": "FAIL", "detail": "unknown probe type", "secs": 0}


def cmd_list(a, reg):
    rows = []
    for n, d in reg["doors"].items():
        r = d["rails"]
        rows.append((n, r["read"]["kind"], r["search"]["kind"], r["post"]["kind"], (d.get("human_step") or "-")[:60]))
    if a.json:
        print(json.dumps(reg["doors"])); return
    w = [max(len(str(x[i])) for x in rows + [("door", "read", "search", "post", "human step")]) for i in range(5)]
    for x in [("door", "read", "search", "post", "human step")] + rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(x)))
    print("%d doors | registry %s (updated %s)" % (len(rows), REG.relative_to(ROOT), reg.get("updated")))


def cmd_probe(a, reg):
    names = a.doors or list(reg["doors"])
    out = [probe_one(n, reg["doors"][n]) for n in names if n in reg["doors"]]
    if a.json:
        print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": out}))
    else:
        for r in out:
            print("%-14s %-15s %5ss  %s" % (r["door"], r["state"], r["secs"], r["detail"]))
        fails = [r for r in out if r["state"] == "FAIL"]
        print("%d probed | %d OK | %d FAIL | %d no-probe/configured-out" % (
            len(out), sum(r["state"] == "OK" for r in out), len(fails), sum(r["state"] in ("NO-PROBE", "CONFIGURED-OUT") for r in out)))
    (ROOT / "ops" / "doors_latest.json").write_text(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "results": out}, indent=1), encoding="utf-8")
    sys.exit(1 if any(r["state"] == "FAIL" for r in out) else 0)


def rail_cmd(reg, door, rail):
    d = reg["doors"].get(door)
    if not d:
        print("[no door] %r; have: %s" % (door, ", ".join(reg["doors"]))); sys.exit(2)
    r = d["rails"].get(rail)
    if not r or not r.get("cmd"):
        note = (r or {}).get("note") or d.get("human_step") or "no rail"
        print("[shut] %s/%s is %s: %s" % (door, rail, (r or {}).get("kind", "none"), note)); sys.exit(2)
    return r


def cmd_open(a, reg):
    if a.rail == "post":
        print("[refused] use `door.py post <door> --human-fired \"...\"` for a post rail"); sys.exit(2)
    r = rail_cmd(reg, a.door, a.rail)
    argv = shlex.split(r["cmd"].replace("{args}", " ".join(shlex.quote(x) for x in a.args)))
    argv = [PY if x == "py" else x for x in argv]
    sys.exit(subprocess.call(argv, cwd=ROOT))


def cmd_post(a, reg):
    if not a.human_fired or len(a.human_fired.strip()) < 8:
        print("[refused] a post rail runs only with --human-fired \"<the user's own words>\""); sys.exit(2)
    r = rail_cmd(reg, a.door, "post")
    argv = shlex.split(r["cmd"].replace("{args}", " ".join(shlex.quote(x) for x in a.args)))
    argv = [PY if x == "py" else x for x in argv]
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "door": a.door, "human_fired": a.human_fired, "argv": argv}
    code = subprocess.call(argv, cwd=ROOT)
    rec["exit"] = code
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    sys.exit(code)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    pr = sub.add_parser("probe"); pr.add_argument("doors", nargs="*")
    op = sub.add_parser("open"); op.add_argument("door"); op.add_argument("rail"); op.add_argument("args", nargs=argparse.REMAINDER)
    po = sub.add_parser("post"); po.add_argument("door"); po.add_argument("--human-fired", default=""); po.add_argument("args", nargs=argparse.REMAINDER)
    a = p.parse_args()
    reg = load()
    {"list": cmd_list, "probe": cmd_probe, "open": cmd_open, "post": cmd_post}[a.cmd](a, reg)


if __name__ == "__main__":
    main()
