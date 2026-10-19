"""Configuración central. Todo se puede sobrescribir con variables de entorno."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
IS_VERCEL = bool(os.environ.get("VERCEL"))
# En Vercel solo se puede escribir en /tmp (temporal): ahí va la caché de descargas
_DEFAULT_DATA = Path("/tmp/quiniela") if IS_VERCEL else BASE_DIR
CACHE_DIR = Path(os.environ.get("QUINIELA_CACHE_DIR", _DEFAULT_DATA / "cache"))
DB_PATH = Path(os.environ.get("QUINIELA_DB", _DEFAULT_DATA / "quiniela.db"))

# --- Web ---------------------------------------------------------------------
# Ruta bajo la que vive la app: "" en local, "/quiniela" en chaochaito.com/quiniela
BASE_PATH = os.environ.get("QUINIELA_BASE_PATH", "").rstrip("/")
SECRET_KEY = os.environ.get("QUINIELA_SECRET_KEY") or os.urandom(24).hex()
ANALYSIS_TTL = int(os.environ.get("QUINIELA_ANALYSIS_TTL", 3600 if IS_VERCEL else 600))

# --- Fuentes -----------------------------------------------------------------
SELAE_BASE = "https://www.loteriasyapuestas.es/servicios"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"

# Ligas españolas (se usan como plan B si no se encuentra el partido en el calendario)
LEAGUES_MEN = ["esp.1", "esp.2"]          # LALIGA EA Sports, LALIGA Hypermotion
LEAGUES_WOMEN = ["esp.w.1"]               # Liga F

# Competiciones donde se busca cada partido de la quiniela (marcador de ESPN del día)
COMPETITIONS_MEN = [
    "esp.1", "esp.2", "esp.copa_del_rey", "esp.super_cup",
    "uefa.champions", "uefa.europa", "uefa.europa.conf", "uefa.super_cup",
    "eng.1", "ita.1", "ger.1", "fra.1", "por.1", "ned.1",
    # selecciones
    "fifa.world", "uefa.euro", "uefa.nations", "fifa.worldq.uefa", "uefa.euroq", "fifa.friendly",
]
COMPETITIONS_WOMEN = ["esp.w.1", "uefa.wchampions", "esp.w.copa", "fifa.wwc", "uefa.weuro", "uefa.wnations",
                      "fifa.friendly.w"]
NATIONAL_COMPS = {"fifa.world", "uefa.euro", "uefa.nations", "fifa.worldq.uefa", "uefa.euroq", "fifa.friendly",
                  "fifa.wwc", "uefa.weuro", "uefa.wnations", "fifa.friendly.w"}

LEAGUE_NAMES = {
    "esp.1": "Primera", "esp.2": "Segunda", "esp.w.1": "Liga F", "esp.copa_del_rey": "Copa del Rey",
    "esp.super_cup": "Supercopa", "esp.w.copa": "Copa de la Reina",
    "uefa.champions": "Champions", "uefa.europa": "Europa League", "uefa.europa.conf": "Conference",
    "uefa.super_cup": "Supercopa UEFA", "uefa.wchampions": "Champions fem.",
    "eng.1": "Premier", "ita.1": "Serie A", "ger.1": "Bundesliga", "fra.1": "Ligue 1",
    "por.1": "Liga Portugal", "ned.1": "Eredivisie",
    "fifa.world": "Mundial", "uefa.euro": "Eurocopa", "uefa.nations": "Nations League",
    "fifa.worldq.uefa": "Clasif. Mundial", "uefa.euroq": "Clasif. Eurocopa", "fifa.friendly": "Amistoso",
    "fifa.wwc": "Mundial fem.", "uefa.weuro": "Eurocopa fem.", "uefa.wnations": "Nations fem.",
    "fifa.friendly.w": "Amistoso fem.",
}

# Nivel relativo de cada liga doméstica (≈ coeficientes UEFA; LaLiga ≈ 1).
# Sirve para comparar equipos de ligas distintas en competiciones europeas.
LEAGUE_STRENGTH = {
    "eng.1": 1.00, "esp.1": 0.97, "ita.1": 0.92, "ger.1": 0.92, "fra.1": 0.85,
    "por.1": 0.78, "ned.1": 0.76, "bel.1": 0.70, "tur.1": 0.68, "eng.2": 0.68,
    "esp.2": 0.62, "ita.2": 0.60, "ger.2": 0.62, "fra.2": 0.55, "cze.1": 0.62, "aut.1": 0.62,
    "gre.1": 0.62, "sco.1": 0.60, "sui.1": 0.60, "den.1": 0.60, "ukr.1": 0.58, "nor.1": 0.55,
    "swe.1": 0.52, "pol.1": 0.55, "srb.1": 0.55, "cro.1": 0.55, "isr.1": 0.50, "cyp.1": 0.50,
    "esp.w.1": 0.95, "eng.w.1": 1.00, "fra.w.1": 0.90, "ger.w.1": 0.90, "ita.w.1": 0.75,
}
DEFAULT_STRENGTH = 0.50
STRENGTH_EXPONENT = 0.5      # cuánto se nota la diferencia de nivel entre ligas

HTTP_TIMEOUT = float(os.environ.get("QUINIELA_HTTP_TIMEOUT", 15))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)

# Tiempos de caché (segundos)
TTL_QUINIELA = 30 * 60
TTL_TEAMS = 7 * 24 * 3600
TTL_SCHEDULE_CURRENT = 3 * 3600
TTL_SCHEDULE_PAST = 30 * 24 * 3600
TTL_SCOREBOARD = 30 * 60
TTL_SUMMARY_FINISHED = 365 * 24 * 3600
TTL_SUMMARY_PENDING = 30 * 60

# --- Modelo ------------------------------------------------------------------
H2H_YEARS = 3                 # temporadas hacia atrás para enfrentamientos directos
H2H_WEIGHT = 20.0             # cuántos "partidos virtuales" pesa el modelo frente al H2H
# Peso de las cuotas cuando existen: 1.0 = el signo que se muestra es el del mercado y el modelo
# queda como "segunda opinión" en el detalle de cada partido. Sin cuotas, manda el modelo.
MARKET_WEIGHT = float(os.environ.get("QUINIELA_MARKET_WEIGHT", 1.0))
PRIOR_GAMES = 6.0             # encogimiento de ratings hacia la media de liga
PREV_SEASON_WEIGHT = 0.35     # peso de la temporada anterior (sobre todo al inicio)
FORM_IMPACT = 0.10            # ±10% de goles esperados según la forma reciente
ABSENCE_IMPACT = 0.06         # -6% de ataque por cada baja importante marcada a mano
AUTO_ABSENCE_UNIT = 0.5       # una baja leída de futbolfantasy cuenta como media baja "importante"
AUTO_ABSENCE_CAP = 5.0        # máximo de bajas automáticas que se tienen en cuenta por equipo
FF_BASE = "https://www.futbolfantasy.com"
TTL_BAJAS = 3 * 3600
RED_CARD_IMPACT = 0.04        # -4% si hubo roja en el último partido (posible sanción)
DIXON_COLES_RHO = -0.10       # corrige la infraestimación de empates en Poisson
CARDS_LAST_N = int(os.environ.get("QUINIELA_CARDS_LAST_N", 3))
MAX_GOALS = 10

PRICE_PER_BET = float(os.environ.get("QUINIELA_PRICE", 0.75))  # € por apuesta
NUM_APUESTAS = int(os.environ.get("QUINIELA_APUESTAS", 4))       # columnas sencillas que jugamos
MAX_PARTICIPANTES = int(os.environ.get("QUINIELA_MAX_PARTICIPANTES", 15))
ADMIN_PIN = os.environ.get("QUINIELA_ADMIN_PIN", "")             # para quitar participantes (vacío = sin PIN en local)
TTL_LIVE = 60                                                    # segundos de caché del marcador en directo
