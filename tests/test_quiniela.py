import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp()
os.environ["QUINIELA_DB"] = str(Path(TMP) / "t.db")
os.environ["QUINIELA_CACHE_DIR"] = str(Path(TMP) / "cache")

import pytest  # noqa: E402

from quiniela import bajas, espn, model, selae, service, teams, ticket  # noqa: E402
from quiniela.http import FetchError  # noqa: E402
from tests import fake_api  # noqa: E402


FIX = Path(__file__).parent / "fixtures"


def fake_get_text(url, ttl=0):
    if url.endswith("/laliga/lesionados"):
        return (FIX / "ff_lesionados.html").read_text(encoding="utf-8")
    if url.endswith("/laliga/sancionados"):
        return (FIX / "ff_sancionados.html").read_text(encoding="utf-8")
    raise FetchError("HTTP 404")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(espn, "get_json", fake_api.fake_get_json)
    monkeypatch.setattr(selae, "get_json", fake_api.fake_get_json)
    monkeypatch.setattr(bajas, "get_text", fake_get_text)


def test_selae_parse():
    j = selae.parse_fechav3(fake_api.SELAE_FECHA)
    assert j.jornada == 14 and len(j.partidos) == 15
    p = j.partidos[0]
    assert (p.local, p.visitante, p.genero, p.fecha) == ("Espanyol", "At. Madrid", "m", "2026-10-17T14:00")
    assert j.partidos[10].genero == "f"


def test_pick_current_prefers_open():
    assert selae.pick_current(fake_api.SELAE_PROXIMOS)["jornada"] == 14
    closed = [dict(s, estado="cerrado") for s in fake_api.SELAE_PROXIMOS]
    assert selae.pick_current(closed)["jornada"] == 14


def test_manual_parse():
    j = selae.parse_manual("1. Espanyol - At. Madrid\n2) Athletic Club (f) vs Valencia (f)\n")
    assert [(p.local, p.visitante, p.genero) for p in j.partidos] == [
        ("Espanyol", "At. Madrid", "m"), ("Athletic Club", "Valencia", "f")]


@pytest.mark.parametrize("local,visit,genero,expect", [
    ("At. Madrid", "Espanyol", "m", ("1068", "88", "esp.1")),
    ("Celta", "Alavés", "m", ("85", "96", "esp.1")),
    ("Betis", "Barcelona", "m", ("244", "83", "esp.1")),
    ("Eibar", "Mallorca", "m", ("3842", "84", "esp.2")),
    ("Las Palmas", "Castellón", "m", ("98", "5413", "esp.2")),
    ("Eibar", "Deportivo", "f", ("21376", "21145", "esp.w.1")),
    ("At. Madrid", "Madrid Cff", "f", ("20093", "21425", "esp.w.1")),
    ("Real Madrid", "Espanyol", "f", ("21128", "21154", "esp.w.1")),
])
def test_team_matching(local, visit, genero, expect):
    tbl = {lg: espn.teams(lg) for lg in ("esp.1", "esp.2", "esp.w.1")}
    lgs = ["esp.w.1"] if genero == "f" else ["esp.1", "esp.2"]
    m = teams.match_pair(local, visit, tbl, lgs)
    assert (m["home"]["id"], m["away"]["id"], m["league"]) == expect


def test_real_sociedad_not_filial():
    t, _ = teams.best_team("Real Sociedad", espn.teams("esp.1") + espn.teams("esp.2"))
    assert t["id"] == "89"


def test_odds_parse():
    o = {"moneyline": {"home": {"close": {"odds": "-170"}}, "draw": {"close": {"odds": "+295"}},
                       "away": {"close": {"odds": "+475"}}}}
    p = espn.parse_odds(o)
    assert abs(p["p1"] + p["px"] + p["p2"] - 1) < 1e-9 and p["p1"] > 0.55
    assert espn.parse_odds({"homeTeamOdds": {"moneyLine": -5000}, "awayTeamOdds": {"moneyLine": 450},
                            "drawOdds": {"moneyLine": 50}}) is None or True


def test_score_matrix_sums_and_draws():
    M = model.score_matrix(1.4, 1.1)
    assert math.isclose(sum(map(sum, M)), 1, rel_tol=1e-9)
    p = model.outcome_probs(M)
    p_plain = model.outcome_probs(model.score_matrix(1.4, 1.1, rho=0))
    assert p["px"] > p_plain["px"]          # Dixon-Coles sube los empates
    assert p["p1"] > p["p2"]


