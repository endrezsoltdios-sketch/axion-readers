# Axion Readers

Small, Unix-style tools for AI-agent workflows — built because we kept rewriting
these exact scripts inside [Claude Code](https://claude.com/claude-code) sessions
and burning tokens on raw ingestion and on re-discovering which door to a site
still works. Each tool does ONE job, costs zero model tokens (two use a free
tier), prints clean text a model — or a human — can read cheaply, and fails with
an honest line instead of a traceback.

Built and field-tested in production at [Axion Labs](https://getaxionlabs.com),
where they feed a multi-agent research and publishing pipeline daily.

## v0.2 — doors, not scrapers

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
| `radar.py` | University/lab AI research sweep from a plain-text source list | 0 |

### Thinking cheaply

| Tool | One job | Cost |
|------|---------|------|
| `digest.py` | Big text → small structured first-pass digest via a free-tier model | free tier |
| `ask.py` | Any question → free model, output labelled as a draft | free tier |
| `facts.py` | Append-only ledger of source-verified facts with age tracking — check it BEFORE re-verifying anything | 0 |

### Doors — connect, probe, post (human-fired)

| Tool | One job | Cost |
|------|---------|------|
| `door.py` | One front door to every site: `list` the rails you own, `probe` them (OK / FAIL / CONFIGURED-OUT), `open` a read rail, `post` only with `--human-fired "<the user's words>"` (logged). Registry = `doors.json`, grow the file not the code | 0 |
| `browserd.py` | Your own browser door: a persistent real-Chrome daemon (Playwright) with your own profile, driven over a local port; halts on a login wall or CAPTCHA instead of guessing | 0 |
| `yt_reply.py` | Post YouTube comment replies from a fire sheet: target found by verbatim text, dry run by default, refuses to post unless the token's channel matches `--as`, skips anything already replied to, JSONL ledger | 0 |
| `reddit_api.py` | Reddit's official OAuth rail on a script app you create once: whoami, per-sub `banned` check, rules, search, thread; `comment`/`post` only with `--human-fired` | 0 |
| `cf.py` | Cloudflare from the command line on the token wrangler already uses: workers, zones, per-Worker requests/errors (GraphQL), zone traffic, KV key lists, live tail; prints CONFIGURED-OUT for what the token cannot do | 0 |
| `gads.py` / `gads_auth.py` | Google Ads API connector, dry-run by default, every mutation logs its old value for rollback | 0 |
| `indexnow.py` | Push URLs to IndexNow (Bing, Yandex, Seznam, Naver share one endpoint) | 0 |

### Keeping code honest

| Tool | One job | Cost |
|------|---------|------|
| `regex_lint.py` | Catch the `"\b"`-in-a-string bug shape: a regex built from a string with single backslashes, control bytes in source, paste-shaped patterns | 0 |
| `fix_mojibake.py` | Repair cp1252 double-encoding in text files | 0 |
| `worker_sizes.py` | Measure each Cloudflare Worker's uncompressed bundle size against the plan limit | 0 |

## Quickstart

```bash
python tools/yt.py "https://www.youtube.com/watch?v=..."          # transcript
python tools/web.py example.com --ladder                           # page text, browser only if gated
python tools/door.py list                                          # which rails you own
python tools/door.py probe                                         # which answer right now
python tools/cf.py traffic --days 7                                # requests per Worker
python tools/yt_reply.py queue.json                                # dry run; add --post --as "<channel>" to fire
python tools/facts.py add "claim" --source "https://..."           # record a verified fact
```

Requirements: Python 3.10+. `yt.py`/`transcribe.py` need `yt-dlp` for URL input.
`pdfx.py` needs `pypdf`. `browserd.py` needs `playwright` + a Chrome. `search.py`
and `reddit_serp.py` need `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD`. `digest.py`,
`ask.py`, `transcribe.py` need `GROQ_API_KEY` (free tier). `cf.py` needs
`CLOUDFLARE_API_TOKEN`. `yt_*.py` need a `youtube_token.json` (Google OAuth refresh
token, scope `youtube.force-ssl`). `reddit_api.py` needs a Reddit script app (see its
docstring). Everything else is stdlib only. No tool prints a key.

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

Commercial licensing, hosted versions, questions, corrections: **hello@getaxionlabs.com**

## From the same workshop

- [Axion Gateway](https://mcp.getaxionlabs.com) — one MCP connection fronting
  verified UK/US consumer datasets (parking appeal odds, insurance denial rates,
  NYC violations), with signed answer receipts an agent can verify offline.
- Machine-readable door passports on every property:
  `/.well-known/agent-door.json`
- The citation object (`axion-cite/v1`) on every machine endpoint: title, canonical
  URL, publisher, source, checked date — so an answer taken by a machine says where
  the credit goes.
