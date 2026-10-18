"""Orquesta: quiniela vigente -> datos de cada partido -> pronóstico."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import bajas as ffbajas, config, espn, model, storage, teams as teammatch, ticket
from .http import FetchError

log = logging.getLogger(__name__)
POOL = 8


def jornada_key(j):
    if j.get("jornada") and j.get("temporada"):
        return f'{j["temporada"]}-J{j["jornada"]}'
    return f'sorteo-{j.get("fecha_sorteo")}'


def _leagues_for(genero):
    return config.LEAGUES_WOMEN if genero == "f" else config.LEAGUES_MEN


def _comps_for(genero):
    return config.COMPETITIONS_WOMEN if genero == "f" else config.COMPETITIONS_MEN


def _match_date(p):
    if p.get("fecha"):
        try:
            return datetime.fromisoformat(p["fecha"]).date()
        except ValueError:
            pass
    return None


def _days_for(p):
    d = _match_date(p)
    if d:
        return [d + timedelta(days=k) for k in (-1, 0, 1)]
    today = date.today()
    return [today + timedelta(days=k) for k in range(-1, 8)]


def _fetch_boards(partidos):
    """Marcadores de ESPN de todas las competiciones y días que puede necesitar la jornada."""
    wanted = {(c, d) for p in partidos for c in _comps_for(p["genero"]) for d in _days_for(p)}
    boards = {}

    def job(cd):
        try:
            return espn.scoreboard(*cd)
        except FetchError:
            return []

    with ThreadPoolExecutor(POOL) as ex:
        for cd, evs in zip(wanted, ex.map(job, wanted)):
            boards[cd] = evs
    return boards


def _locate(p, boards, teams_by_league):
    """Encuentra el partido en ESPN. Devuelve dict con liga, ids (local del boleto = 'home'), evento."""
    seen, events = set(), []
    for c in _comps_for(p["genero"]):
        for d in _days_for(p):
            for ev in boards.get((c, d), []):
                if ev["id"] not in seen:
                    seen.add(ev["id"])
                    events.append(ev)
    hit = teammatch.match_event(p["local"], p["visitante"], events)
    if hit:
        ev = hit["event"]
        h, a = (ev["away"], ev["home"]) if hit["swapped"] else (ev["home"], ev["away"])
        return {"league": ev["league"], "event": ev, "neutral": hit["swapped"],
                "home": {"id": h["id"], "name": h["name"]}, "away": {"id": a["id"], "name": a["name"]}}
    # plan B: listas de equipos de las ligas españolas (sin fecha o partido aún no publicado)
    m = teammatch.match_pair(p["local"], p["visitante"], teams_by_league, _leagues_for(p["genero"]))
    if m:
        return {"league": m["league"], "event": None, "neutral": False,
                "home": {"id": m["home"]["id"], "name": m["home"]["displayName"]},
                "away": {"id": m["away"]["id"], "name": m["away"]["displayName"]}}
    return None


def _team_leagues(comp, tid, genero, season, info_cache):
    """Competiciones (liga, temporadas) de las que sacar resultados de un equipo."""
    if comp in config.NATIONAL_COMPS:
        pool = [c for c in _comps_for(genero) if c in config.NATIONAL_COMPS]
        return pool, [season, season - 1], None
    leagues = [comp]
    domestic = comp if comp in ("esp.1", "esp.2", "esp.w.1") else None
    if not domestic:
        key = (comp, tid)
        if key not in info_cache:
            info_cache[key] = espn.team_info(comp, tid)
        domestic = info_cache[key].get("default_league")
        if model.is_competition(domestic):
            domestic = None          # ESPN no conoce su liga (p.ej. Ucrania): sin ajuste de nivel
        if domestic and domestic not in leagues:
            leagues.append(domestic)
    if comp in ("esp.1", "esp.2"):
        leagues = config.LEAGUES_MEN          # ascensos/descensos: mira Primera y Segunda
    return leagues, list(range(season, season - config.H2H_YEARS - 1, -1)), domestic


def analyze(jornada):
    """`jornada`: dict de selae.Jornada.to_dict(). Devuelve el análisis completo."""
    key = jornada_key(jornada)
    partidos = jornada["partidos"]
    ref_date = next((d for d in map(_match_date, partidos) if d), None)
    season = espn.season_for(ref_date or date.today())
    avisos = []

    # 1) localizar cada partido en el calendario de ESPN (ligas, copas europeas, selecciones)
    boards = _fetch_boards(partidos)
    teams_by_league = {}
    for lg in sorted({lg for p in partidos for lg in _leagues_for(p["genero"])}):
        try:
            teams_by_league[lg] = espn.teams(lg)
        except FetchError:
            teams_by_league[lg] = []
    matched = []
    for p in partidos:
        m = _locate(p, boards, teams_by_league)
        if not m:
            avisos.append(f'Partido {p["num"]}: no se encontró "{p["local"]} - {p["visitante"]}" en ESPN.')
        matched.append(m)

    # 2) de qué competiciones sacar los resultados de cada equipo
    info_cache, jobs, domestic = {}, {}, {}
    for p, m in zip(partidos, matched):
        if not m:
            continue
        for side in ("home", "away"):
            tid = m[side]["id"]
            lgs, seasons, dom = _team_leagues(m["league"], tid, p["genero"], season, info_cache)
            prev = jobs.get(tid)
            jobs[tid] = (sorted(set(lgs) | set(prev[0] if prev else [])), seasons)
            domestic[tid] = dom or domestic.get(tid)

    # 3) resultados de cada equipo en paralelo
    with ThreadPoolExecutor(POOL) as ex:
        futs = {tid: ex.submit(espn.team_results, lgs, tid, seasons) for tid, (lgs, seasons) in jobs.items()}
        results = {}
        for tid, f in futs.items():
            try:
                results[tid] = f.result()
            except Exception:  # noqa: BLE001 - un equipo no debe tumbar la jornada
                log.exception("Error cargando %s", tid)
                results[tid] = []

    # 4) resumen de cada partido: cuotas + últimos 5 partidos de cada equipo (cualquier competición)
    def summary_job(m):
        ev = m.get("event") or espn.find_event(m["league"], m["home"]["id"], m["away"]["id"], ref_date)
        if not ev:
            return None, None, {}
        odds, last5 = ev.get("odds"), {}
        try:
            sm = espn.summary(m["league"], ev["id"], finished=ev.get("completed", False))
            odds = odds or espn.odds_from_summary(sm)
            last5 = espn.last_five_from_summary(sm)
        except FetchError:
            pass
        if odds and m.get("neutral"):   # ESPN tiene los equipos al revés que el boleto
            odds = dict(odds, p1=odds["p2"], p2=odds["p1"])
        return ev, odds, last5

    with ThreadPoolExecutor(POOL) as ex:
        sums = [ex.submit(summary_job, m) if m else None for m in matched]
        sums = [(_safe(f) or (None, None, {})) if f else (None, None, {}) for f in sums]
    for _, _, last5 in sums:
        for tid, rows in last5.items():
            if tid in results:
                have = {r["id"] for r in results[tid]}
                results[tid] += [r for r in rows if r["id"] not in have]
                results[tid].sort(key=lambda r: r.get("date") or "")

    # 5) medias de goles por competición (temporada actual y anterior)
    cutoff = datetime(season - 1, 7, 1).isoformat()
    by_league = {}
    for res in results.values():
        for r in res:
            if r.get("league") and (r.get("date") or "") >= cutoff:
                by_league.setdefault(r["league"], {})[r["id"]] = r
    league_avgs = {lg: model.league_averages(list(rows.values()), lg) for lg, rows in by_league.items()}
    for m in matched:
        if m and m["league"] not in league_avgs:
            league_avgs[m["league"]] = model.league_averages([], m["league"])

    # 6) tarjetas recientes
    with ThreadPoolExecutor(POOL) as ex:
        card_f = {tid: ex.submit(_recent_cards, tid, results.get(tid, []), season) for tid in jobs}
        cards = {tid: _safe(f) for tid, f in card_f.items()}

    bajas = storage.get_bajas(key)

    # 7) bajas y sancionados de futbolfantasy (solo fútbol masculino)
    ff = {}
    if any(p["genero"] != "f" for p in partidos):
        try:
            ff = ffbajas.load_all()
        except Exception:  # noqa: BLE001 - las bajas son un extra, nunca deben tumbar el análisis
            log.exception("No se pudieron leer las bajas de futbolfantasy")
        if not any(ff.values()):
            avisos.append("No se pudieron leer las bajas de futbolfantasy; solo cuentan las marcadas a mano.")

    # 8) pronóstico de cada partido
    out = []
    for p, m, (ev, mk, _) in zip(partidos, matched, sums):
        auto = None
        if m and ff and p["genero"] != "f":
            auto = {"local": ffbajas.team_absences([p["local"], m["home"]["name"]], m["league"], ff),
                    "visitante": ffbajas.team_absences([p["visitante"], m["away"]["name"]], m["league"], ff)}
        out.append(_analyze_match(p, m, ev, mk, results, league_avgs, cards, bajas.get(p["num"], {}),
                                  season, domestic, auto))

    catorce = [r["probs"] for r in out[:14]]
    sugeridas = None
    if len(catorce) == 14:
        cols = ticket.top_columns(catorce, config.NUM_APUESTAS)
        pleno15 = out[14]["pleno"][0]["resultado"] if len(out) > 14 and out[14].get("pleno") else None
        sugeridas = {"columnas": cols, "pleno": pleno15,
                     "coste": round(len(cols) * config.PRICE_PER_BET, 2),
                     "p14_total": sum(c["p14"] for c in cols),
                     "confirmadas": storage.get_apuestas(key) is not None}
    presets = [dict(pr, **ticket.optimize(catorce, pr["dobles"], pr["triples"])) for pr in ticket.PRESETS] \
        if len(catorce) == 14 else []
    return {
        "key": key,
        "jornada": {k: v for k, v in jornada.items() if k != "partidos"},
        "temporada_espn": season,
        "partidos": out,
        "boletos": presets,
        "apuestas": sugeridas,
        "precio_apuesta": config.PRICE_PER_BET,
        "avisos": avisos,
        "generado": datetime.now().isoformat(timespec="seconds"),
    }


def _safe(fut):
    try:
        return fut.result()
    except Exception:  # noqa: BLE001
        log.exception("Tarea fallida")
        return None


def _recent_cards(team_id, matches, season):
    start = datetime(season, 7, 1).isoformat()
    recent = [m for m in matches if m.get("league") and not m.get("friendly")
              and (m.get("date") or "") >= start][-config.CARDS_LAST_N:]
    yel = red = n = 0
    red_last = False
    for i, m in enumerate(recent):
        try:
            sm = espn.summary(m["league"], m["id"], finished=True)
        except FetchError:
            continue
        c = espn.cards_from_summary(sm, team_id)
        if c is None:
            continue
        n += 1
        yel += c[0]
        red += c[1]
        if i == len(recent) - 1 and c[1] > 0:
            red_last = True
    if n == 0:
        return None
    return {"partidos": n, "amarillas_media": round(yel / n, 2), "rojas": red, "roja_ultimo": red_last}


def _analyze_match(p, m, ev, market, results, league_avgs, cards, bajas, season, domestic=None, auto=None):
    base = {"num": p["num"], "local": p["local"], "visitante": p["visitante"], "genero": p["genero"],
            "fecha": p.get("fecha")}
    if not m:
        probs = {"p1": 0.45, "px": 0.28, "p2": 0.27}
        return base | {"reconocido": False, "probs": probs, "signo": "1", "confianza": "baja", "fuente": "modelo",
                       "doble": "1X", "motivos": ["Equipos no reconocidos: se usa la media histórica."],
                       "pleno": None}
    lg = m["league"]
    hid, aid = m["home"]["id"], m["away"]["id"]
    domestic = domestic or {}
    dh, da = domestic.get(hid), domestic.get(aid)
    avg = league_avgs[lg]
    if lg in model.CROSS_LEAGUE:
        # Las valoraciones de cada equipo están "en la moneda" de su liga: la base debe ser una
        # media de liga doméstica, no la de la Champions (si no, se cuenta dos veces su nivel).
        ah = (league_avgs.get(dh) or model.default_avg(dh)) if dh else model.GENERIC_AVG
        aa = (league_avgs.get(da) or model.default_avg(da)) if da else model.GENERIC_AVG
        avg = ((ah[0] + aa[0]) / 2, (ah[1] + aa[1]) / 2)
    avg_for = lambda l: league_avgs.get(l) or (model.default_avg(l) if l else model.GENERIC_AVG)  # noqa: E731
    hp = model.team_profile(hid, results.get(hid, []), season, avg, avg_for)
    ap = model.team_profile(aid, results.get(aid, []), season, avg, avg_for)
    hc, ac = cards.get(hid), cards.get(aid)
    habs, aabs = int(bajas.get("local", 0)), int(bajas.get("visitante", 0))
    auto = auto or {}
    hauto = (auto.get("local") or {}).get("unidades", 0)
    aauto = (auto.get("visitante") or {}).get("unidades", 0)
    strength = (1.0, 1.0)
    if lg in model.CROSS_LEAGUE and dh and da and dh != da:
        strength = (config.LEAGUE_STRENGTH.get(dh, config.DEFAULT_STRENGTH),
                    config.LEAGUE_STRENGTH.get(da, config.DEFAULT_STRENGTH))
    lh, la = model.expected_goals(hp, ap, avg,
                                  habs + config.AUTO_ABSENCE_UNIT * hauto, aabs + config.AUTO_ABSENCE_UNIT * aauto,
                                  bool(hc and hc["roja_ultimo"]), bool(ac and ac["roja_ultimo"]),
                                  strength=strength, neutral=m.get("neutral", False))
    M = model.score_matrix(lh, la)
    p_model = model.outcome_probs(M)
    all_matches = results.get(hid, []) + results.get(aid, [])
    h2h = model.h2h_summary(hid, aid, all_matches)
    p_h2h = model.apply_h2h(p_model, h2h)
    final = model.blend_market(p_h2h, market)
    dbl, dbl_p = model.double_of(final)
    goles_mercado, M_pleno = None, M
    if market:
        goles_mercado = model.fit_market(market, market.get("total"), lh + la)
        M_pleno = model.score_matrix(*goles_mercado)
    signo_modelo = model.sign_of(p_h2h)

    motivos = []
    if hp["form"]:
        motivos.append(f'Forma local {hp["form"]} vs visitante {ap["form"] or "—"}')
    n_h2h = h2h["g"] + h2h["e"] + h2h["p"]
    if n_h2h:
        motivos.append(f'H2H últimos {config.H2H_YEARS} años (local): {h2h["g"]}V {h2h["e"]}E {h2h["p"]}D')
    for side, name in (("local", p["local"]), ("visitante", p["visitante"])):
        info = auto.get(side) or {}
        js = info.get("jugadores") or []
        nb = sum(1 for j in js if j["estado"] in ("baja", "sancionado"))
        nd = sum(1 for j in js if j["estado"] == "duda")
        if nb or nd:
            motivos.append(f"{name}: {nb} baja(s) y {nd} duda(s) según futbolfantasy")
    if habs or aabs:
        motivos.append(f"Ajuste manual de bajas clave: local {habs}, visitante {aabs}")
    for name, c in ((p["local"], hc), (p["visitante"], ac)):
        if c and c["roja_ultimo"]:
            motivos.append(f"{name} vio roja en su último partido (posible sanción)")
    if strength != (1.0, 1.0):
        motivos.append(f"Nivel de liga: {config.LEAGUE_NAMES.get(dh, dh)} {strength[0]:.2f} vs "
                       f"{config.LEAGUE_NAMES.get(da, da)} {strength[1]:.2f}")
    if m.get("neutral"):
        motivos.append("Campo neutral o local distinto al del boleto")
    if hp["season_games"] < 4 or ap["season_games"] < 4:
        motivos.append("Pocos partidos esta temporada: más peso a la anterior")

    return base | {
        "reconocido": True,
        "liga": lg,
        "liga_nombre": config.LEAGUE_NAMES.get(lg, lg),
        "espn": {"home": m["home"], "away": m["away"], "event_id": ev["id"] if ev else None,
                 "neutral": bool(m.get("neutral"))},
        "probs": {k: round(v, 4) for k, v in final.items()},
        "probs_modelo": {k: round(v, 4) for k, v in p_h2h.items()},
        "mercado": market,
        "fuente": "mercado" if market and config.MARKET_WEIGHT >= 0.5 else "modelo",
        "modelo": {"signo": signo_modelo, "confianza": model.confidence(p_h2h),
                   "coincide": signo_modelo == model.sign_of(final)},
        "bajas_auto": auto,
        "goles_esperados": [round(lh, 2), round(la, 2)],
        "signo": model.sign_of(final),
        "confianza": model.confidence(final),
        "doble": dbl,
        "doble_prob": round(dbl_p, 4),
        "local_stats": hp,
        "visitante_stats": ap,
        "tarjetas": {"local": hc, "visitante": ac},
        "h2h": h2h,
        "bajas": {"local": habs, "visitante": aabs},
        "pleno": model.pleno(M_pleno, top=5),
        "pleno_fuente": "mercado" if goles_mercado else "modelo",
        "goles_mercado": list(goles_mercado) if goles_mercado else None,
        "pleno_modelo": model.pleno(M) if goles_mercado else None,
        "motivos": motivos,
    }


# --- Evaluación de aciertos ------------------------------------------------------

def _final_score(league, event_id):
    sm = espn.summary(league, event_id)
    comp = ((sm.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None
    sc = {}
    for c in comp.get("competitors", []):
        sc[c.get("homeAway")] = espn._score(c.get("score"))
    if None in (sc.get("home"), sc.get("away")):
        return None
    return sc["home"], sc["away"]


def evaluate(key):
    saved = storage.get_prediction(key)
    if not saved:
        raise KeyError(key)
    data = saved["datos"]
    filas, aciertos_signo, jugados = [], 0, 0
    boleto = data.get("boleto") or {}
    columnas = data.get("columnas") or ([boleto["signos"]] if boleto.get("signos") else [])
    aciertos_col = [0] * len(columnas)
    for i, pm in enumerate(data["partidos"]):
        ev = (pm.get("espn") or {}).get("event_id")
        res = None
        if ev and pm.get("liga"):
            try:
                res = _final_score(pm["liga"], ev)
            except FetchError:
                res = None
            if res and (pm.get("espn") or {}).get("neutral"):
                res = (res[1], res[0])
        fila = {"num": pm["num"], "partido": f'{pm["local"]} - {pm["visitante"]}', "pronostico": pm["signo"]}
        if res:
            real = "1" if res[0] > res[1] else "X" if res[0] == res[1] else "2"
            fila |= {"marcador": f"{res[0]}-{res[1]}", "real": real, "acierto": real == pm["signo"]}
            if i < 14:
                jugados += 1
                aciertos_signo += real == pm["signo"]
                for c, col in enumerate(columnas):
                    if i < len(col) and real in col[i]:
                        aciertos_col[c] += 1
            elif pm.get("pleno"):
                cat = lambda g: "M" if g >= 3 else str(g)  # noqa: E731
                fila["pleno_real"] = f"{cat(res[0])}-{cat(res[1])}"
                fila["acierto_pleno"] = fila["pleno_real"] == pm["pleno"][0]["resultado"]
        filas.append(fila)
    ev = {"filas": filas, "jugados": jugados, "aciertos_signo": aciertos_signo,
          "aciertos_boleto": max(aciertos_col) if columnas else None, "aciertos_columnas": aciertos_col,
          "porcentaje": round(100 * aciertos_signo / jugados, 1) if jugados else None,
          "evaluado": datetime.now().isoformat(timespec="seconds")}
    storage.save_evaluation(key, ev)
    return ev



# --- Apuestas confirmadas y estado en directo ---------------------------------------

def apuestas_payload(analisis, columnas, pleno):
    """Lo que se guarda al confirmar: partidos (con ids de ESPN), columnas y pleno."""
    partidos = []
    for p in analisis["partidos"]:
        e = p.get("espn") or {}
        partidos.append({k: p.get(k) for k in ("num", "local", "visitante", "genero", "fecha", "liga", "liga_nombre",
                                                "signo")} |
                        {"event_id": e.get("event_id"), "neutral": bool(e.get("neutral")),
                         "home_id": (e.get("home") or {}).get("id"), "away_id": (e.get("away") or {}).get("id")})
    return {"jornada": analisis["jornada"], "partidos": partidos, "columnas": columnas, "pleno": pleno,
            "precio_apuesta": config.PRICE_PER_BET}


def _cat(g):
    return "M" if g >= 3 else str(g)


def estado(key=None):
    """Estado de las apuestas confirmadas: cada partido pendiente / acierto / fallo por columna."""
    saved = storage.get_apuestas(key)
    if not saved:
        return None
    d = saved["datos"]
    cols, pleno = d["columnas"], d.get("pleno")
    ids_antes = [pm.get("event_id") for pm in d["partidos"]]

    def job(pm):
        if not pm.get("liga"):
            return None
        ev_id = pm.get("event_id")
        if not ev_id and pm.get("home_id") and pm.get("away_id"):
            # el partido no estaba aún en ESPN al confirmar: se vuelve a buscar
            h, a = (pm["away_id"], pm["home_id"]) if pm.get("neutral") else (pm["home_id"], pm["away_id"])
            ev = espn.find_event(pm["liga"], h, a, _match_date(pm), days=2)
            ev_id = ev["id"] if ev else None
            if ev_id:
                pm["event_id"] = ev_id
        if not ev_id:
            return None
        try:
            return espn.live_status(pm["liga"], ev_id)
        except FetchError:
            return None

    with ThreadPoolExecutor(POOL) as ex:
        lives = list(ex.map(job, d["partidos"]))
    if any(pm.get("event_id") != before for pm, before in zip(d["partidos"], ids_antes)):
        storage.update_apuestas_datos(saved["key"], d)   # guarda los partidos encontrados ahora

    filas, totales = [], [{"aciertos": 0, "fallos": 0, "pendientes": 0} for _ in cols]
    for i, (pm, lv) in enumerate(zip(d["partidos"], lives)):
        hs = as_ = None
        state = "pre"
        detalle = ""
        if lv:
            hs, as_, state, detalle = lv["home"], lv["away"], lv["state"], lv["detalle"]
            if pm.get("neutral") and hs is not None:
                hs, as_ = as_, hs
        terminado = state == "post" and hs is not None and as_ is not None
        real = ("1" if hs > as_ else "X" if hs == as_ else "2") if (hs is not None and as_ is not None) else None
        fila = {"num": pm["num"], "local": pm["local"], "visitante": pm["visitante"],
                "liga_nombre": pm.get("liga_nombre"), "fecha": pm.get("fecha"),
                "marcador": f"{hs}-{as_}" if hs is not None and as_ is not None and state != "pre" else None,
                "estado": "final" if terminado else ("en_juego" if state == "in" else "pendiente"),
                "detalle": detalle, "signo_actual": real if state != "pre" else None, "sin_datos": lv is None}
        if i < 14:
            fila["apuestas"] = []
            for c, col in enumerate(cols):
                sig = col[i]
                if terminado:
                    ok = real in sig
                    fila["apuestas"].append({"signo": sig, "estado": "acierto" if ok else "fallo"})
                    totales[c]["aciertos" if ok else "fallos"] += 1
                else:
                    fila["apuestas"].append({"signo": sig, "estado": "pendiente"})
                    totales[c]["pendientes"] += 1
        else:
            real_pleno = f"{_cat(hs)}-{_cat(as_)}" if hs is not None and as_ is not None else None
            fila["pleno"] = {"apuesta": pleno, "actual": real_pleno if state != "pre" else None,
                             "estado": ("acierto" if real_pleno == pleno else "fallo") if terminado else "pendiente"}
        filas.append(fila)
    return {"key": saved["key"], "jornada": d["jornada"], "confirmado": saved["creado"], "filas": filas,
            "totales": totales, "num_apuestas": len(cols),
            "pendientes": sum(1 for f in filas if f["estado"] != "final"),
            "actualizado": datetime.now().isoformat(timespec="seconds")}
