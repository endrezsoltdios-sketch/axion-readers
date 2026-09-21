/* traffic_class.test.mjs - the edge classifier against named cases (22 Sep 2026).
 * Run: node test/traffic_class.test.mjs (the range table is a generated JS module, no import hook needed). */
import { classify, pathClass, parseCidr, parseIp, inCidr, addressIn, RANGE_OPERATORS, RANGES_FETCHED,
  AI_UAS, CRAWLER_UAS, TOOL_UAS, HEADLESS_UAS, MONITOR_UAS, buildTable, verifyOperator, browserEvidence } from "../src/traffic_class.js";
import { dataPoint, withTrafficMeter, meterTraffic } from "../src/traffic_meter.js";

let pass = 0, fail = 0;
const check = (name, ok, extra = "") => { if (ok) pass++; else { fail++; console.log("FAIL", name, extra); } };

const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const req = (path, headers = {}, cf = {}, method = "GET") =>
  Object.assign(new Request("https://example.com" + path, { method, headers }), { cf });
const browserHeaders = (ua = CHROME) => ({ "user-agent": ua, "accept": "text/html,application/xhtml+xml", "accept-language": "en-GB,en;q=0.9", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "cf-connecting-ip": "86.1.2.3" });

// address maths
check("ipv4 parse", parseIp("192.168.1.10").n === 3232235786n);
check("ipv6 parse", parseIp("2001:db8::1").n === 0x20010db8000000000000000000000001n);
check("ipv6 embedded v4", parseIp("::ffff:1.2.3.4").n === 0xffff01020304n);
check("bad ip", parseIp("999.1.1.1") === null && parseIp("nope") === null);
check("cidr v4 in", inCidr(parseIp("66.249.66.5"), parseCidr("66.249.64.0/19")));
check("cidr v4 out", !inCidr(parseIp("66.250.0.1"), parseCidr("66.249.64.0/19")));
check("cidr v6 in", inCidr(parseIp("2001:4860:4801:10::1"), parseCidr("2001:4860:4801:10::/64")));
check("cidr family mismatch", !inCidr(parseIp("1.2.3.4"), parseCidr("2001:db8::/32")));
check("cidr /0", inCidr(parseIp("9.9.9.9"), parseCidr("0.0.0.0/0")));
check("bad cidr", parseCidr("1.2.3.4/40") === null);

// the range table shipped
check("range operators present", ["google", "bing", "openai", "perplexity", "apple"].every((o) => RANGE_OPERATORS.includes(o)), RANGE_OPERATORS.join(","));
check("ranges dated", /^\d{4}-\d{2}-\d{2}T/.test(RANGES_FETCHED), RANGES_FETCHED);
check("google block known", addressIn("google", "66.249.66.1") === true);
check("hetzner is not google", addressIn("google", "5.9.10.11") === false);
check("unknown operator is null", addressIn("nobody", "1.1.1.1") === null);

// path classes
check("page", pathClass("/", "GET") === "page" && pathClass("/insurers/aetna", "GET") === "page");
check("api", pathClass("/api/answer", "GET") === "api" && pathClass("/q", "POST") === "api");
check("machine", ["/.well-known/openid-configuration", "/llms.txt", "/openapi.json", "/auth.md", "/a2a", "/robots.txt", "/sitemap.xml", "/insurers/aetna.md"].every((p) => pathClass(p, "GET") === "machine"));
check("asset", pathClass("/img/clock.png", "GET") === "asset" && pathClass("/app.js", "GET") === "asset");
check("beacon", pathClass("/e", "POST") === "beacon" && pathClass("/stats", "GET") === "beacon");
check("probe", ["/wp-login.php", "/." + "env", "/xmlrpc.php", "/vendor/phpunit/x", "/.git/config", "/backup.sql"].every((p) => pathClass(p, "GET") === "probe"));
check("well-known is machine not probe", pathClass("/.well-known/agent-card.json", "GET") === "machine");

