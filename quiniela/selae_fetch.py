"""Descarga robusta de los servicios JSON de Loterías (protegidos por Akamai).

Prueba varias estrategias, en orden, y recuerda la que funcionó:
  1. curl_cffi imitando la huella TLS de Chrome (la que más suele funcionar)
  2. requests con cabeceras de navegador y cookies de la web
  3. Navegador real con Playwright (opcional: pip install playwright && playwright install chromium)
"""
from __future__ import annotations

import json
import logging
import time

from . import config
from .http import FetchError, _cache_path

log = logging.getLogger(__name__)

HOME = "https://www.loteriasyapuestas.es/es/resultados/quiniela"
API_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": HOME,
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}
PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

_sessions = {}
_preferred = None
last_report = {}


def _parse(text):
    data = json.loads(text)
    if not isinstance(data, (list, dict)):
        raise ValueError("respuesta no JSON")
    return data


def _curl_cffi(url, params):
    from curl_cffi import requests as creq  # import perezoso: dependencia opcional
    s = _sessions.get("curl")
    if s is None:
        s = creq.Session(impersonate="chrome")
        try:
            s.get(HOME, headers=PAGE_HEADERS, timeout=config.HTTP_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            log.debug("warm-up curl_cffi: %s", e)
        _sessions["curl"] = s
    r = s.get(url, params=params, headers=API_HEADERS, timeout=config.HTTP_TIMEOUT)
    if r.status_code != 200:
        _sessions.pop("curl", None)
        raise FetchError(f"HTTP {r.status_code}")
    return _parse(r.text)


def _requests(url, params):
    import requests
    s = _sessions.get("requests")
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = config.USER_AGENT
        try:
            s.get(HOME, headers=PAGE_HEADERS, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as e:
            log.debug("warm-up requests: %s", e)
        _sessions["requests"] = s
    r = s.get(url, params=params, headers=API_HEADERS, timeout=config.HTTP_TIMEOUT)
    if r.status_code != 200:
        _sessions.pop("requests", None)
        raise FetchError(f"HTTP {r.status_code}")
    return _parse(r.text)


def _playwright(url, params):
    from urllib.parse import urlencode
    from playwright.sync_api import sync_playwright
    full = url + ("?" + urlencode(params) if params else "")
    with sync_playwright() as p:
        browser = None
        for kw in ({"channel": "chrome"}, {}):  # Chrome instalado; si no, Chromium de Playwright
            try:
                browser = p.chromium.launch(headless=True, **kw)
                break
            except Exception:  # noqa: BLE001
                continue
        if browser is None:
            raise FetchError("Playwright sin navegador instalado")
        try:
            page = browser.new_page(locale="es-ES", user_agent=config.USER_AGENT)
            page.goto(HOME, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            res = page.evaluate(
                """async (u) => { const r = await fetch(u, {headers: {'Accept': 'application/json'}});
                                  return [r.status, await r.text()]; }""", full)
        finally:
            browser.close()
    if res[0] != 200:
        raise FetchError(f"HTTP {res[0]}")
    return _parse(res[1])


STRATEGIES = [("curl_cffi", _curl_cffi), ("requests", _requests), ("navegador", _playwright)]


def get(url, params=None, ttl=0):
    """JSON de Loterías con caché en disco y varias estrategias de descarga."""
    global _preferred
    path = _cache_path(url, params)
    if path.exists() and ttl > 0 and time.time() - path.stat().st_mtime < ttl:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    order = sorted(STRATEGIES, key=lambda s: s[0] != _preferred)
    errors = {}
    for name, fn in order:
        try:
            data = fn(url, params)
        except ImportError:
            errors[name] = "no instalado"
            continue
        except Exception as e:  # noqa: BLE001
            errors[name] = str(e)[:120]
            log.info("Loterías vía %s falló: %s", name, e)
            continue
        _preferred = name
        last_report.clear()
        last_report.update({"ok": name, "errores": errors})
        log.info("Loterías leída vía %s", name)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data
    last_report.clear()
    last_report.update({"ok": None, "errores": errors})
    if path.exists():
        log.warning("Loterías bloqueada; uso la última copia guardada")
        return json.loads(path.read_text(encoding="utf-8"))
    detalle = "; ".join(f"{k}: {v}" for k, v in errors.items())
    raise FetchError(f"Loterías bloqueó todas las vías ({detalle})")
