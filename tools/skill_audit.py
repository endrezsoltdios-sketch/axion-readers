#!/usr/bin/env python3
"""skill_audit.py -- which skills were actually invoked, from the session transcripts (6 Sep 2026).

Why: arXiv 2608.11888 "Agent Skills Can Be Harmful" (verified 6 Sep 2026) measured 307 skill-induced
failures; relevant skills made agents worse by forcing procedure. Retire on evidence; this is the evidence: every Skill tool call in every transcript under the
project's Claude directory, counted per skill, against the skills that exist on disk.

  py scripts/skill_audit.py [--days 30] [--out knowledge/skill_audit_<date>.md] [--project-dir DIR] [--commands-dir DIR]
  py scripts/skill_audit.py --selftest        # fixture transcript + two skills, no real transcripts read
The project dir defaults to Claude Code's own naming of this repo's path under ~/.claude/projects.
Reads only. 0 tokens. Exit 0.
"""
import argparse, json, re, sys, tempfile, time
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJ = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))   # Claude Code's project-dir naming
CMDS = ROOT / ".claude" / "commands"


def skills_on_disk(cmds=CMDS):
    names = {}
    for p in sorted(cmds.glob("*.md")):
        head = p.read_text(encoding="utf-8", errors="replace")[:600]
        m = re.search(r"^description:\s*(.+)$", head, re.M)
        names[p.stem] = (m.group(1).strip()[:90] if m else "")
    return names


def audit(proj, cmds, days, out):
    since = time.time() - days * 86400
    counts, sessions, files = Counter(), Counter(), 0
    for f in proj.glob("*.jsonl"):
        if f.stat().st_mtime < since:
            continue
        files += 1
        seen = set()
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"name":"Skill"' not in line and '"name": "Skill"' not in line:
                    continue
                for m in re.finditer(r'"name":\s*"Skill".{0,400}?"skill":\s*"([^"]+)"', line):
                    sk = m.group(1).split(":")[-1]
                    counts[sk] += 1; seen.add(sk)
        for sk in seen:
            sessions[sk] += 1
    disk = skills_on_disk(cmds)
    never = sorted(n for n in disk if counts[n] == 0)
    used = sorted(((n, counts[n], sessions[n]) for n in disk if counts[n]), key=lambda x: -x[1])
    foreign = sorted((n, c) for n, c in counts.items() if n not in disk)
    L = ["# Skill invocation audit -- %s (last %d days, %d transcript files)" % (date.today().isoformat(), days, files), "",
         "Source: every `Skill` tool call in %s/*.jsonl modified in the window. A skill invoked by name in chat and a skill loaded by the harness both appear here; a skill only READ as a file does not." % proj.name, "",
         "## Invoked (%d of %d project skills)" % (len(used), len(disk)), "", "| skill | calls | sessions | description |", "|---|---|---|---|"]
    L += ["| %s | %d | %d | %s |" % (n, c, s, disk[n].replace("|", "/")) for n, c, s in used]
    L += ["", "## Never invoked in the window (%d)" % len(never), "", "| skill | description |", "|---|---|"]
    L += ["| %s | %s |" % (n, disk[n].replace("|", "/")) for n in never]
    L += ["", "## Invoked but not a project skill (plugins / built-ins)", ""] + ["- %s: %d" % (n, c) for n, c in foreign]
    L += ["", "## Reading rule", "", "Zero calls in %d days is evidence, not a verdict: a skill tied to a paused lane waits for the lane; a skill nobody reached for while its lane was active is the retire list. Retirement = move the file to a _retired/ folder with the date and this file's name in your decisions log, same turn." % days]
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    return used, never, foreign, L


def selftest():
    with tempfile.TemporaryDirectory() as d:
        proj, cmds = Path(d) / "proj", Path(d) / "cmds"
        proj.mkdir(); cmds.mkdir()
        (cmds / "foo.md").write_text("---\ndescription: Foo does x\n---\nbody", encoding="utf-8")
        (cmds / "bar.md").write_text("---\ndescription: Bar\n---\n", encoding="utf-8")
        (proj / "s1.jsonl").write_text('{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"foo"}}]}}\n'
                                       '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"plugin:baz"}}]}}\n'
                                       '{"type":"assistant","message":{"content":[{"type":"tool_use","name": "Skill","input":{"skill":"foo"}}]}}\n', encoding="utf-8")
        used, never, foreign, L = audit(proj, cmds, 30, Path(d) / "out.md")
        checks = [("foo counted twice in one session", used == [("foo", 2, 1)]),
                  ("bar never invoked", never == ["bar"]),
                  ("plugin skill counted as foreign, prefix stripped", foreign == [("baz", 1)]),
                  ("report written", (Path(d) / "out.md").read_text(encoding="utf-8").count("| foo | 2 | 1 | Foo does x |") == 1)]
    bad = [n for n, ok in checks if not ok]
    print(json.dumps({"selftest": "PASS" if not bad else "FAIL", "checks": len(checks), "failed": bad}))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=str(ROOT / "knowledge" / ("skill_audit_%s.md" % date.today().isoformat())))
    ap.add_argument("--project-dir", default=str(PROJ), help="Claude Code transcript dir (default: this repo's under ~/.claude/projects)")
    ap.add_argument("--commands-dir", default=str(CMDS))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    proj = Path(a.project_dir)
    if not proj.is_dir():
        sys.exit("no transcript dir at %s -- pass --project-dir" % proj)
    out = Path(a.out)
    used, never, foreign, L = audit(proj, Path(a.commands_dir), a.days, out)
    print("\n".join(L[:8 + len(used)]))
    print("\nnever invoked: %d -> %s" % (len(never), ", ".join(never)))
    print("wrote", out.relative_to(ROOT) if out.is_relative_to(ROOT) else out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