// classes
let c = classify(req("/insurers/aetna", browserHeaders(), { asn: 5089, asOrganization: "Virgin Media", country: "GB" }));
check("person", c.cls === "person" && c.family === "chrome" && c.ver === "-" && c.pcls === "page" && c.country === "GB", JSON.stringify(c));
c = classify(req("/", browserHeaders(), { asn: 16509, asOrganization: "Amazon.com, Inc.", country: "US" }));
check("person-dc by asn", c.cls === "person-dc" && c.dc === true, c.cls);
c = classify(req("/", browserHeaders(), { asn: 999999, asOrganization: "Some Hosting GmbH", country: "DE" }));
check("person-dc by org word", c.cls === "person-dc", c.cls);
c = classify(req("/", { "user-agent": CHROME, "cf-connecting-ip": "86.1.2.3" }, { asn: 5089, asOrganization: "Virgin Media" }));
check("browser string without nav headers on a page = headless", c.cls === "headless", c.cls);
c = classify(req("/img/x.png", { "user-agent": CHROME, "cf-connecting-ip": "86.1.2.3" }, { asn: 5089, asOrganization: "Virgin Media" }));
check("asset without nav headers stays person", c.cls === "person", c.cls);
c = classify(req("/", browserHeaders("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/140.0.0.0 Safari/537.36"), { asn: 5089 }));
check("headless by name", c.cls === "headless", c.cls);
c = classify(req("/api/answer", { "user-agent": "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)", "cf-connecting-ip": "20.171.207.5" }, { asn: 8075 }));
check("ai gptbot", c.cls === "ai" && c.operator === "openai-gptbot", JSON.stringify(c));
check("gptbot from openai block is verified or impersonated, never n", c.ver === "v" || c.ver === "i", c.ver);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; GPTBot/1.2)", "cf-connecting-ip": "5.9.10.11" }, { asn: 24940 }));
check("gptbot from hetzner = impersonated", c.ver === "i", c.ver);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "cf-connecting-ip": "160.79.104.10" }, { asn: 399358, verifiedBotCategory: "AI Crawler" }));
check("claudebot: no list, cloudflare category = c", c.cls === "ai" && c.operator === "anthropic-claudebot" && c.ver === "c", JSON.stringify(c));
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)", "cf-connecting-ip": "160.79.104.10" }, { asn: 399358 }));
check("claudebot: no list, no category = n", c.ver === "n", c.ver);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "cf-connecting-ip": "66.249.66.1" }, { asn: 15169 }));
check("googlebot verified", c.cls === "crawler" && c.operator === "googlebot" && c.ver === "v", JSON.stringify(c));
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "cf-connecting-ip": "5.9.10.11" }, { asn: 24940 }));
check("googlebot impersonated", c.ver === "i", c.ver);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)", "cf-connecting-ip": "40.77.167.1" }, { asn: 8075 }));
check("bingbot verified", c.cls === "crawler" && c.ver === "v", JSON.stringify(c));
c = classify(req("/api/x", { "user-agent": "curl/8.4.0" }, {}));
check("tool curl", c.cls === "tool" && c.operator === "curl", JSON.stringify(c));
c = classify(req("/", { "user-agent": "python-requests/2.32" }, {}));
check("tool python", c.cls === "tool", c.cls);
c = classify(req("/", { "user-agent": "Go-http-client/2.0" }, {}));
check("tool go", c.cls === "tool", c.cls);
c = classify(req("/", { "user-agent": "Mozilla/5.0+(compatible; UptimeRobot/2.0; http://www.uptimerobot.com/)" }, {}));
check("monitor", c.cls === "monitor", c.cls);
c = classify(req("/", { "user-agent": "example-bot/1.0 (+https://example.com)" }, {}));
check("own", c.cls === "own" && c.operator === "own", c.cls);
c = classify(req("/", { "user-agent": "Mozilla/5.0 example-bot-ranges/1.0" }, {}));
check("own by any needle match", c.cls === "own", c.cls);
c = classify(req("/", {}, {}));
check("empty", c.cls === "empty", c.cls);
c = classify(req("/", { "user-agent": "SomethingSpider/3.1 (+http://example.com)" }, {}));
check("other-bot", c.cls === "other-bot" && c.operator.includes("spider"), JSON.stringify(c));
c = classify(req("/", { "user-agent": "facebookexternalhit/1.1" }, {}));
check("social preview = crawler", c.cls === "crawler" && c.operator === "meta-preview", c.operator);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)", "cf-connecting-ip": "1.2.3.4" }, {}));
check("perplexity impersonated from a random address", c.cls === "ai" && c.ver === "i", JSON.stringify(c));
c = classify(req("/wp-login.php", { "user-agent": "Mozilla/5.0 zgrab/0.x" }, {}));
check("probe path", c.pcls === "probe", c.pcls);
c = classify(req("/insurers/aetna?x=1&secret=2", browserHeaders(), { asn: 5089 }));
check("no query string kept", c.path === "/insurers/aetna", c.path);
c = classify(req("/", browserHeaders(), { asn: 5089, asOrganization: "A".repeat(200) }));
check("network name capped", c.org.length === 60);

