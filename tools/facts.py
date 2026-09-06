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

Store: knowledge/facts.jsonl (append-only; one JSON object per line).
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import os
STORE = Path(os.environ.get("FACTS_STORE") or (Path(__file__).resolve().parent.parent / "knowledge" / "facts.jsonl"))  # env override = test hook only


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
    if f.get("retired"):
        flag = f" ✗RETIRED {f['retired']} — superseded by: {f.get('superseded_by', '?')[:80]}"
    return (f"[{f.get('checked','?')} · {a}d old{flag}] {f['claim']}\n"
            f"    source: {f.get('source','(none recorded)')}")


def save(facts):
    """Rewrite the store (only `supersedes` needs this; `add` stays append-only)."""
    STORE.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in facts), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add")
    p_add.add_argument("claim")
    p_add.add_argument("--source", required=True)
    p_add.add_argument("--checked", default=date.today().isoformat())
    p_add.add_argument("--tags", default="")
    # 6 Sep 2026 (arXiv 2608.20685 MemStrata, 2608.07933 EvoTrustRAG — arxiv DIGEST_2026-09-06):
    # a superseded fact is RETIRED at write time, never merely ranked lower. `add --supersedes
    # "<substring of the old claim>"` records the new fact and marks every matching old one
    # retired today, pointing at the new claim. `find` prints retired rows flagged, never as current.
    p_add.add_argument("--supersedes", default="", help="substring of the claim(s) this fact replaces")
    p_find = sub.add_parser("find")
    p_find.add_argument("query")
    p_find.add_argument("--all", action="store_true", help="include retired facts")
    p_stale = sub.add_parser("stale")
    p_stale.add_argument("--days", type=int, default=90)
    a = ap.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if a.cmd == "add":
        facts = load()
        low = a.claim.lower()
        for f in facts:
            if f["claim"].lower() == low and not f.get("retired"):
                print("DUPLICATE — already recorded:\n" + show(f))
                return
        STORE.parent.mkdir(parents=True, exist_ok=True)
        rec = {"claim": a.claim, "source": a.source,
               "checked": a.checked, "tags": a.tags}
        if a.supersedes:
            key = a.supersedes.lower(); n = 0
            for f in facts:
                if key in f["claim"].lower() and not f.get("retired") and f["claim"].lower() != low:
                    f["retired"] = date.today().isoformat(); f["superseded_by"] = a.claim; n += 1
            if not n:
                print(f"no live fact matches --supersedes '{a.supersedes}' — nothing retired; recording the new fact anyway")
            facts.append(rec); save(facts)
            print(f"recorded (retired {n} superseded fact{'s' if n != 1 else ''}):\n" + show(rec))
            return
        with STORE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("recorded:\n" + show(rec))
    elif a.cmd == "find":
        q = a.query.lower()
        hits = [f for f in load()
                if (q in f["claim"].lower() or q in f.get("tags", "").lower()
                    or q in f.get("source", "").lower()) and (a.all or not f.get("retired"))]
        if not hits:
            print(f"no recorded fact matches '{a.query}' — verify at source, then facts.py add")
        for f in hits:
            print(show(f))
        retired = sum(1 for f in load() if f.get("retired") and (q in f["claim"].lower()))
        if retired and not a.all:
            print(f"({retired} retired fact{'s' if retired != 1 else ''} hidden — --all shows them)")
    elif a.cmd == "stale":
        old = sorted((f for f in load() if age_days(f.get("checked", "")) > a.days and not f.get("retired")),
                     key=lambda f: f.get("checked", ""))
        if not old:
            print(f"nothing older than {a.days} days")
        for f in old:
            print(show(f))


if __name__ == "__main__":
    main()