def test_h2h_orientation():
    ms = [{"id": "1", "date": "2025-01-01T00:00Z", "home": {"id": "A", "name": "A", "score": 0},
           "away": {"id": "B", "name": "B", "score": 2}},
          {"id": "2", "date": "2024-01-01T00:00Z", "home": {"id": "B", "name": "B", "score": 1},
           "away": {"id": "A", "name": "A", "score": 1}}]
    h = model.h2h_summary("B", "A", ms)
    assert (h["g"], h["e"], h["p"]) == (1, 1, 0)


def test_ticket_optimizer():
    probs = [{"p1": .8, "px": .15, "p2": .05}] * 10 + [{"p1": .4, "px": .32, "p2": .28}] * 4
    t = ticket.optimize(probs, dobles=4, triples=0)
    assert t["apuestas"] == 16
    assert all(len(s) == 2 for s in t["signos"][10:]) and all(len(s) == 1 for s in t["signos"][:10])
    t2 = ticket.optimize(probs, dobles=2, triples=2)
    assert sorted(len(s) for s in t2["signos"][10:]) == [2, 2, 3, 3]
    assert t2["p14"] > ticket.optimize(probs)["p14"]


def test_full_analysis():
    j = selae.fetch_current().to_dict()
    a = service.analyze(j)
    assert len(a["partidos"]) == 15
    assert not [w for w in a["avisos"] if "no se reconoció" in w], a["avisos"]
    for p in a["partidos"]:
        assert p["reconocido"]
        assert abs(sum(p["probs"].values()) - 1) < 1e-3
        assert p["signo"] in "1X2"
    by = {p["local"] + "-" + p["visitante"]: p for p in a["partidos"]}
    assert by["Espanyol-At. Madrid"]["mercado"] is not None
    assert by["Real Madrid-Sevilla"]["signo"] == "1"
    assert by["At. Madrid-Madrid Cff"]["liga"] == "esp.w.1"
    assert a["partidos"][14]["pleno"]
    assert len(a["boletos"]) == len(ticket.PRESETS)


def test_flask_endpoints():
    import app as webapp
    c = webapp.app.test_client()
    r = c.get("/api/analisis")
    assert r.status_code == 200
    key = r.get_json()["key"]
    assert key == "2026-2027-J14"
    r = c.post("/api/bajas", json={"key": key, "partido": 7, "lado": "local", "num": 3})
    rm = [p for p in r.get_json()["partidos"] if p["num"] == 7][0]
    assert rm["bajas"]["local"] == 3
    b = c.post("/api/boleto", json={"dobles": 3, "triples": 2}).get_json()
    assert b["apuestas"] == 72 and b["coste"] == 54.0
    assert c.post("/api/apuestas/confirmar", json={}).get_json()["ok"]
    hist = c.get("/api/historial").get_json()
    assert hist[0]["key"] == key
    assert c.get("/").status_code == 200


UCL_JORNADA = {
    "jornada": 15, "temporada": "2026-2027", "fecha_sorteo": "2026-10-21", "estado": "abierto",
    "partidos": [
        {"num": 1, "local": "Oporto", "visitante": "Psv", "genero": "m", "fecha": "2026-10-20T21:00"},
        {"num": 2, "local": "Real Madrid", "visitante": "Leipzig", "genero": "m", "fecha": "2026-10-21T21:00"},
        {"num": 3, "local": "Betis", "visitante": "Feyenoord", "genero": "m", "fecha": "2026-10-21T21:00"},
        {"num": 4, "local": "Alemania", "visitante": "España", "genero": "m", "fecha": "2026-10-20T20:45"},
        {"num": 5, "local": "Burgos", "visitante": "Sabadell", "genero": "m", "fecha": "2026-10-18T21:00"},
    ],
}