/* ---------- 1. scripts wearing a browser string ----------
 * Every case below was "person" before 22 Sep 2026, which is the number that matters most on this site. */
const REAL = {
  chrome: CHROME,
  "chrome mobile": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
  "safari iphone": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
  "safari mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
  firefox: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
  edge: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
  samsung: "Mozilla/5.0 (Linux; Android 14; SAMSUNG SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/135.0.0.0 Mobile Safari/537.36",
};
const HOME = { asn: 5089, asOrganization: "Virgin Media Limited", country: "GB", httpProtocol: "HTTP/2", tlsVersion: "TLSv1.3" };
for (const [name, ua] of Object.entries(REAL)) {
  const cc = classify(req("/insurers/aetna", browserHeaders(ua), HOME));
  check(`real browser stays person: ${name}`, cc.cls === "person", `${cc.cls} ev=${cc.ev}`);
}
c = classify(req("/", { "user-agent": CHROME, accept: "*/*", "accept-language": "en-US", "cf-connecting-ip": "1.2.3.4" }, { asn: 999, asOrganization: "Some ISP" }));
check("python-requests with a Chrome UA and one added header = headless", c.cls === "headless", `${c.cls} ev=${c.ev}`);
c = classify(req("/", { "user-agent": CHROME, accept: "*/*" }, { asn: 999, asOrganization: "Some ISP", httpProtocol: "HTTP/1.1" }));
check("python-requests with a Chrome UA and no navigation headers = headless", c.cls === "headless", c.cls);
c = classify(req("/", { "user-agent": CHROME, accept: "text/html" }, { asn: 999, asOrganization: "Some ISP" }));
check("a single Accept: text/html is not a browser navigation", c.cls === "headless", c.cls);
c = classify(req("/", { "user-agent": CHROME, accept: "text/html", "accept-language": "en-GB" }, { asn: 999, asOrganization: "Some ISP" }));
check("two of the three core signals is enough (old browser, proxy stripped Sec-Fetch)", c.cls === "person", c.cls);
c = classify(req("/api/answer", { "user-agent": CHROME }, { asn: 999, asOrganization: "Some ISP" }));
check("browser UA on /api with no browser headers = headless", c.cls === "headless", c.cls);
c = classify(req("/llms.txt", { "user-agent": CHROME }, { asn: 999, asOrganization: "Some ISP" }));
check("browser UA on a machine document with no browser headers = headless", c.cls === "headless", c.cls);
c = classify(req("/img/x.png", { "user-agent": CHROME }, { asn: 999, asOrganization: "Some ISP" }));
check("assets are still judged on the user-agent alone", c.cls === "person", c.cls);
c = classify(req("/e", { "user-agent": CHROME }, { asn: 999, asOrganization: "Some ISP" }, "POST"));
check("the beacon is still judged on the user-agent alone", c.cls === "person" && c.pcls === "beacon", c.cls);
c = classify(req("/", browserHeaders(), { ...HOME, httpProtocol: "HTTP/1.0" }));
check("HTTP/1.0 under a Chrome 140 string = headless", c.cls === "headless", c.cls);
for (const tls of ["TLSv1", "TLSv1.0", "TLSv1.1"]) {
  c = classify(req("/", browserHeaders(), { ...HOME, tlsVersion: tls }));
  check(`${tls} under a Chrome 140 string = headless`, c.cls === "headless", c.cls);
}
c = classify(req("/", browserHeaders(), { ...HOME, tlsVersion: "TLSv1.2" }));
check("TLSv1.2 is still a browser", c.cls === "person", c.cls);
c = classify(req("/", { ...browserHeaders(REAL.firefox), "sec-ch-ua": '"Chromium";v="140"' }, HOME));
check("Sec-CH-UA on a Firefox string = forged = headless", c.cls === "headless", c.cls);
c = classify(req("/", { ...browserHeaders(REAL["safari iphone"]), "sec-ch-ua": '"Chromium";v="140"' }, HOME));
check("Sec-CH-UA on a Safari string = forged = headless", c.cls === "headless", c.cls);
c = classify(req("/", { ...browserHeaders(), "sec-ch-ua": '"Chromium";v="140", "Google Chrome";v="140"' }, HOME));
check("Sec-CH-UA on a Chrome string is evidence FOR, not against", c.cls === "person" && c.ev === "5", `${c.cls} ev=${c.ev}`);
c = classify(req("/", browserHeaders(), { asn: 16509, asOrganization: "Amazon.com, Inc.", country: "US", httpProtocol: "HTTP/2" }));
check("a browser UA from AWS is person-dc", c.cls === "person-dc" && c.dc === true, c.cls);
c = classify(req("/", browserHeaders(REAL["safari iphone"]), { asn: 13335, asOrganization: "Cloudflare, Inc.", country: "GB", httpProtocol: "HTTP/3", tlsVersion: "TLSv1.3" }));
check("iPhone through iCloud Private Relay is a person, not a datacentre", c.cls === "person" && c.dc === true, `${c.cls} ev=${c.ev}`);
c = classify(req("/", { "user-agent": CHROME, accept: "*/*" }, { asn: 13335, asOrganization: "Cloudflare, Inc." }));
check("a scraper running ON the relay network does not get the pass", c.cls === "headless", c.cls);
c = classify(req("/", { ...browserHeaders(), "accept-language": "" }, { asn: 54113, asOrganization: "Fastly, Inc." }));
check("relay pass needs all three signals, not two", c.cls === "person-dc", c.cls);
let e = browserEvidence(new Headers(browserHeaders()), { httpProtocol: "HTTP/2" }, "chrome");
check("evidence: core 3, score 4 with HTTP/2", e.core === 3 && e.score === 4 && e.lies.length === 0, JSON.stringify(e));
e = browserEvidence(new Headers({ accept: "*/*" }), {}, "chrome");
check("evidence: nothing at all is core 0", e.core === 0 && e.score === 0);
e = browserEvidence(new Headers({ "sec-fetch-dest": "document" }), {}, "chrome");
check("evidence: Sec-Fetch-Dest alone counts as navigation", e.nav === true && e.core === 1);
e = browserEvidence(new Headers({ "sec-fetch-user": "?1" }), {}, "chrome");
check("evidence: Sec-Fetch-User alone counts as navigation", e.nav === true);

