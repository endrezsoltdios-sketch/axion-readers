#!/usr/bin/env python3
r"""regex_lint.py — catch the bug shape of 4 Sep 2026 before it ships (rule 30).

Three times in one day a regex built from a JS STRING silently matched nothing:
  new RegExp("\\bnever\\b")   -> fine (escaped backslash reaches the regex engine)
  new RegExp("\bnever\b")     -> "\b" is a JS BACKSPACE escape: the pattern holds U+0008
  new RegExp(`\s+of\s+${x}`)  -> in a template literal "\s" is just "s"
  /[' + "\\u0000" + ']/       -> a string-concat pattern pasted into a regex LITERAL
None of these throws. Each one makes a gate report green while measuring nothing.

This lint reads .js/.mjs files and flags:
  A. a quoted or template string that is an argument to RegExp(...) and contains a
     single-backslash regex escape (\b \s \w \d \S \W \D \. \( \) \[ \] \{ \} \| \+ \* \?)
  B. any raw control byte 0x00-0x08, 0x0B, 0x0C, 0x0E-0x1F in the file (rule 29)
  C. a regex LITERAL whose class starts with [' or [" followed by ' + ' (the paste shape)
It does NOT execute code. Exit 1 if anything is flagged. Usage:
  py scripts/regex_lint.py business/appeal_engine/site business/denial_facts/site ...
  py scripts/regex_lint.py --all        # the five sites' src/ test/ tools/
"""
import re, sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITES = ["."]   # directories to walk when --all is given
SKIP = {"node_modules", ".wrangler", ".cache", ".build", ".render", "dist", "fixtures"}
CTRL = re.compile(rb"[\x00-\x08\x0B\x0C\x0E-\x1F]")
# RegExp( <string-literal>  — single-line only; multi-line concatenations are checked per literal below
REGEXP_CALL = re.compile(r"RegExp\(\s*(?P<lit>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)")
# a string literal anywhere on a line that ALSO mentions RegExp (covers "a" + "b" concatenations)
ANY_LIT = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`")
BAD_ESC = re.compile(r"(?<!\\)\\([bswdSWD.()\[\]{}|+*?])")
PASTE = re.compile(r"/\[['\"]\s*\+\s*['\"]")


def strip_interp(s):
    """Remove ${...} interpolations (brace-balanced) from a template literal."""
    out, i, depth = [], 0, 0
    while i < len(s):
        if s.startswith("${", i) and depth == 0:
            depth = 1; i += 2; continue
        if depth:
            if s[i] == "{": depth += 1
            elif s[i] == "}": depth -= 1
            i += 1; continue
        out.append(s[i]); i += 1
    return "".join(out)


def files_under(paths):
    for p in paths:
        p = ROOT / p if not pathlib.Path(p).is_absolute() else pathlib.Path(p)
        for f in p.rglob("*"):
            if f.suffix not in (".js", ".mjs") or any(s in f.parts for s in SKIP) or ".pre_" in f.name:
                continue
            yield f


def lint(f):
    out = []
    raw = f.read_bytes()
    for m in CTRL.finditer(raw):
        out.append((raw[:m.start()].count(b"\n") + 1, "B", f"raw control byte 0x{m.group()[0]:02x}"))
    txt = raw.decode("utf-8", "replace")
    lines = txt.splitlines()
    in_regexp_block = 0
    for i, line in enumerate(lines, 1):
        if PASTE.search(line):
            out.append((i, "C", "regex literal starts with a string-concat paste: [' + \""))
        # Track multi-line `new RegExp(` ... `)` blocks so concatenated literals are checked.
        if "RegExp(" in line:
            in_regexp_block = 6  # look at this and the next few lines
        if in_regexp_block > 0:
            # A deliberate negative control (a pinned BROKEN spelling) says so on the line:
            #   // regex-lint: allow — <why>
            if "regex-lint: allow" in line or (i > 1 and "regex-lint: allow" in lines[i - 2]):
                in_regexp_block -= 1
                continue
            for lit in ANY_LIT.finditer(line):
                s = lit.group()
                # String.raw`...` keeps its backslashes: not this bug.
                if line[max(0, lit.start() - 10):lit.start()].rstrip().endswith("String.raw"):
                    continue
                s = strip_interp(s)   # a regex LITERAL inside ${...} is the regex engine's business
                bad = BAD_ESC.findall(s)
                if bad:
                    out.append((i, "A", f"single-backslash \\{bad[0]} inside a string given to RegExp: {s[:70]}"))
            in_regexp_block -= 1
            if ")" in line and "RegExp(" not in line:
                in_regexp_block = 0
    return out


def main():
    args = sys.argv[1:]
    paths = SITES if (not args or args == ["--all"]) else args
    total = 0
    for f in files_under(paths):
        for (ln, kind, msg) in lint(f):
            total += 1
            print(f"{f.relative_to(ROOT)}:{ln} [{kind}] {msg}")
    print(f"regex_lint: {total} finding(s)")
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
