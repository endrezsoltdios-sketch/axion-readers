---
name: library
description: Search your own notes, research and decision files by meaning before writing anything new. BM25 over every paragraph of the markdown folders you name, stdlib only, no embeddings, no keys, no network; prints score, file:line and a snippet. Use when a draft, post, spec or answer should rest on things you already wrote and verified, or when someone asks "have we looked at this before".
license: FSL-1.1-ALv2
---

# Library

Your own corpus, searched by meaning, in about two seconds for 700 files. Nothing leaves the machine.

```bash
python scripts/library.py "bedtime fear"                # top 10 passages: score, file:line, 200-char snippet
python scripts/library.py "refund policy" --json --top 5
python scripts/library.py "x" --rebuild                 # ignore the cached index
python scripts/library.py --selftest                    # fixture corpus, planted line, rebuild-on-change
```

## Which folders it reads

By default `docs/`, `notes/` and `README.md` under the repo root, plus the Claude Code memory folder for this repo
when one exists. Set `LIBRARY_ROOTS` to change it, comma-separated, relative to the repo root; a `.md` entry is one
file, anything else is a folder of `**/*.md`:

```bash
LIBRARY_ROOTS="knowledge,research,DECISIONS.md" python scripts/library.py "pricing test"
```

The index is cached in `ops/library_index.json` and rebuilt when any file changed, appeared or vanished.

## How to use the hits

- A hit is a paragraph with a file and a line. Open it before quoting it; the snippet is 200 characters.
- Take only passages that carry something already verified (a number, a date, a rule, a sourced quote). A passage
  that says "unverified" or "inferred" shapes the angle and is not a claim.
- Cite the `file:line` in whatever you write, so the next reader can check it in one step.
- No hits prints one line and exits 1; that is an answer too (nothing on file, go and measure).

Hosted version and commercial licence: hello@getaxionlabs.com
