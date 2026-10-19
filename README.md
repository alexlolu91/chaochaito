# ChaoChaito Quiniela

Herramienta local que lee **la quiniela vigente** de Loterías y Apuestas del Estado y propone el signo
de cada partido (1, X, 2), las probabilidades, dónde poner dobles/triples y el **Pleno al 15**.

Para publicarla en internet (chaochaito.com/quiniela) mira **DESPLIEGUE.md**.

## Cómo arrancarla

1. Instala **Python 3.10 o superior** (python.org; en Windows marca "Add Python to PATH").
2. Descomprime la carpeta y haz doble clic en:
   - **Windows:** `iniciar.bat`
   - **Mac:** `iniciar.command` (si macOS avisa, clic derecho → Abrir)
   - **Linux:** `bash iniciar.sh`
3. Se abre el navegador en <http://127.0.0.1:5000>. La primera carga tarda 1–2 minutos
   (descarga 4 temporadas de resultados de cada equipo); después va desde la caché.

Manual: `pip install -r requirements.txt` y `python app.py`.

## De dónde salen los datos

| Dato | Fuente |
|---|---|
| Quiniela vigente (15 partidos, jornada, cierre, bote) | loteriasyapuestas.es (`/servicios/proximosv3` y `/servicios/fechav3`) |
| Resultados, goles, forma, local/visitante | API pública de ESPN — Primera (`esp.1`), Segunda (`esp.2`) y Liga F (`esp.w.1`) |
| Enfrentamientos de los últimos 3 años | Calendarios de ESPN de la temporada actual y las 3 anteriores |
| Tarjetas (últimos 3 partidos) | Estadísticas de partido de ESPN |
| Cuotas de mercado (cuando hay) | Cuotas que publica ESPN (DraftKings) |
| Lesionados, dudas y sancionados | futbolfantasy.com (LaLiga, LaLiga 2, Champions, Europa League, Premier, Serie A) |
| Bajas extra | Ajuste manual +/− en el detalle de cada partido |

La quiniela cambia según la jornada: Primera, Segunda, Liga F (los equipos con `(f)`), Copa del Rey,
**Champions, Europa League, Conference** y **selecciones** (Mundial, Eurocopa, clasificatorios, Nations
League, amistosos). La app busca cada partido en el calendario de ESPN de ese día en todas esas
competiciones y entiende nombres en español ("Oporto", "Nápoles", "Alemania"...).

Para equipos extranjeros usa su liga doméstica (Porto → Liga Portugal) más la competición europea, y
los últimos 5 partidos de cada equipo en cualquier competición. Para comparar equipos de ligas
distintas aplica un **nivel de liga** (≈ coeficientes UEFA, editable en `config.py`).

**Sobre bajas:** se leen de futbolfantasy (cada 3 horas). Una baja o sanción cuenta como media baja
"clave" (−3% de goles), una duda la mitad, con un máximo por equipo. Las sanciones solo se aplican en
su competición (una roja en Liga no cuenta en Champions). El ajuste manual sirve para bajas que la web
no recoja.

**Si Loterías bloquea la consulta** (a veces responde 403 a programas que no son un navegador),
usa **"Introducir a mano"**: una línea por partido `Local - Visitante`, con `(f)` en los de Liga F.

## Cómo calcula el pronóstico

1. **Ataque y defensa** de cada equipo: goles a favor/en contra frente a lo esperado según jugara en
   casa o fuera, temporada actual (peso 1) y anterior (peso 0,35), encogido hacia la media de la liga.
2. **Forma** de los 5 últimos partidos: ±10% de goles esperados.
3. **Bajas** marcadas y **roja en el último partido** (posible sanción).
4. **Poisson con corrección Dixon-Coles** → matriz de marcadores → probabilidades 1/X/2 y Pleno al 15.
5. **H2H 3 años**: mezcla bayesiana (el modelo pesa como 20 partidos frente a los enfrentamientos reales).
6. **Mercado primero**: si hay cuotas, el signo y las probabilidades de la tabla son los del mercado.
   Al desplegar un partido se ve la **opinión del modelo** y si coincide o discrepa (las discrepancias
   son buenos candidatos a doble). Sin cuotas, la tabla muestra el modelo. Se cambia con
   `QUINIELA_MARKET_WEIGHT` (1 = solo mercado).

El **boleto** elige, para el número de dobles y triples que pidas, la combinación que maximiza la
probabilidad de 14 aciertos (programación dinámica sobre los 14 partidos), y muestra coste,
aciertos esperados y probabilidad de pleno.

Ajustes (pesos, precio por apuesta, etc.) en `quiniela/config.py` o por variables de entorno
(`QUINIELA_MARKET_WEIGHT`, `QUINIELA_PRICE`…).

## Las 4 apuestas, participantes y estado

- **Pronóstico** (`/`): además del análisis, propone **4 apuestas sencillas** (3 €): las 4 combinaciones
  de signos más probables. La 1 lleva el favorito en todos los partidos; las otras cambian los más
  dudosos (en naranja). El Pleno al 15 es el resultado más probable. Pulsa **Confirmar estas apuestas**
  para fijarlas (se pueden volver a confirmar mientras no cierre la jornada).
- **¿Quieres participar?** (`/participar`): nombre + casilla «Bizum hecho». El contador X/15 sale en todas
  las páginas y se reinicia con cada jornada. El máximo se cambia con `QUINIELA_MAX_PARTICIPANTES`.
  Para que solo tú puedas quitar a alguien, define `QUINIELA_ADMIN_PIN` (en local no hace falta).
- **Estado de mi Quiniela** (`/estado`): los 15 partidos con el marcador en directo de ESPN. Amarillo =
  pendiente o en juego, verde = acertado, rojo = fallado. «Todas» muestra las 4 apuestas a la vez; al
  elegir una apuesta se colorea la fila entera. Se actualiza solo cada minuto.

- **Premios** (`/premios`): cada jornada con sus participantes (nombres y número), el premio ganado
  y lo que toca por persona. El administrador apunta el importe; para jornadas antiguas sin apuntados en la
  web se pueden escribir los nombres a mano.
- **Bote común** (`/bote`): el dinero que sobra de cada jornada. El administrador añade movimientos
  (jornada + importe; en negativo para sacar dinero) y se ve el total acumulado.

La casilla de Bizum es de confianza: la app no comprueba pagos.

## Guardar y medir aciertos

"Guardar pronóstico" guarda la jornada y tu boleto en `quiniela.db` (SQLite). Cuando se hayan jugado
los partidos, pulsa **Evaluar** en el historial: compara con los resultados finales y da el % de
aciertos del signo recomendado y del boleto. Es la base para la futura web pública de seguimiento.

## Estructura

```
app.py                 servidor Flask y API (/api/analisis, /api/boleto, /api/guardar, /api/evaluar…)
quiniela/selae.py      lectura de la quiniela vigente
quiniela/espn.py       cliente ESPN (equipos, calendarios, marcadores, cuotas, tarjetas)
quiniela/teams.py      empareja "At. Madrid", "Celta"… con los equipos de ESPN
quiniela/model.py      modelo de probabilidades
quiniela/ticket.py     optimizador de dobles/triples
quiniela/service.py    orquestación y evaluación de aciertos
quiniela/storage.py    SQLite
templates/index.html   interfaz
tests/                 pruebas con un simulador de las APIs (python -m pytest)
```

Juego responsable: la quiniela es un juego de azar; el modelo ayuda a ordenar la información,
no garantiza premios.