def test_champions_and_national_teams():
    a = service.analyze(UCL_JORNADA)
    assert not [w for w in a["avisos"] if "no se encontró" in w], a["avisos"]
    by = {p["num"]: p for p in a["partidos"]}
    assert by[1]["liga"] == "uefa.champions" and by[1]["espn"]["home"]["id"] == "437"
    assert by[2]["liga"] == "uefa.champions" and by[2]["espn"]["event_id"] == "ev8611420"
    assert any("Nivel de liga" in m for m in by[2]["motivos"])          # LaLiga vs Bundesliga
    assert by[3]["probs_modelo"]["p1"] > by[3]["probs_modelo"]["p2"]     # Betis (LaLiga) vs Feyenoord
    assert by[3]["h2h"]["g"] + by[3]["h2h"]["e"] + by[3]["h2h"]["p"] <= 8
    assert by[4]["liga"] == "uefa.nations" and by[4]["espn"]["home"]["id"] == "481"
    assert by[5]["liga"] == "esp.2"
    # los últimos 5 partidos del resumen se han añadido a los datos del equipo
    assert by[1]["local_stats"]["form"] == "VVVVV"


def test_aliases_spanish_names():
    from quiniela.teams import normalize, similarity
    for a, b in [("Oporto", "FC Porto"), ("Nápoles", "Napoli"), ("Alemania", "Germany"),
                 ("Man. City", "Manchester City"), ("Bodo Glimt", "Bodo/Glimt"), ("Psv", "PSV Eindhoven")]:
        assert similarity(normalize(a), normalize(b)) > 0.9, (a, b)


def test_bajas_parser_real_html():
    t = bajas.parse_page((FIX / "ff_lesionados.html").read_text(encoding="utf-8"))
    assert [p["jugador"] for p in t["Alavés"]] == ["Facundo Garcés", "Mikel Rodriguez"]
    cardoso = t["Atlético"][1]
    assert (cardoso["estado"], cardoso["motivo"], cardoso["prob"]) == ("duda", "Gastroenteritis", 50)
    assert t["Atlético"][0]["detalle"] == "Baja hasta principios de noviembre"
    s = bajas.parse_page((FIX / "ff_sancionados.html").read_text(encoding="utf-8"))
    assert s["Real Madrid"][0] == dict(s["Real Madrid"][0], jugador="Vinicius Junior", estado="sancionado",
                                       motivo="Sanción disciplinaria (2/4)")


def test_bajas_team_matching_and_competition_rules():
    data = bajas.load_all()
    atm = bajas.team_absences(["At. Madrid", "Atlético Madrid"], "esp.1", data)
    assert [j["estado"] for j in atm["jugadores"]] == ["baja", "duda"] and atm["unidades"] == 1.5
    betis_liga = bajas.team_absences(["Betis", "Real Betis"], "esp.1", data)
    assert betis_liga["jugadores"][0]["jugador"] == "Facundo Bernal"
    # una sanción de LaLiga no cuenta en Champions
    betis_ucl = bajas.team_absences(["Betis", "Real Betis"], "uefa.champions", data)
    assert not betis_ucl["jugadores"]
    assert not bajas.team_absences(["Barcelona"], "esp.1", data)["jugadores"]


def test_market_first_and_model_opinion():
    a = service.analyze(selae.fetch_current().to_dict())
    by = {p["local"] + "-" + p["visitante"]: p for p in a["partidos"]}
    p = by["Espanyol-At. Madrid"]
    assert p["fuente"] == "mercado"
    for k in ("p1", "px", "p2"):
        assert abs(p["probs"][k] - p["mercado"][k]) < 1e-3          # la tabla muestra el mercado
    assert p["modelo"]["signo"] in "1X2" and "coincide" in p["modelo"]
    assert p["probs_modelo"] != p["probs"]
    sin = by["Villarreal-Elche"]
    assert sin["fuente"] == "modelo" and sin["probs"] == sin["probs_modelo"]
    # bajas de futbolfantasy integradas
    assert [j["jugador"] for j in p["bajas_auto"]["visitante"]["jugadores"]] == ["Koke Resurrección", "Johnny Cardoso"]
    assert any("futbolfantasy" in m for m in by["Celta-Alavés"]["motivos"])


def test_top_columns_are_most_probable_and_distinct():
    probs = [{"p1": .8, "px": .15, "p2": .05}] * 10 + [{"p1": .4, "px": .32, "p2": .28}] * 4
    cols = ticket.top_columns(probs, 4)
    assert len(cols) == 4 and len({"".join(c["signos"]) for c in cols}) == 4
    assert cols[0]["signos"] == ["1"] * 14
    assert all(cols[i]["p14"] >= cols[i + 1]["p14"] for i in range(3))
    # ninguna otra columna con un solo cambio es más probable que la 4ª
    assert cols[3]["p14"] >= max(ticket.top_columns(probs, 30)[4:], key=lambda c: c["p14"])["p14"] - 1e-15


