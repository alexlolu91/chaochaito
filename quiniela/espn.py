"""Cliente de la API pública de ESPN (sin clave): ligas, copas europeas y selecciones."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import config
from .http import get_json, FetchError


def season_for(d=None):
    """Año de inicio de la temporada (ESPN usa 2026 para la 2026-27)."""
    d = d or date.today()
    return d.year if d.month >= 7 else d.year - 1


# --- Equipos -------------------------------------------------------------------

def teams(league):
    data = get_json(f"{config.ESPN_BASE}/{league}/teams", ttl=config.TTL_TEAMS)
    out = []
    for sp in data.get("sports", []):
        for lg in sp.get("leagues", []):
            for t in lg.get("teams", []):
                team = t.get("team", t)
                out.append({k: team.get(k) for k in
                            ("id", "displayName", "shortDisplayName", "name", "location",
                             "abbreviation", "nickname")} | {"logo": _logo(team)})
    return out


def _logo(team):
    if team.get("logo"):
        return team["logo"]
    logos = team.get("logos") or []
    return logos[0].get("href") if logos else None


# --- Partidos de un equipo ---------------------------------------------------------

def _score(v):
    if isinstance(v, dict):
        v = v.get("value", v.get("displayValue"))
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_event(ev, league=None):
    """Normaliza un evento ESPN a un dict plano."""
    comp = (ev.get("competitions") or [{}])[0]
    status = (comp.get("status") or ev.get("status") or {}).get("type", {})
    home = away = None
    for c in comp.get("competitors", []):
        side = {
            "id": str(c.get("id") or c.get("team", {}).get("id")),
            "name": c.get("team", {}).get("displayName"),
            "short": c.get("team", {}).get("shortDisplayName"),
            "score": _score(c.get("score")),
            "form": c.get("form"),
        }
        if c.get("homeAway") == "home":
            home = side
        else:
            away = side
    if not home or not away:
        return None
    return {
        "id": str(ev.get("id")),
        "date": ev.get("date") or comp.get("date"),
        "league": league,
        "competition": (ev.get("league") or {}).get("abbreviation") or
                       (ev.get("seasonType") or {}).get("name"),
        "completed": bool(status.get("completed")),
        "state": status.get("state"),
        "home": home,
        "away": away,
        "odds": parse_odds((comp.get("odds") or [None])[0]),
        "friendly": _is_friendly(ev, comp),
    }


def _is_friendly(ev, comp):
    txt = " ".join(str(x) for x in (ev.get("league"), ev.get("seasonType"), ev.get("name"),
                                    comp.get("type"), comp.get("altGameNote"))).lower()
    return "friendl" in txt or "amistoso" in txt


def team_schedule(league, team_id, season):
    current = season >= season_for()
    try:
        data = get_json(
            f"{config.ESPN_BASE}/{league}/teams/{team_id}/schedule",
            {"season": season},
            ttl=config.TTL_SCHEDULE_CURRENT if current else config.TTL_SCHEDULE_PAST,
        )
    except FetchError:
        return []
    out = []
    for ev in data.get("events", []):
        e = parse_event(ev, league)
        if e:
            out.append(e)
    return out


def team_results(leagues, team_id, seasons):
    """Partidos terminados de un equipo en varias ligas/temporadas, sin duplicados, por fecha."""
    seen, out = set(), []
    for lg in leagues:
        for s in seasons:
            for e in team_schedule(lg, team_id, s):
                if e["completed"] and e["home"]["score"] is not None and e["id"] not in seen:
                    seen.add(e["id"])
                    out.append(e)
    out.sort(key=lambda e: e["date"] or "")
    return out


# --- Marcadores del día y cuotas ---------------------------------------------------

_bad_slugs = set()


def scoreboard(league, day):
    if league in _bad_slugs:
        return []
    past = day < date.today() - timedelta(days=2)
    try:
        data = get_json(f"{config.ESPN_BASE}/{league}/scoreboard", {"dates": day.strftime("%Y%m%d")},
                        ttl=config.TTL_SCHEDULE_PAST if past else config.TTL_SCOREBOARD)
    except FetchError as e:
        if "HTTP 400" in str(e) or "HTTP 404" in str(e):
            _bad_slugs.add(league)   # competición que ESPN no tiene: no insistir
        raise
    return [e for e in (parse_event(ev, league) for ev in data.get("events", [])) if e]


def team_info(league, team_id):
    """Datos del equipo, incluida su liga doméstica (defaultLeague)."""
    try:
        data = get_json(f"{config.ESPN_BASE}/{league}/teams/{team_id}", ttl=config.TTL_TEAMS)
    except FetchError:
        return {}
    t = data.get("team") or {}
    dl = t.get("defaultLeague") or {}
    return {"id": str(t.get("id") or team_id), "name": t.get("displayName"),
            "default_league": dl.get("slug") or dl.get("abbreviation"),
            "standing": t.get("standingSummary")}


def last_five_from_summary(sm):
    """Últimos 5 partidos de cada equipo (cualquier competición) desde el resumen del partido."""
    out = {}
    for block in sm.get("lastFiveGames") or []:
        tid = str((block.get("team") or {}).get("id"))
        tname = (block.get("team") or {}).get("displayName")
        rows = []
        for g in block.get("events") or []:
            hid, aid = str(g.get("homeTeamId")), str(g.get("awayTeamId"))
            hs, as_ = _score(g.get("homeTeamScore")), _score(g.get("awayTeamScore"))
            if hs is None or as_ is None:
                continue
            opp = (g.get("opponent") or {}).get("displayName")
            league_name = " ".join(str(g.get(k) or "") for k in ("leagueName", "competitionName")).lower()
            rows.append({
                "id": str(g.get("id")), "date": g.get("gameDate"), "league": None,
                "competition": g.get("leagueAbbreviation") or g.get("leagueName"),
                "completed": True, "state": "post",
                "home": {"id": hid, "name": tname if hid == tid else opp, "score": hs},
                "away": {"id": aid, "name": tname if aid == tid else opp, "score": as_},
                "odds": None, "friendly": "friendl" in league_name,
            })
        out[tid] = rows
    return out


def find_event(league, home_id, away_id, around, days=4):
    """Busca el partido home-away en el marcador de ESPN alrededor de una fecha."""
    around = around or date.today()
    for delta in sorted(range(-days, days + 1), key=abs):
        d = around + timedelta(days=delta)
        try:
            evs = scoreboard(league, d)
        except FetchError:
            continue
        for e in evs:
            if e["home"]["id"] == str(home_id) and e["away"]["id"] == str(away_id):
                return e
    return None


def summary(league, event_id, finished=False):
    return get_json(f"{config.ESPN_BASE}/{league}/summary", {"event": event_id},
                    ttl=config.TTL_SUMMARY_FINISHED if finished else config.TTL_SUMMARY_PENDING)


def american_to_prob(ml):
    try:
        ml = float(str(ml).replace("+", ""))
    except (TypeError, ValueError):
        return None
    if ml == 0:
        return None
    return 100 / (ml + 100) if ml > 0 else -ml / (-ml + 100)


def parse_odds(o):
    """Probabilidades 1X2 implícitas (sin margen) a partir del bloque de cuotas ESPN."""
    if not o:
        return None
    h = d = a = None
    ml = o.get("moneyline") or {}
    if ml:
        def pick(side):
            s = ml.get(side) or {}
            return (s.get("close") or s.get("open") or {}).get("odds")
        h, d, a = pick("home"), pick("draw"), pick("away")
    if h is None:
        h = (o.get("homeTeamOdds") or {}).get("moneyLine")
        a = (o.get("awayTeamOdds") or {}).get("moneyLine")
        d = (o.get("drawOdds") or {}).get("moneyLine")
    probs = [american_to_prob(x) for x in (h, d, a)]
    if any(p is None for p in probs):
        return None
    tot = sum(probs)
    if not 0.9 < tot < 1.4:   # cuotas absurdas (p.ej. partido ya decidido)
        return None
    return {"p1": probs[0] / tot, "px": probs[1] / tot, "p2": probs[2] / tot,
            "provider": (o.get("provider") or {}).get("name")}


def odds_from_summary(sm):
    for key in ("pickcenter", "odds"):
        for o in sm.get(key) or []:
            p = parse_odds(o)
            if p:
                return p
    return None


def cards_from_summary(sm, team_id):
    """Tarjetas (amarillas, rojas) de un equipo en un partido terminado."""
    for t in (sm.get("boxscore") or {}).get("teams", []):
        if str((t.get("team") or {}).get("id")) == str(team_id):
            stats = {s.get("name"): s.get("displayValue") for s in t.get("statistics", [])}
            try:
                return int(float(stats.get("yellowCards") or 0)), int(float(stats.get("redCards") or 0))
            except ValueError:
                return None
    return None


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def live_status(league, event_id):
    """Estado en directo de un partido: pre / in / post, marcador y minuto."""
    data = get_json(f"{config.ESPN_BASE}/{league}/summary", {"event": event_id}, ttl=config.TTL_LIVE)
    comp = ((data.get("header") or {}).get("competitions") or [{}])[0]
    st = (comp.get("status") or {}).get("type") or {}
    sc = {}
    for c in comp.get("competitors", []):
        sc[c.get("homeAway")] = _score(c.get("score"))
    state = st.get("state") or "pre"
    if st.get("completed"):
        state = "post"
    return {"state": state, "home": sc.get("home"), "away": sc.get("away"),
            "detalle": st.get("shortDetail") or st.get("detail") or "",
            "cancelado": (st.get("name") or "") in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_ABANDONED")}
