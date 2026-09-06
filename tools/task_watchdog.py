#!/usr/bin/env python3
"""task_watchdog.py — catches scheduled-task misses the scheduler hides.

Why this exists (measured 1 Sep 2026): the Claude Code scheduler keeps all run
state server-side, defers recurring tasks into late catch-up bursts, and
SILENTLY DISABLES one-shots whose fire time passes while the app is closed.
Two builds were lost that way in three days, and transcript-based drift checks
said "0 overdue" through all of it.

Fix: trust only artifacts. knowledge/tasks_registry.json (start from tasks_registry.example.json) says what should run and
what file each run must produce; this tool checks the files. It runs from
Windows Task Scheduler (independent of the app) and alerts Telegram on misses.

  py scripts/task_watchdog.py            check, print report, exit 1 on misses
  py scripts/task_watchdog.py --alert    same + Telegram message if misses found
  py scripts/task_watchdog.py --registry PATH   (for tests)
  py scripts/task_watchdog.py --selftest        fixture registry in a temp dir: OK / MISSED / one-shot / unregistered

Checks per task:
  cron     newest proof file must be newer than the last expected fire time
           (+ grace). proof_glob null => reported UNVERIFIABLE, never OK.
  one_shot after fireAt + grace, a proof file newer than fireAt must exist,
           else MISSED (and the scheduler has silently disabled it — the
           report prints the recovery instruction).
Also flags task dirs in ~/.claude/scheduled-tasks that are neither registered
nor retired — so the registry cannot rot silently.
"""
import argparse
import glob
import json
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT / "knowledge" / "tasks_registry.json"
TASK_DIR = Path.home() / ".claude" / "scheduled-tasks"


def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def last_cron_fire(cron, now):
    """Last expected fire time for the cron patterns we actually use:
    'M H * * *' (daily) and 'M H * * D' (weekly, D=0-6, 1=Monday). Local time."""
    parts = cron.split()
    if len(parts) != 5:
        return None
    minute, hour, dom, mon, dow = parts
    if dom != "*" or mon != "*":
        return None
    try:
        m, h = int(minute), int(hour)
    except ValueError:
        return None
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if dow == "*":
        if cand > now:
            cand -= timedelta(days=1)
        return cand
    try:
        want = int(dow) % 7  # cron: 0/7=Sunday, 1=Monday
    except ValueError:
        return None
    want_py = (want - 1) % 7  # python: 0=Monday
    while cand.weekday() != want_py or cand > now:
        cand -= timedelta(days=1)
    return cand


def newest_match(pattern):
    newest = None
    for f in glob.glob(str(ROOT / pattern), recursive=True):
        p = Path(f)
        if p.is_file():
            t = datetime.fromtimestamp(p.stat().st_mtime)
            if newest is None or t > newest[0]:
                newest = (t, p)
    return newest


def check(registry_path, now=None, task_dir=TASK_DIR):
    reg = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    now = now or datetime.now()
    grace = timedelta(hours=reg.get("grace_hours_default", 6))
    ok, missed, unverifiable, lines = [], [], [], []

    for t in reg.get("tasks", []):
        if t.get("paused"):
            ok.append(t["id"])
            lines.append(f"PAUSED {t['id']}: deliberately stopped — see registry note; not a miss")
            continue
        fire = last_cron_fire(t["cron"], now)
        if fire is None:
            unverifiable.append(t["id"])
            lines.append(f"UNPARSEABLE {t['id']}: cron '{t['cron']}' not supported — fix registry")
            continue
        # A task registered today must not alarm about fires scheduled before it
        # existed — 'since' (YYYY-MM-DD) marks registration day; expected fires
        # before it are nobody's failure.
        since = t.get("since")
        if since and fire < datetime.fromisoformat(since):
            ok.append(t["id"])
            lines.append(f"NEW {t['id']}: registered {since}, no fire due yet (next after {t['cron']})")
            continue
        if t.get("proof_glob") is None:
            unverifiable.append(t["id"])
            lines.append(f"UNVERIFIABLE {t['id']}: no proof_glob — cannot confirm runs; "
                         f"add one after its next run")
            continue
        proof = newest_match(t["proof_glob"])
        deadline = fire + grace
        if proof and proof[0] >= fire:
            ok.append(t["id"])
            lines.append(f"OK {t['id']}: expected {fire:%d %b %H:%M}, "
                         f"proof {proof[0]:%d %b %H:%M} ({proof[1].name})")
        elif now < deadline:
            ok.append(t["id"])
            lines.append(f"PENDING {t['id']}: expected {fire:%d %b %H:%M}, "
                         f"still inside grace until {deadline:%H:%M}")
        else:
            newest = f"{proof[0]:%d %b %H:%M} ({proof[1].name})" if proof else "none ever"
            missed.append(t["id"])
            lines.append(f"MISSED {t['id']}: expected {fire:%d %b %H:%M}, newest proof {newest}. "
                         f"Recover: open the app and run the task's SKILL.md directly, "
                         f"or re-arm via update_scheduled_task.")

    for t in reg.get("one_shots", []):
        fire = datetime.fromisoformat(t["fireAt"])
        if now < fire + grace:
            ok.append(t["id"])
            lines.append(f"PENDING {t['id']}: one-shot due {fire:%d %b %H:%M}")
            continue
        proof = newest_match(t["proof_glob"]) if t.get("proof_glob") else None
        if proof and proof[0] >= fire:
            ok.append(t["id"])
            lines.append(f"OK {t['id']}: one-shot fired, proof {proof[0]:%d %b %H:%M}")
        else:
            missed.append(t["id"])
            lines.append(f"MISSED {t['id']}: one-shot due {fire:%d %b %H:%M} never produced proof — "
                         f"the scheduler has silently DISABLED it. Recover: run its SKILL.md "
                         f"directly ({task_dir / t['id'] / 'SKILL.md'}), then move it to 'retired'.")

    known = {t["id"] for t in reg.get("tasks", [])} \
        | {t["id"] for t in reg.get("one_shots", [])} \
        | set(reg.get("retired", []))
    if task_dir.exists():
        for d in sorted(task_dir.iterdir()):
            if d.is_dir() and d.name not in known:
                missed.append(f"unregistered:{d.name}")
                lines.append(f"UNREGISTERED {d.name}: task dir exists but is not in the registry — "
                             f"register or retire it (law: same turn it is created)")

    header = (f"task_watchdog {now:%Y-%m-%d %H:%M} — "
              f"{len(ok)} ok · {len(missed)} MISSED/unregistered · "
              f"{len(unverifiable)} unverifiable")
    return header, lines, missed