/* ---------- 2. declared bots: every needle must reach its own operator ---------- */
const synth = (needle) => `Mozilla/5.0 (compatible; ${needle}/1.0; +https://example.com/robot)`;
let shadowed = [];
for (const [needle, op, key] of AI_UAS) {
  const got = classify(req("/", { "user-agent": synth(needle) }, {}));
  if (got.cls !== "ai" || got.operator !== op) shadowed.push(`${needle} -> ${got.cls}/${got.operator} (wanted ai/${op})`);
  if (key && !RANGE_OPERATORS.includes(key)) shadowed.push(`${needle} -> range key ${key} we do not hold`);
}
check(`every AI needle reaches its own operator, none shadowed (${AI_UAS.length} rows)`, shadowed.length === 0, shadowed.join(" | "));
shadowed = [];
for (const [needle, op, key] of CRAWLER_UAS) {
  const got = classify(req("/", { "user-agent": synth(needle) }, {}));
  if (got.cls !== "crawler" || got.operator !== op) shadowed.push(`${needle} -> ${got.cls}/${got.operator} (wanted crawler/${op})`);
  if (key && !RANGE_OPERATORS.includes(key)) shadowed.push(`${needle} -> range key ${key} we do not hold`);
}
check(`every crawler needle reaches its own operator, none shadowed (${CRAWLER_UAS.length} rows)`, shadowed.length === 0, shadowed.join(" | "));
const named = { "ClaudeBot/1.0; +claudebot@anthropic.com": ["ai", "anthropic-claudebot"], "Claude-User/1.0": ["ai", "anthropic-claude-user"],
  "GPTBot/1.2; +https://openai.com/gptbot": ["ai", "openai-gptbot"], "OAI-SearchBot/1.0; +https://openai.com/searchbot": ["ai", "openai-searchbot"],
  "SomeNewThing/1.0; +https://openai.com/newbot": ["ai", "openai-other"], "PerplexityBot/1.0": ["ai", "perplexity-bot"],
  "Bytespider; spider-feedback@bytedance.com": ["ai", "bytedance"], "CCBot/2.0 (https://commoncrawl.org/faq/)": ["ai", "commoncrawl"],
  "cohere-ai": ["ai", "cohere"], "meta-externalagent/1.1": ["ai", "meta-ai"], "FacebookBot/1.0": ["ai", "meta-facebookbot"],
  "Amazonbot/0.1": ["ai", "amazon"], "Applebot-Extended/0.1": ["ai", "apple-extended"], "DuckAssistBot/1.0": ["ai", "duckduckgo-assist"],
  "YouBot/1.0": ["ai", "you-com"], "MistralAI-User/1.0": ["ai", "mistral-user"], "PanguBot/1.0": ["ai", "huawei-ai"],
  "Googlebot/2.1; +http://www.google.com/bot.html": ["crawler", "googlebot"], "Google-Read-Aloud": ["crawler", "google-read-aloud"],
  "bingbot/2.0": ["crawler", "bingbot"], "Applebot/0.1": ["crawler", "applebot"], "DuckDuckBot/1.1": ["crawler", "duckduckbot"],
  "YandexBot/3.0": ["crawler", "yandex"], "YandexImages/3.0": ["crawler", "yandex-other"], "Baiduspider/2.0": ["crawler", "baidu"],
  "PetalBot;+https://webmaster.petalsearch.com/site/petalbot": ["crawler", "huawei-petal"], "Bravebot/1.0": ["crawler", "brave-search"],
  "SemrushBot/7~bl": ["crawler", "semrush"], "AhrefsBot/7.0": ["crawler", "ahrefs"], "AhrefsSiteAudit/6.1": ["crawler", "ahrefs-audit"],
  "DotBot/1.2": ["crawler", "moz"], "rogerbot/1.0": ["crawler", "moz-roger"], "MJ12bot/v1.4.8": ["crawler", "majestic"],
  "DataForSeoBot/1.0": ["crawler", "dataforseo"], "Screaming Frog SEO Spider/21.0": ["crawler", "screamingfrog"],
  "facebookexternalhit/1.1": ["crawler", "meta-preview"], "Twitterbot/1.0": ["crawler", "x-preview"],
  "LinkedInBot/1.0": ["crawler", "linkedin"], "Slackbot-LinkExpanding 1.0": ["crawler", "slack"], "Discordbot/2.0": ["crawler", "discord"],
  "TelegramBot (like TwitterBot)": ["crawler", "telegram"], "SkypeUriPreview Preview/0.5": ["crawler", "skype"],
  "Embedly/0.2": ["crawler", "embedly"], "Iframely/1.3": ["crawler", "iframely"], "redditbot/1.0": ["crawler", "reddit"] };
