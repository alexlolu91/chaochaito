"""Simulador de las APIs de SELAE y ESPN para probar sin red.
Reproduce la forma real de las respuestas (comprobada el 17/10/2026)."""
import random
import re
from datetime import date, datetime, timedelta

SELAE_PROXIMOS = [
    {"fecha": "2026-10-18 00:00:00", "dia_semana": "domingo", "id_sorteo": "1325906059", "game_id": "LAQU",
     "apertura": "2026-10-14 00:00:00", "cierre": "2026-10-17 14:00:00", "anyo": "2026",
     "premio_bote": "2000000", "estado": "abierto", "jornada": 14},
    {"fecha": "2026-10-21 00:00:00", "id_sorteo": "1326206060", "game_id": "LAQU", "estado": "cerrado", "jornada": 15},
]
_P = [("Espanyol (m)", "At. Madrid (m)", "2026\\/10\\/17 14:00:00"), ("Villarreal (m)", "Elche (m)", "2026\\/10\\/17 16:15:00"),
      ("Betis (m)", "Barcelona (m)", "2026\\/10\\/17 18:30:00"), ("Valencia (m)", "Athletic Club (m)", "2026\\/10\\/17 21:00:00"),
      ("Celta (m)", "Alavés (m)", "2026\\/10\\/18 16:15:00"), ("Málaga (m)", "Real Sociedad (m)", "2026\\/10\\/18 18:30:00"),
      ("Real Madrid (m)", "Sevilla (m)", "2026\\/10\\/18 21:00:00"), ("Eibar (m)", "Mallorca (m)", "2026\\/10\\/17 16:15:00"),
      ("Las Palmas (m)", "Castellón (m)", "2026\\/10\\/17 21:00:00"), ("Burgos (m)", "Sabadell (m)", "2026\\/10\\/18 21:00:00"),
      ("Athletic Club (f)", "Valencia (f)", "2026\\/10\\/17 16:30:00"), ("Real Madrid (f)", "Espanyol (f)", "2026\\/10\\/17 20:30:00"),
      ("Eibar (f)", "Deportivo (f)", "2026\\/10\\/18 16:00:00"), ("At. Madrid (f)", "Madrid Cff (f)", "2026\\/10\\/18 18:00:00"),
      ("Getafe (m)", "Rayo Vallecano (m)", "2026\\/10\\/19 21:00:00")]
SELAE_FECHA = [{"fecha_sorteo": "2026-10-18 00:00:00", "jornada": "14", "temporada": "2026-2027",
                "premio_bote": "2000000", "partidos": [{"local": a, "visitante": b, "fecha": f} for a, b, f in _P]}]

TEAMS = {
    "esp.1": [("83", "Barcelona", "Barcelona"), ("86", "Real Madrid", "Real Madrid"), ("1068", "Atlético Madrid", "Atlético"),
              ("88", "Espanyol", "Espanyol"), ("102", "Villarreal", "Villarreal"), ("3751", "Elche", "Elche"),
              ("244", "Real Betis", "Betis"), ("94", "Valencia", "Valencia"), ("93", "Athletic Club", "Athletic"),
              ("85", "Celta Vigo", "Celta Vigo"), ("96", "Alavés", "Alavés"), ("99", "Málaga", "Málaga"),
              ("89", "Real Sociedad", "Real Sociedad"), ("243", "Sevilla", "Sevilla"), ("2922", "Getafe", "Getafe"),
              ("101", "Rayo Vallecano", "Rayo"), ("97", "Osasuna", "Osasuna"), ("87", "Racing Santander", "Racing"),
              ("90", "Deportivo", "Deportivo"), ("1538", "Levante", "Levante")],
    "esp.2": [("3842", "Eibar", "Eibar"), ("84", "Mallorca", "Mallorca"), ("98", "Las Palmas", "Las Palmas"),
              ("5413", "Castellón", "Castellón"), ("12597", "Burgos", "Burgos"), ("5409", "Sabadell", "Sabadell"),
              ("20983", "Real Sociedad II", "Real Sociedad II"), ("92", "Real Oviedo", "Oviedo"), ("3788", "Sporting Gijón", "Sporting")],
    "esp.w.1": [("21372", "Athletic Club", "Athletic"), ("21374", "Valencia", "Valencia"), ("21128", "Real Madrid", "Real Madrid"),
                ("21154", "Espanyol", "Espanyol"), ("21376", "Eibar", "Eibar"), ("21145", "Deportivo La Coruña", "Deportivo"),
                ("20093", "Atlético Madrid", "Atlético Madrid"), ("21425", "Madrid CFF", "Madrid CFF"), ("20091", "Barcelona", "Barcelona")],
}
TEAMS["por.1"] = [("437", "FC Porto", "Porto"), ("2250", "Sporting CP", "Sporting")]
TEAMS["ned.1"] = [("148", "PSV Eindhoven", "PSV"), ("142", "Feyenoord Rotterdam", "Feyenoord")]
TEAMS["ger.1"] = [("11420", "RB Leipzig", "Leipzig"), ("132", "Bayern Munich", "Bayern")]
TEAMS["uefa.champions"] = [("437", "FC Porto", "Porto"), ("148", "PSV Eindhoven", "PSV"),
                           ("86", "Real Madrid", "Real Madrid"), ("11420", "RB Leipzig", "Leipzig"),
                           ("244", "Real Betis", "Betis"), ("142", "Feyenoord Rotterdam", "Feyenoord")]
