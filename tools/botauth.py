#!/usr/bin/env python3
"""botauth.py -- Web Bot Auth for OUR OWN readers (Fig Tree primitive, 6 Sep 2026).

Why: the user asked for connectors that reach any site. The honest mechanism the web is
converging on is Web Bot Auth (IETF draft-meunier-web-bot-auth-architecture): a bot signs
each request with an Ed25519 key, publishes the public key in a directory at
/.well-known/http-message-signatures-directory, and registers once with Cloudflare. A
signed request says who we are; sites that trust verified agents let us in on purpose.
That is the opposite of evasion, and it is how our readers stop being "generic-bot" in
other people's meters -- and how other people's agents can prove themselves at OUR doors.

Needs `cryptography` (installed: 49.0.0). Private key lives OUTSIDE git:
business/agent_economy/botauth/<name>.pem (the .gitignore *.pem rule) -- never printed.

  py scripts/botauth.py keygen [--name axion-reader]        one-time: PEM + public JWK + thumbprint; prints the JWK and the Worker secret material
  py scripts/botauth.py headers https://example.com/path    the three headers for one request (JSON)
  py scripts/botauth.py test                                 signs a request to Cloudflare's own tester, crawltest.com/cdn-cgi/web-bot-auth
                                                             401 = formatted correctly, key not yet registered (expected before the form)
                                                             200 = registered and verified · 400 = malformed (a defect here)
  py scripts/botauth.py verify-directory https://open.getaxionlabs.com
                                                             fetches our directory, checks Content-Type, JWKS, and the response signature
  py scripts/botauth.py selftest                             sign -> verify round trip, no network
Exit 0 ok / 1 defect / 2 not configured.
"""
import base64, hashlib, json, os, secrets, sys, time, urllib.parse, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEYDIR = ROOT / "business" / "agent_economy" / "botauth"
DEFAULT_NAME = "axion-reader"
SIGNATURE_AGENT = os.environ.get("BOTAUTH_SIGNATURE_AGENT", "https://example.com")   # origin serving /.well-known/http-message-signatures-directory

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
except ImportError:
    print("[not configured] pip install cryptography"); sys.exit(2)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwk_of(pub):
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"kty": "OKP", "crv": "Ed25519", "x": b64u(raw)}


def thumbprint(jwk):
    """RFC 7638 / RFC 8037 A.3: SHA-256 over the canonical JSON of the required members, base64url."""
    canon = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"]}, separators=(",", ":"), sort_keys=True).encode()
    return b64u(hashlib.sha256(canon).digest())


def load_private(name=DEFAULT_NAME):
    p = KEYDIR / f"{name}.pem"
    if not p.exists():
        print(f"[not configured] no key at {p.relative_to(ROOT)} -- run: py scripts/botauth.py keygen"); sys.exit(2)
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def signature_base(components, params_str):
    """RFC 9421 signature base: one line per covered component, then the @signature-params line."""
    lines = [f'{name}: {value}' for name, value in components]
    lines.append(f'"@signature-params": {params_str}')
    return "\n".join(lines).encode()


def params(component_names, keyid, tag, created, expires, nonce):
    inner = " ".join(component_names)
    return f'({inner});alg="ed25519";keyid="{keyid}";nonce="{nonce}";tag="{tag}";created={created};expires={expires}'


def request_headers(url, priv=None, agent=SIGNATURE_AGENT, ttl=300):
    """The three Web Bot Auth headers for a request to url. Components: @authority + signature-agent."""
    priv = priv or load_private()
    kid = thumbprint(jwk_of(priv.public_key()))
    authority = urllib.parse.urlsplit(url).netloc.lower()
    created = int(time.time()); expires = created + ttl
    nonce = b64u(secrets.token_bytes(48))
    comps = [('"@authority"', authority), ('"signature-agent"', f'"{agent}"')]
    p = params(['"@authority"', '"signature-agent"'], kid, "web-bot-auth", created, expires, nonce)
    sig = priv.sign(signature_base(comps, p))
    return {"Signature-Agent": f'"{agent}"', "Signature-Input": f"sig1={p}", "Signature": f"sig1=:{base64.b64encode(sig).decode()}:"}


def verify(pub, headers, authority, agent=None):
    """Verify a Web Bot Auth request or a directory response signature. Returns (ok, why)."""
    try:
        si = headers["Signature-Input"]; sg = headers["Signature"]
        label, p = si.split("=", 1)
        sig_b64 = sg.split("=", 1)[1].strip(":")
        inner = p[1:p.index(")")]
        names = inner.split(" ")
        comps = []
        for n in names:
            base_name = n.split(";")[0]
            if base_name == '"@authority"':
                comps.append((n, authority))
            elif base_name == '"signature-agent"':
                comps.append((n, f'"{agent}"'))
            else:
                return False, f"unsupported component {n}"
        pub.verify(base64.b64decode(sig_b64), signature_base(comps, p))
        exp = int(p.split("expires=")[1].split(";")[0])
        if exp < time.time():
            return False, "expired"
        return True, "ok"
    except Exception as e:
        return False, str(e)