let wrong = [];
for (const [tail, [cls, op]] of Object.entries(named)) {
  const got = classify(req("/", { "user-agent": `Mozilla/5.0 (compatible; ${tail})` }, {}));
  if (got.cls !== cls || got.operator !== op) wrong.push(`${tail} -> ${got.cls}/${got.operator}`);
}
check(`named operator strings land where they should (${Object.keys(named).length} strings)`, wrong.length === 0, wrong.join(" | "));
/* No bot, tool, monitor or headless needle may hide inside a real browser's user-agent. "ab/" did, and never
 * matched ApacheBench, the thing it was written for. */
let collide = [];
for (const [name, ua] of Object.entries(REAL)) {
  const u = ua.toLowerCase();
  for (const [needle] of [...AI_UAS, ...CRAWLER_UAS]) if (u.includes(needle.toLowerCase())) collide.push(`${name} contains bot needle ${needle}`);
  for (const needle of [...TOOL_UAS, ...MONITOR_UAS, ...HEADLESS_UAS]) if (u.includes(needle)) collide.push(`${name} contains needle ${needle}`);
}
check("no needle hides inside a real browser user-agent", collide.length === 0, collide.join(" | "));
for (const [name, ua] of Object.entries(REAL)) check(`real browser is never a bot: ${name}`, ["person", "person-dc"].includes(classify(req("/", browserHeaders(ua), HOME)).cls));

