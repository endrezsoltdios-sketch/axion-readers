#!/usr/bin/env python3
"""gads.py — Google Ads API connector (Axion own-stack, 1 Sep 2026).

One tool, one job: talk to the Google Ads API. Text in, text out.

Read commands (safe, no state change):
  py scripts\\gads.py stats [--days 7]        campaign performance table
  py scripts\\gads.py ads                     ad status + policy approval
  py scripts\\gads.py terms [--days 7]        top search terms
  py scripts\\gads.py budgets                 campaign budgets
  py scripts\\gads.py raw "SELECT ..."        any GAQL query (escape hatch)

Mutate commands (dry-run by default; --apply to execute; every apply logs
old value to business/ads/gads_mutations.log for rollback):
  py scripts\\gads.py pause-campaign --id CAMPAIGN_ID [--apply]
  py scripts\\gads.py enable-campaign --id CAMPAIGN_ID [--apply]
  py scripts\\gads.py pause-ad --adgroup AG_ID --ad AD_ID [--apply]
  py scripts\\gads.py set-budget --id BUDGET_ID --gbp 8.00 [--apply]
      Budget INCREASES additionally require --approve-spend (human-fired
      spend law). Decreases and pauses need only --apply.

Env (in ClaudeTrader/.env — never printed by this tool):
  GADS_DEVELOPER_TOKEN, GADS_CLIENT_ID, GADS_CLIENT_SECRET,
  GADS_REFRESH_TOKEN, GADS_CUSTOMER_ID (10 digits, no dashes),
  GADS_LOGIN_CUSTOMER_ID (manager/MCC id, no dashes),
  GADS_API_VERSION (optional, default v21 — bump here on version sunset).
"""
import argparse
import json
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
MUTATION_LOG = ROOT / "business" / "ads" / "gads_mutations.log"


def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def need(env, key):
    v = env.get(key)
    if not v:
        sys.exit(f"MISSING {key} in .env — run scripts/gads_auth.py setup first "
                 f"(see business/ads/GADS_SETUP.md).")
    return v


def http_post(url, data, headers, as_json=True):
    body = json.dumps(data).encode() if as_json else urllib.parse.urlencode(data).encode()
    if as_json:
        headers = {**headers, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:2000]
        sys.exit(f"HTTP {e.code} from {url.split('?')[0]}\n{detail}\n"
                 f"(404 on googleads.googleapis.com usually means the API version "
                 f"was sunset — set GADS_API_VERSION in .env to the current one.)")


def access_token(env):
    tok = http_post(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": need(env, "GADS_CLIENT_ID"),
            "client_secret": need(env, "GADS_CLIENT_SECRET"),
            "refresh_token": need(env, "GADS_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        },
        {},
        as_json=False,
    )
    return tok["access_token"]


class Api:
    def __init__(self):
        self.env = load_env()
        self.version = self.env.get("GADS_API_VERSION", "v21")
        self.cid = need(self.env, "GADS_CUSTOMER_ID").replace("-", "")
        self.headers = {
            "Authorization": f"Bearer {access_token(self.env)}",
            "developer-token": need(self.env, "GADS_DEVELOPER_TOKEN"),
        }
        login = self.env.get("GADS_LOGIN_CUSTOMER_ID", "").replace("-", "")
        if login:
            self.headers["login-customer-id"] = login
        self.base = f"https://googleads.googleapis.com/{self.version}/customers/{self.cid}"

    def search(self, gaql):
        out, token = [], None
        while True:
            payload = {"query": gaql}
            if token:
                payload["pageToken"] = token
            resp = http_post(f"{self.base}/googleAds:search", payload, self.headers)
            out.extend(resp.get("results", []))
            token = resp.get("nextPageToken")
            if not token:
                return out

    def mutate(self, endpoint, operations):
        return http_post(f"{self.base}/{endpoint}:mutate",
                         {"operations": operations}, self.headers)


def gbp(micros):
    return float(micros or 0) / 1e6


def table(rows, cols):
    if not rows:
        print("(no rows)")
        return
    widths = [max(len(c), max(len(str(r[i])) for r in rows)) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("-+-".join("-" * w for w in widths))
    for r in rows:
        print(" | ".join(str(v).ljust(w) for v, w in zip(r, widths)))


def log_mutation(action, detail, old_value):
    MUTATION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "detail": detail,
        "old_value": old_value,
    }
    with MUTATION_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"LOGGED rollback value → {MUTATION_LOG.relative_to(ROOT)}")


