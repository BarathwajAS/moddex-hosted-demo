#!/usr/bin/env python3
"""
MODDEX - Vehicle Modification Visualizer
Local desktop tool. Python standard library only. No pip installs required.

Run:  python ModStudio.py
Or on Windows, double-click START_MODSTUDIO.bat
"""

import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import sa_auth
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
# When frozen into a single .exe, read-only resources live in a temp folder that
# PyInstaller unpacks (BUNDLE_DIR), while anything the shop writes -- cache,
# products, config, credentials -- must sit beside the .exe (APP_DIR) so it
# survives across launches and upgrades.
if getattr(sys, "frozen", False):
    BUNDLE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BUNDLE_DIR = APP_DIR = os.path.dirname(os.path.abspath(__file__))

UI_DIR = os.path.join(BUNDLE_DIR, "ui")
ASSETS_DIR = os.path.join(BUNDLE_DIR, "assets")
CATALOG_DIR = os.path.join(BUNDLE_DIR, "catalog")
DATA_DIR = os.path.join(APP_DIR, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
VEHICLE_DIR = os.path.join(DATA_DIR, "vehicles")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
PRODUCT_DIR = os.path.join(DATA_DIR, "products")
_CRED_EXTERNAL = os.path.join(APP_DIR, "credentials.json")
_CRED_BUNDLED = os.path.join(BUNDLE_DIR, "credentials.json")
CRED_PATH = _CRED_EXTERNAL if os.path.isfile(_CRED_EXTERNAL) else _CRED_BUNDLED
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DB_PATH = os.path.join(DATA_DIR, "cache_index.sqlite")

for _d in (DATA_DIR, CACHE_DIR, VEHICLE_DIR, EXPORT_DIR, PRODUCT_DIR):
    os.makedirs(_d, exist_ok=True)

# ----------------------------------------------------------------------------
# Config (API key is obfuscated on disk, never sent to the UI)
# ----------------------------------------------------------------------------
_DEFAULT_CONFIG = {
    "api_key_enc": "",
    "shop_name": "",
    "model": "gemini-2.5-flash-image",
    "edit_mode": "cumulative",     # cumulative | sequential
    "auth_mode": "aistudio",       # aistudio (API key) | vertex (gcloud ADC)
    "gcp_project": "",
    "gcp_location": "us-central1",
    "cache_cap_mb": 5000,
    "calls_today": 0,
    "calls_date": "",
    "calls_total": 0,
    "daily_cap": 0,          # 0 = unlimited
}

_MACHINE_SALT = (os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME")
                 or socket.gethostname() or "modstudio")


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _key_stream() -> bytes:
    return hashlib.sha256(("modstudio::" + _MACHINE_SALT).encode()).digest()


def obfuscate(plain: str) -> str:
    if not plain:
        return ""
    return base64.b64encode(_xor_bytes(plain.encode(), _key_stream())).decode()


def deobfuscate(enc: str) -> str:
    if not enc:
        return ""
    try:
        return _xor_bytes(base64.b64decode(enc.encode()), _key_stream()).decode()
    except Exception:
        return ""


_cfg_lock = threading.Lock()


def load_config() -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    with _cfg_lock:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)


def get_api_key() -> str:
    """Environment wins over the stored key, so a hosted deployment can
    supply credentials without them ever sitting in the build."""
    env = (os.environ.get("MODDEX_API_KEY")
           or os.environ.get("GEMINI_API_KEY") or "").strip()
    if env:
        return env
    return deobfuscate(load_config().get("api_key_enc", ""))


APIKEY_TXT = os.path.join(APP_DIR, "apikey.txt")


def import_key_file() -> None:
    """On startup, if apikey.txt sits next to the tool and no key is stored yet,
    read it, encrypt it into config, then shred the plaintext file so the key
    does not linger on disk in the clear."""
    if not os.path.exists(APIKEY_TXT):
        return
    try:
        with open(APIKEY_TXT, "r", encoding="utf-8") as f:
            key = "".join(
                ln for ln in f.read().splitlines()
                if not ln.strip().startswith("#")
            ).strip()
    except Exception:
        return
    if not key:
        return
    cfg = load_config()
    if cfg.get("api_key_enc") and deobfuscate(cfg["api_key_enc"]) == key:
        pass  # already imported
    else:
        cfg["api_key_enc"] = obfuscate(key)
        save_config(cfg)
        print("  API key imported from apikey.txt and encrypted.")
    try:
        os.remove(APIKEY_TXT)
        print("  apikey.txt deleted (the key is now stored encrypted).")
    except Exception:
        print("  NOTE: could not delete apikey.txt - remove it yourself.")


# ===== v12 REMOTE HARDENING ==============================================
_PROTECTED_PREFIXES = ("/api/", "/img/")


def _remote_mode() -> bool:
    """True when the tool is exposed beyond this machine (tunnel or host)."""
    return (os.environ.get("MODDEX_REMOTE") or "").strip().lower() \
        not in ("", "0", "false", "no", "off")


def _staff_pin() -> str:
    return (os.environ.get("MODDEX_PIN") or "").strip()


def _daily_cap() -> int:
    """Hard ceiling on billable generations per day. 0 disables the limit.
    Cached previews are always free and never counted against it."""
    env = (os.environ.get("MODDEX_DAILY_CAP") or "").strip()
    if env.isdigit():
        return int(env)
    try:
        return int(load_config().get("daily_cap", 0) or 0)
    except Exception:
        return 0


def calls_used_today() -> int:
    cfg = load_config()
    if cfg.get("calls_date") != time.strftime("%Y-%m-%d"):
        return 0
    return int(cfg.get("calls_today", 0) or 0)


def bump_call_counter() -> None:
    cfg = load_config()
    today = time.strftime("%Y-%m-%d")
    if cfg.get("calls_date") != today:
        cfg["calls_date"] = today
        cfg["calls_today"] = 0
    cfg["calls_today"] = int(cfg.get("calls_today", 0)) + 1
    cfg["calls_total"] = int(cfg.get("calls_total", 0)) + 1
    save_config(cfg)


