/* traffic_class.js - who is really at the door: one class per request, decided at the edge (22 Sep 2026).
 *
 * (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
 *
 * Why: the meter we had counted declared AI user-agents and JavaScript beacons. It could not tell a person from a
 * headless browser, a curl from a crawler, Googlebot from a box in Hetzner wearing Googlebot's name, or our own
 * scanners from strangers. Cloudflare's own verdict (request.cf.botManagement) costs an Enterprise plan. This
 * module reaches the same facts from what every plan gives a Worker: the user-agent, the navigation headers a real
 * browser always sends, the connecting address against the blocks each operator publishes for its own bots
 * (data/bot_ranges.js, tools/bot_ranges.py), the network the address belongs to (request.cf.asn and
 * asOrganization), the negotiated protocol and TLS version, and Cloudflare's free verified-bot category when set.
 *
 * Classes (blob "cls"):
 *   person      browser user-agent, browser navigation evidence, not from a hosting network
 *   person-dc   browser user-agent from a hosting or cloud network: automation dressed as a browser, or a VPN
 *   headless    a headless browser by its own name, OR a browser string whose request is not a browser's
 *   ai          a declared AI operator (GPTBot, ClaudeBot, PerplexityBot, ChatGPT-User ...)
 *   crawler     a declared search or social crawler (Googlebot, bingbot, Applebot, facebookexternalhit ...)
 *   tool        an HTTP library or command (curl, python-requests, Go-http-client, axios, okhttp ...)
 *   monitor     an uptime or performance checker
 *   own         your own readers and scanners (any user-agent carrying OWN_UA_NEEDLE)
 *   other-bot   says bot, spider or crawl and matched no name above
 *   empty       no user-agent at all
 *
 * Verified (blob "ver"), for ai and crawler only:
 *   v   the connecting address is inside the operator's own published blocks
 *   i   the operator publishes blocks WE HOLD AND PARSED and the address is outside them: impersonated
 *   c   no usable block list for that operator, but Cloudflare's verified-bot category is set
 *   n   no proof either way (no list, no category, or no address on the request)
 *   -   not a declared bot
 * "i" is only ever said against a non-empty parsed list. An operator whose list is missing, empty or unparseable
 * reads "c" or "n", never "i" (hardened 22 Sep 2026: an empty array is truthy, so a failed fetch used to make every
 * real Googlebot an impersonator).
 *
 * Browser evidence (blob "ev", the digit 0-5; "" when the user-agent is not a browser string). A real browser on a
 * document, API or machine-document request carries, reliably, in every current release:
 *   Accept-Language           set from the platform's language list
 *   Sec-Fetch-Mode/Dest/Site  Chrome 76+, Firefox 90+, Safari 16.4+, Edge: all of them, on every request
 *   Accept: text/html...      on a document navigation
 * Two of those three are required on page, api and machine paths; fewer is a script wearing a browser string and
 * reads "headless". Asset, beacon and probe paths are left alone (a cached subresource or a hotlinked image is a
 * poor place to judge a person). Two further signals only ADD confidence, never subtract, because a proxy can
 * strip them: Sec-CH-UA on a Chromium family, and HTTP/2 or HTTP/3.
 * Three contradictions are fatal on their own, because no current browser can produce them:
 *   HTTP/1.0                    no browser released this century speaks it
 *   TLSv1 / TLSv1.0 / TLSv1.1   every browser dropped these in 2020; a Chrome 140 string cannot arrive on one
 *   Sec-CH-UA on Firefox/Safari client hints are Chromium-only; Gecko and WebKit never send that header
 *
 * Privacy: nothing here stores an address, a full user-agent, a query string, a cookie or a referrer. The meter
 * writes the class, the operator, the network name, the country and the path only.
 */
import RANGES from "../data/bot_ranges.js";

/* Your own readers and scanners: any user-agent containing this needle is classed "own", so your own audits
 * never inflate a stranger count. Set it to the word your tools put in their user-agent; "" turns the class off. */
export const OWN_UA_NEEDLE = "example-bot";

/* Declared AI operators: [needle, operator id, range list key or null]. First match wins, so every row is more
 * specific than the rows below it and the generic catch-alls sit at the end of the table. Needles marked (k) are
 * from knowledge of the operator's published token rather than a list we fetch; a wrong one simply never matches. */