/* ---------- 3. the range table ---------- */
check("cidr with an empty prefix length is rejected", parseCidr("1.2.3.4/") === null && parseCidr("2001:db8::/") === null);
check("cidr with a junk prefix length is rejected", parseCidr("1.2.3.4/x") === null && parseCidr("1.2.3.4/1/2") === null);
check("cidr /32 and /128 are exact", inCidr(parseIp("1.2.3.4"), parseCidr("1.2.3.4/32")) && !inCidr(parseIp("1.2.3.5"), parseCidr("1.2.3.4/32"))
  && inCidr(parseIp("2001:db8::1"), parseCidr("2001:db8::1/128")) && !inCidr(parseIp("2001:db8::2"), parseCidr("2001:db8::1/128")));
check("bare address without a slash is a host route", parseCidr("8.8.8.8").bits === 32 && parseCidr("2001:db8::1").bits === 128);
check("ipv6 :: alone is all zeros", parseIp("::").n === 0n);
check("ipv6 all forms of the same address agree", parseIp("2001:db8:0:0:0:0:0:1").n === parseIp("2001:db8::1").n && parseIp("2001:0db8::0001").n === parseIp("2001:db8::1").n);
check("ipv6 compressed form must cover at least one group", parseIp("1:2:3:4:5:6:7::8") === null && parseIp("1:2:3:4:5:6:7:8") !== null);
check("ipv6 too many groups is rejected", parseIp("1:2:3:4:5:6:7:8:9") === null && parseIp("1:2:3:4:5:6:7") === null);
check("ipv6 two compressions is rejected", parseIp("1::2::3") === null);
check("ipv6 zone id is rejected", parseIp("fe80::1%eth0") === null);
check("ipv6 embedded v4, compressed and full", parseIp("::ffff:1.2.3.4").n === 0xffff01020304n && parseIp("64:ff9b::1.2.3.4").n === parseIp("64:ff9b::102:304").n);
check("ipv6 embedded v4 with a bad octet is rejected", parseIp("::ffff:1.2.3.256") === null && parseIp("1.2.3.4::") === null);
check("ipv6 in a v6 cidr, not in a v4 one", inCidr(parseIp("2001:4860:4801:10::1"), parseCidr("2001:4860:4801::/48")) && !inCidr(parseIp("::ffff:66.249.66.1"), parseCidr("66.249.64.0/19")));
const T = buildTable({ real: ["66.249.64.0/19"], empty: [], junk: ["not-an-address", "1.2.3.4/99"], wide: ["0.0.0.0/0"], notalist: "x" });
check("a table drops operators with nothing parseable", Object.keys(T).sort().join(",") === "real", Object.keys(T).join(","));
check("a /0 never enters the table", addressIn("wide", "9.9.9.9", T) === null);
check("an operator with no usable list reads n, never i", verifyOperator("empty", "1.2.3.4", false, T) === "n" && verifyOperator("junk", "1.2.3.4", false, T) === "n");
check("an operator with no usable list reads c when Cloudflare says so", verifyOperator("empty", "1.2.3.4", true, T) === "c" && verifyOperator(null, "1.2.3.4", true, T) === "c");
check("a real list still says v and i", verifyOperator("real", "66.249.66.1", false, T) === "v" && verifyOperator("real", "5.9.10.11", false, T) === "i");
check("no address on the request is n or c, never i", verifyOperator("real", "", false, T) === "n" && verifyOperator("real", "", true, T) === "c");
check("google key covers googlebot AND the special crawlers", addressIn("google", "66.249.66.1") === true && addressIn("google", "66.249.90.1") === true);
check("an openai address is not inside the google key", addressIn("google", "23.102.140.115") === false);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; GPTBot/1.2)" }, { verifiedBotCategory: "AI Crawler" }));
check("a declared bot with no address at all is c, not i", c.ver === "c", c.ver);
c = classify(req("/", { "user-agent": "Mozilla/5.0 (compatible; GPTBot/1.2)" }, {}));
check("a declared bot with no address and no category is n", c.ver === "n", c.ver);