def telegram_alert(text):
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_TOKEN")
    chat = env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[no telegram credentials in .env — alert not sent]")
        return False
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:3900]}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            ok = json.loads(resp.read().decode()).get("ok", False)
            print("[telegram alert sent]" if ok else "[telegram refused the message]")
            return ok
    except Exception as e:  # alert failure must not mask the report
        print(f"[telegram unreachable: {type(e).__name__}]")
        return False


def selftest():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "proof").mkdir(); (d / "proof" / "ran.md").write_text("x", encoding="utf-8")
        (d / "tasks" / "stray").mkdir(parents=True); (d / "tasks" / "known").mkdir()
        now = datetime.now().replace(hour=23, minute=59)          # every daily 00:00 fire is due and past grace
        reg = {"grace_hours_default": 6,
               "tasks": [{"id": "good", "cron": "0 0 * * *", "proof_glob": str(d / "proof" / "*.md")},
                         {"id": "missed", "cron": "0 0 * * *", "proof_glob": str(d / "proof" / "never_*.md")},
                         {"id": "blind", "cron": "0 0 * * *", "proof_glob": None},
                         {"id": "fresh", "cron": "0 0 * * *", "proof_glob": str(d / "nothing"), "since": (now + timedelta(days=1)).date().isoformat()},
                         {"id": "known", "cron": "0 0 * * *", "proof_glob": None, "paused": True}],
               "one_shots": [{"id": "shot", "fireAt": (now - timedelta(days=2)).isoformat(timespec="minutes"), "proof_glob": str(d / "proof" / "never_*.md")},
                             {"id": "later", "fireAt": (now + timedelta(days=2)).isoformat(timespec="minutes"), "proof_glob": None}],
               "retired": []}
        (d / "reg.json").write_text(json.dumps(reg), encoding="utf-8")
        header, lines, missed = check(d / "reg.json", now=now, task_dir=d / "tasks")
        kinds = {ln.split()[1].rstrip(":"): ln.split()[0] for ln in lines}
    checks = [("proof newer than fire = OK", kinds.get("good") == "OK"),
              ("no proof past grace = MISSED", kinds.get("missed") == "MISSED"),
              ("null proof_glob = UNVERIFIABLE, never OK", kinds.get("blind") == "UNVERIFIABLE"),
              ("registered after the fire = NEW", kinds.get("fresh") == "NEW"),
              ("paused = PAUSED", kinds.get("known") == "PAUSED"),
              ("one-shot past + no proof = MISSED", kinds.get("shot") == "MISSED"),
              ("one-shot in the future = PENDING", kinds.get("later") == "PENDING"),
              ("stray task dir = UNREGISTERED", kinds.get("stray") == "UNREGISTERED" and "unregistered:stray" in missed),
              ("missed list exact", sorted(missed) == ["missed", "shot", "unregistered:stray"]),
              ("header counts", "3 MISSED/unregistered" in header and "1 unverifiable" in header)]
    bad = [n for n, ok in checks if not ok]
    print(json.dumps({"selftest": "PASS" if not bad else "FAIL", "checks": len(checks), "failed": bad}))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alert", action="store_true", help="Telegram alert on misses")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())

    header, lines, missed = check(a.registry)
    print(header)
    for line in lines:
        print("  " + line)
    if missed and a.alert:
        bad = [ln for ln in lines if ln.startswith(("MISSED", "UNREGISTERED"))]
        telegram_alert("SCHEDULER WATCHDOG\n" + header + "\n" + "\n".join(bad))
    sys.exit(1 if missed else 0)


if __name__ == "__main__":
    main()