export const AI_UAS = [
  ["GPTBot", "openai-gptbot", "openai"],
  ["OAI-SearchBot", "openai-searchbot", "openai"],
  ["ChatGPT-User", "openai-chatgpt-user", "openai"],
  ["ClaudeBot", "anthropic-claudebot", null],
  ["Claude-User", "anthropic-claude-user", null],
  ["Claude-SearchBot", "anthropic-searchbot", null],
  ["Claude-Web", "anthropic-claude-web", null],
  ["anthropic-ai", "anthropic-ai", null],
  ["PerplexityBot", "perplexity-bot", "perplexity"],
  ["Perplexity-User", "perplexity-user", "perplexity"],
  ["Google-Extended", "google-extended", "google"],
  ["GoogleOther", "google-other", "google"],
  ["Google-CloudVertexBot", "google-vertex", "google"],
  ["Bytespider", "bytedance", null],
  ["CCBot", "commoncrawl", null],
  ["cohere-ai", "cohere", null],
  ["cohere-training-data-crawler", "cohere", null],
  ["meta-externalagent", "meta-ai", null],
  ["meta-externalfetcher", "meta-fetcher", null],
  ["FacebookBot", "meta-facebookbot", null],
  ["Amazonbot", "amazon", null],
  ["Applebot-Extended", "apple-extended", "apple"],
  ["DuckAssistBot", "duckduckgo-assist", null],
  ["YouBot", "you-com", null],
  ["MistralAI-User", "mistral-user", null],
  ["xAI-Bot", "xai", null],                       // (k) xAI/Grok token, unverified against a published list
  ["Grokbot", "xai", null],                       // (k)
  ["Diffbot", "diffbot", null],
  ["FirecrawlAgent", "firecrawl", null],          // (k)
  ["ImagesiftBot", "imagesift", null],
  ["omgili", "webz", null],
  ["Webzio-Extended", "webz", null],
  ["Timpibot", "timpi", null],
  ["AI2Bot", "ai2", null],
  ["Kangaroo Bot", "kangaroo", null],             // (k)
  ["PanguBot", "huawei-ai", null],
  /* generic catch-alls, last: a new bot from a named operator still lands on that operator and its range list */
  ["MistralAI", "mistral-other", null],
  ["Bytedance", "bytedance-other", null],
  ["cohere", "cohere-other", null],
  ["openai.com", "openai-other", "openai"],
  ["anthropic", "anthropic-other", null],
  ["perplexity", "perplexity-other", "perplexity"],
];

export const CRAWLER_UAS = [
  ["Googlebot", "googlebot", "google"],
  ["Google-InspectionTool", "google-inspection", "google"],
  ["Storebot-Google", "google-store", "google"],
  ["AdsBot-Google", "google-ads", "google"],
  ["Mediapartners-Google", "google-adsense", "google"],
  ["APIs-Google", "google-apis", "google"],
  ["FeedFetcher-Google", "google-feed", "google"],
  ["Google-Read-Aloud", "google-read-aloud", "google"],
  ["Google-Safety", "google-safety", "google"],
  ["bingbot", "bingbot", "bing"],
  ["adidxbot", "bing-ads", "bing"],
  ["BingPreview", "bing-preview", "bing"],
  ["msnbot", "msnbot", "bing"],
  ["Applebot", "applebot", "apple"],
  ["DuckDuckBot", "duckduckbot", null],
  ["DuckDuckGo-Favicons-Bot", "duckduckgo-favicons", null],
  ["YandexBot", "yandex", null],
  ["Baiduspider", "baidu", null],
  ["PetalBot", "huawei-petal", null],
  ["AspiegelBot", "huawei-aspiegel", null],
  ["Bravebot", "brave-search", null],             // (k) search.brave.com crawler token
  ["SeznamBot", "seznam", null],
  ["Qwantify", "qwant", null],
  ["Sogou", "sogou", null],
  ["Exabot", "exalead", null],
  ["Mail.RU_Bot", "mailru", null],
  ["AhrefsBot", "ahrefs", null],
  ["AhrefsSiteAudit", "ahrefs-audit", null],
  ["SemrushBot", "semrush", null],
  ["SiteAuditBot", "semrush-audit", null],
  ["MJ12bot", "majestic", null],
  ["DotBot", "moz", null],
  ["rogerbot", "moz-roger", null],
  ["DataForSeoBot", "dataforseo", null],
  ["SerpstatBot", "serpstat", null],
  ["Screaming Frog SEO Spider", "screamingfrog", null],
  ["Barkrowler", "babbar", null],
  ["BLEXBot", "webmeup", null],
  ["ZoominfoBot", "zoominfo", null],
  ["facebookexternalhit", "meta-preview", null],
  ["Facebot", "meta-facebot", null],
  /* Telegram before Twitter: the real Telegram string is "TelegramBot (like TwitterBot)", so the other order
   * filed every Telegram link preview under X (caught by the needle test, 22 Sep 2026). */
  ["TelegramBot", "telegram", null],
  ["Twitterbot", "x-preview", null],
  ["LinkedInBot", "linkedin", null],
  ["Slackbot", "slack", null],
  ["Slack-ImgProxy", "slack-img", null],
  ["Discordbot", "discord", null],
  ["WhatsApp", "whatsapp", null],
  ["SkypeUriPreview", "skype", null],
  ["vkShare", "vk", null],
  ["Embedly", "embedly", null],
  ["Iframely", "iframely", null],
  ["Yahoo Link Preview", "yahoo-preview", null],
  ["Pinterestbot", "pinterest", null],
  ["redditbot", "reddit", null],
  ["Mastodon", "mastodon", null],
  ["Bluesky", "bluesky", null],
  ["archive.org_bot", "internet-archive", null],
  ["ia_archiver", "internet-archive", null],
  ["Wayback", "internet-archive", null],
  /* generic catch-alls, last */
  ["Pinterest", "pinterest-other", null],
  ["Ahrefs", "ahrefs-other", null],
  ["Semrush", "semrush-other", null],
  ["Yandex", "yandex-other", null],
];

