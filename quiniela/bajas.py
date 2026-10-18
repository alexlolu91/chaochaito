"""Bajas (lesionados, dudas) y sancionados desde futbolfantasy.com."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from . import config, teams as teammatch
from .http import FetchError, get_text

log = logging.getLogger(__name__)

# Páginas de futbolfantasy por competición
PAGES = ["laliga", "laliga2", "champions", "europa-league", "premier-league", "serie-a"]
PAGE_FOR_COMP = {"esp.1": "laliga", "esp.2": "laliga2", "uefa.champions": "champions",
                 "uefa.europa": "europa-league", "eng.1": "premier-league", "ita.1": "serie-a"}
STATUS_ICONS = {"lesionado_box": "baja", "duda_box": "duda", "disponible_box": "disponible",
                "sancionado": "sancionado"}
WEIGHT = {"baja": 1.0, "sancionado": 1.0, "duda": 0.5, "disponible": 0.0}


class _FFParser(HTMLParser):
    """Recorre las secciones por equipo y las fichas de jugador (div.elemento)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.teams = {}            # nombre equipo -> [jugadores]
        self.team = None
        self.player = None
        self._in_header = False
        self._capture = []         # pila de (tag, campo)

    def _push_player(self):
        if self.player and self.player.get("jugador") and self.team:
            self.teams.setdefault(self.team, []).append(self.player)
        self.player = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "section" and ("lesionados" in cls or "sancionados" in cls):
            self._push_player()
            self.team = None
        elif tag == "header" and "v3-widget-header" in cls:
            self._in_header = True
        elif tag == "a" and self._in_header:
            self._capture.append(("a", "_team"))
        elif tag == "div" and "elemento" in cls.split():
            self._push_player()
            self.player = {"estado": "sancionado" if "sancionado" in cls else "baja", "motivo": "",
                           "desde": "", "detalle": "", "prob": None}
        elif self.player is not None:
            if tag == "img":
                src = a.get("src") or ""
                for key, st in STATUS_ICONS.items():
                    if key in src:
                        self.player["estado"] = st
            elif tag == "a" and "jugador" in cls.split():
                self._capture.append(("a", "jugador"))
            elif tag == "span":
                field = None
                if cls.strip() == "lesion":
                    field = "motivo"
                elif "rounded-card__date" in cls:
                    field = "desde"
                elif "rounded-card__status" in cls:
                    field = "detalle"
                elif "sancion" in cls.split():
                    field = "motivo"
                elif cls.startswith("prob-"):
                    field = "prob"
                self._capture.append(("span", field))

    def handle_endtag(self, tag):
        if tag == "header":
            self._in_header = False
        if self._capture and self._capture[-1][0] == tag:
            self._capture.pop()
        if tag == "section":
            self._push_player()

    def handle_data(self, data):
        if not self._capture:
            return
        field = self._capture[-1][1]
        text = " ".join(data.split())
        if not field or not text:
            return
        if field == "_team":
            self.team = text
        elif self.player is not None:
            if field == "prob":
                try:
                    self.player["prob"] = int(text.rstrip("%"))
                except ValueError:
                    pass
            else:
                prev = self.player.get(field) or ""
                self.player[field] = (prev + " " + text).strip()

    def close(self):
        super().close()
        self._push_player()


def parse_page(html):
    p = _FFParser()
    p.feed(html)
    p.close()
    for players in p.teams.values():
        for pl in players:
            pl["desde"] = pl["desde"].replace("Desde", "desde").strip()
    return p.teams


def load_all():
    """{(página, tipo): {equipo: [jugadores]}} para todas las páginas. Lo que falle, se omite."""
    jobs = [(pg, kind) for pg in PAGES for kind in ("lesionados", "sancionados")]

    def job(pk):
        pg, kind = pk
        try:
            return parse_page(get_text(f"{config.FF_BASE}/{pg}/{kind}", ttl=config.TTL_BAJAS))
        except FetchError as e:
            log.info("futbolfantasy %s/%s no disponible: %s", pg, kind, e)
            return {}

    with ThreadPoolExecutor(6) as ex:
        return dict(zip(jobs, ex.map(job, jobs)))


def _best_team(names, table, min_score=0.85):
    norm = [teammatch.normalize(n) for n in names if n]
    best, best_s = None, 0.0
    for team in table:
        tn = teammatch.normalize(team)
        s = max((teammatch.similarity(n, tn) for n in norm), default=0)
        if s > best_s:
            best, best_s = team, s
    return best if best_s >= min_score else None


def team_absences(names, comp, data):
    """Bajas de un equipo. `names`: nombres con los que buscarlo (boleto, ESPN).
    Lesiones: página de la competición si está, si no cualquier otra.
    Sanciones: solo de la competición del partido (una roja en liga no sanciona en Champions)."""
    page = PAGE_FOR_COMP.get(comp)
    order = ([page] if page else []) + [p for p in PAGES if p != page]
    players, fuente = [], None
    for pg in order:
        table = data.get((pg, "lesionados")) or {}
        t = _best_team(names, table)
        if t:
            players = [dict(p) for p in table[t]]
            fuente = pg
            break
    if page:
        table = data.get((page, "sancionados")) or {}
        t = _best_team(names, table)
        if t:
            have = {p["jugador"] for p in players}
            players += [dict(p, estado="sancionado") for p in table[t] if p["jugador"] not in have]
            fuente = fuente or page
    units = min(config.AUTO_ABSENCE_CAP, sum(WEIGHT.get(p["estado"], 0) for p in players))
    order_key = {"baja": 0, "sancionado": 1, "duda": 2, "disponible": 3}
    players.sort(key=lambda p: order_key.get(p["estado"], 9))
    return {"jugadores": players, "unidades": units, "fuente": fuente}