# ---------------- read commands ----------------

def cmd_stats(api, days):
    rows = api.search(
        "SELECT campaign.id, campaign.name, campaign.status, metrics.impressions, "
        "metrics.clicks, metrics.ctr, metrics.average_cpc, metrics.cost_micros, "
        f"metrics.conversions FROM campaign WHERE segments.date DURING LAST_{days}_DAYS")
    out = []
    for r in rows:
        c, m = r["campaign"], r.get("metrics", {})
        out.append([c["id"], c["name"][:34], c["status"],
                    m.get("impressions", "0"), m.get("clicks", "0"),
                    f"{float(m.get('ctr', 0)) * 100:.2f}%",
                    f"£{gbp(m.get('averageCpc')):.2f}",
                    f"£{gbp(m.get('costMicros')):.2f}",
                    m.get("conversions", "0")])
    table(out, ["id", "campaign", "status", "impr", "clicks", "ctr", "avg_cpc", "cost", "conv"])


def cmd_ads(api):
    rows = api.search(
        "SELECT campaign.name, ad_group.id, ad_group_ad.ad.id, ad_group_ad.status, "
        "ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.review_status "
        "FROM ad_group_ad WHERE ad_group_ad.status != 'REMOVED'")
    out = []
    for r in rows:
        pol = r["adGroupAd"].get("policySummary", {})
        out.append([r["campaign"]["name"][:30], r["adGroup"]["id"],
                    r["adGroupAd"]["ad"]["id"], r["adGroupAd"]["status"],
                    pol.get("approvalStatus", "?"), pol.get("reviewStatus", "?")])
    table(out, ["campaign", "adgroup_id", "ad_id", "status", "approval", "review"])


def cmd_terms(api, days, limit):
    rows = api.search(
        "SELECT search_term_view.search_term, metrics.clicks, metrics.impressions, "
        f"metrics.cost_micros FROM search_term_view WHERE segments.date DURING LAST_{days}_DAYS "
        f"ORDER BY metrics.clicks DESC LIMIT {limit}")
    out = [[r["searchTermView"]["searchTerm"][:60], r["metrics"].get("clicks", "0"),
            r["metrics"].get("impressions", "0"), f"£{gbp(r['metrics'].get('costMicros')):.2f}"]
           for r in rows]
    table(out, ["search_term", "clicks", "impr", "cost"])


def cmd_budgets(api):
    rows = api.search(
        "SELECT campaign.id, campaign.name, campaign_budget.id, "
        "campaign_budget.amount_micros, campaign_budget.resource_name FROM campaign "
        "WHERE campaign.status != 'REMOVED'")
    out = [[r["campaign"]["id"], r["campaign"]["name"][:34], r["campaignBudget"]["id"],
            f"£{gbp(r['campaignBudget'].get('amountMicros')):.2f}/day"]
           for r in rows]
    table(out, ["campaign_id", "campaign", "budget_id", "daily_budget"])


def cmd_raw(api, gaql):
    print(json.dumps(api.search(gaql), indent=2))


# ---------------- mutate commands ----------------

def set_campaign_status(api, campaign_id, status, apply):
    rows = api.search(f"SELECT campaign.id, campaign.name, campaign.status FROM campaign "
                      f"WHERE campaign.id = {int(campaign_id)}")
    if not rows:
        sys.exit(f"campaign {campaign_id} not found")
    old = rows[0]["campaign"]["status"]
    name = rows[0]["campaign"]["name"]
    print(f"{'APPLY' if apply else 'DRY-RUN'}: campaign {campaign_id} '{name}' {old} → {status}")
    if not apply:
        print("Re-run with --apply to execute.")
        return
    log_mutation(f"campaign_status_{status}", {"campaign_id": campaign_id, "name": name}, old)
    api.mutate("campaigns", [{
        "update": {"resourceName": f"customers/{api.cid}/campaigns/{int(campaign_id)}",
                   "status": status},
        "updateMask": "status"}])
    print("DONE — verified by API accept; run 'stats' to confirm.")


