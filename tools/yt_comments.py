#!/usr/bin/env python3
"""yt_comments.py — read YouTube comment threads through our OWN OAuth token (Fig Tree primitive, 4 Sep 2026).

Written by the denialfacts customer-voice pass when vidIQ ran out of credits. Reads only; uses the
repo-root youtube_token*.json refresh tokens already on disk (never printed). Data API quota: ~1 unit
per commentThreads page, 100 per search — cheap. Cost in tokens: 0.

  py scripts/yt_comments.py search "how to appeal a denied health insurance claim"   # 25 videos
  py scripts/yt_comments.py comments VIDEO_ID [VIDEO_ID ...] > out.txt               # top 100 by relevance + 3 replies each
Pipe the output into py scripts/digest.py --frame thread for a free first pass; quotes must be
checked against this raw output before they are used (facts expire; free wings invent).
"""
import json,sys,urllib.parse,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for _s in (sys.stdout,sys.stderr):
    try: _s.reconfigure(encoding="utf-8",errors="replace")
    except Exception: pass
def http(url,data=None,headers=None,timeout=30):
    req=urllib.request.Request(url,data=data,headers={"User-Agent":"AxionReader/1.0","**":""} if False else {"User-Agent":"AxionReader/1.0",**(headers or {})})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r: return r.status,r.read().decode("utf-8","replace")
    except urllib.error.HTTPError as e: return e.code,e.read().decode("utf-8","replace")[:600]
    except Exception as e: return -1,f"{type(e).__name__}: {e}"
def access_token():
    for tf in ["youtube_token.json","youtube_token_appealsdesk.json","youtube_token_personal.json","youtube_token_littledreamers.json"]:
        p=ROOT/tf
        if not p.exists(): continue
        d=json.loads(p.read_text(encoding="utf-8"))
        body=urllib.parse.urlencode({"client_id":d.get("client_id"),"client_secret":d.get("client_secret"),"refresh_token":d.get("refresh_token"),"grant_type":"refresh_token"}).encode()
        st,txt=http(d.get("token_uri","https://oauth2.googleapis.com/token"),body,{"Content-Type":"application/x-www-form-urlencoded"})
        if st==200:
            try: return json.loads(txt)["access_token"],tf
            except Exception: pass
        else: print("[token %s] %s %s"%(tf,st,txt[:120]),file=sys.stderr)
    return None,None
tok,src=access_token()
if not tok: print("NO TOKEN"); sys.exit(1)
print("[auth via %s]"%src,file=sys.stderr)
H={"Authorization":"Bearer "+tok}
# No verb = an auth check, not a crash. Until 5 Sep 2026 this fell straight into
# sys.argv[1] and raised IndexError at the reader, which is a traceback where the
# house rule wants an honest line. Reaching this point already proves the OAuth
# refresh worked, so say so and exit 2 (usage), not 1 (broken).
if len(sys.argv)<2:
    print("usage: yt_comments.py search \"query\"  |  yt_comments.py comments VIDEO_ID [...]")
    print("[auth OK] token refreshed from %s — no verb given, nothing read."%src)
    sys.exit(2)
mode=sys.argv[1]
if mode=="search":
    q=sys.argv[2]
    st,txt=http("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=25&regionCode=US&relevanceLanguage=en&q="+urllib.parse.quote(q),headers=H)
    if st!=200: print(st,txt); sys.exit(1)
    for it in json.loads(txt).get("items",[]):
        print(it["id"]["videoId"],"|",it["snippet"]["publishedAt"][:10],"|",it["snippet"]["title"][:100],"|",it["snippet"]["channelTitle"][:40])
else:
    for vid in sys.argv[2:]:
        st,txt=http("https://www.googleapis.com/youtube/v3/commentThreads?part=snippet,replies&order=relevance&maxResults=100&videoId="+vid,headers=H)
        print("##### VIDEO %s (%s)"%(vid,st))
        if st!=200: print(txt[:300]); continue
        d=json.loads(txt)
        for it in d.get("items",[]):
            s=it["snippet"]["topLevelComment"]["snippet"]
            print("- [%s|%s] %s"%(s["publishedAt"][:10],s.get("likeCount",0),s["textOriginal"].replace("\n"," ")[:420]))
            for r in (it.get("replies") or {}).get("comments",[])[:3]:
                rs=r["snippet"]
                print("   > [%s] %s"%(rs["publishedAt"][:10],rs["textOriginal"].replace("\n"," ")[:300]))
