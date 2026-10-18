"""Persistencia: SQLite en local, Postgres en la web (si existe DATABASE_URL / POSTGRES_URL)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS pronosticos (
    jornada_key TEXT PRIMARY KEY,
    creado      TEXT NOT NULL,
    datos       TEXT NOT NULL,
    evaluacion  TEXT
);
CREATE TABLE IF NOT EXISTS apuestas (
    jornada_key TEXT PRIMARY KEY,
    creado      TEXT NOT NULL,
    datos       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS participantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    jornada_key TEXT NOT NULL,
    nombre      TEXT NOT NULL,
    nombre_norm TEXT NOT NULL,
    bizum       INTEGER NOT NULL,
    creado      TEXT NOT NULL,
    UNIQUE (jornada_key, nombre_norm)
);
CREATE TABLE IF NOT EXISTS bajas (
    jornada_key TEXT NOT NULL,
    partido     INTEGER NOT NULL,
    lado        TEXT NOT NULL CHECK (lado IN ('local','visitante')),
    num         INTEGER NOT NULL,
    PRIMARY KEY (jornada_key, partido, lado)
);
CREATE TABLE IF NOT EXISTS kv (
    k       TEXT PRIMARY KEY,
    v       TEXT NOT NULL,
    creado  TEXT NOT NULL
);
"""
SCHEMA_PG = SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")

_schema_ready = set()
_schema_lock = threading.Lock()


def pg_url():
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""


class _PG:
    """Envoltorio mínimo para usar los mismos SQL con '?' en Postgres."""

    def __init__(self, c):
        self.c = c

    def execute(self, sql, params=()):
        cur = self.c.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur


@contextmanager
def conn():
    url = pg_url()
    if url:
        import psycopg  # solo hace falta en la web
        c = psycopg.connect(url)
        try:
            with _schema_lock:
                if "pg" not in _schema_ready:
                    for stmt in filter(str.strip, SCHEMA_PG.split(";")):
                        c.execute(stmt)
                    c.commit()
                    _schema_ready.add("pg")
            yield _PG(c)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
    else:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH)
        try:
            key = str(config.DB_PATH)
            if key not in _schema_ready:
                c.executescript(SCHEMA)
                _schema_ready.add(key)
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def _is_unique_violation(e):
    return isinstance(e, sqlite3.IntegrityError) or type(e).__name__ in ("UniqueViolation", "IntegrityError")


# --- Almacén clave/valor (análisis calculado, jornada introducida a mano) -----------

def set_kv(k, value):
    with conn() as c:
        c.execute("INSERT INTO kv (k, v, creado) VALUES (?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, "
                  "creado=excluded.creado", (k, json.dumps(value), datetime.now().isoformat(timespec="seconds")))


def get_kv(k):
    """Devuelve (valor, fecha_iso) o (None, None)."""
    with conn() as c:
        row = c.execute("SELECT v, creado FROM kv WHERE k=?", (k,)).fetchone()
    return (json.loads(row[0]), row[1]) if row else (None, None)


def del_kv(k):
    with conn() as c:
        c.execute("DELETE FROM kv WHERE k=?", (k,))


def get_bajas(key):
    with conn() as c:
        rows = c.execute("SELECT partido, lado, num FROM bajas WHERE jornada_key=?", (key,)).fetchall()
    out = {}
    for partido, lado, num in rows:
        out.setdefault(partido, {"local": 0, "visitante": 0})[lado] = num
    return out


def set_baja(key, partido, lado, num):
    num = max(0, min(int(num), 5))
    with conn() as c:
        c.execute("INSERT INTO bajas VALUES (?,?,?,?) ON CONFLICT(jornada_key, partido, lado) "
                  "DO UPDATE SET num=excluded.num", (key, int(partido), lado, num))


def save_prediction(key, data):
    with conn() as c:
        c.execute("INSERT INTO pronosticos (jornada_key, creado, datos) VALUES (?,?,?) "
                  "ON CONFLICT(jornada_key) DO UPDATE SET creado=excluded.creado, datos=excluded.datos, "
                  "evaluacion=NULL",
                  (key, datetime.now().isoformat(timespec="seconds"), json.dumps(data)))