/* HTTP libraries and command-line clients. "apachebench" replaced the needle "ab/" on 22 Sep 2026: ApacheBench
 * sends "ApacheBench/2.3", so "ab/" never matched the thing it was for and could only match something else. */
export const TOOL_UAS = ["curl/", "wget/", "python-requests", "python-urllib", "python/", "aiohttp", "httpx/", "node-fetch", "undici",
  "axios/", "go-http-client", "java/", "okhttp", "libwww-perl", "lwp::", "scrapy", "guzzlehttp", "php/", "ruby", "faraday",
  "postmanruntime", "insomnia", "http.rb", "cpp-httplib", "dart/", "reqwest", "got (", "superagent", "apache-httpclient",
  "restsharp", "powershell", "winhttp", "urllib3", "httpie", "k6/", "apachebench", "wrk/", "siege", "colly", "bun/", "deno/"];

export const MONITOR_UAS = ["uptimerobot", "pingdom", "statuscake", "site24x7", "betteruptime", "better stack", "checkly", "freshping",
  "hetrixtools", "nodeping", "updown.io", "gtmetrix", "lighthouse", "pagespeed", "chrome-lighthouse", "cloudflare-healthchecks",
  "cloudflare-traffic-manager", "cloudflare-alwaysonline", "cloudflare-prefetch", "uptime-kuma", "oh dear", "cron-job.org",
  "webpagetest", "catchpoint", "datadog", "newrelic"];

export const HEADLESS_UAS = ["headlesschrome", "chrome-headless", "chrome-headless-shell", "phantomjs", "puppeteer", "playwright",
  "selenium", "chromedriver", "webdriver", "cypress", "jsdom", "happy-dom", "htmlunit", "electron", "slimerjs"];

/* Hosting and cloud networks by autonomous-system number (request.cf.asn). A browser user-agent from one of these is
 * not a person at a desk (or is a person behind a datacentre VPN, which the report says in words). Google's 15169
 * is here for that reason too: Googlebot is classed by its name before this table is consulted.
 * Deliberately NOT grown from memory on 22 Sep 2026: a wrong number here turns a real ISP's people into person-dc,
 * and DC_ORG below already catches the same operators by the name they publish. */
export const DC_ASNS = new Set([16509, 14618, 8987, 15169, 396982, 8075, 8068, 8069, 24940, 213230, 16276, 14061, 63949,
  20473, 31898, 45102, 45090, 132203, 51167, 12876, 60781, 9009, 212238, 13335, 54113, 20940, 16625, 395403, 62567,
  36352, 55286, 397423, 46844, 174, 3223, 49981, 35913, 199524, 42831, 202425, 197540, 44066, 47583, 393960, 141995]);
