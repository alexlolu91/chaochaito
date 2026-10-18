"""GET con caché en disco (JSON) y reintento suave."""
from __future__ import annotations

import hashlib
import json
import logging
import time

import requests

from . import config

log = logging.getLogger(__name__)
_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json, text/plain, */*"})


class FetchError(RuntimeError):
    pass


def _cache_path(url, params):
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    h = hashlib.sha1(key.encode()).hexdigest()
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"{h}.json"


def get_json(url, params=None, ttl=3600, headers=None, use_stale_on_error=True):
    """Devuelve el JSON de `url`, usando la caché si tiene menos de `ttl` segundos."""
    path = _cache_path(url, params)
    if path.exists() and ttl > 0 and time.time() - path.stat().st_mtime < ttl:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    last_err = None
    for attempt in range(2):
        try:
            r = _session.get(url, params=params, headers=headers, timeout=config.HTTP_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                path.write_text(json.dumps(data), encoding="utf-8")
                return data
            last_err = FetchError(f"HTTP {r.status_code} en {url}")
            if r.status_code in (400, 403, 404):
                break
        except (requests.RequestException, ValueError) as e:
            last_err = e
        time.sleep(0.6 * (attempt + 1))
    if use_stale_on_error and path.exists():
        log.warning("Usando caché caducada para %s (%s)", url, last_err)
        return json.loads(path.read_text(encoding="utf-8"))
    raise FetchError(str(last_err))


def get_text(url, ttl=3600):
    """GET de una página HTML con caché en disco. Si requests es bloqueado, prueba curl_cffi."""
    path = _cache_path(url, {"_fmt": "html"})
    if path.exists() and ttl > 0 and time.time() - path.stat().st_mtime < ttl:
        return path.read_text(encoding="utf-8")
    headers = {"Accept": "text/html,application/xhtml+xml", "Accept-Language": "es-ES,es;q=0.9"}
    text, err = None, None
    try:
        r = _session.get(url, headers=headers, timeout=config.HTTP_TIMEOUT)
        if r.status_code == 200:
            text = r.text
        else:
            err = FetchError(f"HTTP {r.status_code} en {url}")
    except requests.RequestException as e:
        err = e
    if text is None and not (err and "HTTP 404" in str(err)):
        try:
            from curl_cffi import requests as creq
            r = creq.get(url, headers=headers, impersonate="chrome", timeout=config.HTTP_TIMEOUT)
            if r.status_code == 200:
                text = r.text
        except Exception as e:  # noqa: BLE001 - curl_cffi es opcional
            err = err or e
    if text is not None:
        path.write_text(text, encoding="utf-8")
        return text
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FetchError(str(err))
