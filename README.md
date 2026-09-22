# Axion Readers

Small, Unix-style tools for AI-agent workflows — built because we kept rewriting
these exact scripts inside [Claude Code](https://claude.com/claude-code) sessions
and burning tokens on raw ingestion and on re-discovering which door to a site
still works. Each tool does ONE job, costs zero model tokens (two use a free
tier), prints clean text a model — or a human — can read cheaply, and fails with
an honest line instead of a traceback.

Built and field-tested in production at [Axion Labs](https://getaxionlabs.com),
where they feed a multi-agent research and publishing pipeline daily.

## v0.5 — can a machine buyer and a machine reader actually get in?

Latest release: **v0.5.0** (22 September 2026). Ten readers that ask that question from outside, plus two repairs.
v0.4 made a site *readable* by agents; these tools check, from the buyer's side of the wire, that the readability
and the price survive the edge, the card, the DNS and the crawler rules. Every one answers `--selftest` with no
network and `--help` with exit 0.

| Tool | One job | Cost |
|---|---|---|
| `buyer_probe.py` | Walk the whole path an unnamed, unsigned, script-only machine buyer walks: front door, catalog, service description, priced operation, the 402, the price in it, the payment destination, the checkout link, a wrong key refused. Reads seven edge settings by their effect, because a dashboard is the only other place they show. Nothing is posted or paid. | 0 |
| `a2a_audit.py` | Every agent card a host publishes against ten public A2A card rules, each row carrying the JSON path it looked at, so a FAIL names the field to change. | 0 |
| `dns_aid_conform.py` | Every DNS-AID item the reference implementation documents, per zone, over DNS-over-HTTPS: the SVCB record per agent, the `_index._agents` record in both forms, private-use parameters, the DNSSEC AD flag, and whether the target answers on the port it advertises. | 0 |
| `twin_diff.py` | The markdown-twin differential: your HTML against your twin (title, h1, every number, every link, byte identity with the negotiated variant), and the field reference's 14 conformance items as AGREE, DIFFER or ABSENT. | 0 |
| `readiness_read.py` | Three graders, one reader: your own scan beside two keyless outside graders, item by item, disagreements first. A scanner that agrees with itself proves nothing. | 0 |
| `agent_ready_scan.py` | Your own agent readiness scanner: robots, sitemap, Link headers, DNS-AID, markdown, llms.txt, Content-Signal, api-catalog, RFC 8414/9728, auth.md, agent card, JWKS, MCP card, x402, MPP. Any host, yours or a stranger's. | 0 |
| `crawler_gate.py` | Does the edge honour the AI crawlers robots.txt invites by name? 17 named crawlers plus two controls, front page and a content page, per host. A row fails only when the promise and the behaviour disagree. | 0 |
| `vendor_news.py` | What the AI vendors actually shipped since you last looked: 13 declared feeds, dated rows with one URL each, a per-source seen list, `--new-only`. | 0 |
| `context_cost.py` | What your instruction files cost on every prompt, always-on load priced apart from on-demand, characters beside every token estimate. `--ablate` names the lines a newer model may no longer need. | 0 |
| `edge/src/robots_ai.js` | A robots.txt that names the AI crawlers it welcomes, one group each, every group repeating the wildcard rules exactly, because RFC 9309 applies only the most specific group. | 0 |

Repairs in the same release: `dns_aid.py --index` publishes and proves the `_index._agents` TXT record, and
`arxiv_sweep.py` no longer returns zero papers on an HTTP 406 (arXiv refuses a user-agent carrying a URL or an
`@`; the agent string is a bare token now, and the same window reads 1,456 papers).

Hostnames are an environment list per tool, so nothing here has an estate baked into it:

```bash
export DOORS=example.com,example.org     # buyer_probe, a2a_audit, crawler_gate
export HOSTS=example.com,example.org     # twin_diff, readiness_read
export ZONES=example.com,example.org     # dns_aid_conform
export TWIN_REASONS='{"md.noindex":"deliberate: canonical Link instead"}'   # twin_diff: a DIFFER needs a reason
python tools/buyer_probe.py example.com --selftest
```

Each reader's user-agent is an environment value with a neutral default (`BUYER_PROBE_UA`, `A2A_AUDIT_UA`,
`CRAWLER_GATE_UA`, `TWIN_DIFF_UA`, `READINESS_READ_UA`, `READY_SCAN_UA`, `DNS_AID_CONFORM_UA`, `VENDOR_NEWS_UA`).
Set it to something that names you: `crawler_gate.py` needs it to tell your own requests from a crawler's.

## v0.4 — the agent readiness lane

v0.4.0 (22 September 2026). Six tools and one JavaScript folder for the two halves of making a
site readable by agents: the Cloudflare doors that have no API, and then finding out who actually came.

Three of these drive a **logged-in Chrome**, because the pages they read have no API at all. Start one by hand,
log in once, and leave it open:

```bash
chrome --remote-debugging-port=9226 --user-data-dir=/tmp/cfprofile https://dash.cloudflare.com/
export CLOUDFLARE_ACCOUNT_ID=<the id in your dashboard URL>
```

No tool here types a password. A login wall is printed and the tool stops.

### `dns_aid.py` — DNS for AI Discovery records, published and proved

Publishes the DNS-AID entry points a zone needs (`_a2a._agents.<zone>` and `_mcp._agents.<zone>`, SVCB records)
through the dashboard's own Add record form, then proves each one back over DNS-over-HTTPS before calling it
done. It skips a record DNS already answers and touches no other record. `--dnssec` presses Enable DNSSEC on the
zone, because the draft wants signed zones. Written because an API token with zone read and no DNS write cannot
add these, and the alternative was a person clicking four fields per record per zone.

```bash
python tools/dns_aid.py example.com example.org      # add what is missing, then verify over DoH
```

Needs: `CLOUDFLARE_ACCOUNT_ID` and a Chrome on `--remote-debugging-port=9226` logged into the dashboard.
`MCP_HOST` sets the `_mcp` target (default `mcp.example.com`). `--check` reads DoH only and needs neither.

### `cf_readiness.py` — Cloudflare's Agent Readiness diagnostic, as a file you can diff

Reads Cloudflare's Agent Readiness page for every zone and keeps the answer: the pass mark on each of the 21
items across four levels, and Cloudflare's own one-line Status under each item ("auth.md exists but OAuth
Protected Resource Metadata was not found"). The page is a click-per-item accordion with no API, so ten zones by
hand is an hour of clicking and no record. Writes a JSON file and a markdown table. Nothing is typed, no setting
is changed, and only the accordion rows are clicked.

```bash
python tools/cf_readiness.py example.com example.org     # or no zones at all, to read every zone you hold
```

Needs: `CLOUDFLARE_ACCOUNT_ID`, a Chrome on `--remote-debugging-port=9226` logged into the dashboard, and
`playwright`. `CLOUDFLARE_API_TOKEN` (Zone Read) only when you name no zones and want them listed for you.

### `cf_bot_submit.py` — the Bots and Agents Directory form, from a JSON spec

Fills and submits Cloudflare's Bots and Agents Directory (BotBase) application for a reader you operate, from a
JSON spec. The form is React: values go in through the native setter plus an input event, list controls are
opened with pointer events and their options clicked by text. Every value is read back and compared to the spec
before anything is pressed, and a mismatch stops the run. `--discover` dumps the form's fields and every list's
options, which is how you write the spec in the first place.

```bash
python tools/cf_bot_submit.py botbase/mybot.json     # fill, read back, open the review, screenshot, STOP
```

Needs: `CLOUDFLARE_ACCOUNT_ID` and a Chrome on `--remote-debugging-port=9226` logged into the dashboard.
Submitting is human fired: without `--apply` it stops at the review screen.

### `bot_ranges.py` — the address blocks crawler operators publish about themselves

Fetches the IP ranges Google, Bing, OpenAI, Perplexity and Apple publish for their own bots into one JSON file
and one JavaScript module the edge classifier imports. A user-agent is a claim; these files are how you check it.
A source that is down is carried over from the previous file, so a bot never turns "impersonated" because a list
was offline, and `--check` exits 1 once the file is more than 14 days old.

```bash
python tools/bot_ranges.py       # fetch and write edge/data/bot_ranges.json + .js
```

Needs: nothing but network access. `BOT_RANGES_UA` sets the user-agent this reader identifies itself with.

### `traffic_read.py` — who was really at the door, read back from the edge meter

Reads the data points the edge meter wrote (see `edge/`) over the Workers Analytics Engine SQL API and prints one
column per host: people by page view and people confirmed by beacon, browser strings arriving from hosting
networks, headless browsers, declared AI agents split into verified, impersonated and unproven, crawlers, HTTP
tools, uptime monitors, your own scanners, and probe paths. Two people numbers, both named for what they are,
because one of them is always the one someone quotes.

```bash
python tools/traffic_read.py --days 7 --paths
```

Needs: `CLOUDFLARE_API_TOKEN` with **Account Analytics Read** and `CLOUDFLARE_ACCOUNT_ID`. `TRAFFIC_HOSTS` is a
comma-separated column order; leave it unset and the reader uses the hosts it finds. `TRAFFIC_DATASET` names the
dataset (default `edge_traffic`).

### `cfdash.py` — the dashboard driver the three above use

Already shipped in v0.2.2 and unchanged: one command per call over CDP against a Chrome you started yourself
(`goto`, `text`, `shot`, `click`, `fill`, `select`, `eval`, `evalfile`). It never types a password and prints
`login_wall: true` and stops when it meets one.

### `edge/` — the classifier and meter the reader reads

The JavaScript half: `withTrafficMeter(handler)` wraps a Worker and writes one classified Analytics Engine data
point per outside request, never twice for a handler that re-enters itself. No address, no full user-agent, no
query string, no cookie and no referrer is stored, and its 126-case suite asserts that. v0.5 adds
`robots_ai.js`, the robots.txt builder that names the AI crawlers it welcomes, with a 13-check suite. See
[edge/README.md](edge/README.md).

```bash
node edge/test/traffic_class.test.mjs      # 126 checks
node edge/test/robots_ai.test.mjs          # 13 checks
```

## v0.3 — three tools as skills

v0.3.0 (16 September 2026). The three tools we reach for most inside agent sessions now
ship as installable skills under `skills/`, each a `SKILL.md` plus the tool it drives, so an agent can load the
instructions and the command together:

```bash
npx skills add endrezsoltdios-sketch/axion-readers            # pick wordy-audit, library, guard
```

| Skill | One job |
|---|---|
| `wordy-audit` | Measure a page against the "too many words" checklist and put the page that ranks for the same keyword beside it: words, above-the-fold words, H2s, eyebrows, CTAs, em-dashes, real images, repeated sentences, keyword in the H1. Our own pages read 3,000-5,600 words with 43-265 dashes and no images; the pages that outrank them read 1,100-1,400 words, 0-26 dashes, 5-6 images. |
| `library` | Search your own notes, research and decisions by meaning before writing anything new: BM25 over every paragraph, stdlib only, no embeddings, no keys, ~2 s for 700 files. Prints score, `file:line`, snippet. |
| `guard` | A local policy engine for agent tool calls: request (principal, resource, action) meets a YAML policy, gets ALLOW / DENY / ASK, and every decision is evidence. Claude Code PreToolUse adapter, shadow mode, replay of real traces, hash-chain seal and verify, audit for redundant calls and overreach. Adapters classify, only the policy decides; the selftest asserts that adding an adapter changes no decision. |

The same three files live in `tools/` for plain command-line use. See [CHANGELOG.md](CHANGELOG.md).

## v0.2 — doors, not scrapers

v0.2.5 (6 September 2026, pace fix for `reddit.py`; v0.2.4 the same day added the arXiv lane) brought the arXiv lane (`arxiv_sweep.py`,
`arxiv_fetch.py`), a read-only Reddit door on public feeds (`reddit.py`), and two
tools for keeping an agent estate honest (`skill_audit.py`, `task_watchdog.py`).
See [CHANGELOG.md](CHANGELOG.md).

v0.1 was six readers. v0.2 adds the **door pattern**: for every outside site you
touch, write down the rail you *own* to it (an official API on your own OAuth, a
public feed, a plain page fetch, a real browser with your own profile, or a human)
— then probe those rails, dispatch to them, and refuse to run any *posting* rail
unless a human fired it in words. Nothing here works around a site's defences: a
shut door is reported with the exact human step that opens it. That rule cost us
nothing and saved us a channel more than once.

## The tools

### Reading the outside world

| Tool | One job | Cost |
|------|---------|------|
| `yt.py` | YouTube video → clean merged prose transcript (~70% smaller than raw captions), `--deictic` flags the seconds a speaker points at the screen | 0 |
| `web.py` | Any URL → readable text, junk stripped, polite cap, `--links`; `--ladder` falls to a real browser only when the page is JS-gated, never past a CAPTCHA | 0 |
| `search.py` | Google results → ranked readable list (`--site`, `--us`, `--read N`) | ~$0.002/query (DataForSEO) |
| `sitemap.py` | A site's whole URL inventory + lastmod, without crawling | 0 |
| `diffwatch.py` | Watch pages for change; prints diffs of changed pages only | 0 |
| `pdfx.py` | PDF → text/markdown, page ranges, honest about scans | 0 (`pypdf`) |
| `transcribe.py` | Audio/video file or URL → text, SRT or VTT | free tier (GROQ whisper) |
| `tiktok_read.py` | TikTok video/profile → the public rehydration payload as JSON (hashtags, indexEnabled, stats); waits for the payload to settle | 0 |
| `yt_comments.py` | YouTube comment threads + search on your own OAuth token | 0 |
| `reddit_serp.py` | Reddit sentiment on a topic via Google's index | ~$0.002/query |
| `reddit.py` | Read-only Reddit door, two rungs: Reddit's public Atom feeds (search, new, thread, `voices` = verbatim quotes with author/date/permalink; paces itself, backs off once on 429, then halts) and `browserd.py` rendering Reddit's JSON (`--deep`: scores, nested replies, rules, about). No write op exists. `python tools/reddit.py voices "parking charge" --subs LegalAdviceUK --num 8` | 0 |
| `radar.py` | University/lab AI research sweep from a plain-text source list | 0 |
| `arxiv_sweep.py` | Every new arXiv paper in your categories by metadata (arXiv's own API, no key), scored by whole-word term hits against the lanes in `knowledge/arxiv_lanes.json` (start from `arxiv_lanes.example.json`), seen-ledger so a paper is scored once, ranked markdown shortlist for a reader with judgment. `python tools/arxiv_sweep.py --days 7` | 0 |
| `arxiv_fetch.py` | Full text of the papers a sweep kept: arXiv's HTML build via `web.py`, abstract page as fallback, 3.2 s pacing, header says which. `python tools/arxiv_fetch.py arxiv/sweep_2026-09-06.json --min-score 4` | 0 |

### Thinking cheaply

| Tool | One job | Cost |
|------|---------|------|
| `digest.py` | Big text → small structured first-pass digest via a free-tier model | free tier |
| `ask.py` | Any question → free model, output labelled as a draft | free tier |
| `facts.py` | Ledger of source-verified facts with age tracking — check it BEFORE re-verifying anything; `add --supersedes "<old claim>"` retires the fact it replaces at write time, so a stale value is never served as current (`find --all` shows retired rows) | 0 |

### Doors — connect, probe, post (human-fired)

| Tool | One job | Cost |
|------|---------|------|
| `door.py` | One front door to every site: `list` the rails you own, `probe` them (OK / FAIL / CONFIGURED-OUT), `open` a read rail, `post` only with `--human-fired "<the user's words>"` (logged). Registry = `doors.json`, grow the file not the code | 0 |
| `cfdash.py` | Drive a dashboard that never goes idle (Cloudflare's, for one) in a Chrome you started with `--remote-debugging-port`: one command per call over CDP — `goto`, `text`, `shot`, `click`, `fill`, `select`, `eval`, `evalfile`. Waits for domcontentloaded plus a fixed settle, never types a password, prints `login_wall: true` and stops when it meets one | 0 (`playwright`) |
| `browserd.py` | Your own browser door: a persistent real-Chrome daemon (Playwright) with your own profile, driven over a local port; halts on a login wall or CAPTCHA instead of guessing | 0 |
| `yt_reply.py` | Post YouTube comment replies from a fire sheet: target found by verbatim text, dry run by default, refuses to post unless the token's channel matches `--as`, skips anything already replied to, JSONL ledger | 0 |
| `reddit_api.py` | Reddit's official OAuth rail on a script app you create once: whoami, per-sub `banned` check, rules, search, thread; `comment`/`post` only with `--human-fired` | 0 |
| `botauth.py` | Web Bot Auth for your own readers: generate an Ed25519 key, publish it (signed) at `/.well-known/http-message-signatures-directory`, sign every request with `Signature-Agent` / `Signature-Input` / `Signature` (RFC 9421), test against Cloudflare's checker. Honest identity is the connector: sites that verify signed agents let you in on purpose. `web.py --signed` uses it | 0 (`cryptography`) |
| `cf.py` | Cloudflare from the command line on the token wrangler already uses: workers, zones, per-Worker requests/errors (GraphQL), zone traffic, KV key lists, live tail; prints CONFIGURED-OUT for what the token cannot do | 0 |
| `gads.py` / `gads_auth.py` | Google Ads API connector, dry-run by default, every mutation logs its old value for rollback | 0 |
| `indexnow.py` | Push URLs to IndexNow (Bing, Yandex, Seznam, Naver share one endpoint) | 0 |

### Agent readiness and edge traffic (v0.4, sections above)

| Tool | One job | Cost |
|------|---------|------|
| `dns_aid.py` | Publish `_a2a._agents` / `_mcp._agents` SVCB records through the dashboard form, prove each back over DNS-over-HTTPS, optionally press Enable DNSSEC | 0 |
| `cf_readiness.py` | Cloudflare's Agent Readiness diagnostic for every zone as a JSON file and a markdown table, with Cloudflare's own Status line per item | 0 (`playwright`) |
| `cf_bot_submit.py` | Fill Cloudflare's Bots and Agents Directory form from a JSON spec, read every value back, stop at the review unless `--apply` | 0 |
| `bot_ranges.py` | The address blocks Google, Bing, OpenAI, Perplexity and Apple publish for their own bots, into one data file | 0 |
| `traffic_read.py` | Who was at each door, from the edge meter over the Analytics Engine SQL API: people, verified bots, impersonators, tools, headless, monitors, probes | 0 |
| `edge/` | The Worker-side classifier and meter the reader reads, plus its 126-case suite | 0 |

### Measuring pages and searching your own corpus

| Tool | One job | Cost |
|------|---------|------|
| `wordy_audit.py` | One page against the too-many-words checklist, the ranking page beside it. `python tools/wordy_audit.py https://yours.com/p --winner https://theirs.com/p --kw "keyword"`; `--json`; `--selftest` | 0 |
| `library.py` | BM25 search over your own markdown folders (`LIBRARY_ROOTS`), cached index, `file:line` hits. `python tools/library.py "refund policy" --top 5` | 0 |
| `guard.py` | Policy decisions for tool calls with evidence: `init`, `check`, `explain`, `hook --shadow`, `replay`, `audit`, `seal`, `verify`, `selftest` | 0 (`pyyaml`) |

### Keeping code honest

| Tool | One job | Cost |
|------|---------|------|
| `regex_lint.py` | Catch the `"\b"`-in-a-string bug shape: a regex built from a string with single backslashes, control bytes in source, paste-shaped patterns | 0 |
| `fix_mojibake.py` | Repair cp1252 double-encoding in text files | 0 |
| `worker_sizes.py` | Measure each Cloudflare Worker's uncompressed bundle size against the plan limit | 0 |
| `skill_audit.py` | Which Claude Code skills were actually invoked: counts every `Skill` tool call in the project's session transcripts (`~/.claude/projects/<repo>`) against `.claude/commands/*.md` — the evidence for retiring skills nobody reaches for. `python tools/skill_audit.py --days 30` | 0 |
| `task_watchdog.py` | Catch scheduled-task misses the scheduler hides: a registry (`knowledge/tasks_registry.json`, start from the example) says what should run and which file proves it ran; the tool checks the files, exit 1 on a miss, `--alert` posts to Telegram. `python tools/task_watchdog.py --registry knowledge/tasks_registry.json` | 0 |

## Quickstart

```bash
python tools/yt.py "https://www.youtube.com/watch?v=..."          # transcript
python tools/web.py example.com --ladder                           # page text, browser only if gated
python tools/door.py list                                          # which rails you own
python tools/door.py probe                                         # which answer right now
python tools/cf.py traffic --days 7                                # requests per Worker
python tools/yt_reply.py queue.json                                # dry run; add --post --as "<channel>" to fire
python tools/facts.py add "claim" --source "https://..."           # record a verified fact
python tools/arxiv_sweep.py --days 7                               # ranked new papers, your lanes
python tools/reddit.py voices "parking charge" --subs LegalAdviceUK # verbatim Reddit voices, no login
```

Every tool answers `--help`; the v0.2.4 and v0.3.0 tools also answer `--selftest` (`reddit.py selftest`,
`guard.py selftest`) with no network, so you can check a copy before trusting it.

The v0.4 tools answer `--selftest` too, with no network and no Chrome:
`dns_aid.py`, `cf_readiness.py`, `cf_bot_submit.py`, `bot_ranges.py`, `traffic_read.py`.

Every v0.5 tool answers `--selftest` with no network, and the counts on the published copies are:
`buyer_probe.py` 26, `a2a_audit.py` 30, `dns_aid_conform.py` 18, `twin_diff.py` 30, `readiness_read.py` 30,
`vendor_news.py` 20, `crawler_gate.py` 22, `context_cost.py` 20, `agent_ready_scan.py` 32, and the two repairs
`dns_aid.py` 17, `arxiv_sweep.py` 13. `buyer_probe.py` runs its checks against a stub HTTP server it starts on
loopback, so the selftest never leaves the machine.

Requirements: Python 3.10+. `yt.py`/`transcribe.py` need `yt-dlp` for URL input.
`pdfx.py` needs `pypdf`. `browserd.py` needs `playwright` + a Chrome. `search.py`
and `reddit_serp.py` need `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD`. `digest.py`,
`ask.py`, `transcribe.py` need `GROQ_API_KEY` (free tier). `cf.py` needs
`CLOUDFLARE_API_TOKEN`. `guard.py` needs `pyyaml`. `yt_*.py` need a `youtube_token.json` (Google OAuth refresh
token, scope `youtube.force-ssl`). `reddit_api.py` needs a Reddit script app (see its
docstring); `reddit.py --deep` needs `browserd.py` running. `task_watchdog.py --alert`
reads `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from an environment file beside the tools.
`dns_aid.py`, `cf_readiness.py` and `cf_bot_submit.py` need `CLOUDFLARE_ACCOUNT_ID` and a
Chrome started with `--remote-debugging-port=9226` that you logged into yourself;
`cf_readiness.py` also needs `playwright`. `traffic_read.py` needs a `CLOUDFLARE_API_TOKEN`
carrying **Account Analytics Read**. `edge/` needs Node 18+ to run its suite. Everything
else is stdlib only. No tool prints a key.

## The token pipeline

```
yt.py / web.py  →  digest.py (free model, first pass)  →  your frontier model
                                                            verifies the DRAFT,
facts.py find <topic>  →  already verified? cite it, don't re-verify.
door.py probe   →  which rails are alive, before an agent burns a turn finding out.
```

Ingestion on free compute, judgment on frontier compute, verification done once.
In our production use this cuts research reading cost by roughly 80–90%.

## Design rules

- One tool, one job. Text in, text out. Everything composes.
- Zero model calls inside a tool (the two free-tier tools excepted, and they label their output as a draft).
- Honest failure: tools report what they couldn't fetch instead of guessing.
- Polite: our reader identifies itself and caps output.
- **Posting is human-fired.** No tool here posts, comments or submits without a `--human-fired` quote, which it logs. Read rails may run unattended; write rails may not.
- **No evasion.** A login wall, a CAPTCHA or a rate limit is a wall. The tools stop there and print the human step.

## License

[FSL-1.1-ALv2](LICENSE.md) — free to use, including commercially, inside your
own workflows; you may not offer these tools (or substantially similar
derivatives) as a competing product or service. Converts to Apache 2.0 two
years after each release. Copyright 2026 Axion Labs (Zsolt Dios).

Commercial licensing, hosted versions (weekly wordy audits of a whole site with the referring-domain count beside
each row; managed guard policies), questions, corrections: **hello@getaxionlabs.com**

## From the same workshop

- [Axion Gateway](https://mcp.getaxionlabs.com) — one MCP connection fronting
  verified UK/US consumer datasets (parking appeal odds, insurance denial rates,
  NYC violations), with signed answer receipts an agent can verify offline.
- Machine-readable door passports on every property:
  `/.well-known/agent-door.json`
- The citation object (`axion-cite/v1`) on every machine endpoint: title, canonical
  URL, publisher, source, checked date — so an answer taken by a machine says where
  the credit goes.
