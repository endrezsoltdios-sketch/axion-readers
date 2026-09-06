# Changelog

## v0.2.5 — 6 September 2026

- `reddit.py`: feed rung pace 4 s → 30 s. Re-measured the same afternoon from a second IP: Reddit's anonymous Atom/RSS budget is about one request per 30 s per IP (30/60/90 s gaps answered 200 every time; 8–20 s gaps drew 429s, and a 429 spends budget too). The dawn reading of "~10 requests per burst" was stale by evening — a rate limit measured once is a claim, not a fact.

## v0.2.4 — 6 September 2026

- `arxiv_sweep.py` — every new paper in your arXiv categories, read by metadata through arXiv's own API (no key), scored against the lanes in `knowledge/arxiv_lanes.json` (`knowledge/arxiv_lanes.example.json` ships three neutral lanes: agents and the machine web, retrieval and answers, efficiency). Seen-ledger so a paper is scored once; ranked markdown shortlist. Field note from the first run: term matching must be whole-word — `fine` was hitting `fine-tuning` and `cost` was hitting `costly`.
- `arxiv_fetch.py` — full text of the papers a sweep kept: arXiv's HTML build through `web.py`, abstract page as fallback, 3.2 s pacing (arXiv's own ask), a header that says which source answered, never invented text.
- `reddit.py` — read-only Reddit door with two rungs. Feed rung: Reddit's public Atom feeds (`search.rss`, `new.rss`, `<thread>.rss`), no login, no key; measured 6 September 2026: four anonymous requests inside ~5 s draw a 429, so the rung paces at 4 s, backs off once on 429 (Retry-After or 60 s) and then halts rather than loop. Browser rung: `browserd.py` rendering Reddit's JSON for scores, nested replies, rules and about; anonymous rendering returns score 0 on real posts, so scores are only trusted logged in. `voices` writes verbatim quotes with author, date and permalink. There is no post, comment or vote op and the selftest asserts none exists.
- `skill_audit.py` — which Claude Code skills were actually invoked, counted from the project's session transcripts against the skills on disk. Motivated by arXiv 2608.11888 ("Agent Skills Can Be Harmful"): zero invocations in a window is the evidence a retire decision needs. The transcript directory is derived from the repo path the way Claude Code names it.
- `task_watchdog.py` — catch scheduled-task misses the scheduler hides. Field note (1 September 2026): the Claude Code scheduler keeps run state server-side and silently disables one-shots whose fire time passes while the app is closed — two builds were lost in three days while transcript-based checks said "0 overdue". The fix is to trust only artifacts: a registry (`knowledge/tasks_registry.example.json`) names the proof file each run must produce.
- Release gate: importing any published tool must not touch the network (connect and urlopen patched to raise in a subprocess import). Every v0.2.4 tool answers `--selftest` (`reddit.py selftest`) with no network.

## v0.2.3 — 6 September 2026

- `facts.py` — `add --supersedes "<old claim>"` retires the fact it replaces at write time and points it at the new one; `find` hides retired rows (`--all` shows them); `stale` skips them. Motivated by three papers in the same month (arXiv 2608.20685, 2608.07933, 2608.09393): a superseded fact must be retired, not ranked lower. `FACTS_STORE` env var overrides the ledger path for tests.

## v0.2.2 — 6 September 2026

- `cfdash.py` — drive a never-idle dashboard (Cloudflare's) in a Chrome started with `--remote-debugging-port`, one command per call over CDP. Built because both a browser extension waiting for document_idle and a single-threaded Playwright daemon wedge on such pages. Stops at login walls; `evalfile` runs multi-line JS from a file because shell quoting mangles it.

## v0.2.1 — 6 September 2026

- `botauth.py` — Web Bot Auth for your own readers (IETF draft-meunier-web-bot-auth-architecture, RFC 9421 signatures, RFC 8037 thumbprints): `keygen`, `headers`, `test` (Cloudflare's crawltest.com checker), `verify-directory`, `selftest`. A Cloudflare Workers module that serves the signed key directory is in the same family (see the Axion Open worker); the directory response must itself be signed or it can be mirrored.
- `web.py --signed` — attach the three signature headers to a page fetch.
- Field note: a PKCS8 secret piped into a Worker arrives with a trailing newline; strip whitespace before base64 padding arithmetic or the first deploy returns 500.

## v0.2.0 — 6 September 2026

**Doors, not scrapers.** Nineteen tools added; the six readers of v0.1 unchanged.

- `door.py` + `doors.json` — the door registry: rails you own per site, `probe`, `open`, human-fired `post`.
- `browserd.py` — persistent real-Chrome daemon with your own profile; halts at login walls and CAPTCHAs.
- `yt_reply.py`, `yt_comments.py` — YouTube read + human-fired reply on your own OAuth token.
- `reddit_api.py` — Reddit's official OAuth rail; `reddit_serp.py` — Reddit via Google's index.
- `cf.py` — Cloudflare workers / zones / traffic / KV / tail on the wrangler token.
- `gads.py`, `gads_auth.py` — Google Ads connector, dry-run first, rollback log.
- `indexnow.py` — URL pings to IndexNow.
- `sitemap.py`, `diffwatch.py`, `pdfx.py`, `transcribe.py`, `tiktok_read.py`, `ask.py` — more readers.
- `regex_lint.py`, `fix_mojibake.py`, `worker_sizes.py` — keeping code honest.

Field notes shipped with this release:
- A YouTube reply carrying a link was accepted by the API and never became visible; four linkless replies posted the same minute were public within two minutes. `yt_reply.py` records this in its docstring; the tool does not strip links for you — it makes the read-back cheap so you see it.
- Reddit refuses logins inside automation Chrome. The browser rail stays read-only; the official API rail exists for writing, on a script app you create yourself.
- A Cloudflare token that deploys Workers may still be unable to read KV (error 10000) or zone analytics. `cf.py` prints CONFIGURED-OUT for exactly those calls rather than failing the whole command.

## v0.1.0 — 31 August 2026

Six token-friendly reading tools: `yt.py`, `web.py`, `search.py`, `digest.py`, `facts.py`, `radar.py`.