TEAMS["uefa.nations"] = [("164", "Spain", "Spain"), ("481", "Germany", "Germany")]
DOMESTIC = {"437": "por.1", "2250": "por.1", "148": "ned.1", "142": "ned.1", "11420": "ger.1", "132": "ger.1",
            "86": "esp.1", "244": "esp.1"}
UCL_DAYS = {date(2026, 10, 20): [("437", "148")], date(2026, 10, 21): [("86", "11420"), ("244", "142")]}
NATIONS_DAYS = {date(2026, 10, 20): [("481", "164")]}   # Alemania - España

STRENGTH = {"83": 2.0, "86": 2.0, "1068": 1.6, "20091": 2.6, "21128": 1.7, "88": 0.9, "3751": 0.8, "5409": 0.7}


def _team_json(lg):
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": i, "displayName": d, "shortDisplayName": s,
                                                         "name": d, "location": d}} for i, d, s in TEAMS[lg]]}]}]}


def _event(eid, d, lg, h, a, hs=None, as_=None, done=True, odds=None):
    name = dict((i, n) for i, n, _ in TEAMS[lg])
    comp = {"status": {"type": {"completed": done, "state": "post" if done else "pre"}},
            "competitors": [
                {"id": h, "homeAway": "home", "team": {"id": h, "displayName": name[h]},
                 "score": {"value": float(hs), "displayValue": str(hs)} if hs is not None else "0"},
                {"id": a, "homeAway": "away", "team": {"id": a, "displayName": name[a]},
                 "score": {"value": float(as_), "displayValue": str(as_)} if as_ is not None else "0"}],
            "odds": [odds] if odds else [None]}
    return {"id": eid, "date": d.strftime("%Y-%m-%dT%H:%MZ"), "competitions": [comp]}


def _schedule(lg, tid, season):
    if lg not in TEAMS:
        return {"events": []}
    ids = [i for i, _, _ in TEAMS[lg]]
    if tid not in ids:
        return {"events": []}
    rng = random.Random(f"{lg}-{season}")
    evs = []
    start = datetime(season, 8, 15)
    end = datetime(2026, 10, 12)
    for k in range(30):
        d = start + timedelta(days=7 * k)
        if d > end:
            break
        order = ids[:]
        rng.shuffle(order)
        for x in range(0, len(order) - 1, 2):
            h, a = order[x], order[x + 1]
            if tid not in (h, a):
                continue
            mr = random.Random(f"{lg}-{season}-{k}-{h}-{a}")
            lh = 1.35 * STRENGTH.get(h, 1) / STRENGTH.get(a, 1) ** .5
            la = 1.05 * STRENGTH.get(a, 1) / STRENGTH.get(h, 1) ** .5
            hs = sum(mr.random() < lh / 6 for _ in range(6))
            as_ = sum(mr.random() < la / 6 for _ in range(6))
            evs.append(_event(f"{season}{k:02d}{h}{a}", d, lg, h, a, hs, as_))
    return {"events": evs}