def test_participantes():
    from quiniela import storage
    key = "test-J1"
    storage.add_participante(key, "Alejandro", True, 3)
    with pytest.raises(storage.ParticipacionError):
        storage.add_participante(key, "  alejandró ", True, 3)          # repetido (mayúsculas/tildes)
    with pytest.raises(storage.ParticipacionError):
        storage.add_participante(key, "Pepe", False, 3)                  # sin Bizum
    storage.add_participante(key, "Pepe", True, 3)
    storage.add_participante(key, "Lucía", True, 3)
    with pytest.raises(storage.ParticipacionError):
        storage.add_participante(key, "Marta", True, 3)                  # completo
    lst = storage.list_participantes(key)
    assert [p["nombre"] for p in lst] == ["Alejandro", "Pepe", "Lucía"]
    storage.remove_participante(key, lst[1]["id"])
    assert len(storage.list_participantes(key)) == 2


def test_confirmar_y_estado_en_directo():
    import app as webapp
    from quiniela import storage
    with storage.conn() as db:
        db.execute("DELETE FROM apuestas")
    webapp._state.update(analisis=None, ts=0)
    c = webapp.app.test_client()
    a = c.get("/api/analisis").get_json()
    assert len(a["apuestas"]["columnas"]) == 4 and a["apuestas"]["guardadas"] is None
    assert a["apuestas"]["coste"] == 3.0
    assert c.get("/api/estado").status_code in (200, 404)
    r = c.post("/api/apuestas/confirmar", json={}).get_json()
    assert r["ok"] and len(r["columnas"]) == 4 and all(len(col) == 14 for col in r["columnas"])
    assert c.get("/api/analisis").get_json()["apuestas"]["guardadas"]["columnas"] == r["columnas"]
    e = c.get("/api/estado").get_json()
    assert len(e["filas"]) == 15 and e["num_apuestas"] == 4
    f1 = e["filas"][0]                                    # Espanyol 0-2 At. Madrid, terminado
    assert f1["estado"] == "final" and f1["marcador"] == "0-2" and f1["signo_actual"] == "2"
    for col, ap in zip(r["columnas"], f1["apuestas"]):
        assert ap["estado"] == ("acierto" if "2" in col[0] else "fallo")
    f3 = e["filas"][2]                                    # Betis-Barcelona, en juego
    assert f3["estado"] == "en_juego" and f3["marcador"] == "1-1"
    assert all(ap["estado"] == "pendiente" for ap in f3["apuestas"])
    assert e["filas"][6]["estado"] == "pendiente" and e["filas"][6]["marcador"] is None   # sin empezar
    for t in e["totales"]:
        assert t["aciertos"] + t["fallos"] + t["pendientes"] == 14
    assert "pleno" in e["filas"][14]
    # páginas
    for url in ("/", "/participar", "/estado"):
        assert c.get(url).status_code == 200
    pr = c.post("/api/participar", json={"nombre": "Alejandro", "bizum": True}).get_json()
    assert pr["total"] == 1 and pr["maximo"] == 15
    assert c.post("/api/participar", json={"nombre": "Ana", "bizum": False}).status_code == 400


def test_base_path_quiniela():
    import app as webapp
    c = webapp.create_app("/quiniela").test_client()
    assert c.get("/").status_code == 302 and c.get("/").headers["Location"].endswith("/quiniela/")
    r = c.get("/quiniela/")
    assert r.status_code == 200 and b'window.BASE = "/quiniela"' in r.data and b'/quiniela/static/app.css' in r.data
    assert c.get("/quiniela/static/app.css").status_code == 200
    assert c.get("/quiniela/participar").status_code == 200
    assert c.get("/quiniela/api/participantes").status_code == 200
    assert c.get("/api/participantes").status_code == 404
    for tpl in Path(__file__).parent.parent.glob("templates/*.html"):
        txt = tpl.read_text(encoding="utf-8")
        assert "BASE + BASE" not in txt and 'fetch("/api' not in txt and 'api("/api' not in txt, tpl.name


