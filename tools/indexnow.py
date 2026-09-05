#!/usr/bin/env python3
"""indexnow.py — push URLs to IndexNow (Bing, Yandex, Seznam, Naver share one endpoint). 5 Sep 2026.

Each site already serves its IndexNow key at /<key>.txt (see src/index.js). Google does not
take IndexNow; this is for the engines that do, and it is instant and free. 0 tokens.

  py scripts/indexnow.py                          # every URL in every site's sitemap (max 10,000 per call)
  MSYS_NO_PATHCONV=1 py scripts/indexnow.py example.com /appeal-rejected-what-now   # Git Bash rewrites /paths otherwise
  py scripts/indexnow.py --changed-since 2026-09-04   # only sitemap <lastmod> on/after the date, where present
Prints one line per site: URLs sent and the HTTP status (200/202 = accepted).
"""
import argparse, json, re, sys, urllib.request

SITES = {
    "example.com": "<your-indexnow-key>",
    "example.com": "<your-indexnow-key>",
    "example.com": "<your-indexnow-key>",
    "example.com": "<your-indexnow-key>",
    "example.com": "<your-indexnow-key>",
}


def sitemap_urls(host, since=None):
    # our own edge answers 1010/403 to python-urllib's default UA (security review 5 Sep) — send a browser UA
    req = urllib.request.Request(f"https://{host}/sitemap.xml", headers={"user-agent": "Mozilla/5.0 axion-indexnow/1"})
    xml = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    out = []
    for m in re.finditer(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", m.group(1))
        mod = re.search(r"<lastmod>([^<]+)</lastmod>", m.group(1))
        if not loc:
            continue
        if since and mod and mod.group(1)[:10] < since:
            continue
        out.append(loc.group(1).strip())
    return out


def submit(host, key, urls):
    body = json.dumps({"host": host, "key": key, "keyLocation": f"https://{host}/{key}.txt", "urlList": urls[:10000]}).encode()
    # api.indexnow.org began answering 422 for valid submissions on 5 Sep 2026 (measured on two sites,
    # including a control whose key serves 200 to every UA). Bing's own endpoint accepts the same body.
    results = []
    for endpoint in ("https://api.indexnow.org/indexnow", "https://www.bing.com/indexnow"):
        req = urllib.request.Request(endpoint, data=body, headers={"content-type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return f"{r.status} via {endpoint.split('/')[2]}"
        except urllib.error.HTTPError as e:
            results.append(f"{e.code}@{endpoint.split('/')[2]}")
        except Exception as e:
            results.append(f"ERR@{endpoint.split('/')[2]}:{e}")
    return " ; ".join(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host", nargs="?")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--changed-since")
    a = ap.parse_args()
    targets = {a.host: SITES[a.host]} if a.host else SITES
    for host, key in targets.items():
        try:
            urls = [f"https://{host}{p}" for p in a.paths] if a.paths else sitemap_urls(host, a.changed_since)
            if not urls:
                print(f"{host:<22} 0 urls (nothing matched)"); continue
            print(f"{host:<22} {len(urls):>4} urls -> {submit(host, key, urls)}")
        except Exception as e:
            print(f"{host:<22} ERROR {e}")


if __name__ == "__main__":
    main()