def _scoreboard(lg, day):
    d = datetime.strptime(day, "%Y%m%d")
    out = []
    special = {"uefa.champions": UCL_DAYS, "uefa.nations": NATIONS_DAYS}.get(lg)
    if special is not None:
        return {"events": [_event(f"ev{h}{a}", d.replace(hour=19), lg, h, a, done=False)
                           for h, a in special.get(d.date(), [])]}
    if lg not in TEAMS:
        return {"events": []}
    pairs = {"esp.1": [("88", "1068"), ("244", "83"), ("86", "243")], "esp.2": [("98", "5413")]}.get(lg, [])
    if d.date() == date(2026, 10, 18):
        for h, a in pairs:
            odds = {"provider": {"name": "DraftKings"}, "moneyline": {
                "home": {"close": {"odds": "+150"}}, "draw": {"close": {"odds": "+240"}}, "away": {"close": {"odds": "+170"}}}}
            out.append(_event(f"ev{h}{a}", d.replace(hour=18), lg, h, a, done=False, odds=odds))
    return {"events": out}


def _summary(lg, eid):
    rng = random.Random(eid)
    teams = []
    for tid, _, _ in TEAMS[lg]:   # simplificación: estadísticas para todos los equipos
        teams.append({"team": {"id": tid}, "statistics": [
            {"name": "yellowCards", "displayValue": str(rng.randint(0, 5))},
            {"name": "redCards", "displayValue": "1" if rng.random() < .1 else "0"}]})
    last5 = []
    m = re.match(r"ev(\d+?)(437|148|11420|142|164)?$", eid)
    for tid in {"ev437148": ("437", "148"), "ev8611420": ("86", "11420"), "ev481164": ("481", "164")}.get(eid, ()):
        last5.append({"team": {"id": tid, "displayName": tid}, "events": [
            {"id": f"l5{tid}{k}", "gameDate": f"2026-10-1{k + 3}T19:00Z", "homeTeamId": tid, "awayTeamId": "999",
             "homeTeamScore": str(2 if tid in ("437", "86", "164") else 1), "awayTeamScore": "1",
             "leagueName": "Some League", "opponent": {"displayName": "Rival"}} for k in range(5)]})
    live = LIVE.get(eid, ("post", "2", "1"))
    header = {"competitions": [{"status": {"type": {"completed": live[0] == "post", "state": live[0],
                                                    "shortDetail": "67'" if live[0] == "in" else ""}},
                                "competitors": [{"homeAway": "home", "score": live[1]},
                                                {"homeAway": "away", "score": live[2]}]}]}
    return {"lastFiveGames": last5, "boxscore": {"teams": teams}, "header": header}


# estado en directo simulado de los partidos de la jornada 14
LIVE = {"ev881068": ("post", "0", "2"),      # Espanyol 0-2 At. Madrid (terminado)
        "ev24483": ("in", "1", "1"),         # Betis 1-1 Barcelona (en juego)
        "ev86243": ("pre", "0", "0"),        # Real Madrid - Sevilla (sin empezar)
        "ev985413": ("post", "4", "0")}      # Las Palmas 4-0 Castellón (terminado)


def fake_get_json(url, params=None, ttl=0, headers=None, use_stale_on_error=True):
    params = params or {}
    if "proximosv3" in url:
        return SELAE_PROXIMOS
    if "fechav3" in url:
        return SELAE_FECHA
    m = re.search(r"/soccer/([^/]+)/(.*)$", url)
    lg, rest = m.group(1), m.group(2)
    if rest == "teams":
        if lg not in TEAMS:
            raise AssertionError(url)
        return _team_json(lg)
    tm = re.match(r"teams/(\d+)$", rest)
    if tm:
        tid = tm.group(1)
        name = next((n for lst in TEAMS.values() for i, n, _ in lst if i == tid), tid)
        dl = DOMESTIC.get(tid)
        return {"team": {"id": tid, "displayName": name, "defaultLeague": {"slug": dl} if dl else {}}}
    sm = re.match(r"teams/(\d+)/schedule", rest)
    if sm:
        return _schedule(lg, sm.group(1), int(params["season"]))
    if rest == "scoreboard":
        return _scoreboard(lg, params["dates"])
    if rest == "summary":
        return _summary(lg, params["event"])
    raise AssertionError(url)
