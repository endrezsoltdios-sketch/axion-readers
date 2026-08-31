#!/usr/bin/env python3
"""facts.py — the house ledger of verified facts. One job: never verify the
same claim twice. Built 31 Aug 2026 (token-reduction order).

Every session that verifies a load-bearing claim at source records it here;
every session checks here BEFORE re-verifying. Honours the facts-expire law:
every entry carries its check date and prints its age.

Usage:
  py scripts/facts.py add "SI 2026/336 revokes the 2021 MTD regs (reg 48)" --source "https://www.legislation.gov.uk/uksi/2026/336/regulation/48/made"
  py scripts/facts.py find mtd
  py scripts/facts.py find "art 50"
  py scripts/facts.py stale --days 60      # facts older than N days, oldest first

Store: facts.jsonl beside this script (override: FACTS_PATH env var) (append-only; one JSON object per line).
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

STORE = Path(os.environ.get("FACTS_PATH", Path(__file__).resolve().parent / "facts.jsonl"))


def load():
    if not STORE.exists():
        return []
    out = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def age_days(iso):
    try:
        d = date.fromisoformat(iso)
        return (date.today() - d).days
    except ValueError:
        return -1


def show(f):
    a = age_days(f.get("checked", ""))
    flag = " ⚠STALE" if a > 90 else ""
    return (f"[{f.get('checked','?')} · {a}d old{flag}] {f['claim']}\n"
            f"    source: {f.get('source','(none recorded)')}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("claim")
    p_add.add_argument("--source", required=True)
    p_add.add_argument("--checked", default=date.today().isoformat())
    p_add.add_argument("--tags", default="")
    p_find = sub.add_parser("find")
    p_find.add_argument("query")
    p_stale = sub.add_parser("stale")
    p_stale.add_argument("--days", type=int, default=90)
    a = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "add":
        facts = load()
        low = a.claim.lower()
        for f in facts:
            if f["claim"].lower() == low:
                print("DUPLICATE — already recorded:\n" + show(f))
                return
        STORE.parent.mkdir(parents=True, exist_ok=True)
        rec = {"claim": a.claim, "source": a.source,
               "checked": a.checked, "tags": a.tags}
        with STORE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("recorded:\n" + show(rec))
    elif a.cmd == "find":
        q = a.query.lower()
        hits = [f for f in load()
                if q in f["claim"].lower() or q in f.get("tags", "").lower()
                or q in f.get("source", "").lower()]
        if not hits:
            print(f"no recorded fact matches '{a.query}' — verify at source, then facts.py add")
        for f in hits:
            print(show(f))
    elif a.cmd == "stale":
        old = sorted((f for f in load() if age_days(f.get("checked", "")) > a.days),
                     key=lambda f: f.get("checked", ""))
        if not old:
            print(f"nothing older than {a.days} days")
        for f in old:
            print(show(f))


if __name__ == "__main__":
    main()
