# Changelog

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
