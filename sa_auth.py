"""
Service-account authentication for ModDex -- pure Python standard library.

Why this exists
---------------
Vertex AI normally authenticates through the Google Cloud CLI (gcloud + ADC).
That means every machine needs gcloud installed and a browser login. A shop
cannot do that.

A service account removes it completely. The app carries its own credentials
file and mints its own access tokens. Click and run, no setup, no login.

The only hard part is that Google requires the token request to be signed with
RS256, and Python's standard library has no RSA. So this module implements the
minimum needed: PKCS#8 / PKCS#1 DER parsing to recover the private key, and
EMSA-PKCS1-v1_5 signing via modular exponentiation. No pip installs, which is
what keeps the final build a single self-contained executable.
"""

import base64
import hashlib
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_HOST = "oauth2.googleapis.com"
TOKEN_PATH = "/token"
SCOPE = "https://www.googleapis.com/auth/cloud-platform"
GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"


class ServiceAccountError(Exception):
    def __init__(self, message, hint=""):
        super().__init__(message)
        self.hint = hint


# ---------------------------------------------------------------------------
# Minimal DER reader (only what an RSA private key needs)
# ---------------------------------------------------------------------------
def _der_len(buf, i):
    """Return (length, next_index) for a DER length field starting at i."""
    n = buf[i]
    i += 1
    if n < 0x80:
        return n, i
    count = n & 0x7F
    if count == 0 or count > 4:
        raise ServiceAccountError("Malformed private key (bad DER length).")
    return int.from_bytes(buf[i:i + count], "big"), i + count


def _der_expect(buf, i, tag):
    if buf[i] != tag:
        raise ServiceAccountError("Malformed private key (unexpected DER tag).")
    length, i = _der_len(buf, i + 1)
    return i, length


def _der_ints(buf, i, count):
    """Read `count` consecutive DER INTEGERs, returning them as Python ints."""
    out = []
    for _ in range(count):
        i, length = _der_expect(buf, i, 0x02)
        out.append(int.from_bytes(buf[i:i + length], "big"))
        i += length
    return out, i


def _pem_to_der(pem: str) -> bytes:
    lines = [ln.strip() for ln in pem.strip().splitlines()]
    body = "".join(ln for ln in lines if ln and not ln.startswith("-----"))
    try:
        return base64.b64decode(body)
    except Exception:
        raise ServiceAccountError("Could not decode the private key in the credentials file.")


def parse_private_key(pem: str):
    """Recover (n, d) from a PKCS#8 or PKCS#1 RSA private key."""
    der = _pem_to_der(pem)
    i, _ = _der_expect(der, 0, 0x30)                 # outer SEQUENCE

    # PKCS#8 wraps PKCS#1: SEQUENCE { INTEGER 0, AlgorithmIdentifier, OCTET STRING }
    probe = i
    ints, probe2 = _der_ints(der, probe, 1)
    if ints[0] == 0 and der[probe2] == 0x30:
        j, alg_len = _der_expect(der, probe2, 0x30)  # AlgorithmIdentifier
        j += alg_len
        j, _ = _der_expect(der, j, 0x04)             # OCTET STRING payload
        der = der[j:]
        i, _ = _der_expect(der, 0, 0x30)             # inner PKCS#1 SEQUENCE

    # PKCS#1 RSAPrivateKey: version, n, e, d, ...
    vals, _ = _der_ints(der, i, 4)
    n, d = vals[1], vals[3]
    if n <= 0 or d <= 0:
        raise ServiceAccountError("Private key in the credentials file is unusable.")
    return n, d


# ---------------------------------------------------------------------------
# RS256 signing (EMSA-PKCS1-v1_5, RFC 8017)
# ---------------------------------------------------------------------------
_SHA256_DIGEST_INFO = bytes([
    0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65,
    0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20,
])


def rs256_sign(message: bytes, n: int, d: int) -> bytes:
    k = (n.bit_length() + 7) // 8
    t = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        raise ServiceAccountError("RSA key is too small to sign with.")
    padded = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    sig = pow(int.from_bytes(padded, "big"), d, n)
    return sig.to_bytes(k, "big")


def _b64u(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


# ---------------------------------------------------------------------------
# Credentials + token exchange
# ---------------------------------------------------------------------------
_cache = {"tok": "", "exp": 0.0}
_lock = threading.Lock()

REQUIRED = ("client_email", "private_key", "project_id")


def load_credentials(path: str) -> dict:
    if not os.path.isfile(path):
        raise ServiceAccountError(
            "No credentials file found.",
            hint="Place credentials.json next to the app, or connect Google Cloud in Settings.")
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except Exception as e:
        raise ServiceAccountError("credentials.json is not valid JSON: %s" % e)
    if info.get("type") != "service_account":
        raise ServiceAccountError(
            "That credentials file is not a service account key.",
            hint="Download a JSON key for a service account, not an OAuth client.")
    missing = [k for k in REQUIRED if not info.get(k)]
    if missing:
        raise ServiceAccountError("credentials.json is missing: %s" % ", ".join(missing))
    return info


def _post_form(url: str, fields: dict, timeout=60) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
            msg = j.get("error_description") or j.get("error") or body
        except Exception:
            msg = body
        hint = ""
        if "invalid_grant" in body:
            hint = ("The service account key was rejected. It may have been deleted or "
                    "the machine clock may be wrong. Check the Windows date and time.")
        elif e.code == 403:
            hint = "The service account lacks permission. Grant it the Vertex AI User role."
        raise ServiceAccountError(str(msg)[:400], hint=hint)
    except urllib.error.URLError as e:
        raise ServiceAccountError("Network error: %s" % e.reason,
                                  hint="Check the internet connection on this machine.")


def get_token(info: dict) -> str:
    """Mint (and cache) an access token. Tokens last an hour; we refresh at 50 min."""
    with _lock:
        if _cache["tok"] and time.time() < _cache["exp"]:
            return _cache["tok"]

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        kid = info.get("private_key_id")
        if kid:
            header["kid"] = kid
        aud = "https://" + TOKEN_HOST + TOKEN_PATH
        claims = {
            "iss": info["client_email"],
            "scope": SCOPE,
            "aud": aud,
            "iat": now,
            "exp": now + 3600,
        }

        def seg(obj):
            return _b64u(json.dumps(obj, separators=(",", ":")).encode("utf-8"))

        signing_input = seg(header) + b"." + seg(claims)
        n, d = parse_private_key(info["private_key"])
        jwt = signing_input + b"." + _b64u(rs256_sign(signing_input, n, d))

        res = _post_form(aud, {"grant_type": GRANT, "assertion": jwt.decode("ascii")})
        tok = res.get("access_token")
        if not tok:
            raise ServiceAccountError("Google did not return an access token.")
        _cache["tok"] = tok
        _cache["exp"] = time.time() + min(int(res.get("expires_in", 3600)) - 600, 3000)
        return tok


def invalidate():
    with _lock:
        _cache["tok"] = ""
        _cache["exp"] = 0.0


def self_test(path: str) -> dict:
    """Verify the credentials file end to end. Costs nothing."""
    info = load_credentials(path)
    tok = get_token(info)
    return {
        "ok": True,
        "project": info["project_id"],
        "client_email": info["client_email"],
        "token_prefix": tok[:6],
    }
