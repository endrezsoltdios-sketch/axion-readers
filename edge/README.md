# edge — who is really at the door, decided at the edge

Four JavaScript modules and one generated data file for a Cloudflare Worker (or any runtime with a `Request` that
carries `cf`). They answer a question a page-view counter cannot: of everything that hit this host, what was a
person, what was a declared AI agent telling the truth, what was a declared AI agent lying about who it is, and
what was a script wearing a browser's name.

Nothing here stores an address, a full user-agent, a query string, a cookie or a referrer. The class, the
operator, the network name and the path are what a data point carries, and the suite asserts that.

```
src/traffic_class.js    the classifier: one class per request, pure functions, no I/O
src/traffic_meter.js    withTrafficMeter(handler): one Analytics Engine data point per outside request
src/traffic_checks.js   the checks to run against your own worker from your own suite
test/traffic_class.test.mjs   the classifier's own suite, 126 named cases
data/bot_ranges.js      generated: the address blocks operators publish for their own bots
```

## Use it

```js
import { withTrafficMeter } from "./traffic_meter.js";

export default withTrafficMeter({
  async fetch(request, env, ctx) {
    return new Response("hello");
  },
});
```

In `wrangler.jsonc`:

```jsonc
"analytics_engine_datasets": [{ "binding": "TRAFFIC", "dataset": "edge_traffic" }]
```

The wrapper meters `fetch` exactly once per outside request. A handler that re-enters its own `this.fetch` (a
markdown twin, a figure hook, a front door answering for a skill) is not counted twice, and no marker header is
needed. A thrown handler is metered as status 500 and re-thrown untouched. A failing store never breaks a page.

Read the meter back with `python ../tools/traffic_read.py` (Analytics Engine SQL API, Account Analytics Read).

## Classes

| `cls` | what it means |
|---|---|
| `person` | browser user-agent, browser navigation headers, not from a hosting network |
| `person-dc` | browser user-agent from a hosting or cloud network: automation dressed as a browser, or a VPN |
| `headless` | a headless browser by its own name, or a browser string on a page request with no navigation headers |
| `ai` | a declared AI operator (GPTBot, ClaudeBot, PerplexityBot, ChatGPT-User and the rest) |
| `crawler` | a declared search or social crawler (Googlebot, bingbot, Applebot, facebookexternalhit) |
| `tool` | an HTTP library or command (curl, python-requests, Go-http-client, axios, okhttp) |
| `monitor` | an uptime or performance checker |
| `own` | your own readers and scanners, so your own audits never inflate a stranger count |
| `other-bot` / `empty` | says bot, spider or crawl and matched nothing above / no user-agent at all |

For `ai` and `crawler` a second field says whether the claim is true: `v` the connecting address is inside the
blocks that operator publishes, `i` the operator publishes blocks and the address is outside them (impersonated),
`c` the operator publishes no list but Cloudflare's free verified-bot category is set, `n` no proof either way.

A user-agent is a claim. Googlebot from a Hetzner box is not Google. Cloudflare's own verified-bot flag in
`request.cf` costs an Enterprise plan; this reaches the same fact from the operators' own published files, free.

## Two things to set

1. `OWN_UA_NEEDLE` in `src/traffic_class.js` is the word your own tools put in their user-agent. It ships as
   `example-bot`. Set it, or set it to `""` to turn the `own` class off.
2. `data/bot_ranges.js` is generated. Refresh it with `python ../tools/bot_ranges.py`, which fetches the files
   Google, Bing, OpenAI, Perplexity and Apple publish about their own bots. Never hand edit it. A source that is
   down is carried over from the previous file, so a bot never turns "impersonated" because a list was offline.

## Run the suite

```bash
node test/traffic_class.test.mjs      # 126 named cases: address maths, path classes, every class, the meter
```

To check your own worker end to end, call `trafficChecks` from `src/traffic_checks.js` inside your own suite with
the worker as exported, your test env, a context and your `check(name, ok, extra)`.

## License

[FSL-1.1-ALv2](../LICENSE.md). Copyright 2026 Axion Labs (Zsolt Dios).
