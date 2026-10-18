"""Emparejar los nombres del boleto de Loterías con los equipos de ESPN."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Abreviaturas habituales del boleto -> forma larga
ABBREV = {
    "at": "atletico", "atl": "atletico", "r": "real", "rc": "real club", "dep": "deportivo",
    "sp": "sporting", "rac": "racing", "ath": "athletic", "cult": "cultural",
}
# Alias de nombre completo (ya normalizado) -> nombre normalizado de ESPN
ALIASES = {
    "atletico": "atletico madrid",
    "celta": "celta vigo",
    "betis": "real betis",
    "athletic": "athletic club",
    "deportivo": "deportivo",
    "deportivo coruna": "deportivo",
    "r sociedad": "real sociedad",
    "sporting": "sporting gijon",
    "racing": "racing santander",
    "racing s": "racing santander",
    "leganes": "leganes",
    "madrid cff": "madrid cff",
    "rayo": "rayo vallecano",
    "r oviedo": "real oviedo",
    "oviedo": "real oviedo",
    "r zaragoza": "real zaragoza",
    "zaragoza": "real zaragoza",
    "valladolid": "real valladolid",
    "r valladolid": "real valladolid",
    "huesca": "sd huesca",
    "gijon": "sporting gijon",
    "cultural": "cultural leonesa",
    "andorra": "fc andorra",
    "badalona": "fc badalona",
    "tenerife": "cd tenerife",
    "dux": "dux logrono",
    "logrono": "dux logrono",
    # --- clubes europeos (nombre en el boleto -> ESPN) ---
    "oporto": "porto", "sporting portugal": "sporting cp", "sporting lisboa": "sporting cp",
    "sporting lisboa": "sporting cp", "sp lisboa": "sporting cp", "braga": "sc braga",
    "bayern": "bayern munich", "bayern munchen": "bayern munich", "b munich": "bayern munich",
    "dortmund": "borussia dortmund", "b dortmund": "borussia dortmund", "borussia d": "borussia dortmund",
    "leverkusen": "bayer leverkusen", "b leverkusen": "bayer leverkusen", "leipzig": "rb leipzig",
    "frankfurt": "eintracht frankfurt", "e frankfurt": "eintracht frankfurt", "eintracht": "eintracht frankfurt",
    "stuttgart": "vfb stuttgart", "m gladbach": "borussia monchengladbach", "gladbach": "borussia monchengladbach",
    "inter": "internazionale", "inter milan": "internazionale", "inter de milan": "internazionale",
    "milan": "ac milan", "napoles": "napoli", "roma": "as roma", "juve": "juventus",
    "fiorentina": "fiorentina", "bolonia": "bologna",
    "man city": "manchester city", "manchester c": "manchester city",
    "manchester utd": "manchester united", "manchester u": "manchester united", "man united": "manchester united",
    "man utd": "manchester united", "tottenham": "tottenham hotspur", "newcastle": "newcastle united",
    "west ham": "west ham united", "nottingham": "nottingham forest", "wolves": "wolverhampton wanderers",
    "psg": "paris saint germain", "paris sg": "paris saint germain", "paris saint germain": "paris saint germain",
    "paris": "paris saint germain", "marsella": "marseille", "olympique marsella": "marseille",
    "lyon": "lyon", "olympique lyon": "lyon", "lyonnais": "lyon", "niza": "nice", "monaco": "monaco",
    "brujas": "club brugge", "club brujas": "club brugge", "gante": "gent", "amberes": "antwerp",
    "anderlecht": "anderlecht", "salzburgo": "red bull salzburg", "rb salzburgo": "red bull salzburg",
    "estrella roja": "red star belgrade", "copenhague": "fc copenhagen", "basilea": "basel",
    "feyenoord": "feyenoord rotterdam", "az": "az alkmaar", "alkmaar": "az alkmaar",
    "shakhtar": "shakhtar donetsk", "dinamo kiev": "dynamo kyiv", "dinamo zagreb": "dinamo zagreb",
    "slavia praga": "slavia prague", "sparta praga": "sparta prague", "olympiakos": "olympiacos",
    "estambul": "istanbul basaksehir", "o marsella": "marseille", "d zagreb": "dinamo zagreb",
    "nott forest": "nottingham forest", "v plzen": "viktoria plzen", "plzen": "viktoria plzen",
    "stade rennais": "rennes", "bodo": "bodo glimt", "bodo glimt": "bodo glimt",
    # --- selecciones ---
    "espana": "spain", "alemania": "germany", "francia": "france", "inglaterra": "england",
    "italia": "italy", "paises bajos": "netherlands", "holanda": "netherlands", "belgica": "belgium",
    "croacia": "croatia", "suiza": "switzerland", "dinamarca": "denmark", "suecia": "sweden",
    "noruega": "norway", "polonia": "poland", "escocia": "scotland", "gales": "wales",
    "irlanda": "republic of ireland", "rep irlanda": "republic of ireland",
    "irlanda norte": "northern ireland", "irlanda del norte": "northern ireland", "irl norte": "northern ireland",
    "turquia": "turkiye", "grecia": "greece", "rep checa": "czechia", "republica checa": "czechia",
    "chequia": "czechia", "eslovaquia": "slovakia", "eslovenia": "slovenia", "hungria": "hungary",
    "rumania": "romania", "ucrania": "ukraine", "bosnia": "bosnia and herzegovina",
    "islandia": "iceland", "finlandia": "finland", "macedonia": "north macedonia",
    "macedonia norte": "north macedonia", "macedonia del norte": "north macedonia", "chipre": "cyprus",
    "luxemburgo": "luxembourg", "feroe": "faroe islands", "islas feroe": "faroe islands",
    "lituania": "lithuania", "letonia": "latvia", "bielorrusia": "belarus", "kazajistan": "kazakhstan",
    "azerbaiyan": "azerbaijan", "moldavia": "moldova", "brasil": "brazil", "mexico": "mexico",
    "eeuu": "united states", "estados unidos": "united states", "usa": "united states",
    "japon": "japan", "corea sur": "south korea", "corea del sur": "south korea", "marruecos": "morocco",
    "arabia saudi": "saudi arabia", "arabia saudita": "saudi arabia", "egipto": "egypt",
    "tunez": "tunisia", "argelia": "algeria", "camerun": "cameroon", "costa marfil": "ivory coast",
    "nueva zelanda": "new zealand", "sudafrica": "south africa", "canada": "canada",
}
STOP = {"fc", "cf", "cd", "ud", "sd", "rcd", "club", "de", "la", "el", "sad", "c", "f"}


_SPECIAL = str.maketrans({"ø": "o", "Ø": "o", "æ": "ae", "ß": "ss", "ł": "l", "đ": "d", "ı": "i", "œ": "oe"})


def strip_accents(s):
    s = s.translate(_SPECIAL)
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalize(name):
    s = strip_accents((name or "").lower())
    s = re.sub(r"[\.\-'’,/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = [ABBREV.get(t, t) for t in s.split()]
    s = " ".join(toks)
    return ALIASES.get(s, s)


def _core(s):
    return " ".join(t for t in s.split() if t not in STOP) or s


def similarity(a, b):
    """Puntuación 0..1 entre dos nombres ya normalizados."""
    if a == b:
        return 1.0
    ca, cb = _core(a), _core(b)
    if ca == cb:
        return 0.97
    score = SequenceMatcher(None, ca, cb).ratio()
    ta, tb = set(ca.split()), set(cb.split())
    if ta and tb:
        inter = len(ta & tb) / min(len(ta), len(tb))
        score = max(score, 0.55 + 0.4 * inter) if inter else score
        # 'Real Sociedad' frente a 'Real Sociedad II': penaliza filiales
        if ("ii" in tb) != ("ii" in ta) or ("b" in tb) != ("b" in ta):
            score -= 0.15
    return max(0.0, min(score, 0.96))


def team_variants(team):
    keys = ("displayName", "shortDisplayName", "name", "location", "nickname")
    return {normalize(team.get(k)) for k in keys if team.get(k)}


def best_team(name, teams):
    """Devuelve (equipo, puntuación) del mejor candidato en la lista `teams` (dicts ESPN)."""
    n = normalize(name)
    best, best_s = None, 0.0
    for t in teams:
        s = max((similarity(n, v) for v in team_variants(t)), default=0.0)
        if s > best_s:
            best, best_s = t, s
    return best, best_s


def match_pair(local, visitante, teams_by_league, leagues):
    """Busca la liga donde ambos equipos encajan mejor. Devuelve dict o None."""
    best = None
    for lg in leagues:
        teams = teams_by_league.get(lg) or []
        h, sh = best_team(local, teams)
        a, sa = best_team(visitante, teams)
        if not h or not a or h.get("id") == a.get("id"):
            continue
        score = sh + sa
        if best is None or score > best["score"]:
            best = {"league": lg, "home": h, "away": a, "score": score, "home_score": sh, "away_score": sa}
    if best and min(best["home_score"], best["away_score"]) >= 0.55:
        return best
    return None


def match_event(local, visitante, events, min_side=0.6):
    """Busca entre los partidos de ESPN de ese día el que corresponde a 'local - visitante'.
    Admite que ESPN tenga los equipos invertidos (campo neutral)."""
    nl, nv = normalize(local), normalize(visitante)
    best = None
    for ev in events:
        h = {normalize(ev["home"]["name"])} | ({normalize(ev["home"].get("short"))} if ev["home"].get("short") else set())
        a = {normalize(ev["away"]["name"])} | ({normalize(ev["away"].get("short"))} if ev["away"].get("short") else set())
        sh = max(similarity(nl, x) for x in h)
        sa = max(similarity(nv, x) for x in a)
        rh = max(similarity(nl, x) for x in a)
        ra = max(similarity(nv, x) for x in h)
        for score_l, score_v, swapped in ((sh, sa, False), (rh, ra, True)):
            if min(score_l, score_v) < min_side:
                continue
            total = score_l + score_v - (0.05 if swapped else 0)
            if best is None or total > best["score"]:
                best = {"event": ev, "score": total, "swapped": swapped}
    return best
