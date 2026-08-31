# Axion Readers

Six tiny, Unix-style reading tools for AI-agent workflows — built because we kept
rewriting these exact scripts inside [Claude Code](https://claude.com/claude-code)
sessions and burning tokens on raw ingestion. Each tool does ONE job, costs zero
model tokens (except `digest.py`, which uses a free tier), and prints clean text
a model — or a human — can read cheaply.

Built and field-tested in production at [Axion Labs](https://getaxionlabs.com),
where they feed a multi-agent research pipeline daily.

## The tools

| Tool | One job | Cost |
|------|---------|------|
| `yt.py` | YouTube video → clean merged prose transcript (~70% smaller than raw captions), `--deictic` flags the seconds a speaker points at the screen | 0 |
| `web.py` | Any URL → readable text, junk stripped, polite cap, `--links` mode | 0 |
| `search.py` | Google results → ranked readable list (`--site`, `--us`, `--read N`) | ~$0.002/query (DataForSEO) |
| `digest.py` | Big text → small structured first-pass digest via a free-tier model, so your frontier model reads a 1k-token draft instead of a 25k-token transcript | free tier |
| `facts.py` | Append-only ledger of source-verified facts with age tracking — check it BEFORE re-verifying anything | 0 |
| `radar.py` | University/lab AI research sweep (MIT, Stanford, Berkeley, CMU, arXiv + any RSS you add) from a plain-text source list | 0 |

## Quickstart

```bash
python tools/yt.py "https://www.youtube.com/watch?v=..."          # transcript
python tools/web.py example.com                                    # page text
python tools/radar.py --days 8                                     # research sweep
python tools/facts.py add "claim" --source "https://..."           # record a verified fact
python tools/yt.py <url> --out t.txt && python tools/digest.py t.txt --frame video
```

Requirements: Python 3.10+. `yt.py` needs `yt-dlp` (`pip install yt-dlp`; a
[Deno](https://deno.land) install future-proofs YouTube extraction). `search.py`
needs `DATAFORSEO_LOGIN`/`DATAFORSEO_PASSWORD` env vars. `digest.py` needs
`GROQ_API_KEY` (free tier). Everything else is stdlib only.

## The token pipeline

The pattern these tools exist for:

```
yt.py / web.py  →  digest.py (free model, first pass)  →  your frontier model
                                                            verifies the DRAFT,
facts.py find <topic>  →  already verified? cite it, don't re-verify.
```

Ingestion on free compute, judgment on frontier compute, verification done once.
In our production use this cuts research reading cost by roughly 80–90%.

## Design rules

- One tool, one job. Text in, text out. Everything composes.
- Zero model calls inside the tool (digest.py's single free-tier call excepted).
- Honest failure: tools report what they couldn't fetch instead of guessing.
- Polite: our reader identifies itself (`AxionReader/1.0`) and caps output.

## License

[FSL-1.1-ALv2](LICENSE.md) — free to use, including commercially, inside your
own workflows; you may not offer these tools (or substantially similar
derivatives) as a competing product or service. Converts to Apache 2.0 two
years after each release. Copyright 2026 Axion Labs (Zsolt Dios).

Commercial licensing, questions, corrections: **hello@getaxionlabs.com**

## From the same workshop

- [Axion Gateway](https://mcp.getaxionlabs.com) — one MCP connection fronting
  verified UK/US consumer datasets (parking appeal odds, insurance denial rates,
  NYC violations), with signed answer receipts an agent can verify offline.
- Machine-readable door passports on every property:
  `/.well-known/agent-door.json`
