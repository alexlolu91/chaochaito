"""Modelo de pronóstico: Poisson (ataque/defensa) + forma + H2H + cuotas + bajas."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from . import config

DEFAULT_LEAGUE_AVG = {   # goles por partido local / visitante si no hay datos suficientes
    "esp.1": (1.45, 1.10),
    "esp.2": (1.25, 0.95),
    "esp.w.1": (1.55, 1.30),
    "eng.1": (1.55, 1.25), "ita.1": (1.45, 1.15), "ger.1": (1.70, 1.35), "fra.1": (1.50, 1.20),
    "por.1": (1.45, 1.10), "ned.1": (1.75, 1.35), "bel.1": (1.60, 1.25), "tur.1": (1.60, 1.25),
    "sco.1": (1.45, 1.20), "eng.2": (1.40, 1.10),
    "uefa.champions": (1.70, 1.30), "uefa.europa": (1.55, 1.20), "uefa.europa.conf": (1.55, 1.20),
    "uefa.wchampions": (1.80, 1.30), "esp.copa_del_rey": (1.60, 1.10),
    "national": (1.45, 1.00),
}
# Competiciones de clubes donde se cruzan equipos de ligas distintas
CROSS_LEAGUE = {"uefa.champions", "uefa.europa", "uefa.europa.conf", "uefa.super_cup", "uefa.wchampions"}
GENERIC_AVG = (1.45, 1.15)


def default_avg(league):
    from . import config
    if league in config.NATIONAL_COMPS:
        return DEFAULT_LEAGUE_AVG["national"]
    return DEFAULT_LEAGUE_AVG.get(league, GENERIC_AVG)


def is_competition(slug):
    """True si el slug no es una liga doméstica (copa europea, selecciones...)."""
    from . import config
    return not slug or slug.startswith("uefa.") or slug.startswith("fifa.") or slug in config.NATIONAL_COMPS


def _season_start(season):
    return datetime(season, 7, 1, tzinfo=timezone.utc)


def _dt(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def league_averages(matches, league):
    """Media de goles local/visitante. Solo se fía de los datos si cubren muchos equipos distintos:
    si solo hay partidos de los equipos de la quiniela (los mejores), la media sale inflada."""
    dh, da = default_avg(league)
    if league in CROSS_LEAGUE or league in config.NATIONAL_COMPS:
        return dh, da
    seen, teams, hg, ag = set(), set(), 0, 0
    for m in matches:
        if m["id"] in seen or m.get("friendly"):
            continue
        seen.add(m["id"])
        teams.update((m["home"]["id"], m["away"]["id"]))
        hg += m["home"]["score"]
        ag += m["away"]["score"]
    n = len(seen)
    if not n:
        return dh, da
    w = min(1.0, n / 60) * min(1.0, len(teams) / 16)
    return dh * (1 - w) + hg / n * w, da * (1 - w) + ag / n * w


def team_profile(team_id, matches, season, league_avg, avg_for=None):
    """Ratings de ataque/defensa y datos de rendimiento de un equipo.
    `avg_for(liga)` da la media de goles de la liga de cada partido (equipos con varias competiciones)."""
    team_id = str(team_id)
    H, A = league_avg
    start = _season_start(season)
    prev_start = _season_start(season - 1)
    gf_w = base_gf_w = ga_w = base_ga_w = 0.0
    played = []
    for m in matches:
        if m.get("friendly"):
            continue
        d = _dt(m["date"])
        if not d:
            continue
        if d >= start:
            w = 1.0
        elif d >= prev_start:
            w = config.PREV_SEASON_WEIGHT
        else:
            continue
        is_home = m["home"]["id"] == team_id
        if not is_home and m["away"]["id"] != team_id:
            continue
        gf = m["home"]["score"] if is_home else m["away"]["score"]
        ga = m["away"]["score"] if is_home else m["home"]["score"]
        H, A = avg_for(m.get("league")) if avg_for else league_avg
        gf_w += w * gf
        ga_w += w * ga
        base_gf_w += w * (H if is_home else A)
        base_ga_w += w * (A if is_home else H)
        played.append((d, is_home, gf, ga, m))
    H, A = league_avg
    prior = config.PRIOR_GAMES * (H + A) / 2
    att = (gf_w + prior) / (base_gf_w + prior)
    dfn = (ga_w + prior) / (base_ga_w + prior)   # >1 = encaja más que la media

    played.sort(key=lambda x: x[0])
    cur = [p for p in played if p[0] >= start]
    last5 = played[-5:]
    pts = [3 if gf > ga else 1 if gf == ga else 0 for _, _, gf, ga, _ in last5]
    form = "".join("V" if p == 3 else "E" if p == 1 else "D" for p in pts)
    ppg5 = sum(pts) / len(pts) if pts else None

    def split(rows, home):
        rs = [r for r in rows if r[1] == home]
        w = sum(1 for r in rs if r[2] > r[3]); dr = sum(1 for r in rs if r[2] == r[3])
        return {"pj": len(rs), "g": w, "e": dr, "p": len(rs) - w - dr,
                "gf": sum(r[2] for r in rs), "gc": sum(r[3] for r in rs)}

    return {
        "attack": round(att, 3),
        "defense": round(dfn, 3),
        "form": form,
        "ppg5": round(ppg5, 2) if ppg5 is not None else None,
        "season_home": split(cur, True),
        "season_away": split(cur, False),
        "season_games": len(cur),
        "last_matches": [
            {"date": d.strftime("%d/%m"), "home": m["home"]["name"], "away": m["away"]["name"],
             "score": f'{m["home"]["score"]}-{m["away"]["score"]}', "id": m["id"], "league": m.get("league")}
            for d, _, _, _, m in last5[::-1]
        ],
    }


def form_factor(ppg5):
    if ppg5 is None:
        return 1.0
    f = 1 + config.FORM_IMPACT * (ppg5 - 1.4) / 1.6
    return max(1 - config.FORM_IMPACT, min(1 + config.FORM_IMPACT, f))


def expected_goals(home, away, league_avg, home_abs=0, away_abs=0, home_red=False, away_red=False,
                   strength=(1.0, 1.0), neutral=False):
    H, A = league_avg
    if neutral:
        H = A = (H + A) / 2
    lh = H * home["attack"] * away["defense"] * form_factor(home.get("ppg5"))
    la = A * away["attack"] * home["defense"] * form_factor(away.get("ppg5"))
    ratio = (strength[0] / strength[1]) ** config.STRENGTH_EXPONENT if strength[1] else 1.0
    lh *= ratio
    la /= ratio
    lh *= (1 - config.ABSENCE_IMPACT) ** home_abs * (1 - config.RED_CARD_IMPACT if home_red else 1)
    la *= (1 - config.ABSENCE_IMPACT) ** away_abs * (1 - config.RED_CARD_IMPACT if away_red else 1)
    # las bajas del rival también se notan algo en defensa
    la *= (1 + config.ABSENCE_IMPACT / 2) ** home_abs
    lh *= (1 + config.ABSENCE_IMPACT / 2) ** away_abs
    return max(0.15, min(lh, 5.0)), max(0.1, min(la, 5.0))


def _pois(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(lh, la, rho=None, max_goals=None):
    rho = config.DIXON_COLES_RHO if rho is None else rho
    n = (max_goals or config.MAX_GOALS) + 1
    M = [[_pois(i, lh) * _pois(j, la) for j in range(n)] for i in range(n)]
    # corrección Dixon-Coles para marcadores bajos
    M[0][0] *= max(0.0, 1 - lh * la * rho)
    M[0][1] *= max(0.0, 1 + lh * rho)
    M[1][0] *= max(0.0, 1 + la * rho)
    M[1][1] *= max(0.0, 1 - rho)
    tot = sum(map(sum, M))
    return [[v / tot for v in row] for row in M]


def outcome_probs(M):
    p1 = sum(M[i][j] for i in range(len(M)) for j in range(len(M)) if i > j)
    px = sum(M[i][i] for i in range(len(M)))
    return {"p1": p1, "px": px, "p2": 1 - p1 - px}


def apply_h2h(p, h2h, weight=None):
    """Mezcla bayesiana con los enfrentamientos directos (desde la óptica del local actual)."""
    weight = config.H2H_WEIGHT if weight is None else weight
    n = h2h["g"] + h2h["e"] + h2h["p"]
    if n == 0:
        return dict(p)
    return {
        "p1": (p["p1"] * weight + h2h["g"]) / (weight + n),
        "px": (p["px"] * weight + h2h["e"]) / (weight + n),
        "p2": (p["p2"] * weight + h2h["p"]) / (weight + n),
    }


def blend_market(p, market, weight=None):
    if not market:
        return dict(p)
    w = config.MARKET_WEIGHT if weight is None else weight
    return {k: (1 - w) * p[k] + w * market[k] for k in ("p1", "px", "p2")}


H2H_MAX = 8   # solo los enfrentamientos más recientes


def h2h_summary(home_id, away_id, matches):
    home_id, away_id = str(home_id), str(away_id)
    g = e = p = 0
    rows, seen = [], set()
    for m in sorted(matches, key=lambda m: m["date"] or "", reverse=True):
        ids = {m["home"]["id"], m["away"]["id"]}
        if ids != {home_id, away_id} or m["id"] in seen or m.get("friendly"):
            continue
        if len(seen) >= H2H_MAX:
            break
        seen.add(m["id"])
        hs, as_ = m["home"]["score"], m["away"]["score"]
        if m["home"]["id"] != home_id:
            hs, as_ = as_, hs
        if hs > as_:
            g += 1
        elif hs == as_:
            e += 1
        else:
            p += 1
        d = _dt(m["date"])
        rows.append({"date": d.strftime("%d/%m/%Y") if d else "", "home": m["home"]["name"],
                     "away": m["away"]["name"], "score": f'{m["home"]["score"]}-{m["away"]["score"]}'})
    return {"g": g, "e": e, "p": p, "matches": rows}


SIGNS = ("1", "X", "2")


def sign_of(p):
    vals = [p["p1"], p["px"], p["p2"]]
    return SIGNS[vals.index(max(vals))]


def confidence(p):
    mx = max(p["p1"], p["px"], p["p2"])
    if mx >= 0.62:
        return "alta"
    if mx >= 0.47:
        return "media"
    return "baja"


def double_of(p):
    order = sorted(zip(SIGNS, (p["p1"], p["px"], p["p2"])), key=lambda x: -x[1])
    return "".join(sorted((order[0][0], order[1][0]), key=SIGNS.index)), order[0][1] + order[1][1]


def _cat(g):
    return "M" if g >= 3 else str(g)


def pleno(M, top=3):
    """Probabilidades de cada resultado del Pleno al 15 (0,1,2,M por equipo)."""
    probs = {}
    for i, row in enumerate(M):
        for j, v in enumerate(row):
            key = f"{_cat(i)}-{_cat(j)}"
            probs[key] = probs.get(key, 0) + v
    best = sorted(probs.items(), key=lambda kv: -kv[1])[:top]
    return [{"resultado": k, "prob": round(v, 4)} for k, v in best]



def prob_over(M, line):
    """P(goles totales > línea). Con línea entera (asiática) se ignora el empate exacto (devolución)."""
    over = sum(M[i][j] for i in range(len(M)) for j in range(len(M)) if i + j > line)
    if float(line).is_integer():
        push = sum(M[i][j] for i in range(len(M)) for j in range(len(M)) if i + j == line)
        return over / (1 - push) if push < 1 else over
    return over


def fit_market(market, total=None, prior_sum=2.6):
    """Goles esperados (local, visitante) que reproducen las cuotas del mercado: 1X2 y, si hay, la línea
    de más/menos goles. Sin línea de goles, el total se ancla suavemente al del modelo."""
    def err(lh, la):
        M = score_matrix(lh, la, max_goals=8)
        p = outcome_probs(M)
        e = (p["p1"] - market["p1"]) ** 2 + (p["p2"] - market["p2"]) ** 2
        if total:
            e += (prob_over(M, total["line"]) - total["p_over"]) ** 2
        else:
            e += 0.002 * (lh + la - prior_sum) ** 2
        return e

    best = (float("inf"), 1.3, 1.1)
    for i in range(2, 51):
        for j in range(2, 51):
            v = err(i / 10, j / 10)
            if v < best[0]:
                best = (v, i / 10, j / 10)
    _, bh, ba = best
    for i in range(-10, 11):
        for j in range(-10, 11):
            lh, la = bh + i / 100, ba + j / 100
            if lh > 0.05 and la > 0.05:
                v = err(lh, la)
                if v < best[0]:
                    best = (v, lh, la)
    return round(best[1], 2), round(best[2], 2)