/* ---------- 5. privacy: no blob may carry the address or the user-agent ---------- */
const SECRETS = ["86.1.2.3", "2a00:23c5:1234::9", "AppleWebKit", "Mozilla", "sessionid=abc", "https://google.com/", "secret"];
const privacyCases = [
  req("/insurers/aetna?utm=x&secret=abc", { ...browserHeaders(), cookie: "sessionid=abc", referer: "https://google.com/", "cf-connecting-ip": "86.1.2.3" }, HOME),
  req("/", { "user-agent": CHROME, "cf-connecting-ip": "2a00:23c5:1234::9" }, HOME),
  req("/api/x", { "user-agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "cf-connecting-ip": "66.249.66.1" }, { asn: 15169 }),
  req("/e", { "user-agent": "curl/8.4.0", "cf-connecting-ip": "86.1.2.3" }, {}, "POST"),
];
let leaked = [];
for (const r of privacyCases) {
  const pt = dataPoint(r, 200);
  const all = [...pt.indexes, ...pt.blobs].join(" | ");
  for (const s of SECRETS) if (all.includes(s)) leaked.push(`${new URL(r.url).pathname}: ${s}`);
  if (all.includes("?") || all.includes("utm=")) leaked.push(`${new URL(r.url).pathname}: query string`);
}
check("no blob of any data point carries the address, the user-agent, a cookie, a referrer or a query", leaked.length === 0, leaked.join(" | "));

// data point shape and the meter
const p = dataPoint(req("/api/answer", browserHeaders(), { asn: 5089, asOrganization: "Virgin Media", country: "GB" }), 200);
check("datapoint index", p.indexes[0] === "example.com");
check("datapoint blobs", p.blobs.length === 13 && p.blobs[0] === "example.com" && p.blobs[1] === "person" && p.blobs[4] === "api" && p.blobs[6] === "200" && p.blobs[7] === "GB" && p.blobs[11] === "0", JSON.stringify(p.blobs));
check("datapoint blob13 is the browser evidence", p.blobs[12] === "3" && dataPoint(req("/", { "user-agent": "curl/8.4.0" }), 200).blobs[12] === "", JSON.stringify(p.blobs));
check("datapoint doubles", p.doubles[0] === 1 && p.doubles[1] === 5089 && p.doubles[2] === 200);
check("no binding = null, no throw", meterTraffic(req("/", {}), {}, 200) === null);

const written = [];
const TRAFFIC = { writeDataPoint(d) { written.push(d); } };
const handler = {
  async fetch(request, env, ctx) {
    const u = new URL(request.url);
    if (u.pathname === "/outer") { const inner = await this.fetch(new Request("https://example.com/inner", { headers: request.headers }), env, ctx); return new Response("outer+" + await inner.text(), { status: 201 }); }
    if (u.pathname === "/boom") throw new Error("x");
    return new Response("inner", { status: 200 });
  },
  scheduled() { return "kept"; },
};
const w = withTrafficMeter(handler);
check("other handlers kept", w.scheduled() === "kept");
let res = await w.fetch(req("/outer", browserHeaders(), { asn: 5089 }), { TRAFFIC }, {});
check("re-entry counted once", written.length === 1 && written[0].blobs[10] === "/outer" && written[0].blobs[6] === "201", JSON.stringify(written));
check("body untouched", (await res.text()) === "outer+inner");
let threw = false;
try { await w.fetch(req("/boom", browserHeaders(), { asn: 5089 }), { TRAFFIC }, {}); } catch (e) { threw = true; }
check("throw metered as 500 and rethrown", threw && written.length === 2 && written[1].blobs[6] === "500", JSON.stringify(written[1] && written[1].blobs));
res = await w.fetch(req("/x", browserHeaders(), { asn: 5089 }), {}, {});
check("no binding, response still served", res.status === 200 && written.length === 2);
const bad = { writeDataPoint() { throw new Error("quota"); } };
res = await w.fetch(req("/x", browserHeaders(), { asn: 5089 }), { TRAFFIC: bad }, {});
check("a failing store never breaks a page", res.status === 200);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
