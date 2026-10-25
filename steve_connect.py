"""STEVE connector protocol: Ed25519 keys, RFC 9421 request signing, and the /connect/v1 endpoints.

Only the Python standard library and `cryptography` are used.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

BASE_URL = "https://stevecad.studio/connect/v1"


def b64(data):
    return base64.b64encode(data).decode("ascii")


def load_or_create_key(path):
    """The connector's Ed25519 private key, kept in `path` with mode 0600 (created on first start)."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def public_key_raw(key):
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def fingerprint(key):
    """`SHA256:` plus unpadded base64 of the SHA-256 of the raw 32-byte public key."""
    return "SHA256:" + b64(hashlib.sha256(public_key_raw(key)).digest()).rstrip("=")


def canonical_json(value):
    """JSON with keys sorted, no spaces, UTF-8: the form STEVE signs run envelopes in."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class SteveError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class SteveClient:
    def __init__(self, key, base_url=BASE_URL, log=print):
        self.key = key
        self.base_url = base_url.rstrip("/")
        self.fingerprint = fingerprint(key)
        self.token = None
        self.token_expires = 0
        self.log = log

    # Signing ---------------------------------------------------------------------------------------------------------

    def _signed_headers(self, method, path, query, body):
        digest = "sha-256=:" + b64(hashlib.sha256(body).digest()) + ":"
        params = ('("@method" "@path" "@query" "content-digest");created=%d;nonce="%s";keyid="%s";alg="ed25519";'
                  'tag="steve-connect-v1"' % (int(time.time()), secrets.token_urlsafe(24), self.fingerprint))
        base = "\n".join([
            '"@method": ' + method.upper(),
            '"@path": ' + path,
            '"@query": ' + (query or "?"),
            '"content-digest": ' + digest,
            '"@signature-params": ' + params,
        ])
        signature = b64(self.key.sign(base.encode("utf-8")))
        headers = {
            "Content-Digest": digest,
            "Signature-Input": "steve=" + params,
            "Signature": "steve=:" + signature + ":",
            "User-Agent": "RenderBuddy-connector/0.1",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return headers

    def request(self, method, path_or_url, body=None, content_type="application/json", params=None, timeout=60,
                raw=False):
        """A signed request. `path_or_url` is relative to the base URL ("/messages") or a full URL."""
        if path_or_url.startswith("http"):
            url = path_or_url
        elif path_or_url.startswith("/connect/"):
            url = urllib.parse.urljoin(self.base_url, path_or_url)
        else:
            url = self.base_url + path_or_url
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        parts = urllib.parse.urlsplit(url)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        body = body or b""
        headers = self._signed_headers(method, parts.path, "?" + parts.query if parts.query else "?", body)
        if body or method in ("POST", "PUT"):
            headers["Content-Type"] = content_type
        req = urllib.request.Request(url, data=body if method in ("POST", "PUT") else None, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                d = json.loads(detail).get("detail", {})
                code, message = (d.get("code"), d.get("message")) if isinstance(d, dict) else (None, str(d))
            except ValueError:
                code, message = None, detail[:500]
            raise SteveError(e.code, code, message) from None
        if raw:
            return data
        return json.loads(data) if data else None

    # Endpoints -------------------------------------------------------------------------------------------------------

    def keys(self):
        with urllib.request.urlopen(self.base_url + "/keys", timeout=30) as resp:
            return json.loads(resp.read())

    def enroll(self, code, name, version, catalog):
        return self.request("POST", "/enroll", {
            "code": code, "public_key": b64(public_key_raw(self.key)), "name": name, "version": version,
            "catalog": catalog})

    def refresh_token(self, force=False):
        if not force and self.token and time.time() < self.token_expires - 120:
            return
        self.token = None
        answer = self.request("POST", "/token")
        self.token = answer["token"]
        self.token_expires = time.time() + answer.get("expires_in", 900)

    def put_catalog(self, catalog):
        return self.request("PUT", "/catalog", catalog)

    def messages(self, after, wait=30):
        params = {"wait": wait}
        if after is not None:
            params["after"] = after
        return self.request("GET", "/messages", params=params, timeout=wait + 30)

    def report(self, run_id, state, **fields):
        return self.request("POST", f"/runs/{run_id}", {"state": state, **fields})

    def upload(self, run_id, name, path, content_type):
        with open(path, "rb") as f:
            data = f.read()
        return self.request("PUT", f"/runs/{run_id}/files/{name}", data, content_type=content_type, timeout=600)

    def event(self, name, data):
        return self.request("POST", "/events", {"name": name, "data": data})


def trusted_keys(signed_list):
    """kid -> Ed25519 public key, from STEVE's signed key list ({"list": {"keys": [...]}, "signatures": [...]})."""
    out = {}
    for entry in signed_list.get("list", {}).get("keys", []):
        raw = entry["public_key"]
        raw = base64.b64decode(raw + "=" * (-len(raw) % 4)) if not isinstance(raw, bytes) else raw
        out[entry["kid"]] = Ed25519PublicKey.from_public_bytes(raw)
    return out


def verify_message(message, keys):
    """True when the message's envelope is signed by one of STEVE's trusted keys."""
    key = keys.get(message.get("kid"))
    if key is None:
        return False
    sig = message.get("sig", "")
    try:
        key.verify(base64.b64decode(sig + "=" * (-len(sig) % 4)), canonical_json(message["envelope"]))
        return True
    except (InvalidSignature, ValueError):
        return False