const DC_ORG = /hosting|cloud|server|datacenter|data center|data-center|vps|colocation|colo\b|dedicated|hetzner|ovh|digitalocean|linode|akamai|vultr|amazon|microsoft|google llc|oracle|alibaba|tencent|contabo|leaseweb|scaleway|m247|datacamp|choopa|packet|equinix|limestone|psychz|quadranet|hostinger|namecheap|godaddy|ionos|1&1|strato|serverius|worldstream|zenlayer|g-core|gcore|stackpath|fastly|cloudflare|cogent|\baws\b|\bazure\b|\bhost\b|upcloud|kamatera|netcup|selectel|timeweb|aeza|alexhost|mevspace|interserver|webnx|nocix/i;

/* The three networks Apple names as iCloud Private Relay egress partners, plus Cloudflare's own WARP. Traffic from
 * them is a datacentre address carrying a real person's browser. A relay is only allowed to read "person" when all
 * three core navigation signals are present: a scraper running ON one of these networks does not forge all three. */
export const RELAY_ASNS = new Set([13335, 20940, 16625, 54113]);

/* ---------- address blocks ---------- */
function ip4(s) {
  const p = s.split(".");
  if (p.length !== 4) return null;
  let n = 0n;
  for (const x of p) { const v = Number(x); if (!/^\d{1,3}$/.test(x) || v > 255) return null; n = (n << 8n) | BigInt(v); }
  return n;
}
function ip6(s) {
  if (s.includes(".")) { // embedded v4 tail
    const i = s.lastIndexOf(":"); const v4 = ip4(s.slice(i + 1)); if (v4 === null) return null;
    s = s.slice(0, i + 1) + (v4 >> 16n).toString(16) + ":" + (v4 & 0xffffn).toString(16);
  }
  const halves = s.split("::");
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(":") : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
  const fill = 8 - head.length - tail.length;
  /* "::" stands for at least one group of zeros, so a compressed form with nothing left to fill is not an address
   * (1:2:3:4:5:6:7::8 is a typo, not 1:2:3:4:5:6:7:8). An uncompressed form must be exactly eight groups. */
  if (halves.length === 2 ? fill < 1 : fill !== 0) return null;
  const groups = [...head, ...Array(fill).fill("0"), ...tail];
  let n = 0n;
  for (const g of groups) { if (!/^[0-9a-f]{1,4}$/i.test(g)) return null; n = (n << 16n) | BigInt(parseInt(g, 16)); }
  return n;
}
export function parseIp(s) {
  if (!s) return null;
  s = String(s).trim();
  if (s.includes(":")) { const n = ip6(s); return n === null ? null : { fam: 6, n }; }
  const n = ip4(s); return n === null ? null : { fam: 4, n };
}
export function parseCidr(c) {
  const parts = String(c).split("/");
  if (parts.length > 2) return null;
  const ip = parseIp(parts[0]); if (!ip) return null;
  const width = ip.fam === 4 ? 32 : 128;
  /* The prefix length must be written out. Number("") is 0, so "1.2.3.4/" used to parse as 0.0.0.0/0 and verify
   * every address on earth as that operator (caught 22 Sep 2026). */
  let bits = width;
  if (parts.length === 2) {
    if (!/^\d{1,3}$/.test(parts[1])) return null;
    bits = Number(parts[1]);
  }
  if (bits < 0 || bits > width) return null;
  const mask = bits === 0 ? 0n : ((1n << BigInt(width)) - 1n) ^ ((1n << BigInt(width - bits)) - 1n);
  return { fam: ip.fam, net: ip.n & mask, mask, bits };
}
export function inCidr(ip, cidr) {
  return !!ip && !!cidr && ip.fam === cidr.fam && (ip.n & cidr.mask) === cidr.net;
}
/* A published list is parsed once at module load. A /0 is dropped whatever it claims to be: no operator publishes
 * "the whole internet is my bot", and one such row would verify every impersonator. */