# ----------------------------------------------------------------------------
# Cache index database
# ----------------------------------------------------------------------------
_db_lock = threading.Lock()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _db_lock, db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                hash        TEXT PRIMARY KEY,
                vehicle_id  TEXT NOT NULL,
                angle       TEXT NOT NULL,
                state_json  TEXT NOT NULL,
                prompt      TEXT NOT NULL,
                path        TEXT NOT NULL,
                bytes       INTEGER NOT NULL,
                created_at  REAL NOT NULL,
                last_used   REAL NOT NULL,
                hits        INTEGER NOT NULL DEFAULT 0
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                product_id  TEXT PRIMARY KEY,
                label       TEXT NOT NULL,
                vtype       TEXT NOT NULL,
                category    TEXT NOT NULL,
                spec        TEXT,
                created_at  REAL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                vehicle_id  TEXT PRIMARY KEY,
                label       TEXT,
                vtype       TEXT,
                created_at  REAL
            )""")
        # v17 PRICING: add the price column to shops that already have products
        _pcols = [r[1] for r in conn.execute("PRAGMA table_info(products)")]
        if "price" not in _pcols:
            conn.execute("ALTER TABLE products ADD COLUMN price REAL NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_vehicle ON cache(vehicle_id)")


def cache_lookup(h: str):
    with _db_lock, db() as conn:
        row = conn.execute("SELECT * FROM cache WHERE hash=?", (h,)).fetchone()
        if row and os.path.exists(row["path"]):
            conn.execute("UPDATE cache SET hits=hits+1, last_used=? WHERE hash=?",
                         (time.time(), h))
            return dict(row)
        if row:
            # File was deleted underneath us; drop the stale index row.
            conn.execute("DELETE FROM cache WHERE hash=?", (h,))
    return None


def cache_store(h, vehicle_id, angle, state, prompt, path):
    size = os.path.getsize(path)
    now = time.time()
    with _db_lock, db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?,?,?,?,?,?)",
            (h, vehicle_id, angle, json.dumps(state, sort_keys=True),
             prompt, path, size, now, now, 0))
    enforce_cache_cap()


def enforce_cache_cap() -> None:
    cap = int(load_config().get("cache_cap_mb", 5000)) * 1024 * 1024
    with _db_lock, db() as conn:
        total = conn.execute("SELECT COALESCE(SUM(bytes),0) t FROM cache").fetchone()["t"]
        if total <= cap:
            return
        rows = conn.execute("SELECT hash, path, bytes FROM cache ORDER BY last_used ASC").fetchall()
        for r in rows:
            if total <= cap * 0.9:
                break
            try:
                os.remove(r["path"])
            except OSError:
                pass
            conn.execute("DELETE FROM cache WHERE hash=?", (r["hash"],))
            total -= r["bytes"]


# ----------------------------------------------------------------------------
# The hash: the heart of the no-regeneration guarantee
# ----------------------------------------------------------------------------
def canonical_state(state: dict) -> dict:
    """Normalise a config so equivalent configs always hash identically."""
    mods = {k: v for k, v in (state.get("mods") or {}).items() if v}
    custom = re.sub(r"\s+", " ", (state.get("custom") or "")).strip().lower()
    return {
        "vehicle_id": state.get("vehicle_id", ""),
        "vtype": state.get("vtype", "bike"),
        "angle": state.get("angle", "side"),
        "mods": dict(sorted(mods.items())),
        "custom": custom,
        "v": 1,  # bump to invalidate every cache entry after a prompt-logic change
    }


def state_hash(state: dict) -> str:
    canon = canonical_state(state)
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def cache_path_for(state: dict, h: str) -> str:
    c = canonical_state(state)
    d = os.path.join(CACHE_DIR, safe_name(c["vehicle_id"]), safe_name(c["angle"]))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, h + ".png")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s or "unknown")[:64]


# ----------------------------------------------------------------------------
# Mod catalog
# ----------------------------------------------------------------------------
_catalog_cache = {}


def load_catalog(vtype: str) -> dict:
    vtype = "car" if vtype == "car" else "bike"
    path = os.path.join(CATALOG_DIR, f"mods_{vtype}.json")
    mtime = os.path.getmtime(path) if os.path.exists(path) else 0
    key = (vtype, mtime)
    if key not in _catalog_cache:
        with open(path, "r", encoding="utf-8") as f:
            _catalog_cache.clear()
            _catalog_cache[key] = json.load(f)
    return _catalog_cache[key]


def variant_lookup(vtype: str):
    """Return {category_id: {variant_id: variant}}."""
    cat = load_catalog(vtype)
    return {c["id"]: {v["id"]: v for v in c["variants"]} for c in cat["categories"]}


# ----------------------------------------------------------------------------
# Shop products (the shop's REAL inventory -- loaded once, reused forever)
# ----------------------------------------------------------------------------
MAX_REFS = 2          # customer vehicle + 2 refs = 3 images (2.5-flash sweet spot)


def product_dir(pid: str) -> str:
    return os.path.join(PRODUCT_DIR, safe_name(pid))


def product_ref_paths(pid: str):
    pd = product_dir(pid)
    if not os.path.isdir(pd):
        return []
    out = [os.path.join(pd, f) for f in sorted(os.listdir(pd))
           if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))]
    return out[:MAX_REFS]


def product_get(pid: str):
    with _db_lock, db() as conn:
        r = conn.execute("SELECT * FROM products WHERE product_id=?",
                         (safe_name(pid),)).fetchone()
    return dict(r) if r else None


def product_list(vtype: str = ""):
    with _db_lock, db() as conn:
        if vtype:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM products WHERE vtype=? ORDER BY category, label", (vtype,))]
        else:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM products ORDER BY category, label")]
    for r in rows:
        r["refs"] = [rel_product_url(p) for p in product_ref_paths(r["product_id"])]
    return rows


def rel_product_url(path: str) -> str:
    return "/img/product/" + os.path.relpath(path, PRODUCT_DIR).replace(os.sep, "/")


# ----------------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------------
PRESERVE = (
    "CRITICAL RULES: This is a real customer's vehicle. Preserve the exact same vehicle, "
    "the same camera angle, the same perspective, the same framing, the same background, "
    "the same lighting and the same shadows. Do not change the vehicle's model, body shape, "
    "proportions, or position. Do not move the camera. Do not add or remove any other objects. "
    "Do not add text, watermarks, logos or borders. Change ONLY the numbered "
    "modifications listed above. Do not alter any part of the vehicle that is not "
    "explicitly listed. "
    "Output a single photorealistic image at the same resolution."
)


PRESERVE_BG = (
    "CRITICAL RULES: This is a real customer's vehicle. Preserve the exact same "
    "vehicle, the same camera angle, the same perspective, the same framing and the "
    "same position and size of the vehicle in the frame. Do not change the vehicle's "
    "model, body shape or proportions. Do not move the camera. "
    "The background and the lighting MUST be replaced exactly as described in the "
    "numbered modifications: remove every trace of the original surroundings, "
    "including any people, other vehicles, buildings, signage and clutter, and cut "
    "the vehicle out cleanly so no fragment of the old scene remains around its "
    "edges, under it or through its windows. Relight the vehicle so that it sits "
    "believably in the new environment, with matching reflections, colour cast and a "
    "correct contact shadow on the new ground. Apart from that relighting, change "
    "nothing about the vehicle that is not explicitly listed. "
    "Do not add text, watermarks, logos or borders. "
    "Output a single photorealistic image at the same resolution."
)


REF_RULES = (
    "REFERENCE IMAGES: Image 1 is the customer's actual vehicle and is the ONLY image whose "
    "vehicle, angle, framing, background and lighting you must keep. The images after it are "
    "product reference photos. Use them ONLY to copy the product's shape, proportions, detailing "
    "and finish. Completely ignore the background, the lighting, the surroundings and any other "
    "vehicle appearing in the reference photos. Do not copy any vehicle from a reference photo "
    "into the result. Do not copy any watermark, logo overlay, text or border from a reference photo."
)


def build_prompt_and_refs(state: dict):
    """Returns (prompt, [reference image paths]).

    Generic catalog mods are described in words. Shop products are described in words AND
    backed by the shop's own reference photographs, which is what makes a specific real
    product reproducible instead of a generic lookalike.
    """
    c = canonical_state(state)
    lookup = variant_lookup(c["vtype"])
    lines, ref_paths = [], []
    bg_lines = []          # v17 PRICING + BACKGROUNDS: always applied last

    for cat_id, var_id in c["mods"].items():
        vid = str(var_id)
        if vid.startswith("shop:"):
            prod = product_get(vid[5:])
            if not prod:
                continue
            idx = []
            for pth in product_ref_paths(vid[5:]):
                if len(ref_paths) >= MAX_REFS:
                    break
                ref_paths.append(pth)
                idx.append(len(ref_paths) + 1)      # image 1 is the vehicle
            where = (" shown in reference image " + " and ".join(str(i) for i in idx)) if idx else ""
            spec = (prod.get("spec") or "").strip()
            lines.append(
                f"- Fit the exact {prod['label']}{where} onto this vehicle. Reproduce its "
                f"specific shape, proportions, contours and finish faithfully."
                + (f" {spec}" if spec else ""))
        else:
            v = lookup.get(cat_id, {}).get(vid)
            if v and v.get("prompt"):
                if v.get("bg") or cat_id == "background":
                    bg_lines.append("- " + v["prompt"])
                else:
                    lines.append("- " + v["prompt"])

    if c["custom"]:
        lines.append("- " + (state.get("custom") or "").strip())

    # the scene swap goes last so every part change is decided first
    lines.extend(bg_lines)
    preserve = PRESERVE_BG if bg_lines else PRESERVE

    noun = "car" if c["vtype"] == "car" else "motorcycle"
    if not lines:
        return (f"Return this photo of the {noun} unchanged, cleanly and sharply. {PRESERVE}", [])

    numbered = "\n".join(
        f"{i}. " + (ln[2:] if ln.startswith("- ") else ln)
        for i, ln in enumerate(lines, 1))
    prompt = (f"Edit this photograph of a {noun}. Apply ALL {len(lines)} of the following "
              f"modifications. Every single one must be clearly visible in the result:\n"
              + numbered + "\n\n" + preserve)
    if ref_paths:
        prompt += "\n\n" + REF_RULES
    return prompt, ref_paths


def build_prompt(state: dict) -> str:
    return build_prompt_and_refs(state)[0]


# ----------------------------------------------------------------------------
# Gemini client (Google AI Studio)
# ----------------------------------------------------------------------------
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL_FALLBACKS = [
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
    "gemini-2.0-flash-preview-image-generation",
]


class GeminiError(Exception):
    def __init__(self, message, status=None, hint=""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def _http_json(url, payload=None, method="GET", timeout=180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        hint = ""
        if e.code == 400 and "API key not valid" in msg:
            hint = "That API key was rejected. Re-copy it from aistudio.google.com."
        elif e.code == 403:
            hint = "Key is valid but not permitted for this model. Enable billing in Google Cloud Console."
        elif e.code == 429:
            hint = ("Quota exhausted. The free tier usually does not cover image output. "
                    "Enable billing on your Google Cloud project (set a budget cap of Rs.500).")
        elif e.code == 404:
            hint = "Model not available to this key. The app will try the next model automatically."
        raise GeminiError(msg, status=e.code, hint=hint)
    except urllib.error.URLError as e:
        raise GeminiError(f"Network error: {e.reason}", status=0,
                          hint="Check the internet connection on this machine.")


def test_api_key(key: str) -> dict:
    """Verify the key and report which image models it can reach."""
    info = _http_json(f"{GEMINI_BASE}/models?key={urllib.parse.quote(key)}&pageSize=200")
    names = [m.get("name", "").split("/")[-1] for m in info.get("models", [])]
    image_models = [m for m in MODEL_FALLBACKS if m in names]
    return {
        "ok": True,
        "total_models": len(names),
        "image_models": image_models,
        "chosen": image_models[0] if image_models else None,
    }


def gemini_edit_image(key: str, model: str, prompt: str, image_bytes: bytes,
                      mime: str = "image/png", refs=None) -> bytes:
    parts = [{"text": prompt},
             {"inline_data": {"mime_type": mime,
                              "data": base64.b64encode(image_bytes).decode()}}]
    for rb, rm in (refs or [])[:MAX_REFS]:
        parts.append({"inline_data": {"mime_type": rm,
                                      "data": base64.b64encode(rb).decode()}})
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"], "temperature": 0.15},
    }

    tried, last = [], None
    order = [model] + [m for m in MODEL_FALLBACKS if m != model]
    for m in order:
        tried.append(m)
        url = f"{GEMINI_BASE}/models/{m}:generateContent?key={urllib.parse.quote(key)}"
        try:
            resp = _http_json(url, payload, method="POST")
        except GeminiError as e:
            last = e
            if e.status in (404, 400):
                continue        # model name not usable for this key -> try next
            raise
        cands = resp.get("candidates") or []
        if not cands:
            fb = (resp.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(f"Model returned no image (blocked: {fb})" if fb
                              else "Model returned no candidates.", status=200,
                              hint="Try rewording the custom request.")
        for part in cands[0].get("content", {}).get("parts", []):
            blob = part.get("inline_data") or part.get("inlineData")
            if blob and blob.get("data"):
                if m != model:
                    cfg = load_config(); cfg["model"] = m; save_config(cfg)
                return base64.b64decode(blob["data"])
        txt = " ".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
        last = GeminiError(f"Model replied with text instead of an image: {txt[:300]}",
                           status=200, hint="Usually a safety refusal. Reword the request.")
    raise last or GeminiError(f"No usable image model. Tried: {', '.join(tried)}")


# ----------------------------------------------------------------------------
# Vertex AI backend -- authenticates with gcloud ADC, spends Google Cloud credits
# ----------------------------------------------------------------------------
SCHEME = "http" + "s" + "://"

VERTEX_MODELS = [
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
]

_token_cache = {"tok": "", "exp": 0.0}
_token_lock = threading.Lock()


def _gcloud_exe():
    for name in ("gcloud.cmd", "gcloud"):
        p = shutil.which(name)
        if p:
            return p
    for guess in (
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"),
        os.path.expandvars(r"%APPDATA%\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"),
        r"C:\Program Files (x86)\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
        r"C:\Program Files\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd",
    ):
        if os.path.isfile(guess):
            return guess
    return None


GCLOUD_MISSING_HINT = ("Install the Google Cloud CLI from cloud.google.com/sdk/docs/install, "
                       "then close and reopen ModDex.")
ADC_HINT = "Open Command Prompt and run:  gcloud auth application-default login"


def _run_gcloud(args, timeout=90):
    exe = _gcloud_exe()
    if not exe:
        raise GeminiError("Google Cloud CLI (gcloud) is not installed.", status=0,
                          hint=GCLOUD_MISSING_HINT)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run([exe] + args, capture_output=True, text=True,
                           timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        raise GeminiError("gcloud timed out.", status=0, hint=ADC_HINT)
    except OSError as e:
        raise GeminiError(f"Could not run gcloud: {e}", status=0, hint=GCLOUD_MISSING_HINT)
    if r.returncode != 0:
        raise GeminiError((r.stderr or r.stdout or "gcloud failed").strip()[:400],
                          status=0, hint=ADC_HINT)
    return (r.stdout or "").strip()


# ===== HOSTED PITCH SECURITY ==============================================
def _load_service_account_credentials() -> dict:
    """Load Vertex credentials from a hosting secret, never from shipped code.

    MODDEX_VERTEX_CREDENTIALS accepts the original JSON object. The _B64 form
    is convenient on hosts whose secret editor does not accept multiline text.
    A local credentials.json remains supported for an owner's private machine.
    """
    raw = (os.environ.get("MODDEX_VERTEX_CREDENTIALS") or "").strip()
    b64 = (os.environ.get("MODDEX_VERTEX_CREDENTIALS_B64") or "").strip()
    if b64 and not raw:
        try:
            raw = base64.b64decode(b64).decode("utf-8")
        except Exception as e:
            raise sa_auth.ServiceAccountError("Hosted Vertex credential is not valid base64: %s" % e)
    if raw:
        try:
            info = json.loads(raw)
        except Exception as e:
            raise sa_auth.ServiceAccountError("Hosted Vertex credential is not valid JSON: %s" % e)
        if info.get("type") != "service_account":
            raise sa_auth.ServiceAccountError("Hosted Vertex credential is not a service-account key.")
        missing = [k for k in sa_auth.REQUIRED if not info.get(k)]
        if missing:
            raise sa_auth.ServiceAccountError("Hosted Vertex credential is missing: %s" % ", ".join(missing))
        return info
    return sa_auth.load_credentials(CRED_PATH)


def have_bundled_credentials() -> bool:
    return bool((os.environ.get("MODDEX_VERTEX_CREDENTIALS") or "").strip()
                or (os.environ.get("MODDEX_VERTEX_CREDENTIALS_B64") or "").strip()
                or os.path.isfile(CRED_PATH))


def vertex_token() -> str:
    """Access token for Vertex.

    Preferred path is a bundled service account: no gcloud, no browser login,
    nothing for the shop to install. Falls back to gcloud ADC for development
    machines that were set up the old way.
    """
    cfg = load_config()
    if cfg.get("auth_mode") == "service_account" or (
            have_bundled_credentials() and not _gcloud_exe()):
        try:
            return sa_auth.get_token(_load_service_account_credentials())
        except sa_auth.ServiceAccountError as e:
            raise GeminiError(str(e), status=0, hint=e.hint)
    return _gcloud_token()


def _gcloud_token() -> str:
    """ADC access token, cached for 45 minutes (they last 60)."""
    with _token_lock:
        if _token_cache["tok"] and time.time() < _token_cache["exp"]:
            return _token_cache["tok"]
        tok = _run_gcloud(["auth", "application-default", "print-access-token"])
        if not tok:
            raise GeminiError("gcloud returned an empty access token.", status=0, hint=ADC_HINT)
        _token_cache["tok"] = tok
        _token_cache["exp"] = time.time() + 45 * 60
        return tok


def vertex_host(location: str) -> str:
    return ("aiplatform.googleapis.com" if location == "global"
            else f"{location}-aiplatform.googleapis.com")


def vertex_url(project: str, location: str, model: str) -> str:
    return (SCHEME + vertex_host(location) +
            f"/v1/projects/{project}/locations/{location}"
            f"/publishers/google/models/{model}:generateContent")


def _vertex_post(url, payload, token, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        hint = ""
        if e.code == 401:
            _token_cache["tok"] = ""
            hint = "Credentials expired. " + ADC_HINT
        elif e.code == 403:
            hint = ("Vertex AI is not enabled on this project, or this account cannot use it. "
                    "Run:  gcloud services enable aiplatform.googleapis.com")
        elif e.code == 404:
            hint = "That model is not offered in this region. ModDex will try the next one."
        elif e.code == 429:
            hint = "Vertex rate limit. Wait a few seconds and press Generate again."
        raise GeminiError(msg, status=e.code, hint=hint)
    except urllib.error.URLError as e:
        raise GeminiError(f"Network error: {e.reason}", status=0,
                          hint="Check the internet connection on this machine.")


def vertex_edit_image(project: str, location: str, model: str, prompt: str,
                      image_bytes: bytes, mime: str = "image/png", refs=None) -> bytes:
    token = vertex_token()
    parts = [{"text": prompt},
             {"inlineData": {"mimeType": mime,
                             "data": base64.b64encode(image_bytes).decode()}}]
    for rb, rm in (refs or [])[:MAX_REFS]:
        parts.append({"inlineData": {"mimeType": rm,
                                     "data": base64.b64encode(rb).decode()}})
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"], "temperature": 0.15},
    }
    order = [model] + [m for m in VERTEX_MODELS if m != model]
    tried, last = [], None
    for m in order:
        tried.append(m)
        try:
            resp = _vertex_post(vertex_url(project, location, m), payload, token)
        except GeminiError as e:
            last = e
            if e.status in (404, 400):
                continue
            raise
        cands = resp.get("candidates") or []
        if not cands:
            fb = (resp.get("promptFeedback") or {}).get("blockReason")
            raise GeminiError(f"Model returned no image (blocked: {fb})" if fb
                              else "Model returned no candidates.", status=200,
                              hint="Try rewording the custom request.")
        for part in cands[0].get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                if m != model:
                    cfg = load_config(); cfg["model"] = m; save_config(cfg)
                return base64.b64decode(blob["data"])
        txt = " ".join(p.get("text", "") for p in cands[0].get("content", {}).get("parts", []))
        last = GeminiError(f"Model replied with text instead of an image: {txt[:300]}",
                           status=200, hint="Usually a safety refusal. Reword the request.")
    raise last or GeminiError(f"No usable Vertex image model. Tried: {', '.join(tried)}")


def test_vertex(project: str = "", location: str = "us-central1") -> dict:
    """Check gcloud + ADC + project + Vertex reachability. Costs nothing."""
    cfg_mode = load_config().get("auth_mode")
    using_sa = cfg_mode == "service_account" or have_bundled_credentials()
    if not using_sa and not _gcloud_exe():
        raise GeminiError("Google Cloud CLI (gcloud) is not installed.", status=0,
                          hint=GCLOUD_MISSING_HINT)
    tok = vertex_token()
    project = (project or "").strip()
    if not project and using_sa:
        project = _load_service_account_credentials()["project_id"]
    if not project:
        project = _run_gcloud(["config", "get-value", "project"]).strip()
        if project in ("", "(unset)", "None"):
            raise GeminiError("No Google Cloud project is set.", status=0,
                              hint="Run:  gcloud config set project YOUR_PROJECT_ID")
    location = (location or "us-central1").strip()

    url = (SCHEME + vertex_host(location) +
           f"/v1/projects/{project}/locations/{location}"
           "/publishers/google/models/" + VERTEX_MODELS[0])
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + tok)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            msg = body
        if e.code == 403:
            raise GeminiError(msg, status=403,
                              hint=("Enable Vertex AI on this project. Run:  "
                                    f"gcloud services enable aiplatform.googleapis.com --project {project}"))
        if e.code == 401:
            _token_cache["tok"] = ""
            raise GeminiError(msg, status=401, hint=ADC_HINT)
        if e.code != 404:
            raise GeminiError(msg, status=e.code, hint="")
    except urllib.error.URLError as e:
        raise GeminiError(f"Network error: {e.reason}", status=0,
                          hint="Check the internet connection on this machine.")
    return {"ok": True, "project": project, "location": location, "model": VERTEX_MODELS[0]}


# ----------------------------------------------------------------------------
# Image helpers (pure stdlib PNG/JPEG size sniffing; no Pillow dependency)
# ----------------------------------------------------------------------------
def sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "ModDex/1.1"

    def log_message(self, fmt, *args):
        if "--verbose" in sys.argv:
            sys.stderr.write("  %s\n" % (fmt % args))

    # -- helpers ------------------------------------------------------------
    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                         "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data: blob:; media-src 'self'; "
                         "connect-src 'self'; object-src 'none'; form-action 'self'")
        if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def _err(self, msg, code=400, hint=""):
        self._json({"error": msg, "hint": hint}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = b""
        while len(raw) < n:
            chunk = self.rfile.read(min(65536, n - len(raw)))
            if not chunk:
                break
            raw += chunk
        return json.loads(raw.decode("utf-8"))

    def _serve_file(self, path):
        if not os.path.isfile(path):
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    # -- auth (v12) ---------------------------------------------------------
    def _auth_ok(self) -> bool:
        """HTTP Basic against MODDEX_PIN. No PIN set means local desktop use
        and the gate stays open, so nothing changes for the shop machine."""
        pin = _staff_pin()
        if not pin:
            return True
        hdr = self.headers.get("Authorization") or ""
        if not hdr.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(hdr[6:]).decode("utf-8", "replace")
        except Exception:
            return False
        supplied = raw.split(":", 1)[1] if ":" in raw else raw
        return secrets.compare_digest(supplied.strip(), pin)

    def _auth_challenge(self):
        body = b'{"error":"Staff PIN required."}'
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ModDex", charset="UTF-8"')
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _blocked(self, p: str) -> bool:
        # A hosted pitch is private end-to-end: HTML, scripts, product photos,
        # customer photos and APIs all require the staff PIN. /healthz is the
        # sole exception so the hosting platform can monitor the process.
        return bool(_staff_pin()) and not self._auth_ok()

    # -- routing ------------------------------------------------------------
    def do_GET(self):
        p = self.path.split("?")[0]
        q = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}

        if p == "/healthz":
            return self._send(200, b"ok", "text/plain")

        if self._blocked(p):
            return self._auth_challenge()

        if p in ("/", "/index.html"):
            return self._serve_file(os.path.join(UI_DIR, "index.html"))
        if p == "/manifest.webmanifest":
            return self._serve_file(os.path.join(UI_DIR, "manifest.webmanifest"))
        if p == "/sw.js":
            return self._serve_file(os.path.join(UI_DIR, "sw.js"))
        if p.startswith("/ui/"):
            return self._serve_file(os.path.join(UI_DIR, os.path.basename(p)))
        if p.startswith("/assets/"):
            rel = p[len("/assets/"):].replace("..", "")
            return self._serve_file(os.path.join(ASSETS_DIR, rel))
        if p.startswith("/img/cache/"):
            rel = p[len("/img/cache/"):].replace("..", "")
            return self._serve_file(os.path.join(CACHE_DIR, rel))
        if p.startswith("/img/product/"):
            rel = p[len("/img/product/"):].replace("..", "")
            return self._serve_file(os.path.join(PRODUCT_DIR, rel))
        if p.startswith("/img/vehicle/"):
            rel = p[len("/img/vehicle/"):].replace("..", "")
            return self._serve_file(os.path.join(VEHICLE_DIR, rel))

        if p == "/api/status":
            cfg = load_config()
            with _db_lock, db() as conn:
                r = conn.execute("SELECT COUNT(*) n, COALESCE(SUM(bytes),0) b FROM cache").fetchone()
            return self._json({
                "configured": bool(cfg.get("api_key_enc")) or (
                    cfg.get("auth_mode") in ("vertex", "service_account")
                    and bool(cfg.get("gcp_project"))),
                "builtin_engine": have_bundled_credentials(),
                "auth_mode": cfg.get("auth_mode", "aistudio"),
                "gcp_project": cfg.get("gcp_project", ""),
                "gcp_location": cfg.get("gcp_location", "us-central1"),
                "shop_name": cfg.get("shop_name", ""),
                "model": cfg.get("model"),
                "edit_mode": cfg.get("edit_mode"),
                "cache_cap_mb": cfg.get("cache_cap_mb"),
                "cached_images": r["n"],
                "cache_mb": round(r["b"] / 1048576, 1),
                "calls_today": cfg.get("calls_today", 0) if cfg.get("calls_date") == time.strftime("%Y-%m-%d") else 0,
                "calls_total": cfg.get("calls_total", 0),
            })

        if p == "/api/catalog":
            vtype = (q.get("type") or ["bike"])[0]
            try:
                return self._json(load_catalog(vtype))
            except Exception as e:
                return self._err(f"Catalog load failed: {e}", 500)

        if p == "/api/angles":
            with open(os.path.join(CATALOG_DIR, "angles.json"), encoding="utf-8") as f:
                return self._json(json.load(f))

        if p == "/api/examples":
            with open(os.path.join(CATALOG_DIR, "examples.json"), encoding="utf-8") as f:
                return self._json(json.load(f))

        if p == "/api/products":
            vtype = (q.get("type") or [""])[0]
            return self._json({"products": product_list(vtype)})

        if p == "/api/vehicles":
            with _db_lock, db() as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM vehicles ORDER BY created_at DESC LIMIT 50")]
            for r in rows:
                vd = os.path.join(VEHICLE_DIR, r["vehicle_id"])
                r["angles"] = sorted(os.path.splitext(f)[0] for f in os.listdir(vd)) if os.path.isdir(vd) else []
            return self._json({"vehicles": rows})

        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            body = self._body()
        except Exception as e:
            return self._err(f"Bad request body: {e}")

        if self._blocked(p):
            return self._auth_challenge()

        # ---- API key setup ------------------------------------------------
        if p == "/api/key/test":
            key = (body.get("key") or "").strip()
            if not key:
                return self._err("Paste your Google AI Studio API key first.")
            try:
                res = test_api_key(key)
            except GeminiError as e:
                return self._err(str(e), 200 if e.status else 502, e.hint)
            if not res["image_models"]:
                return self._err(
                    "Key works, but no image-capable model is available to it.",
                    200,
                    "Enable billing on the Google Cloud project behind this key. "
                    "Image output is not on the free tier.")
            cfg = load_config()
            cfg["api_key_enc"] = obfuscate(key)
            cfg["model"] = res["chosen"]
            if body.get("shop_name"):
                cfg["shop_name"] = body["shop_name"].strip()
            save_config(cfg)
            return self._json({"ok": True, "model": res["chosen"],
                               "image_models": res["image_models"]})

        if p == "/api/vertex/test":
            try:
                res = test_vertex((body.get("project") or "").strip(),
                                  (body.get("location") or "us-central1").strip())
            except GeminiError as e:
                return self._err(str(e), 200 if e.status else 502, e.hint)
            cfg = load_config()
            cfg["auth_mode"] = "vertex"
            cfg["gcp_project"] = res["project"]
            cfg["gcp_location"] = res["location"]
            cfg["model"] = res["model"]
            if body.get("shop_name"):
                cfg["shop_name"] = body["shop_name"].strip()
            save_config(cfg)
            return self._json(dict(res))

        if p == "/api/settings":
            cfg = load_config()
            for k in ("shop_name", "edit_mode", "cache_cap_mb", "model",
                      "auth_mode", "gcp_project", "gcp_location"):
                if k in body:
                    cfg[k] = body[k]
            save_config(cfg)
            return self._json({"ok": True})

        # ---- Shop products (one-time setup, reused forever) ---------------
        if p == "/api/product/save":
            label = (body.get("label") or "").strip()
            category = safe_name(body.get("category") or "")
            vtype = body.get("vtype") or "bike"
            if not label:
                return self._err("Give the product a name, e.g. 'DM Widebody Kit'.")
            if not category:
                return self._err("Pick which category this product belongs to.")
            pid = safe_name(body.get("product_id") or ("prd_" + hashlib.sha256(
                (label + vtype + category + str(time.time())).encode()).hexdigest()[:10]))
            pd = product_dir(pid)
            os.makedirs(pd, exist_ok=True)
            images = body.get("images") or []
            if images:
                for old_f in os.listdir(pd):
                    os.remove(os.path.join(pd, old_f))
                for i, b64 in enumerate(images[:MAX_REFS]):
                    if "," in (b64 or "")[:64]:
                        b64 = b64.split(",", 1)[1]
                    try:
                        raw = base64.b64decode(b64 or "")
                    except Exception:
                        return self._err("Could not decode reference photo %d." % (i + 1))
                    if len(raw) < 1024:
                        return self._err("Reference photo %d looks empty." % (i + 1))
                    if len(raw) > 25 * 1024 * 1024:
                        return self._err("Keep reference photos under 25 MB.")
                    mime = sniff_mime(raw)
                    if mime == "application/octet-stream":
                        return self._err("Reference photos must be JPG, PNG or WEBP.")
                    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
                    with open(os.path.join(pd, "ref%d%s" % (i + 1, ext)), "wb") as f:
                        f.write(raw)
            if not product_ref_paths(pid):
                return self._err("Add at least one reference photo of the product.",
                                 hint="A clear photo of the product itself is the important one.")
            try:
                price = max(0.0, float(body.get("price") or 0))
            except (TypeError, ValueError):
                return self._err("Price must be a plain number in rupees, e.g. 32000.")
            with _db_lock, db() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO products "
                    "(product_id,label,vtype,category,spec,created_at,price) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (pid, label, vtype, category,
                     (body.get("spec") or "").strip(), time.time(), price))
            return self._json({"ok": True, "product_id": pid,
                               "refs": [rel_product_url(x) for x in product_ref_paths(pid)]})

        if p == "/api/product/delete":
            pid = safe_name(body.get("product_id") or "")
            if not product_get(pid):
                return self._err("That product does not exist.")
            with _db_lock, db() as conn:
                conn.execute("DELETE FROM products WHERE product_id=?", (pid,))
                conn.execute("DELETE FROM cache WHERE state_json LIKE ?", ("%shop:" + pid + "%",))
            shutil.rmtree(product_dir(pid), ignore_errors=True)
            return self._json({"ok": True})

        # ---- Vehicle upload ----------------------------------------------
        if p == "/api/vehicle/upload":
            b64 = body.get("image_b64") or ""
            if "," in b64[:64]:
                b64 = b64.split(",", 1)[1]
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return self._err("Could not decode the uploaded image.")
            if len(raw) < 1024:
                return self._err("That image looks empty or corrupt.")
            if len(raw) > 25 * 1024 * 1024:
                return self._err("Image too large. Keep photos under 25 MB.")
            mime = sniff_mime(raw)
            if mime == "application/octet-stream":
                return self._err("Unsupported file. Use JPG, PNG or WEBP.")

            vid = body.get("vehicle_id") or ("veh_" + hashlib.sha256(
                raw[:200000] + str(time.time()).encode()).hexdigest()[:10])
            vid = safe_name(vid)
            angle = safe_name(body.get("angle") or "side")
            ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]

            vd = os.path.join(VEHICLE_DIR, vid)
            os.makedirs(vd, exist_ok=True)
            for old in os.listdir(vd):                     # replace same-angle photo
                if os.path.splitext(old)[0] == angle:
                    os.remove(os.path.join(vd, old))
            dest = os.path.join(vd, angle + ext)
            with open(dest, "wb") as f:
                f.write(raw)

            with _db_lock, db() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO vehicles VALUES (?,?,?,?)",
                    (vid, body.get("label") or "Customer vehicle",
                     body.get("vtype") or "bike", time.time()))
                if body.get("label"):
                    conn.execute("UPDATE vehicles SET label=?, vtype=? WHERE vehicle_id=?",
                                 (body["label"], body.get("vtype") or "bike", vid))
            return self._json({"ok": True, "vehicle_id": vid, "angle": angle,
                               "url": f"/img/vehicle/{vid}/{angle}{ext}"})

        if p == "/api/vehicle/use_example":
            ex_id = safe_name(body.get("example_id") or "")
            src = os.path.join(ASSETS_DIR, "examples", ex_id)
            if not os.path.isdir(src):
                return self._err("Unknown example vehicle.")
            vid = "example_" + ex_id
            vd = os.path.join(VEHICLE_DIR, vid)
            os.makedirs(vd, exist_ok=True)
            angles = []
            for f in sorted(os.listdir(src)):
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    shutil.copy2(os.path.join(src, f), os.path.join(vd, f))
                    angles.append(os.path.splitext(f)[0])
            with _db_lock, db() as conn:
                conn.execute("INSERT OR REPLACE INTO vehicles VALUES (?,?,?,?)",
                             (vid, body.get("label") or ex_id,
                              body.get("vtype") or "bike", time.time()))
            return self._json({"ok": True, "vehicle_id": vid, "angles": angles})

        # ---- Hash preview (used by UI to pre-check cache) ------------------
        if p == "/api/hash":
            h = state_hash(body)
            hit = cache_lookup(h)
            return self._json({"hash": h, "cached": bool(hit),
                               "url": rel_cache_url(hit["path"]) if hit else None})

        # ---- THE MAIN ROUTE ------------------------------------------------
        if p == "/api/generate":
            return self.handle_generate(body)

        if p == "/api/cache/clear":
            with _db_lock, db() as conn:
                conn.execute("DELETE FROM cache")
            shutil.rmtree(CACHE_DIR, ignore_errors=True)
            os.makedirs(CACHE_DIR, exist_ok=True)
            return self._json({"ok": True})

        if p == "/api/export":
            b64 = (body.get("image_b64") or "").split(",", 1)[-1]
            name = safe_name(body.get("filename") or f"modstudio_{int(time.time())}") + ".png"
            dest = os.path.join(EXPORT_DIR, name)
            with open(dest, "wb") as f:
                f.write(base64.b64decode(b64))
            return self._json({"ok": True, "path": dest})

        if p == "/api/open_folder":
            if _remote_mode():
                return self._err("Not available when the tool is hosted remotely.", 403)
            target = {"exports": EXPORT_DIR, "cache": CACHE_DIR,
                      "app": APP_DIR}.get(body.get("which"), APP_DIR)
            try:
                if sys.platform.startswith("win"):
                    os.startfile(target)                                # noqa
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                else:
                    subprocess.Popen(["xdg-open", target])
            except Exception as e:
                return self._err(str(e), 500)
            return self._json({"ok": True})

        if p == "/api/shutdown":
            if _remote_mode():
                return self._err("Not available when the tool is hosted remotely.", 403)
            threading.Timer(0.4, lambda: os._exit(0)).start()
            return self._json({"ok": True})

        return self._err("Unknown endpoint", 404)

    # -- generate -----------------------------------------------------------
    def handle_generate(self, body):
        state = {
            "vehicle_id": body.get("vehicle_id"),
            "vtype": body.get("vtype", "bike"),
            "angle": body.get("angle", "side"),
            "mods": body.get("mods") or {},
            "custom": body.get("custom") or "",
        }
        if not state["vehicle_id"]:
            return self._err("No vehicle loaded. Upload a photo first.")

        h = state_hash(state)

        # 1. CACHE HIT -> instant, free, pixel-identical to what was shown before
        hit = cache_lookup(h)
        if hit and not body.get("regen"):
            return self._json({"ok": True, "hash": h, "cached": True, "cost": 0,
                               "url": rel_cache_url(hit["path"]),
                               "prompt": hit["prompt"], "ms": 0})

        # 2. Locate the source photo
        base_path = find_vehicle_photo(state["vehicle_id"], state["angle"])
        if not base_path:
            return self._err(f"No photo stored for angle '{state['angle']}'.",
                             hint="Go back and upload a photo for this angle.")

        # No mods selected -> just show the untouched original. Never burn a call.
        if not canonical_state(state)["mods"] and not canonical_state(state)["custom"]:
            return self._json({"ok": True, "hash": h, "cached": True, "cost": 0,
                               "url": rel_vehicle_url(base_path), "original": True, "ms": 0})

        cfg = load_config()
        use_vertex = cfg.get("auth_mode") in ("vertex", "service_account")
        # v12: hard daily ceiling on billable calls
        cap = _daily_cap()
        if cap > 0 and calls_used_today() >= cap:
            return self._err(
                "Daily generation limit reached (%d today)." % cap, 429,
                "Previews you have already generated still open instantly. "
                "The limit resets at midnight.")

        key = ""
        if use_vertex:
            if not cfg.get("gcp_project"):
                return self._err("Vertex mode is on but no Google Cloud project is set.",
                                 401, "Open Settings and run the Vertex connection test.")
        else:
            key = get_api_key()
            if not key:
                return self._err("No API key saved.", 401, "Open Settings and add your key.")

        # 3. Choose the source image: cumulative (from original) or sequential
        src_path = base_path
        if cfg.get("edit_mode") == "sequential" and body.get("prev_hash"):
            prev = cache_lookup(body["prev_hash"])
            if prev:
                src_path = prev["path"]

        with open(src_path, "rb") as f:
            src_bytes = f.read()

        prompt, ref_paths = build_prompt_and_refs(state)
        refs = []
        for rp in ref_paths:
            try:
                with open(rp, "rb") as rf:
                    rbytes = rf.read()
                refs.append((rbytes, sniff_mime(rbytes)))
            except OSError:
                pass
        t0 = time.time()
        try:
            if use_vertex:
                out = vertex_edit_image(cfg.get("gcp_project"),
                                        cfg.get("gcp_location") or "us-central1",
                                        cfg.get("model") or VERTEX_MODELS[0],
                                        prompt, src_bytes, sniff_mime(src_bytes), refs)
            else:
                out = gemini_edit_image(key, cfg.get("model") or MODEL_FALLBACKS[0],
                                        prompt, src_bytes, sniff_mime(src_bytes), refs)
        except GeminiError as e:
            return self._err(str(e), 502, e.hint)

        dest = cache_path_for(state, h)
        with open(dest, "wb") as f:
            f.write(out)
        cache_store(h, safe_name(state["vehicle_id"]), safe_name(state["angle"]),
                    canonical_state(state), prompt, dest)
        bump_call_counter()

        return self._json({"ok": True, "hash": h, "cached": False, "cost": 1,
                           "url": rel_cache_url(dest), "prompt": prompt,
                           "ms": int((time.time() - t0) * 1000)})


def rel_cache_url(path: str) -> str:
    return "/img/cache/" + os.path.relpath(path, CACHE_DIR).replace(os.sep, "/")


def rel_vehicle_url(path: str) -> str:
    return "/img/vehicle/" + os.path.relpath(path, VEHICLE_DIR).replace(os.sep, "/")


def find_vehicle_photo(vehicle_id: str, angle: str):
    vd = os.path.join(VEHICLE_DIR, safe_name(vehicle_id))
    if not os.path.isdir(vd):
        return None
    for f in os.listdir(vd):
        if os.path.splitext(f)[0] == safe_name(angle):
            return os.path.join(vd, f)
    return None


# ----------------------------------------------------------------------------
# Launch: open a real app window, not a browser tab
# ----------------------------------------------------------------------------
def find_browser():
    if sys.platform.startswith("win"):
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        la = os.environ.get("LOCALAPPDATA", "")
        cands = [
            os.path.join(pf86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pf86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(la, r"Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        cands = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    else:
        cands = [shutil.which(x) for x in
                 ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def open_app_window(url):
    exe = find_browser()
    profile = os.path.join(DATA_DIR, "window_profile")
    if exe:
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}",
                          "--window-size=1480,940", "--no-first-run",
                          "--no-default-browser-check", "--disable-features=Translate"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    webbrowser.open(url)
    return False


def free_port(start=8765):
    """First free port from 8765 up.

    v19 PHONE ACCESS: this used to start at 8730 and drift, which meant the
    address typed into a phone stopped working after a restart. 8765 is the
    number written in the docs and in START_TUNNEL.bat, so it stays put.
    """
    for p in range(start, start + 40):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return 0


def lan_address() -> str:
    """This machine's address on the local network, or '' if it has none.

    Opening a UDP socket towards a public address makes the operating system
    choose the interface it would really use, without sending any traffic.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.4)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return ""