def list_predictions():
    with conn() as c:
        rows = c.execute("SELECT jornada_key, creado, datos, evaluacion FROM pronosticos "
                         "ORDER BY creado DESC").fetchall()
    return [{"key": k, "creado": cr, "datos": json.loads(d), "evaluacion": json.loads(e) if e else None}
            for k, cr, d, e in rows]


def get_prediction(key):
    with conn() as c:
        row = c.execute("SELECT creado, datos, evaluacion FROM pronosticos WHERE jornada_key=?",
                        (key,)).fetchone()
    if not row:
        return None
    return {"key": key, "creado": row[0], "datos": json.loads(row[1]),
            "evaluacion": json.loads(row[2]) if row[2] else None}


def save_evaluation(key, ev):
    with conn() as c:
        c.execute("UPDATE pronosticos SET evaluacion=? WHERE jornada_key=?", (json.dumps(ev), key))


# --- Apuestas confirmadas -----------------------------------------------------------

def save_apuestas(key, data):
    with conn() as c:
        c.execute("INSERT INTO apuestas (jornada_key, creado, datos) VALUES (?,?,?) "
                  "ON CONFLICT(jornada_key) DO UPDATE SET creado=excluded.creado, datos=excluded.datos",
                  (key, datetime.now().isoformat(timespec="seconds"), json.dumps(data)))


def get_apuestas(key=None):
    """Apuestas de una jornada; sin `key`, las últimas confirmadas."""
    with conn() as c:
        if key:
            row = c.execute("SELECT jornada_key, creado, datos FROM apuestas WHERE jornada_key=?", (key,)).fetchone()
        else:
            row = c.execute("SELECT jornada_key, creado, datos FROM apuestas ORDER BY creado DESC LIMIT 1").fetchone()
    if not row:
        return None
    return {"key": row[0], "creado": row[1], "datos": json.loads(row[2])}


# --- Participantes -------------------------------------------------------------------

def _norm_name(nombre):
    import unicodedata
    s = unicodedata.normalize("NFD", nombre.strip().lower())
    return " ".join("".join(ch for ch in s if unicodedata.category(ch) != "Mn").split())


class ParticipacionError(ValueError):
    pass


def add_participante(key, nombre, bizum, maximo):
    nombre = " ".join((nombre or "").split())
    if not 2 <= len(nombre) <= 40:
        raise ParticipacionError("Escribe tu nombre (entre 2 y 40 letras).")
    if not bizum:
        raise ParticipacionError("Tienes que marcar que has hecho el Bizum.")
    with conn() as c:
        n = c.execute("SELECT COUNT(*) FROM participantes WHERE jornada_key=?", (key,)).fetchone()[0]
        if n >= maximo:
            raise ParticipacionError(f"Ya estamos completos ({maximo}/{maximo}).")
        try:
            c.execute("INSERT INTO participantes (jornada_key, nombre, nombre_norm, bizum, creado) VALUES (?,?,?,?,?)",
                      (key, nombre, _norm_name(nombre), 1, datetime.now().isoformat(timespec="seconds")))
        except Exception as e:  # noqa: BLE001
            if _is_unique_violation(e):
                raise ParticipacionError(f"{nombre} ya está apuntado en esta jornada.") from None
            raise
    return list_participantes(key)


def list_participantes(key):
    with conn() as c:
        rows = c.execute("SELECT id, nombre, creado FROM participantes WHERE jornada_key=? ORDER BY id",
                         (key,)).fetchall()
    return [{"id": i, "nombre": n, "creado": cr} for i, n, cr in rows]


def remove_participante(key, pid):
    with conn() as c:
        c.execute("DELETE FROM participantes WHERE jornada_key=? AND id=?", (key, int(pid)))



def update_apuestas_datos(key, data):
    """Actualiza los datos sin cambiar la fecha de confirmación."""
    with conn() as c:
        c.execute("UPDATE apuestas SET datos=? WHERE jornada_key=?", (json.dumps(data), key))