export function buildTable(operators) {
  const t = {};
  for (const [op, list] of Object.entries(operators || {})) {
    const parsed = (Array.isArray(list) ? list : []).map(parseCidr).filter((c) => c && c.bits > 0);
    if (parsed.length) t[op] = parsed;
  }
  return t;
}
const TABLE = buildTable((RANGES && RANGES.operators) || {});
export const RANGE_OPERATORS = Object.keys(TABLE);
export const RANGES_FETCHED = (RANGES && RANGES.fetched) || "";
export function addressIn(op, ipString, table = TABLE) {
  const ip = parseIp(ipString); const t = table[op];
  if (!ip || !t || !t.length) return null;
  return t.some((c) => inCidr(ip, c));
}
/* v / i / c / n for one declared operator. "i" needs a list we actually hold AND an address we actually parsed. */
export function verifyOperator(rangeKey, ipString, cfCategorySet, table = TABLE) {
  const inside = rangeKey ? addressIn(rangeKey, ipString, table) : null;
  if (inside === true) return "v";
  if (inside === false) return "i";
  return cfCategorySet ? "c" : "n";
}

/* ---------- user-agent ---------- */
const lower = (list) => list.map((row) => [row[0].toLowerCase(), row[1], row[2]]);
const AI_NEEDLES = lower(AI_UAS);
const CRAWLER_NEEDLES = lower(CRAWLER_UAS);
function firstMatch(list, u) {
  for (const row of list) if (u.includes(row[0])) return row;
  return null;
}
export function uaFamily(ua) {
  const u = ua || "";
  const mobile = /mobile|android|iphone|ipad/i.test(u) ? "-mobile" : "";
  if (/Edg\//.test(u)) return "edge" + mobile;
  if (/OPR\/|Opera/.test(u)) return "opera" + mobile;
  if (/SamsungBrowser/.test(u)) return "samsung" + mobile;
  if (/Firefox\//.test(u)) return "firefox" + mobile;
  if (/Chrome\/|CriOS\//.test(u)) return "chrome" + mobile;
  if (/Safari\//.test(u) && /Version\//.test(u)) return "safari" + mobile;
  if (/MSIE|Trident\//.test(u)) return "ie";
  return "";
}
function looksBrowser(ua) { return /Mozilla\/5\.0/.test(ua) && /(Chrome|Firefox|Safari|Edg|OPR|Trident|MSIE)/.test(ua); }

/* ---------- browser evidence ---------- */
/* Families that send Sec-CH-UA. Client hints are a Chromium feature; Gecko and WebKit do not implement them, so
 * that header on a Firefox or Safari string is a forged header, not a browser. */
const CHROMIUM = /^(chrome|edge|opera|samsung)/;
/* Where the evidence rule is applied. Assets, beacons and probe paths are judged on the user-agent alone. */
export const EVIDENCE_PCLS = new Set(["page", "api", "machine"]);

export function browserEvidence(headers, cf, family) {
  const get = (k) => (headers && typeof headers.get === "function" ? headers.get(k) : "") || "";
  const lang = get("accept-language") !== "";
  const nav = !!(get("sec-fetch-mode") || get("sec-fetch-dest") || get("sec-fetch-site") || get("sec-fetch-user"));
  const html = get("accept").toLowerCase().includes("text/html");
  const ch = get("sec-ch-ua") !== "";
  const proto = String((cf && cf.httpProtocol) || "");
  const tls = String((cf && cf.tlsVersion) || "");
  const chromium = CHROMIUM.test(family || "");
  const lies = [];
  if (proto === "HTTP/1.0") lies.push("http/1.0");
  if (/^TLSv1(\.[01])?$/.test(tls)) lies.push("tls:" + tls);
  if (ch && family && !chromium) lies.push("sec-ch-ua:" + family);
  const core = (lang ? 1 : 0) + (nav ? 1 : 0) + (html ? 1 : 0);
  let score = core;
  if (ch && chromium) score += 1;
  if (proto === "HTTP/2" || proto === "HTTP/3") score += 1;
  return { core, score, lies, lang, nav, html, ch, proto, tls };
}

/* ---------- path ---------- */
const ASSET_EXT = /\.(png|jpe?g|gif|svg|webp|avif|ico|css|js|mjs|map|woff2?|ttf|otf|eot|mp4|webm|mp3|pdf|zip)$/i;
const PROBE = /(^\/(wp-|wordpress|xmlrpc|phpmyadmin|pma|admin\/|administrator|cgi-bin|vendor\/|\.git|\.en[v]|\.aws|\.ssh|\.svn|\.DS_Store|config\.|backup|bak\b|_ignition|actuator|telescope|debug\/|console\/|solr|jenkins|manager\/html|owa\/|autodiscover|ecp\/|remote\/|boaform|hudson|struts|cgi|shell|eval|invoker)|\.(php|asp|aspx|jsp|cgi|sql|bak|old|orig|swp|tar|gz|rar|7z)$|\/\.well-known\/(security\.txt)?$)/i;
const MACHINE = /^\/(\.well-known\/|llms(-full)?\.txt$|openapi\.json$|auth\.md$|a2a$|a2a\/|mcp$|mcp\/|api-catalog|agents?\.json$|skills?(\.json)?$|skills\/|robots\.txt$|sitemap[^/]*\.xml$|humans\.txt$|ai\.txt$|manifest\.json$|passport)/i;
export function pathClass(path, method) {
  const p = path || "/";
  if (p === "/e" || p === "/stats" || p.startsWith("/e?") || p === "/beacon") return "beacon";
  if (PROBE.test(p) && !MACHINE.test(p)) return "probe";
  if (MACHINE.test(p) || p.endsWith(".md")) return "machine";
  if (p.startsWith("/api/") || p === "/api" || p === "/q" || p.startsWith("/lookup")) return "api";
  if (ASSET_EXT.test(p)) return "asset";
  if (method === "OPTIONS" || method === "TRACE" || method === "CONNECT") return "probe";
  return "page";
}

/* ---------- the verdict ---------- */
export function classify(request) {
  const url = new URL(request.url);
  const h = request.headers;
  const ua = h.get("user-agent") || "";
  const u = ua.toLowerCase();
  const cf = request.cf || {};
  const asn = Number(cf.asn) || 0;
  const org = String(cf.asOrganization || "").slice(0, 60);
  const dc = DC_ASNS.has(asn) || (org !== "" && DC_ORG.test(org));
  const ip = h.get("cf-connecting-ip") || "";
  const cfCat = typeof cf.verifiedBotCategory === "string" && cf.verifiedBotCategory.length > 0;
  const pcls = pathClass(url.pathname, request.method);
  const base = { host: url.hostname, path: url.pathname.slice(0, 120), method: request.method, pcls, asn, org, dc,
    country: String(cf.country || ""), family: "", operator: "", ver: "-", ev: "" };

  const verdict = (cls, operator, rangeKey) => ({ ...base, cls, operator, ver: verifyOperator(rangeKey, ip, cfCat) });

  if (!ua.trim()) return { ...base, cls: "empty" };
  if (OWN_UA_NEEDLE && u.includes(OWN_UA_NEEDLE)) return { ...base, cls: "own", operator: "own" };
  const ai = firstMatch(AI_NEEDLES, u); if (ai) return verdict("ai", ai[1], ai[2]);
  const cr = firstMatch(CRAWLER_NEEDLES, u); if (cr) return verdict("crawler", cr[1], cr[2]);
  if (MONITOR_UAS.some((m) => u.includes(m))) return { ...base, cls: "monitor" };
  if (HEADLESS_UAS.some((m) => u.includes(m))) return { ...base, cls: "headless", family: uaFamily(ua) };
  if (TOOL_UAS.some((m) => u.includes(m))) return { ...base, cls: "tool", operator: (u.match(/^([a-z0-9._-]+)\//) || [, ""])[1].slice(0, 30) };
  if (looksBrowser(ua)) {
    const family = uaFamily(ua);
    const ev = browserEvidence(h, cf, family);
    const out = { ...base, family, ev: String(ev.score) };
    /* A document, API or machine-document request that a browser could not have sent. */
    if (EVIDENCE_PCLS.has(pcls) && (ev.lies.length > 0 || ev.core < 2)) return { ...out, cls: "headless" };
    /* A person behind iCloud Private Relay or WARP arrives on a datacentre address with a whole browser's request. */
    if (dc && RELAY_ASNS.has(asn) && ev.core === 3) return { ...out, cls: "person" };
    if (dc) return { ...out, cls: "person-dc" };
    return { ...out, cls: "person" };
  }
  if (/bot|spider|crawl|scan|fetch|scrape|probe|check|monitor/.test(u)) return { ...base, cls: "other-bot", operator: (u.match(/([a-z0-9_.-]*(?:bot|spider|crawler))/) || [, ""])[1].slice(0, 30) };
  return { ...base, cls: "other-bot", operator: "" };
}
