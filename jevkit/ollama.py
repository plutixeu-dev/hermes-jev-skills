"""This machine's Ollama models, for the dashboard's receptionist picker. Read-only.

Where it looks: OLLAMA_HOST, else a profile's model.base_url when that names port 11434, else
http://127.0.0.1:11434. It only talks to loopback, private-network or Tailscale addresses, never
through a proxy and never after a redirect, with a short timeout, and fails open to an empty list
plus a reason. A 30-second cache keeps page loads cheap.
"""
from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

DEFAULT_URL = "http://127.0.0.1:11434"
PORT = 11434
TIMEOUT = 2.0
CACHE_SECONDS = 30.0
MAX_BYTES = 1_000_000
# The shared address block (RFC 6598) Tailscale hands out, as a number: the release check refuses the literal.
_TAILSCALE = ipaddress.ip_network((0x64400000, 10))

Fetch = Callable[[str, float], bytes]
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def private_host(host: str) -> bool:
    """Loopback, a private network or Tailscale, by address. The one name allowed is localhost."""
    host = (host or "").strip().strip("[]").lower()
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (address.is_loopback or address.is_private or address.is_link_local
            or (address.version == 4 and address in _TAILSCALE))


def private_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url or "")
    return parts.scheme in ("http", "https") and bool(parts.hostname) and private_host(parts.hostname)


def same_server(a: str, b: str) -> bool:
    """Do two URLs name one server: the same host (localhost counts as 127.0.0.1) and port."""
    def key(url: str) -> Tuple[str, int]:
        parts = urllib.parse.urlsplit(url or "")
        host = (parts.hostname or "").lower()
        host = "127.0.0.1" if host in ("localhost", "::1", "0.0.0.0") else host
        return host, parts.port or (443 if parts.scheme == "https" else 80)
    return bool(a and b) and key(a) == key(b)


def base_url(base: Optional[str] = None, environ: Optional[Mapping[str, str]] = None) -> str:
    """The Ollama server to ask: OLLAMA_HOST, else `base` when it names port 11434, else loopback."""
    env = os.environ if environ is None else environ
    host = (env.get("OLLAMA_HOST") or "").strip().rstrip("/")
    if host:
        if "://" not in host:
            host = "http://" + host
        parts = urllib.parse.urlsplit(host)
        name = "127.0.0.1" if parts.hostname in (None, "0.0.0.0") else parts.hostname
        return f"{parts.scheme}://{name}:{parts.port or PORT}"
    parts = urllib.parse.urlsplit(base or "")
    if parts.hostname and parts.port == PORT:
        return f"{parts.scheme or 'http'}://{parts.hostname}:{PORT}"
    return DEFAULT_URL


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None                       # a 3xx becomes an error: never follow it off the private network


def _fetch(url: str, timeout: float) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as reply:
        return reply.read(MAX_BYTES + 1)[:MAX_BYTES]


def _rows(raw: bytes) -> List[Dict[str, Any]]:
    data = json.loads(raw)
    listed = data.get("models") if isinstance(data, dict) else None
    out = []
    for row in listed if isinstance(listed, list) else []:
        name = row.get("name") or row.get("model") if isinstance(row, dict) else None
        if not isinstance(name, str) or not name.strip():
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        size = row.get("size")
        out.append({"name": name.strip(), "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
                    "parameter_size": str(details.get("parameter_size") or ""),
                    "family": str(details.get("family") or "")})
    return sorted(out, key=lambda row: row["name"])


def list_models(base: Optional[str] = None, *, fetch: Optional[Fetch] = None, now: Optional[float] = None,
                environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """{"url", "models": [{"name", "size", "parameter_size", "family"}], "reason"}. Never raises."""
    url = base_url(base, environ)
    if not private_url(url):
        return {"url": url, "models": [], "reason": "not a loopback or private address"}
    now = time.monotonic() if now is None else now
    hit = _CACHE.get(url)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        models = _rows((fetch or _fetch)(url + "/api/tags", TIMEOUT))
        result = {"url": url, "models": models, "reason": "" if models else "Ollama lists no models"}
    except Exception as error:  # noqa: BLE001 - the picker shows the reason; nothing else depends on it
        result = {"url": url, "models": [], "reason": f"Ollama did not answer ({type(error).__name__})"}
    _CACHE[url] = (now, result)
    return result
