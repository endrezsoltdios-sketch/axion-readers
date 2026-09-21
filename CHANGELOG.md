# Changelog

## v0.4.0 — 22 September 2026

The agent readiness lane: the Cloudflare doors that have no API, and then the measurement of who actually came.

- `dns_aid.py` — publish the DNS for AI Discovery entry points for a zone (`_a2a._agents.<zone>` and
  `_mcp._agents.<zone>`, SVCB) through the dashboard's own Add record form, and prove each one back over
  DNS-over-HTTPS before calling it done. A record DNS already answers is skipped; no other record is touched.
  `--dnssec` presses Enable DNSSEC, because the draft wants signed zones. Written because an API token with zone
  read and no DNS write cannot add these at all, and the manual path is four fields and a Save per record per
  zone. `CLOUDFLARE_ACCOUNT_ID` and a Chrome on `--remote-debugging-port=9226`; `--check` needs neither.
- `cf_readiness.py` — Cloudflare's Agent Readiness diagnostic for every zone, kept as a file you can diff: the
  pass mark on each of the 21 items in four levels, and Cloudflare's own one-line Status under each item once it
  is opened. The page is a click-per-item accordion with no API. Field note: a zone switch passes through the
  login URL for a second or two, so only a wall that stays is a wall; and a stray click can navigate away, so
  every item re-checks the URL and comes back. Needs `playwright`.
- `cf_bot_submit.py` — Cloudflare's Bots and Agents Directory (BotBase) application from a JSON spec. The form is
  React (base-ui): values go in through the native property setter plus an input event, list controls are opened
  with pointer events and their options clicked by text. Every value is read back and compared to the spec before
  anything is pressed, and a mismatch stops the run. `--discover` dumps the fields and every list's options, which
  is how the spec gets written. Submitting is human fired: without `--apply` it stops at the review screen.
- `bot_ranges.py` — the address blocks Google, Bing, OpenAI, Perplexity and Apple publish for their own bots, into
  one JSON file and one JavaScript module. A user-agent is a claim: Googlebot from a Hetzner box is not Google, and
  Cloudflare's own verified-bot flag costs an Enterprise plan. A source that is down is carried over from the
  previous file, so a bot never turns "impersonated" because a list was offline. Field note: the classifier imports
  a JavaScript module rather than the JSON, because Node refuses a JSON import without an attribute while a plain
  module needs no loader hook in any suite.
- `traffic_read.py` — who was really at each door, read back from the edge meter over the Workers Analytics Engine
  SQL API: people by page view and people confirmed by beacon, browser strings from hosting networks, headless
  browsers, declared AI agents split into verified, impersonated and unproven, crawlers, HTTP tools, monitors, your
  own scanners, probe paths. Two people numbers are printed and both are named for what they are, because one of
  them is always the one that gets quoted. Needs a token with Account Analytics Read.
- `edge/` — the JavaScript half, as a second folder: `traffic_class.js` (the classifier: address maths against the
  published blocks, path classes, browser-versus-script evidence), `traffic_meter.js` (`withTrafficMeter(handler)`,
  one Analytics Engine data point per outside request, never twice for a handler that re-enters its own `fetch`, a
  thrown handler metered as 500 and re-thrown untouched, a failing store never breaking a page), `traffic_checks.js`
  (the same contract asserted against your own worker from your own suite), the 126-case suite and the generated
  range table. No address, no full user-agent, no query string, no cookie and no referrer is stored, and the suite
  asserts that rather than promising it.
- Release gate: the JavaScript folder goes through the same substitution and private-string rules as the tools, and
  then its suite is run with `node` against the **published** copy, so a released classifier is proved rather than
  assumed. Our own account id is now an environment variable (`CLOUDFLARE_ACCOUNT_ID`) with a clear error when it
  is absent, rather than a constant anyone could paste into a run against someone else's zone.
- `cf.py` — the public copy no longer names a credentials file: `CLOUDFLARE_API_TOKEN` comes from the environment,
  and the error says so.

## v0.3.0 — 16 September 2026

- Skills: `skills/wordy-audit`, `skills/library`, `skills/guard` — a `SKILL.md` (name, one-paragraph description, how to read the output) beside the tool it drives, installable with `npx skills add endrezsoltdios-sketch/axion-readers`. The release gate now copies each skill's tool from `tools/` and checks the frontmatter, the description length and the private-string scan on the skill text too.
- `wordy_audit.py` — one page against the too-many-words checklist (words, above-the-fold words, H2s, eyebrows, CTAs, em- and en-dashes, real images, repeated sentences, keyword in the H1, the first line after it), the ranking page beside it with `--winner`, `--json`, `--selftest` on a fixture document. Field note: a class-named eyebrow that also sits right before a heading counts on both rules; compare two pages read by the same tool rather than trusting one row.
- `library.py` — BM25 over every paragraph of the markdown folders you name in `LIBRARY_ROOTS` (default `docs`, `notes`, `README.md`), plus the Claude Code memory folder when one exists; index cached and rebuilt on any change; per-file cap so one long file cannot fill the top ten; `--selftest` plants a line and proves rebuild-on-change and delete.
- `guard.py` — local policy engine for tool calls: `init` writes a starter `.guard/policy.yaml`; `check` / `explain` decide one request; `hook --shadow` is a Claude Code PreToolUse adapter that logs and never blocks; `replay` re-decides a recorded trace; `audit` names redundant identical calls and overreach; `seal` / `verify` hash-chain a day's events and print the first broken index. Explicit deny beats ask beats allow; no match is deny; malformed is deny. Adapters classify, policy decides, and the selftest asserts that adding an adapter changes no decision.
- Every tool now carries the copyright header and this repo carries `PROVENANCE.md`: a dated, public record of what each published tool does and the hash it shipped with (prior art).


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