def cmd_keygen(name):
    KEYDIR.mkdir(parents=True, exist_ok=True)
    p = KEYDIR / f"{name}.pem"
    if p.exists():
        print(f"[exists] {p.relative_to(ROOT)} -- refusing to overwrite a signing key"); sys.exit(1)
    priv = Ed25519PrivateKey.generate()
    p.write_bytes(priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    jwk = jwk_of(priv.public_key()); kid = thumbprint(jwk)
    (KEYDIR / f"{name}.public.jwk.json").write_text(json.dumps({"keys": [jwk]}, indent=1), encoding="utf-8")
    pkcs8_b64 = base64.b64encode(priv.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode()
    (KEYDIR / f"{name}.pkcs8.b64").write_text(pkcs8_b64, encoding="utf-8")
    print(json.dumps({"ok": True, "private_pem": str(p.relative_to(ROOT)), "public_jwk": jwk, "thumbprint": kid,
                      "worker_secret_file": str((KEYDIR / f"{name}.pkcs8.b64").relative_to(ROOT)),
                      "next": "wrangler secret put BOTAUTH_PRIVATE_PKCS8 < that file (from sites/axion-open); set BOTAUTH_PUBLIC_X var to the jwk x"}, indent=1))


def cmd_headers(url):
    print(json.dumps(request_headers(url), indent=1))


def cmd_test():
    url = "https://crawltest.com/cdn-cgi/web-bot-auth"
    h = request_headers(url); h["User-Agent"] = "AxionReader/1.0 (+https://getaxionlabs.com; web-bot-auth test)"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
            st, body = r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        st, body = e.code, e.read()[:200]
    meaning = {200: "REGISTERED + VERIFIED", 401: "formatted correctly, key not (yet) registered -- submit the bot form", 400: "MALFORMED -- defect in this file"}.get(st, "unexpected")
    print(json.dumps({"tester": url, "http": st, "meaning": meaning, "body": body.decode("utf-8", "replace")}))
    sys.exit(0 if st in (200, 401) else 1)


def cmd_verify_directory(origin):
    url = origin.rstrip("/") + "/.well-known/http-message-signatures-directory"
    req = urllib.request.Request(url, headers={"Accept": "application/http-message-signatures-directory+json", "User-Agent": "AxionReader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            # HTTP/2 lowercases header names; HTTPMessage.get is case-insensitive, a dict() is not
            st, body = r.status, r.read()
            hdr = {k: r.headers.get(k, "") for k in ("Content-Type", "Signature", "Signature-Input", "Cache-Control")}
    except urllib.error.HTTPError as e:
        print(json.dumps({"url": url, "http": e.code, "ok": False, "body": e.read()[:200].decode("utf-8", "replace")})); sys.exit(1)
    ct = hdr.get("Content-Type", "")
    jwks = json.loads(body)
    keys = [k for k in jwks.get("keys", []) if k.get("kty") == "OKP" and k.get("crv") == "Ed25519"]
    results = []
    for k in keys:
        pub = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(k["x"] + "=="))
        ok, why = verify(pub, {"Signature-Input": hdr.get("Signature-Input", ""), "Signature": hdr.get("Signature", "")}, urllib.parse.urlsplit(url).netloc)
        results.append({"thumbprint": thumbprint(k), "signature": why, "keyid_matches": f'keyid="{thumbprint(k)}"' in hdr.get("Signature-Input", "")})
    good = st == 200 and ct.startswith("application/http-message-signatures-directory+json") and keys and all(r["signature"] == "ok" and r["keyid_matches"] for r in results)
    print(json.dumps({"url": url, "http": st, "content_type": ct, "keys": len(keys), "results": results, "cache_control": hdr.get("Cache-Control"), "ok": bool(good)}, indent=1))
    sys.exit(0 if good else 1)


def cmd_selftest():
    priv = Ed25519PrivateKey.generate(); pub = priv.public_key()
    h = request_headers("https://example.com/x", priv=priv)
    ok, why = verify(pub, h, "example.com", SIGNATURE_AGENT)
    bad, _ = verify(pub, h, "evil.example", SIGNATURE_AGENT)           # authority swap must fail
    tampered = dict(h); tampered["Signature"] = "sig1=:" + base64.b64encode(b"\x00" * 64).decode() + ":"
    bad2, _ = verify(pub, tampered, "example.com", SIGNATURE_AGENT)
    tp_known = thumbprint({"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"  # RFC 8037 A.3
    checks = {"round_trip": ok, "authority_swap_rejected": not bad, "tamper_rejected": not bad2, "rfc8037_thumbprint_vector": tp_known,
              "three_headers": set(h) == {"Signature-Agent", "Signature-Input", "Signature"}}
    print(json.dumps({"selftest": "PASS" if all(checks.values()) else "FAIL", **checks}))
    sys.exit(0 if all(checks.values()) else 1)


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(2)
    op = a[0]
    if op == "keygen":
        cmd_keygen(a[a.index("--name") + 1] if "--name" in a else DEFAULT_NAME)
    elif op == "headers":
        cmd_headers(a[1])
    elif op == "test":
        cmd_test()
    elif op == "verify-directory":
        cmd_verify_directory(a[1] if len(a) > 1 else SIGNATURE_AGENT)
    elif op == "selftest":
        cmd_selftest()
    else:
        print(f"unknown op {op}"); sys.exit(2)


if __name__ == "__main__":
    main()
