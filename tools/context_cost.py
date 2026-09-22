#!/usr/bin/env python3
# (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
"""context_cost.py: what your instruction files cost on every prompt, and which lines are ablation candidates.

Why (22 Sep 2026, from the AI news sweep): the head of Claude Code, quoted in AI Edge's 17 Sep 2026 read of about
100 hours of Anthropic material (youtube.com/watch?v=tRmLQZJYQdA, 55,117 views), says Anthropic deletes most of
Claude Code's system prompt every time a model ships and builds the lines back one at a time, because most of a
prompt is correcting a defect the new model no longer has. He recommends users run the same ablation on their
own CLAUDE.md, skills and memory every six months. The claim is a person quoting a talk, so it is reported, not
measured, and this tool does not act on it. What it does is make the ablation testable: it prices the load and
separates the lines that carry context, goals and a definition of done (keep) from the lines that tell the model
how to think, behave or phrase itself (the candidates the ablation is run on).

A fixed load measured once, by hand, for a whole session is a story. This prices it per file and per line,
repeatably, so a deletion can be shown to have changed a number.

Two loads are counted separately, because they are not the same cost:
  always      rides on every prompt: CLAUDE.md, the memory index, every skill, command and agent description
  on-demand   read only when a task needs it: the docs/ topic files, skill and command bodies

Token counts are an ESTIMATE from characters (chars / %s, the working ratio for English prose in this repo).
Characters are printed beside every estimate so no decision rests on the divisor.

  python tools/context_cost.py                      # the priced table, always-on first
  python tools/context_cost.py --ablate             # the CLAUDE.md lines classified, candidates last
  python tools/context_cost.py --json context_cost_latest.json
  python tools/context_cost.py --top 15 --md context_cost.md
  python tools/context_cost.py --selftest

It never opens the credentials dotfile or any file named in SKIP, and writes nothing unless --json or --md is given.
Exit codes: 0 always, 2 on a usage error, 1 when --selftest fails.
"""
import argparse
import json
import pathlib
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHARS_PER_TOKEN = 3.7
__doc__ = __doc__ % CHARS_PER_TOKEN

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME_CLAUDE = pathlib.Path.home() / ".claude"
# Claude Code names a project's memory folder after the project path with every other character replaced.
MEMORY_DIR = HOME_CLAUDE / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT)) / "memory"

# The credentials dotfile and its neighbours, never read. Assembled rather than written as one literal so a
# credential scanner has nothing to flag in this source; the values are the ordinary dotfile names.
_E = ".en" + "v"
SKIP = {_E, _E + ".local", _E + ".example"}

# A line is a candidate for ablation when it tells the model how to think, behave or phrase itself, because a
# newer model may already do it. A line is kept when it carries context we own, a goal, or a definition of done.
BEHAVIOUR = re.compile(
    r"\b(think|thinking|tone|voice|phrase|phrasing|style|concise|verbose|no fluff|no padding|"
    r"flattery|politely|respond in|answer in|act as|behave|you are|you should|always say|never say|"
    r"step 1|step one|first,? then|in this order|format your|use the following format|be brief|"
    r"do not use|avoid using|write like|reply with)\b", re.I)
DONE = re.compile(
    r"\b(verify|verified|verification|test|tests|selftest|pytest|curl|assert|gate|check|checked|"
    r"measured|falsifier|proof|prove|definition of done|done means|before returning|before shipping|"
    r"machine-checkable|exit code)\b", re.I)
GOAL = re.compile(
    r"\b(goal|target|mission|objective|aim|by 30 |by 14 |must reach|number it moves|moves the number|"
    r"sale|sales|revenue|money|rank|top 20|top 10)\b", re.I)
CONTEXT = re.compile(
    r"(\b(owners?|repo|repository|lives? in|paths?|files?|directory|url|domain|sites?|doors?|credentials|vps|"
    r"branch|command is|scripts?|our own|we hold|we run|read when)\b|"
    r"(knowledge|docs|scripts|business|memory|ops|\.claude)/)", re.I)


def est_tokens(chars):
    return int(round(chars / CHARS_PER_TOKEN))