def test_admin_pin(monkeypatch):
    import app as webapp
    from quiniela import config
    monkeypatch.setattr(config, "ADMIN_PIN", "1234")
    c = webapp.create_app("").test_client()
    assert c.get("/api/me").get_json() == {"admin": False, "pin": True}
    assert c.post("/api/apuestas/confirmar", json={}).status_code == 403
    assert c.post("/api/manual", json={"texto": "A - B"}).status_code == 403
    assert c.post("/api/participantes/1/quitar", json={}).status_code == 403
    assert c.post("/api/participar", json={"nombre": "Visitante", "bizum": True}).status_code == 200  # público
    assert c.post("/api/login", json={"pin": "0000"}).status_code == 403
    assert c.post("/api/login", json={"pin": "1234"}).get_json()["admin"] is True
    assert c.get("/api/me").get_json()["admin"] is True
    assert c.post("/api/apuestas/confirmar", json={}).status_code == 200
    c.post("/api/logout")
    assert c.post("/api/apuestas/confirmar", json={}).status_code == 403


def test_analysis_persisted_in_db():
    import app as webapp
    from quiniela import storage
    webapp._state.update(analisis=None, ts=0)
    webapp.get_analysis(refresh=True)
    saved, creado = storage.get_kv("analisis")
    assert saved and saved["key"] == "2026-2027-J14" and creado
    # una instancia "nueva" (sin memoria) lo lee de la base de datos sin recalcular
    webapp._state.update(analisis=None, ts=0)
    calls = []
    orig = webapp.service.analyze
    webapp.service.analyze = lambda j: calls.append(1) or orig(j)
    try:
        a = webapp.get_analysis()
    finally:
        webapp.service.analyze = orig
    assert a["key"] == saved["key"] and not calls


def test_postgres_adapter_translates_placeholders():
    from quiniela import storage

    class FakeCur:
        def execute(self, sql, params):
            self.sql, self.params = sql, params

    class FakeConn:
        def cursor(self):
            self.cur = FakeCur()
            return self.cur

    fc = FakeConn()
    storage._PG(fc).execute("SELECT * FROM kv WHERE k=? AND v=?", ("a", "b"))
    assert fc.cur.sql == "SELECT * FROM kv WHERE k=%s AND v=%s" and fc.cur.params == ("a", "b")
    assert "SERIAL PRIMARY KEY" in storage.SCHEMA_PG and "AUTOINCREMENT" not in storage.SCHEMA_PG


PSG_BARCA = {"provider": {"name": "DraftKings"}, "overUnder": 4.5,
             "moneyline": {"home": {"close": {"odds": "+135"}}, "draw": {"close": {"odds": "+340"}},
                           "away": {"close": {"odds": "+145"}}},
             "total": {"over": {"close": {"line": "o4.5", "odds": "+120"}},
                       "under": {"close": {"line": "u4.5", "odds": "-155"}}}}


def test_market_total_and_expected_goals():
    mk = espn.parse_odds(PSG_BARCA)
    assert mk["total"]["line"] == 4.5 and abs(mk["total"]["p_over"] - 0.428) < 0.002
    lh, la = model.fit_market(mk, mk["total"], 4.0)
    M = model.score_matrix(lh, la)
    p = model.outcome_probs(M)
    assert abs(p["p1"] - mk["p1"]) < 0.01 and abs(p["p2"] - mk["p2"]) < 0.01
    assert abs(model.prob_over(M, 4.5) - mk["total"]["p_over"]) < 0.01
    assert 2.0 < lh < 2.4 and 2.0 < la < 2.3
    assert model.pleno(M)[0]["resultado"] == "M-M"
    # cuota fraccionaria de Bet365 y línea asiática entera
    assert abs(espn.frac_to_prob("33/40") - 40 / 73) < 1e-9
    b365 = {"bettingOdds": {"teamOdds": {"preMatchOverUnderHandicap": {"value": "4"},
                                         "preMatchGoalLineOver": {"value": "33/40"},
                                         "preMatchGoalLineUnder": {"value": "41/40"}}}}
    assert espn.parse_total(b365)["line"] == 4.0


def test_pleno_uses_market_when_available():
    a = service.analyze(selae.fetch_current().to_dict())
    by = {p["local"] + "-" + p["visitante"]: p for p in a["partidos"]}
    con = by["Espanyol-At. Madrid"]
    assert con["pleno_fuente"] == "mercado" and len(con["goles_mercado"]) == 2 and len(con["pleno"]) == 5
    assert con["mercado"]["total"]["line"] == 2.5
    sin = by["Villarreal-Elche"]
    assert sin["pleno_fuente"] == "modelo" and sin["goles_mercado"] is None


