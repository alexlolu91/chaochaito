"""ChaoChaito Quiniela — servidor web.

En local:   python app.py   ->  http://127.0.0.1:5000
En Vercel:  se carga la variable `app` (ver vercel.json y DESPLIEGUE.md)
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime
from functools import wraps

from flask import Blueprint, Flask, jsonify, redirect, render_template, request, session

from quiniela import config, selae, selae_fetch, service, storage, ticket
from quiniela.http import FetchError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

bp = Blueprint("q", __name__)
_lock = threading.Lock()
_state = {"analisis": None, "ts": 0.0}


# --- Jornada y análisis -------------------------------------------------------------

def current_jornada():
    """Quiniela vigente: Loterías; si falla, la última introducida a mano."""
    try:
        j = selae.fetch_current().to_dict()
        if len(j["partidos"]) >= 14:
            rep = selae_fetch.last_report
            if rep and rep.get("ok") is None:
                return j, "Loterías bloqueó la consulta; muestro la última copia descargada."
            return j, None
        err = "Loterías devolvió menos de 14 partidos"
    except (FetchError, ValueError) as e:
        err = str(e)
    manual, _ = storage.get_kv("jornada_manual")
    if manual:
        return manual, f"Loterías no respondió ({err}); uso la jornada introducida a mano."
    return None, f"No se pudo leer la quiniela de Loterías ({err}). El administrador puede pegarla a mano."


def _age(iso):
    try:
        return time.time() - datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return float("inf")


def get_analysis(refresh=False):
    """Análisis vigente: memoria -> base de datos -> recalcular (lo más lento)."""
    with _lock:
        if not refresh and _state["analisis"] and time.time() - _state["ts"] < config.ANALYSIS_TTL:
            return _state["analisis"]
        if not refresh:
            saved, creado = storage.get_kv("analisis")
            if saved and _age(creado) < config.ANALYSIS_TTL:
                _state.update(analisis=saved, ts=time.time() - _age(creado))
                return saved
        j, aviso = current_jornada()
        if not j:
            saved, _ = storage.get_kv("analisis")
            return saved or {"error": aviso}
        a = service.analyze(j)
        if aviso:
            a["avisos"].insert(0, aviso)
        _store(a)
        return a


def _store(a):
    _state.update(analisis=a, ts=time.time())
    try:
        storage.set_kv("analisis", a)
    except Exception:  # noqa: BLE001 - la caché persistente es un extra
        log.exception("No se pudo guardar el análisis")


def current_key():
    """Clave de la jornada vigente sin lanzar el análisis completo."""
    a = _state.get("analisis") or storage.get_kv("analisis")[0]
    if a and "key" in a:
        return a["key"], a["jornada"]
    j, _ = current_jornada()
    if not j:
        return None, None
    return service.jornada_key(j), {k: v for k, v in j.items() if k != "partidos"}


# --- Administrador ---------------------------------------------------------------------

def is_admin():
    return not config.ADMIN_PIN or bool(session.get("admin"))


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not is_admin():
            return jsonify({"error": "Solo el administrador puede hacer esto.", "need_login": True}), 403
        return f(*a, **kw)
    return wrapper


@bp.get("/api/me")
def api_me():
    return jsonify({"admin": is_admin(), "pin": bool(config.ADMIN_PIN)})


@bp.post("/api/login")
def api_login():
    pin = (request.get_json(silent=True) or {}).get("pin", "")
    if config.ADMIN_PIN and pin == config.ADMIN_PIN:
        session["admin"] = True
        session.permanent = True
        return jsonify({"admin": True})
    return jsonify({"error": "PIN incorrecto"}), 403


@bp.post("/api/logout")
def api_logout():
    session.pop("admin", None)
    return jsonify({"admin": is_admin()})


# --- Páginas -------------------------------------------------------------------------

@bp.get("/")
def index():
    return render_template("index.html", page="inicio")


@bp.get("/participar")
def participar():
    return render_template("participar.html", page="participar")


@bp.get("/estado")
def estado_page():
    return render_template("estado.html", page="estado")


@bp.get("/premios")
def premios_page():
    return render_template("premios.html", page="premios")


@bp.get("/bote")
def bote_page():
    return render_template("bote.html", page="bote")


# --- Pronóstico -------------------------------------------------------------------------

@bp.get("/api/analisis")
def api_analisis():
    refresh = request.args.get("refresh") == "1" and is_admin()
    a = get_analysis(refresh=refresh)
    if "error" in a:
        return jsonify(a), 503
    a = dict(a)
    saved = storage.get_apuestas(a["key"])
    if a.get("apuestas") is not None:
        a["apuestas"] = dict(a["apuestas"], guardadas=(
            {"columnas": saved["datos"]["columnas"], "pleno": saved["datos"]["pleno"], "creado": saved["creado"]}
            if saved else None))
    return jsonify(a)


@bp.get("/api/diagnostico")
@admin_required
def api_diagnostico():
    """Prueba cada vía de lectura de Loterías y dice cuál funciona."""
    url = f"{config.SELAE_BASE}/proximosv3"
    out = {}
    for name, fn in selae_fetch.STRATEGIES:
        try:
            fn(url, {"game_id": "LAQU", "num": 1})
            out[name] = "OK"
        except ImportError:
            out[name] = "no instalado"
        except Exception as e:  # noqa: BLE001
            out[name] = str(e)[:150]
    out["base_de_datos"] = "postgres" if storage.pg_url() else "sqlite"
    out["clave_sesion"] = "QUINIELA_SECRET_KEY" if config.SECRET_KEY_FROM_ENV else "derivada del PIN"
    return jsonify(out)


@bp.post("/api/manual")
@admin_required
def api_manual():
    body = request.get_json(force=True)
    try:
        j = selae.parse_manual(body.get("texto", ""), body.get("fecha"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    storage.set_kv("jornada_manual", j.to_dict())
    with _lock:
        a = service.analyze(j.to_dict())
        _store(a)
    return jsonify(a)


@bp.post("/api/bajas")
@admin_required
def api_bajas():
    b = request.get_json(force=True)
    if b.get("lado") not in ("local", "visitante"):
        return jsonify({"error": "lado debe ser local o visitante"}), 400
    storage.set_baja(b["key"], b["partido"], b["lado"], b.get("num", 0))
    a = get_analysis()
    if "error" in a:
        return jsonify(a), 503
    with _lock:
        j = dict(a["jornada"], partidos=[{k: p[k] for k in ("num", "local", "visitante", "genero", "fecha")}
                                         for p in a["partidos"]])
        a = service.analyze(j)
        _store(a)
    return api_analisis()


@bp.post("/api/boleto")
def api_boleto():
    b = request.get_json(force=True)
    a = get_analysis()
    if "error" in a:
        return jsonify(a), 503
    probs = [p["probs"] for p in a["partidos"][:14]]
    res = ticket.optimize(probs, int(b.get("dobles", 0)), int(b.get("triples", 0)))
    res["coste"] = round(res["apuestas"] * config.PRICE_PER_BET, 2)
    return jsonify(res)


@bp.get("/api/historial")
def api_historial():
    rows = storage.list_predictions()
    return jsonify([{"key": r["key"], "creado": r["creado"], "jornada": r["datos"]["jornada"],
                     "evaluacion": r["evaluacion"]} for r in rows])


@bp.post("/api/evaluar/<key>")
def api_evaluar(key):
    try:
        return jsonify(service.evaluate(key))
    except KeyError:
        return jsonify({"error": "No hay pronóstico guardado para esa jornada"}), 404


# --- Apuestas ---------------------------------------------------------------------------

@bp.post("/api/apuestas/confirmar")
@admin_required
def api_confirmar():
    a = get_analysis()
    if "error" in a:
        return jsonify(a), 503
    sug = a.get("apuestas")
    if not sug:
        return jsonify({"error": "La jornada no tiene 14 partidos"}), 400
    columnas = [c["signos"] for c in sug["columnas"]]
    data = service.apuestas_payload(a, columnas, sug["pleno"])
    storage.save_apuestas(a["key"], data)
    storage.save_prediction(a["key"], {"jornada": a["jornada"], "partidos": a["partidos"], "columnas": columnas})
    return jsonify({"ok": True, "key": a["key"], "columnas": columnas, "pleno": sug["pleno"]})


@bp.get("/api/estado")
def api_estado():
    e = service.estado(request.args.get("key") or None)
    if not e:
        return jsonify({"error": "Todavía no hay apuestas confirmadas."}), 404
    return jsonify(e)


# --- Participantes ----------------------------------------------------------------------

def _participantes_payload(key, jornada):
    lista = storage.list_participantes(key) if key else []
    return {"key": key, "jornada": jornada, "participantes": lista, "total": len(lista),
            "maximo": config.MAX_PARTICIPANTES, "admin": is_admin()}


@bp.get("/api/participantes")
def api_participantes():
    key, jornada = current_key()
    if not key:
        return jsonify({"error": "No se pudo leer la jornada vigente"}), 503
    return jsonify(_participantes_payload(key, jornada))


@bp.post("/api/participar")
def api_participar():
    b = request.get_json(force=True)
    key, jornada = current_key()
    if not key:
        return jsonify({"error": "No se pudo leer la jornada vigente"}), 503
    try:
        storage.add_participante(key, b.get("nombre", ""), bool(b.get("bizum")), config.MAX_PARTICIPANTES)
    except storage.ParticipacionError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(_participantes_payload(key, jornada))


@bp.post("/api/participantes/<int:pid>/quitar")
@admin_required
def api_quitar(pid):
    key, jornada = current_key()
    storage.remove_participante(key, pid)
    return jsonify(_participantes_payload(key, jornada))


# --- Premios y bote común ----------------------------------------------------------------

_KEY_RE = re.compile(r"^(\d{4}-\d{4})-J(\d+)$")


def _split_key(key):
    m = _KEY_RE.match(key or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def _euros(cents):
    return round(cents / 100, 2)


@bp.get("/api/premios")
def api_premios():
    premios = storage.list_premios()
    partic = storage.participantes_por_jornada()
    keys = set(premios) | set(partic) | set(storage.jornadas_con_apuestas())
    _, jornada_actual = current_key()
    filas = []
    for k in keys:
        temporada, num = _split_key(k)
        pr = premios.get(k) or {}
        nombres = partic.get(k) or [n.strip() for n in (pr.get("nombres") or "").split(",") if n.strip()]
        cents = pr.get("importe_cents")
        filas.append({
            "key": k, "temporada": temporada, "jornada": num,
            "participantes": nombres, "n": len(nombres),
            "nombres_manual": not partic.get(k) and bool(nombres),
            "premio": _euros(cents) if cents is not None else None,
            "por_persona": _euros(cents / len(nombres)) if cents and nombres else None,
            "nota": pr.get("nota"),
        })
    filas.sort(key=lambda f: (f["temporada"] or "", f["jornada"] or 0, f["key"]), reverse=True)
    total = sum(f["premio"] or 0 for f in filas)
    return jsonify({"filas": filas, "total": round(total, 2),
                    "con_premio": sum(1 for f in filas if f["premio"]),
                    "temporada_actual": (jornada_actual or {}).get("temporada"),
                    "jornada_actual": (jornada_actual or {}).get("jornada"), "admin": is_admin()})


@bp.post("/api/premios")
@admin_required
def api_premios_guardar():
    b = request.get_json(force=True)
    temporada = str(b.get("temporada") or "").strip()
    if not re.match(r"^\d{4}-\d{4}$", temporada):
        return jsonify({"error": "Temporada con formato 2026-2027"}), 400
    try:
        num = int(b.get("jornada"))
        cents = storage.parse_euros(b.get("importe"))
    except (TypeError, ValueError) as e:
        return jsonify({"error": str(e) if isinstance(e, storage.ImporteError) else "Jornada no válida"}), 400
    if cents < 0:
        return jsonify({"error": "El premio no puede ser negativo"}), 400
    nombres = ", ".join(n.strip() for n in str(b.get("nombres") or "").split(",") if n.strip())
    storage.set_premio(f"{temporada}-J{num}", cents, (b.get("nota") or "").strip()[:120], nombres[:500])
    return api_premios()


@bp.post("/api/premios/<key>/borrar")
@admin_required
def api_premios_borrar(key):
    storage.delete_premio(key)
    return api_premios()


@bp.get("/api/bote")
def api_bote():
    movs = storage.list_bote()
    for m in movs:
        m["importe"] = _euros(m.pop("importe_cents"))
    return jsonify({"movimientos": movs[::-1], "total": round(sum(m["importe"] for m in movs), 2),
                    "admin": is_admin()})


@bp.post("/api/bote")
@admin_required
def api_bote_add():
    b = request.get_json(force=True)
    try:
        storage.add_bote(b.get("jornada"), storage.parse_euros(b.get("importe")), (b.get("nota") or "").strip())
    except storage.ImporteError as e:
        return jsonify({"error": str(e)}), 400
    return api_bote()


@bp.post("/api/bote/<int:mov_id>/borrar")
@admin_required
def api_bote_borrar(mov_id):
    storage.delete_bote(mov_id)
    return api_bote()


# --- Aplicación ---------------------------------------------------------------------------

def create_app(base_path=None):
    base = config.BASE_PATH if base_path is None else base_path.rstrip("/")
    application = Flask(__name__, static_url_path=f"{base}/static")
    application.secret_key = config.SECRET_KEY
    application.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                              SESSION_COOKIE_SECURE=config.IS_VERCEL, PERMANENT_SESSION_LIFETIME=30 * 24 * 3600)
    application.register_blueprint(bp, url_prefix=base or None)

    @application.context_processor
    def inject_base():
        return {"base": base}

    if base:
        @application.get("/")
        def root_redirect():
            return redirect(f"{base}/")

        @application.get(base)
        def base_redirect():
            return redirect(f"{base}/")

    return application


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n  ChaoChaito Quiniela -> http://127.0.0.1:{port}{config.BASE_PATH}/\n")
    app.run(host="127.0.0.1", port=port, debug=False)
