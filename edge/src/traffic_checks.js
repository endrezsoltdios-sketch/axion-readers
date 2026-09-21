/* traffic_checks.js - the checks every door runs on its traffic meter (22 Sep 2026).
 *
 * (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
 *
 * Called from each door's agent-native suite with the worker as exported (already wrapped by withTrafficMeter),
 * the suite's ENV and CTX, the origin and the suite's check(name, ok, extra). The store is a stub that collects
 * data points; what is asserted is the contract tools/traffic_read.py relies on: one point per outside
 * request (a front-door re-entry never doubles it), the host in index and blob 1, the class in blob 2, the path
 * class in blob 5, the status in blob 7, nothing of the visitor's address or full user-agent in any blob.
 */
const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0";
const IP = "86.1.2.3";

export async function trafficChecks({ worker, ENV, CTX, origin, check }) {
  const host = new URL(origin).hostname;
  const written = [];
  const everything = []; // never reset: the privacy scan at the end reads every point this suite caused
  const env = { ...ENV, TRAFFIC: { writeDataPoint(d) { written.push(d); everything.push(d); } } };
  const cf = { asn: 5089, asOrganization: "Virgin Media Limited", country: "GB" };
  const make = (path, headers, method = "GET") => Object.assign(new Request(origin + path, { method, headers }), { cf });
  const browser = { "user-agent": CHROME, accept: "text/html", "accept-language": "en-GB", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document", "cf-connecting-ip": IP };

  written.length = 0;
  let res = await worker.fetch(make("/", browser), env, CTX);
  check("traffic: home served", res.status === 200, String(res.status));
  check("traffic: home = exactly one point (figure hook re-entry not doubled)", written.length === 1, String(written.length));
  const p = written[0] || { blobs: [], indexes: [], doubles: [] };
  check("traffic: index is the host", p.indexes[0] === host, String(p.indexes[0]));
  check("traffic: blobs host/cls/pcls/status", p.blobs[0] === host && p.blobs[1] === "person" && p.blobs[4] === "page" && p.blobs[6] === "200", JSON.stringify(p.blobs));
  check("traffic: country and network kept, address not", p.blobs[7] === "GB" && p.blobs[8] === "Virgin Media Limited" && !JSON.stringify(p).includes(IP), JSON.stringify(p.blobs));
  check("traffic: full user-agent not stored", !JSON.stringify(p).includes("AppleWebKit"), "");
  check("traffic: doubles [1, asn, status]", p.doubles[0] === 1 && p.doubles[1] === 5089 && p.doubles[2] === 200, JSON.stringify(p.doubles));

  written.length = 0;
  res = await worker.fetch(make("/openapi.json", { "user-agent": "curl/8.4.0" }), env, CTX);
  check("traffic: openapi = one machine point from a tool", written.length === 1 && written[0].blobs[1] === "tool" && written[0].blobs[4] === "machine", JSON.stringify(written.map((w) => w.blobs.slice(0, 6))));

  written.length = 0;
  res = await worker.fetch(make("/.well-known/oauth-authorization-server", { "user-agent": "Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)", "cf-connecting-ip": "5.9.10.11" }), env, CTX);
  check("traffic: front-door answer still metered once", written.length === 1 && written[0].blobs[1] === "ai" && written[0].blobs[2] === "openai-gptbot" && written[0].blobs[3] === "i", JSON.stringify(written.map((w) => w.blobs.slice(0, 6))));

  written.length = 0;
  res = await worker.fetch(make("/a2a", { "user-agent": "python-requests/2.32", "content-type": "application/json" }, "POST"), env, CTX);
  check("traffic: a2a POST that re-enters a skill = one point", written.length === 1 && written[0].blobs[5] === "POST" && written[0].blobs[4] === "machine", JSON.stringify(written.map((w) => w.blobs.slice(0, 6))));

  written.length = 0;
  res = await worker.fetch(make("/wp-login.php", { "user-agent": "Mozilla/5.0 zgrab/0.x" }), env, CTX);
  check("traffic: probe path classed probe with its status", written.length === 1 && written[0].blobs[4] === "probe" && written[0].blobs[6] === String(res.status), JSON.stringify(written.map((w) => w.blobs.slice(0, 7))));

  written.length = 0;
  res = await worker.fetch(make("/", { "user-agent": "example-bot/1.0 (+https://example.com)" }), env, CTX);
  check("traffic: your own reader classed own", written.length === 1 && written[0].blobs[1] === "own", JSON.stringify(written.map((w) => w.blobs.slice(0, 3))));

  /* A script wearing a browser string. Every one of these read "person" before 22 Sep 2026, which is the one
   * number on this site that must not be flattered. A page request from a real browser carries at least two of
   * Accept-Language, Sec-Fetch-*, Accept: text/html; a chrome-badged curl carries none of them. */
  written.length = 0;
  res = await worker.fetch(make("/", { "user-agent": CHROME, accept: "*/*", "cf-connecting-ip": IP }), env, CTX);
  check("traffic: a browser string with no browser headers is not a person", written.length === 1 && written[0].blobs[1] === "headless", JSON.stringify(written.map((w) => w.blobs.slice(0, 5))));
  written.length = 0;
  res = await worker.fetch(make("/", { ...browser, "user-agent": EDGE }), env, CTX);
  check("traffic: a real Edge navigation is still a person", written.length === 1 && written[0].blobs[1] === "person" && written[0].blobs[9] === "edge", JSON.stringify(written.map((w) => w.blobs.slice(0, 10))));
  written.length = 0;
  res = await worker.fetch(Object.assign(new Request(origin + "/", { headers: browser }), { cf: { ...cf, asn: 16509, asOrganization: "Amazon.com, Inc." } }), env, CTX);
  check("traffic: a browser from a hosting network is person-dc", written.length === 1 && written[0].blobs[1] === "person-dc" && written[0].blobs[11] === "1", JSON.stringify(written.map((w) => w.blobs.slice(0, 12))));

  res = await worker.fetch(make("/", browser), ENV, CTX);
  check("traffic: no binding, page still served", res.status === 200, String(res.status));
  check("traffic: worker keeps its other handlers", typeof worker.fetch === "function", "");

  /* Privacy, over every point this suite caused: no blob may carry the connecting address, the full user-agent,
   * a query string, a cookie or a referrer. */
  const NEVER = [IP, "5.9.10.11", "AppleWebKit", "Mozilla/5.0", "?", "cookie", "referer"];
  const leaked = [];
  for (const d of everything) {
    const all = [...(d.indexes || []), ...(d.blobs || [])].join(" | ");
    for (const s of NEVER) if (all.includes(s)) leaked.push(`${d.blobs && d.blobs[10]}: ${s}`);
  }
  check("traffic: no blob of any point carries the address, the user-agent, a query, a cookie or a referrer",
    leaked.length === 0 && everything.length >= 8, `${everything.length} points; ${leaked.join(" | ")}`);
}