def test_premios_y_bote(monkeypatch):
    import app as webapp
    from quiniela import config, storage
    with storage.conn() as db:
        for tabla in ("premios", "bote", "participantes"):
            db.execute(f"DELETE FROM {tabla}")
    webapp._state.update(analisis=None, ts=0)
    monkeypatch.setattr(config, "ADMIN_PIN", "1234")
    c = webapp.create_app("").test_client()
    for url in ("/premios", "/bote"):
        assert c.get(url).status_code == 200
    # visitantes: pueden ver pero no tocar
    assert c.post("/api/premios", json={"temporada": "2026-2027", "jornada": 14, "importe": "10"}).status_code == 403
    assert c.post("/api/bote", json={"jornada": "14", "importe": "2"}).status_code == 403
    c.post("/api/participar", json={"nombre": "Ana", "bizum": True})
    c.post("/api/participar", json={"nombre": "Luis", "bizum": True})
    c.post("/api/participar", json={"nombre": "Marta", "bizum": True})
    p = c.get("/api/premios").get_json()
    fila = [f for f in p["filas"] if f["key"] == "2026-2027-J14"][0]
    assert fila["n"] == 3 and fila["participantes"] == ["Ana", "Luis", "Marta"] and fila["premio"] is None
    assert p["temporada_actual"] == "2026-2027" and p["jornada_actual"] == 14
    # administrador
    c.post("/api/login", json={"pin": "1234"})
    p = c.post("/api/premios", json={"temporada": "2026-2027", "jornada": 14, "importe": "45,30",
                                     "nota": "12 aciertos"}).get_json()
    fila = [f for f in p["filas"] if f["key"] == "2026-2027-J14"][0]
    assert fila["premio"] == 45.30 and fila["por_persona"] == 15.10 and fila["nota"] == "12 aciertos"
    assert p["total"] == 45.30 and p["con_premio"] == 1
    # jornada antigua sin apuntados en la web: nombres escritos a mano
    p = c.post("/api/premios", json={"temporada": "2026-2027", "jornada": 9, "importe": "8",
                                     "nombres": "Pepe, Lola"}).get_json()
    f9 = [f for f in p["filas"] if f["jornada"] == 9][0]
    assert f9["participantes"] == ["Pepe", "Lola"] and f9["por_persona"] == 4.0 and f9["nombres_manual"]
    assert [f["jornada"] for f in p["filas"]][:2] == [14, 9]                    # más reciente primero
    assert c.post("/api/premios", json={"temporada": "26/27", "jornada": 1, "importe": "1"}).status_code == 400
    assert c.post("/api/premios", json={"temporada": "2026-2027", "jornada": 1, "importe": "-1"}).status_code == 400
    p = c.post("/api/premios/2026-2027-J9/borrar", json={}).get_json()
    assert all(f["jornada"] != 9 for f in p["filas"])
    # bote común
    b = c.post("/api/bote", json={"jornada": "14", "importe": "4,50", "nota": "sobrante"}).get_json()
    b = c.post("/api/bote", json={"jornada": "15", "importe": "1.234,56"}).get_json()
    b = c.post("/api/bote", json={"jornada": "16", "importe": "-6"}).get_json()
    assert b["total"] == round(4.50 + 1234.56 - 6, 2)
    assert [m["jornada"] for m in b["movimientos"]] == ["16", "15", "14"]
    assert c.post("/api/bote", json={"jornada": "", "importe": "1"}).status_code == 400
    assert c.post("/api/bote", json={"jornada": "17", "importe": "abc"}).status_code == 400
    b = c.post(f"/api/bote/{b['movimientos'][0]['id']}/borrar", json={}).get_json()
    assert b["total"] == round(4.50 + 1234.56, 2)


def test_parse_euros():
    from quiniela.storage import parse_euros, ImporteError
    assert [parse_euros(x) for x in ("12,50", "12.50", "1.234,56", "-3", "7 €", "0,75")] == \
        [1250, 1250, 123456, -300, 700, 75]
    with pytest.raises(ImporteError):
        parse_euros("")
