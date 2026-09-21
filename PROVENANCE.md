# PROVENANCE — dated public disclosure of the tools in this repository

Purpose: prior art. Each row records what a tool does and the SHA-256 (first 16 hex) of the file as shipped in the
release named at the top of this file. A dated public record of a method prevents anyone else from patenting it.
Copyright (c) 2026 Axion Labs / Zsolt Dios. Licence: FSL-1.1-ALv2 (LICENSE.md).

Release: v0.4.0, 22 September 2026. The three skills (`skills/*/SKILL.md`) carry the v0.3.0 date, 16 September
2026. The `edge/` rows are the JavaScript half of the v0.4.0 traffic lane: the classifier, the meter, the shared
checks, the suite and the generated range table.

| Tool | SHA-256 (16) at v0.4.0 | What it does (first line of its docstring) |
|---|---|---|
| arxiv_fetch.py | 9bd0e9a64e19389f | arxiv_fetch.py -- pull the full text of the papers a sweep kept, for a cheap reader to read locally. |
| arxiv_sweep.py | bee41faf54b19b87 | arxiv_sweep.py -- every new arXiv paper in your categories, scored against the lanes you run. |
| ask.py | 009a822ca1f851c6 | ask.py — the free wing on the command line. One job: put a question to a free |
| bot_ranges.py | e52aa6fa594537a8 | bot_ranges.py: fetch the IP ranges the big crawler operators publish for their own bots, into one JSON file the |
| botauth.py | 8be14ff2fba405dc | botauth.py -- Web Bot Auth for OUR OWN readers (Fig Tree primitive, 6 Sep 2026). |
| browserd.py | 88398e13498d5835 | browserd — Axion's own browser door (Fig Tree primitive, 2 Sep 2026). |
| cf.py | 704edbc8a4a9385c | cf.py -- Axion's own Cloudflare connector (Fig Tree primitive, 6 Sep 2026). |
| cf_bot_submit.py | a896165c2834bf82 | cf_bot_submit.py: fill and submit Cloudflare's Bots and Agents Directory application (BotBase) for one of your |
| cf_readiness.py | 1a2e7a88b16dc952 | cf_readiness.py: read Cloudflare's Agent Readiness diagnostic for every zone you hold, through a Chrome |
| cfdash.py | 7f61f25017f4e3d6 | cfdash.py -- drive a logged-in Chrome over CDP, one command per call (Fig Tree primitive, 6 Sep 2026). |
| diffwatch.py | 2797e3e8f81028b2 | diffwatch.py — page change watcher. One job: tell me what changed on a page |
| digest.py | 3fea92b2357cee90 | digest.py — cheap-model first pass over any text. One job: big text in, |
| dns_aid.py | 14c46e013321cf1a | dns_aid.py: publish the DNS for AI Discovery (DNS-AID) entry points for a zone, through the logged-in Cloudflare |
| door.py | 538ac6df2dfa56b9 | door.py -- one front door to every site Axion touches (Fig Tree primitive, 6 Sep 2026). |
| facts.py | b8d5be24f6ff3598 | facts.py — the house ledger of verified facts. One job: never verify the |
| fix_mojibake.py | 5ecefc9dd7c2b771 | fix_mojibake — repair cp1252 double-encoding in text files. Zero model tokens. |
| gads.py | cef5956d857957fe | gads.py — Google Ads API connector (Axion own-stack, 1 Sep 2026). |
| gads_auth.py | 0f3ce3a724deedc6 | gads_auth.py — one-time OAuth setup for the Google Ads connector. |
| guard.py | 67b891062c208542 | guard.py - local policy decision and audit for tool calls. Our first field is our own Claude Code session: the adapter turns a |
| indexnow.py | e4fd42396a53e160 | indexnow.py — push URLs to IndexNow (Bing, Yandex, Seznam, Naver share one endpoint). 5 Sep 2026. |
| library.py | d5c704e5009706e8 | library.py - meaning search over our own idea corpus: BM25 over paragraphs of the markdown folders you name (LIBRARY_ROOTS), |
| pdfx.py | f6b47226b5cdf405 | pdfx.py — PDF in, text or markdown out. One job: read a PDF without opening it. |
| radar.py | 34aa1d94c08452de | radar.py — university & lab AI research radar. One job: what did the |
| reddit.py | 76314ce9b66343a1 | reddit.py — Axion's own READ-ONLY Reddit door (Fig Tree primitive, 4 Sep 2026; feed rung 6 Sep 2026). |
| reddit_api.py | 9ecafa0cd273321b | reddit_api.py -- Reddit's OFFICIAL API rail (Fig Tree primitive, 6 Sep 2026). |
| reddit_serp.py | 2cf29de657efe06b | reddit_serp.py — Reddit sentiment via Google's index (DataForSEO SERP API). |
| regex_lint.py | 605a7023ddb5f85f | regex_lint.py — catch the bug shape of 4 Sep 2026 before it ships (rule 30). |
| search.py | 1c0c9f496cb5d3a4 | search.py — read internet search results as clean text. One job: query in, |
| sitemap.py | d329d2d9a455854e | sitemap.py — read any site's sitemap. One job: the full URL inventory of a |
| skill_audit.py | 894cedd6fc50d428 | skill_audit.py -- which skills were actually invoked, from the session transcripts (6 Sep 2026), |
| task_watchdog.py | f1ecc71b9bd7ec47 | task_watchdog.py — catches scheduled-task misses the scheduler hides. |
| tiktok_read.py | 9c33cea08e514afa | tiktok_read — 0-token reader for TikTok's PUBLIC rehydration payload. |
| traffic_read.py | 904c3d58195b55d8 | traffic_read.py: who is really at each door, read back from the edge meter (people, verified bots, impersonators, |
| transcribe.py | 2438889c69135043 | transcribe.py — audio or video in, text out. One job: turn a media file into |
| web.py | e979446ef087f3f7 | web.py — read any web page as clean text. One job: URL in, readable text out. |
| wordy_audit.py | 6153021e55f8b9ae | wordy_audit.py - measure a page against the "too many words" checklist (Edward Sturm, 14 Sep 2026, watch?v=2Al_GYBMVmg) |
| worker_sizes.py | dbd922780fbe0318 | worker_sizes.py — measure each fact-site Worker's UNCOMPRESSED bundle size |
| yt.py | 5ac045a6cb57d26c | yt.py — read a YouTube video as clean text. One job: URL in, transcript out. |
| yt_comments.py | 09ff98535d35119b | yt_comments.py — read YouTube comment threads through our OWN OAuth token (Fig Tree primitive, 4 Sep 2026). |
| yt_reply.py | 58773333887fdf14 | yt_reply.py -- post YouTube comment replies from a fire sheet. Human-fired only. |
| edge/src/traffic_class.js | 572aba2228c23728 | traffic_class.js - who is really at the door: one class per request, decided at the edge (22 Sep 2026). |
| edge/src/traffic_meter.js | 45e6a125b1313a58 | traffic_meter.js - every request, one data point, classified at the edge (22 Sep 2026). |
| edge/src/traffic_checks.js | bb5c79dc07179657 | traffic_checks.js - the checks every door runs on its traffic meter (22 Sep 2026). |
| edge/test/traffic_class.test.mjs | d29de089b93254bb | traffic_class.test.mjs - the edge classifier against named cases (22 Sep 2026). |
| edge/data/bot_ranges.js | 3ebcc0a858b148c2 | GENERATED by tools/bot_ranges.py on 2026-09-21T19:55:36Z; never hand edit. Address blocks the operators publish for their own bots. */ |
