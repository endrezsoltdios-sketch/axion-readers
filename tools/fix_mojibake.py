"""fix_mojibake — repair cp1252 double-encoding in text files. Zero model tokens.

The wound: text that was already UTF-8 got decoded as cp1252 and re-encoded as
UTF-8, so an em dash (U+2014) becomes the three chars 'a-circumflex, euro, right-
double-quote'. House rule already exists for the cause ("never PowerShell
Set-Content on source"); this repairs the damage already on disk.

Why targeted replacement and not a whole-file `t.encode('cp1252').decode('utf-8')`
round-trip: once a file contains BOTH clean UTF-8 and mojibake (which happens the
moment anyone edits a damaged file with a correct editor), the whole-file reverse
raises and you can repair nothing. Sequence-level replacement fixes the damage and
leaves valid text alone.

Usage:
    py scripts/fix_mojibake.py <path>...            # dry run (default) - shows every change
    py scripts/fix_mojibake.py <path>... --apply    # writes, leaving .pre_audit_20260901 siblings

Exit: 0 clean/ok, 1 damage found in dry-run mode, 2 write failure.
"""
import argparse
import re
import pathlib
import shutil
import sys

# Longest first. 'â€' is a PREFIX of most others, so it must be replaced last or
# it eats the leading two chars of every sequence below it and produces new junk.
REPAIRS = [
    ("â€”", "—"),   # em dash
    ("â€“", "–"),   # en dash
    ("â€™", "’"),   # right single quote
    ("â€˜", "‘"),   # left single quote
    ("â€œ", "“"),   # left double quote
    ("â€¦", "…"),   # ellipsis
    ("â˜…", "★"),   # black star
    ("â˜†", "☆"),   # white star
    ("âœ…", "✅"),   # white heavy check mark
    ("Ã©", "é"),         # e-acute
    ("Ã¨", "è"),         # e-grave
    ("â€", "”"),   # right double quote (explicit form)
    ("â€", "”"),         # bare tail -> right double quote  (MUST BE LAST)
]


# A hand-written table always misses something. The first run of this tool fixed
# 126 em dashes and quotes and left 'A-circumflex plus-minus', 'a-circumflex >=',
# 'a-circumflex ->' untouched, because they were not in the table. So: find every
# RUN of characters that could be double-encoded bytes and try to reverse it. If
# run.encode('cp1252').decode('utf-8') succeeds, the run really was mojibake and
# the decode gives the true original; if it raises, the run is legitimate text and
# is left alone. Correct by construction rather than by enumeration.
_LEAD = "ÂÃâÄÅ"          # Â Ã â Ä Å  - first byte of a UTF-8 seq seen as cp1252
_CONT = ("-ÿ"                            # latin-1 range
         "€‚ƒ„…†‡ˆ‰Š‹Œ"
         "Ž‘’“”•–—˜™š›"
         "œžŸ")                       # cp1252's 0x80-0x9F specials
_MOJI = re.compile(f"[{_LEAD}][{_CONT}]+")


def repair(text):
    """Returns (fixed_text, count). Pure function -- testable without touching disk."""
    n = 0
    for bad, good in REPAIRS:
        c = text.count(bad)
        if c:
            text = text.replace(bad, good)
            n += c

    def _reverse(m):
        nonlocal n
        run = m.group(0)
        try:
            fixed = run.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return run                               # legitimate text, leave alone
        if fixed == run or not fixed:
            return run
        n += 1
        return fixed

    return _MOJI.sub(_reverse, text), n


def main():
    # This tool prints the very characters it repairs. On Windows the console
    # defaults to cp1252 and print() dies with UnicodeEncodeError on '★' or '—'
    # -- which is how the first run of this script crashed on 1 Sep 2026.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                            # noqa: BLE001 - older/piped streams
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="files or directories (*.md scanned recursively)")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--suffix", default=".pre_audit_20260901", help="rollback sibling suffix")
    args = ap.parse_args()

    files = []
    for raw in args.paths:
        p = pathlib.Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.md")))
        elif p.is_file():
            files.append(p)
        else:
            print(f"[skip] not found: {p}")

    total, changed = 0, 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as e:                       # noqa: BLE001
            print(f"[skip] {p}: {type(e).__name__}: {e}")
            continue
        fixed, n = repair(text)
        if not n:
            continue
        changed += 1
        total += n
        print(f"{'FIX ' if args.apply else 'WOULD FIX'} {p}  ({n} sequence(s))")
        for line_no, (a, b) in enumerate(zip(text.splitlines(), fixed.splitlines()), 1):
            if a != b:
                print(f"    L{line_no}: {a.strip()[:90]}")
                print(f"       -> {b.strip()[:90]}")
        if args.apply:
            try:
                # NEVER clobber an existing rollback sibling: on a second pass it
                # holds the TRUE original, and overwriting it with the already-
                # partly-repaired file would destroy the only way back.
                sib = pathlib.Path(str(p) + args.suffix)
                if not sib.exists():
                    shutil.copy2(p, sib)                       # rollback = one rename
                else:
                    print(f"    (rollback sibling already exists, kept: {sib.name})")
                tmp = p.with_suffix(p.suffix + ".tmp")
                tmp.write_text(fixed, encoding="utf-8")        # atomic: tmp then replace
                tmp.replace(p)
            except Exception as e:                             # noqa: BLE001
                print(f"[FAIL] {p}: {type(e).__name__}: {e}")
                return 2

    verb = "repaired" if args.apply else "would repair"
    print(f"\n{verb} {total} sequence(s) in {changed} file(s) of {len(files)} scanned")
    if changed and not args.apply:
        print("dry run - nothing written. Re-run with --apply")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