def adopt_bundled_credentials():
    """If a service account ships with the app, configure it silently.

    This is what makes the build click-and-run: no gcloud, no login, no setup
    screen for the shop to get wrong.
    """
    if not have_bundled_credentials():
        return
    try:
        info = _load_service_account_credentials()
    except sa_auth.ServiceAccountError as e:
        print("  ! credentials.json found but unusable: %s" % e)
        return
    cfg = load_config()
    changed = False
    if cfg.get("auth_mode") != "service_account":
        cfg["auth_mode"] = "service_account"; changed = True
    if not cfg.get("gcp_project"):
        cfg["gcp_project"] = info["project_id"]; changed = True
    if not cfg.get("gcp_location"):
        cfg["gcp_location"] = "us-central1"; changed = True
    if not cfg.get("model"):
        cfg["model"] = VERTEX_MODELS[0]; changed = True
    if changed:
        save_config(cfg)
    print("  Image engine: built in (project %s)" % info["project_id"])


def main():
    init_db()
    import_key_file()
    adopt_bundled_credentials()
    # v13 HOSTED MODE: cloud hosts dictate the port through PORT
    port = int(os.environ.get("PORT")
               or os.environ.get("MODSTUDIO_PORT") or free_port())
    _hosted = bool(os.environ.get("PORT"))
    # Bind every interface so a phone or tablet on the same WiFi can open the
    # tool. Loopback-only was why phones timed out. Set MODSTUDIO_HOST to
    # 127.0.0.1 to go back to this-machine-only.
    host = (os.environ.get("MODSTUDIO_HOST") or "0.0.0.0").strip()
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://127.0.0.1:{port}/"

    print("=" * 60)
    print("  MODDEX")
    print("=" * 60)
    print(f"  Running at {url}")
    print(f"  Data folder: {DATA_DIR}")
    _lan = lan_address()
    if _lan and not _hosted:
        print("  " + "-" * 56)
        print(f"  ON YOUR PHONE (same WiFi):  http://{_lan}:{port}/")
        print("  Type that into Chrome, then Menu > Add to Home Screen.")
        print("  Times out? Run ALLOW_PHONE_ON_WIFI.bat once, then retry.")
        print("  " + "-" * 56)
    if _staff_pin():
        print("  Staff PIN:  ON - the browser will ask for it")
    elif _hosted:
        print("  " + "!" * 56)
        print("  !! NO STAFF PIN AND THIS IS PUBLIC ON THE INTERNET.")
        print("  !! Anyone with the link can spend your Google credit.")
        print("  !! Add a secret named MODDEX_PIN and restart.")
        print("  " + "!" * 56)
    else:
        print("  Staff PIN:  off - set MODDEX_PIN before exposing this online")
    _cap = _daily_cap()
    print("  Daily cap:  %s" % (_cap if _cap > 0 else "unlimited"))
    if _remote_mode():
        print("  Remote mode: ON - open-folder and shutdown are disabled")
    print("  Keep this window open. Close it to quit the tool.")
    print("=" * 60)

    if "--noopen" not in sys.argv and not _remote_mode():
        threading.Timer(0.8, lambda: open_app_window(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
