"""Lectura de la quiniela vigente desde Loterías y Apuestas del Estado (SELAE)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime

from . import config
from . import selae_fetch
from .http import FetchError

HEADERS = {"Referer": "https://www.loteriasyapuestas.es/es/resultados/quiniela"}


def get_json(url, params=None, ttl=0, headers=None, **_):
    """Punto único de descarga (los tests lo sustituyen por un simulador)."""
    return selae_fetch.get(url, params, ttl)


@dataclass
class QMatch:
    num: int                 # 1..15
    local: str               # nombre tal cual en el boleto, sin sufijo
    visitante: str
    genero: str              # "m" | "f"
    fecha: str | None        # ISO "2026-10-17T14:00"

    def to_dict(self):
        return asdict(self)


@dataclass
class Jornada:
    jornada: int | None
    temporada: str | None
    fecha_sorteo: str        # "2026-10-18"
    estado: str              # abierto / cerrado / desconocido
    cierre: str | None
    bote: str | None
    partidos: list = field(default_factory=list)
    fuente: str = "selae"

    def to_dict(self):
        d = asdict(self)
        d["partidos"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.partidos]
        return d


_SUFFIX = re.compile(r"\s*\((m|f)\)\s*$", re.I)


def split_team(raw):
    """'Espanyol (m)' -> ('Espanyol', 'm'). Sin sufijo se asume masculino."""
    raw = (raw or "").strip()
    m = _SUFFIX.search(raw)
    if m:
        return raw[: m.start()].strip(), m.group(1).lower()
    return raw, "m"


def _parse_date(s):
    if not s:
        return None
    s = s.replace("\\", "").strip()
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return None


def parse_fechav3(data):
    """Convierte la respuesta de /servicios/fechav3 en una Jornada."""
    if isinstance(data, list):
        if not data:
            raise ValueError("Respuesta vacía de SELAE")
        data = data[0]
    partidos = []
    for i, p in enumerate(data.get("partidos") or [], start=1):
        local, g1 = split_team(p.get("local"))
        visit, g2 = split_team(p.get("visitante"))
        partidos.append(QMatch(i, local, visit, g1 if g1 == g2 else g1, _parse_date(p.get("fecha"))))
    fecha = (data.get("fecha_sorteo") or "")[:10]
    jornada = data.get("jornada")
    return Jornada(
        jornada=int(jornada) if str(jornada or "").isdigit() else None,
        temporada=data.get("temporada"),
        fecha_sorteo=fecha,
        estado="desconocido",
        cierre=None,
        bote=data.get("premio_bote"),
        partidos=partidos,
    )


def pick_current(proximos):
    """De /proximosv3 elige el sorteo vigente: el primero abierto, o si no, el más próximo."""
    if not proximos:
        return None
    abiertos = [s for s in proximos if (s.get("estado") or "").lower() == "abierto"]
    return (abiertos or proximos)[0]


def fetch_current():
    """Devuelve la Jornada vigente con sus 15 partidos."""
    proximos = get_json(
        f"{config.SELAE_BASE}/proximosv3",
        {"game_id": "LAQU", "num": 2},
        ttl=config.TTL_QUINIELA,
        headers=HEADERS,
    )
    actual = pick_current(proximos)
    if not actual:
        raise FetchError("SELAE no devolvió sorteos próximos")
    fecha = (actual.get("fecha") or "")[:10].replace("-", "")
    detalle = get_json(
        f"{config.SELAE_BASE}/fechav3",
        {"game_id": "LAQU", "fecha_sorteo": fecha},
        ttl=config.TTL_QUINIELA,
        headers=HEADERS,
    )
    j = parse_fechav3(detalle)
    j.estado = actual.get("estado") or "desconocido"
    j.cierre = actual.get("cierre")
    j.bote = actual.get("premio_bote") or j.bote
    if j.jornada is None and actual.get("jornada"):
        j.jornada = int(actual["jornada"])
    return j


def parse_manual(texto, fecha_sorteo=None):
    """Plan B si SELAE bloquea la petición: una línea por partido, 'Local - Visitante'.
    Admite sufijos (m)/(f). Acepta también el JSON de fechav3 pegado tal cual."""
    texto = (texto or "").strip()
    if texto.startswith("[") or texto.startswith("{"):
        import json
        j = parse_fechav3(json.loads(texto))
        j.fuente = "manual"
        return j
    partidos = []
    for line in texto.splitlines():
        line = re.sub(r"^\s*\d+[\.\)\-:]?\s+", "", line).strip()
        if not line:
            continue
        parts = re.split(r"\s+-\s+|\s+vs\.?\s+|\s*–\s*", line, maxsplit=1)
        if len(parts) != 2:
            continue
        local, g1 = split_team(parts[0])
        visit, _ = split_team(parts[1])
        partidos.append(QMatch(len(partidos) + 1, local, visit, g1, None))
    if not partidos:
        raise ValueError("No se reconoció ningún partido. Formato: 'Local - Visitante' por línea.")
    return Jornada(None, None, fecha_sorteo or datetime.now().strftime("%Y-%m-%d"),
                   "manual", None, None, partidos[:15], fuente="manual")
