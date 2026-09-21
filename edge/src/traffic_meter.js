/* traffic_meter.js - every request, one data point, classified at the edge (22 Sep 2026).
 *
 * (c) 2026 Axion Labs / Zsolt Dios. Licensed under FSL-1.1-ALv2 (see LICENSE). Dated provenance: PROVENANCE.md.
 *
 * Store: Workers Analytics Engine (binding TRAFFIC, dataset edge_traffic, declared in each site's wrangler.jsonc).
 * A write is non-blocking and costs the request nothing; the SQL API reads it back (tools/traffic_read.py) with
 * a token that carries Account Analytics Read. KV is not touched: a
 * KV key per request was the old meter's cost and its cap (1,000 KV operations a request on a flooded /answers).
 *
 * Wrapping: withTrafficMeter(handler) returns the worker object with fetch() measured ONCE per outside request.
 * The front-door hooks (agent-native, markdown twin, figure, cite) re-enter through this.fetch(); the wrapper
 * hands the inner handler a `this` whose fetch is the unwrapped one, so re-entries are never counted twice and
 * no marker header is needed. A thrown handler is metered as status 500 and re-thrown untouched.
 *
 * Data point shape (blob order is the contract tools/traffic_read.py reads; append, never reorder):
 *   index1  host
 *   blob1 host  blob2 cls  blob3 operator  blob4 ver  blob5 pcls  blob6 method  blob7 status  blob8 country
 *   blob9 network (asOrganization, 60 chars)  blob10 ua family  blob11 path (120 chars, no query)  blob12 dc (1|0)
 *   blob13 ev (browser evidence 0-5, "" when the user-agent is not a browser string; added 22 Sep 2026, appended
 *              after blob12 so every earlier query still reads the same column)
 *   double1 1  double2 asn  double3 status
 * No address, no full user-agent, no query string, no cookie, no referrer is written. blob12 is the network's own
 * nature, not the visitor's: a person behind iCloud Private Relay reads cls "person" with dc "1".
 */
import { classify } from "./traffic_class.js";

export const TRAFFIC_BINDING = "TRAFFIC";
export const TRAFFIC_DATASET = "edge_traffic";

export function dataPoint(request, status) {
  const c = classify(request);
  return {
    indexes: [c.host],
    blobs: [c.host, c.cls, c.operator || "", c.ver, c.pcls, c.method, String(status || 0), c.country, c.org, c.family, c.path, c.dc ? "1" : "0", c.ev || ""],
    doubles: [1, c.asn, Number(status) || 0],
  };
}

/* Fire-and-forget. Returns the data point it wrote (for tests) or null when there is no binding. Never throws. */
export function meterTraffic(request, env, status) {
  try {
    const ds = env && env[TRAFFIC_BINDING];
    if (!ds || typeof ds.writeDataPoint !== "function") return null;
    const p = dataPoint(request, status);
    ds.writeDataPoint(p);
    return p;
  } catch (e) {
    return null;
  }
}

export function withTrafficMeter(handler) {
  const inner = { ...handler };
  inner.fetch = function (request, env, ctx) { return handler.fetch.call(inner, request, env, ctx); };
  return {
    ...handler,
    async fetch(request, env, ctx) {
      let res;
      try {
        res = await handler.fetch.call(inner, request, env, ctx);
      } catch (e) {
        meterTraffic(request, env, 500);
        throw e;
      }
      meterTraffic(request, env, res && res.status);
      return res;
    },
  };
}