def cmd_pause_ad(api, adgroup, ad, apply):
    rn = f"customers/{api.cid}/adGroupAds/{int(adgroup)}~{int(ad)}"
    rows = api.search(f"SELECT ad_group_ad.status FROM ad_group_ad "
                      f"WHERE ad_group_ad.resource_name = '{rn}'")
    if not rows:
        sys.exit(f"ad {adgroup}~{ad} not found")
    old = rows[0]["adGroupAd"]["status"]
    print(f"{'APPLY' if apply else 'DRY-RUN'}: ad {adgroup}~{ad} {old} → PAUSED")
    if not apply:
        print("Re-run with --apply to execute.")
        return
    log_mutation("ad_status_PAUSED", {"resource": rn}, old)
    api.mutate("adGroupAds", [{
        "update": {"resourceName": rn, "status": "PAUSED"},
        "updateMask": "status"}])
    print("DONE.")


def cmd_set_budget(api, budget_id, pounds, apply, approve_spend):
    rows = api.search(f"SELECT campaign_budget.id, campaign_budget.amount_micros "
                      f"FROM campaign_budget WHERE campaign_budget.id = {int(budget_id)}")
    if not rows:
        sys.exit(f"budget {budget_id} not found")
    old_micros = int(rows[0]["campaignBudget"].get("amountMicros", 0))
    new_micros = int(round(pounds * 1e6))
    print(f"{'APPLY' if apply else 'DRY-RUN'}: budget {budget_id} "
          f"£{old_micros / 1e6:.2f}/day → £{pounds:.2f}/day")
    if new_micros > old_micros and not approve_spend:
        sys.exit("BLOCKED: budget increase requires --approve-spend "
                 "(spend changes are human-fired — Axion spend law).")
    if not apply:
        print("Re-run with --apply to execute.")
        return
    log_mutation("budget_set", {"budget_id": budget_id}, old_micros)
    api.mutate("campaignBudgets", [{
        "update": {"resourceName": f"customers/{api.cid}/campaignBudgets/{int(budget_id)}",
                   "amountMicros": str(new_micros)},
        "updateMask": "amount_micros"}])
    print("DONE.")


def main():
    p = argparse.ArgumentParser(description="Google Ads connector (Axion)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stats");   s.add_argument("--days", type=int, default=7, choices=[7, 14, 30])
    sub.add_parser("ads")
    s = sub.add_parser("terms");   s.add_argument("--days", type=int, default=7, choices=[7, 14, 30]); s.add_argument("--limit", type=int, default=20)
    sub.add_parser("budgets")
    s = sub.add_parser("raw");     s.add_argument("gaql")
    for name in ("pause-campaign", "enable-campaign"):
        s = sub.add_parser(name);  s.add_argument("--id", required=True); s.add_argument("--apply", action="store_true")
    s = sub.add_parser("pause-ad"); s.add_argument("--adgroup", required=True); s.add_argument("--ad", required=True); s.add_argument("--apply", action="store_true")
    s = sub.add_parser("set-budget"); s.add_argument("--id", required=True); s.add_argument("--gbp", type=float, required=True); s.add_argument("--apply", action="store_true"); s.add_argument("--approve-spend", action="store_true")

    a = p.parse_args()
    api = Api()
    if a.cmd == "stats":            cmd_stats(api, a.days)
    elif a.cmd == "ads":            cmd_ads(api)
    elif a.cmd == "terms":          cmd_terms(api, a.days, a.limit)
    elif a.cmd == "budgets":        cmd_budgets(api)
    elif a.cmd == "raw":            cmd_raw(api, a.gaql)
    elif a.cmd == "pause-campaign": set_campaign_status(api, a.id, "PAUSED", a.apply)
    elif a.cmd == "enable-campaign": set_campaign_status(api, a.id, "ENABLED", a.apply)
    elif a.cmd == "pause-ad":       cmd_pause_ad(api, a.adgroup, a.ad, a.apply)
    elif a.cmd == "set-budget":     cmd_set_budget(api, a.id, a.gbp, a.apply, a.approve_spend)


if __name__ == "__main__":
    main()