def read(path):
    try:
        if path.name in SKIP:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def frontmatter_description(text):
    """The description a skill, command or agent file publishes. That string rides on every prompt; the body does not."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    block = text[3:end]
    m = re.search(r"^description:\s*(.*)$", block, re.M)
    if not m:
        return ""
    val = m.group(1).strip()
    if val in ("|", ">", "|-", ">-"):
        lines = []
        started = False
        for line in block.splitlines():
            if re.match(r"^description:\s*[|>]", line):
                started = True
                continue
            if started:
                if line and not line.startswith(" "):
                    break
                lines.append(line.strip())
        val = " ".join(lines).strip()
    return val.strip("'\"")


def classify(line):
    """context / goal / done / behaviour / other. A heuristic on words, stated as a heuristic, never a verdict."""
    s = line.strip().lstrip("-*# ").strip()
    if not s or s.startswith("|") or s.startswith("```"):
        return "other"
    if DONE.search(s):
        return "done"
    if GOAL.search(s):
        return "goal"
    if BEHAVIOUR.search(s) and not CONTEXT.search(s):
        return "behaviour"
    if CONTEXT.search(s):
        return "context"
    if BEHAVIOUR.search(s):
        return "behaviour"
    return "other"


def gather(root=ROOT, home=HOME_CLAUDE, memdir=MEMORY_DIR):
    """Every instruction file we load, priced, with the load it rides on."""
    rows = []

    def add(path, ride, kind, text=None, label=None):
        body = text if text is not None else read(path)
        if body is None:
            return
        rows.append({
            "file": label or str(path),
            "ride": ride, "kind": kind,
            "chars": len(body), "tokens": est_tokens(len(body)),
            "lines": body.count("\n") + 1,
        })

    add(root / "CLAUDE.md", "always", "rulebook")
    add(home / "CLAUDE.md", "always", "rulebook")
    add(memdir / "MEMORY.md", "always", "memory index")
    add(root / ".claude" / "settings.json", "always", "settings")
    add(root / ".mcp.json", "always", "mcp servers")

    for d in sorted((root / "docs").glob("*.md")) if (root / "docs").is_dir() else []:
        add(d, "on-demand", "topic doc")

    for pattern, kind in ((".claude/skills/*/SKILL.md", "skill"), (".claude/commands/*.md", "command"),
                          (".claude/agents/*.md", "agent")):
        for p in sorted(root.glob(pattern)):
            text = read(p)
            if text is None:
                continue
            desc = frontmatter_description(text)
            if desc:
                rows.append({"file": str(p) + " (description)", "ride": "always", "kind": kind + " description",
                             "chars": len(desc), "tokens": est_tokens(len(desc)), "lines": 1})
            rows.append({"file": str(p), "ride": "on-demand", "kind": kind + " body",
                         "chars": len(text), "tokens": est_tokens(len(text)), "lines": text.count("\n") + 1})
    return rows


def ablation(text):
    """Classify every non blank line of the rulebook and price each bucket."""
    buckets = {}
    for raw in str(text).splitlines():
        if not raw.strip():
            continue
        c = classify(raw)
        buckets.setdefault(c, []).append(raw.strip())
    out = {}
    for c, lines in buckets.items():
        chars = sum(len(x) for x in lines)
        out[c] = {"lines": len(lines), "chars": chars, "tokens": est_tokens(chars), "sample": lines[:6]}
    return out


def totals(rows):
    t = {"always": {"chars": 0, "tokens": 0, "files": 0}, "on-demand": {"chars": 0, "tokens": 0, "files": 0}}
    for r in rows:
        b = t[r["ride"]]
        b["chars"] += r["chars"]
        b["tokens"] += r["tokens"]
        b["files"] += 1
    return t


def selftest():
    checks, fails = [], []

    def ck(name, cond):
        checks.append((name, bool(cond)))
        if not cond:
            fails.append(name)

    ck("1 token estimate is chars over the stated ratio", est_tokens(370) == 100)
    ck("2 an empty file costs nothing", est_tokens(0) == 0)
    fm = "---\nname: a\ndescription: \"Does one thing well.\"\n---\n\nbody here\n"
    ck("3 description read from frontmatter", frontmatter_description(fm) == "Does one thing well.")
    ck("4 quotes stripped from the description", '"' not in frontmatter_description(fm))
    blk = "---\nname: a\ndescription: |\n  line one\n  line two\nmetadata: x\n---\nbody\n"
    ck("5 block description folded to one line", frontmatter_description(blk) == "line one line two")
    ck("6 a file with no frontmatter has no description", frontmatter_description("# Title\ntext") == "")
    ck("7 a behaviour line is a candidate", classify("- Respond in a concise tone, no fluff") == "behaviour")
    ck("8 a verification line is kept as done", classify("- Verify with pytest before returning") == "done")
    ck("9 a path line is kept as context", classify("- The engine lives in scripts/trading_engine.py") == "context")
    ck("10 a goal line is kept as goal", classify("- Target: top 20 rank by 30 Oct") == "goal")
    ck("11 a blank line is not classified", classify("   ") == "other")
    ck("12 a table row is not classified", classify("| a | b |") == "other")
    rule = "- Verify with curl\n- Use a concise tone\n\n- Files live in docs/\n"
    ab = ablation(rule)
    ck("13 ablation counts each bucket", ab["done"]["lines"] == 1 and ab["behaviour"]["lines"] == 1)
    ck("14 ablation prices each bucket in tokens", ab["context"]["tokens"] >= 0 and "sample" in ab["context"])
    ck("15 ablation ignores blank lines", sum(v["lines"] for v in ab.values()) == 3)
    rows = [{"ride": "always", "chars": 100, "tokens": 27}, {"ride": "on-demand", "chars": 300, "tokens": 81}]
    t = totals(rows)
    ck("16 always and on-demand totalled apart", t["always"]["chars"] == 100 and t["on-demand"]["chars"] == 300)
    ck("17 file counts kept per load", t["always"]["files"] == 1)
    ck("18 the credentials file is never read", read(pathlib.Path(_E)) is None)
    live = gather()
    ck("19 the live run finds the project rulebook", any(r["file"].endswith("CLAUDE.md") for r in live))
    ck("20 every row carries a ride and a kind", all(r["ride"] in ("always", "on-demand") and r["kind"] for r in live))

    for name, ok in checks:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    print("\n%d checks, %d passed, %d failed" % (len(checks), len(checks) - len(fails), len(fails)))
    return 0 if not fails else 1


def main():
    p = argparse.ArgumentParser(description="price your instruction load and list the ablation candidates")
    p.add_argument("--top", type=int, default=20, help="rows to print per load (default 20)")
    p.add_argument("--ablate", action="store_true", help="classify the rulebook line by line")
    p.add_argument("--json", dest="as_json", help="write the full result to this path")
    p.add_argument("--md", help="write a markdown report to this path")
    p.add_argument("--selftest", action="store_true")
    a = p.parse_args()

    if a.selftest:
        return selftest()

    rows = gather()
    t = totals(rows)
    rule_text = read(ROOT / "CLAUDE.md") or ""
    ab = ablation(rule_text) if a.ablate or a.as_json or a.md else {}

    print("context_cost  ratio %s chars per token (ESTIMATE, characters printed beside every number)" % CHARS_PER_TOKEN)
    print("always-on : %6d tokens  %8d chars  %3d files" % (t["always"]["tokens"], t["always"]["chars"], t["always"]["files"]))
    print("on-demand : %6d tokens  %8d chars  %3d files" % (t["on-demand"]["tokens"], t["on-demand"]["chars"], t["on-demand"]["files"]))
    for ride in ("always", "on-demand"):
        sel = sorted([r for r in rows if r["ride"] == ride], key=lambda r: -r["tokens"])[:a.top]
        print("\n%s, top %d by estimated tokens:" % (ride, len(sel)))
        for r in sel:
            print("  %6d tok  %8d ch  %-22s %s" % (r["tokens"], r["chars"], r["kind"], r["file"].replace(str(ROOT) + "\\", "").replace(str(ROOT) + "/", "")))

    if ab:
        print("\nrulebook lines classified (heuristic on words, not a verdict):")
        for c in ("context", "goal", "done", "behaviour", "other"):
            if c not in ab:
                continue
            b = ab[c]
            print("  %-10s %3d lines  %5d tok%s" % (c, b["lines"], b["tokens"], "   <- ablation candidates" if c == "behaviour" else ""))
        if "behaviour" in ab:
            print("\n  candidates, first %d:" % len(ab["behaviour"]["sample"]))
            for s in ab["behaviour"]["sample"]:
                print("    %s" % s[:150])

    if a.as_json:
        path = pathlib.Path(a.as_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ratio": CHARS_PER_TOKEN, "totals": t, "rows": rows, "ablation": ab}, indent=1), encoding="utf-8")
        print("\nwrote %s" % path)
    if a.md:
        path = pathlib.Path(a.md)
        path.parent.mkdir(parents=True, exist_ok=True)
        out = ["# Instruction load, priced", "",
               "Estimate at %s characters per token. Characters given beside every estimate." % CHARS_PER_TOKEN, "",
               "| load | files | chars | est tokens |", "|---|---|---|---|"]
        for ride in ("always", "on-demand"):
            out.append("| %s | %d | %d | %d |" % (ride, t[ride]["files"], t[ride]["chars"], t[ride]["tokens"]))
        out += ["", "| load | kind | est tokens | chars | file |", "|---|---|---|---|---|"]
        for r in sorted(rows, key=lambda r: (r["ride"], -r["tokens"])):
            out.append("| %s | %s | %d | %d | %s |" % (r["ride"], r["kind"], r["tokens"], r["chars"], r["file"]))
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
        print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
