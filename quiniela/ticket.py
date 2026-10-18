"""Reparto óptimo de dobles y triples para maximizar la probabilidad de 14 aciertos."""
from __future__ import annotations

import math

from .model import SIGNS


def _covers(p):
    """Para un partido: (signos, prob cubierta) con 1, 2 y 3 signos."""
    order = sorted(zip(SIGNS, (p["p1"], p["px"], p["p2"])), key=lambda x: -x[1])
    out = []
    for k in (1, 2, 3):
        chosen = sorted((s for s, _ in order[:k]), key=SIGNS.index)
        out.append(("".join(chosen), sum(v for _, v in order[:k])))
    return out


def optimize(probs, dobles=0, triples=0):
    """`probs`: lista de 14 dicts {p1,px,p2}. Devuelve la columna óptima con exactamente
    `dobles` dobles y `triples` triples (programación dinámica sobre los 14 partidos)."""
    n = len(probs)
    dobles = max(0, min(dobles, n))
    triples = max(0, min(triples, n - dobles))
    cov = [_covers(p) for p in probs]
    NEG = -1e18
    # dp[i][d][t] = mejor suma de log-prob con los i primeros partidos
    dp = [[[NEG] * (triples + 1) for _ in range(dobles + 1)] for _ in range(n + 1)]
    choice = [[[None] * (triples + 1) for _ in range(dobles + 1)] for _ in range(n + 1)]
    dp[0][0][0] = 0.0
    for i in range(n):
        for d in range(dobles + 1):
            for t in range(triples + 1):
                cur = dp[i][d][t]
                if cur == NEG:
                    continue
                for k, (dd, tt) in enumerate(((0, 0), (1, 0), (0, 1))):
                    nd, nt = d + dd, t + tt
                    if nd > dobles or nt > triples:
                        continue
                    val = cur + math.log(max(cov[i][k][1], 1e-12))
                    if val > dp[i + 1][nd][nt]:
                        dp[i + 1][nd][nt] = val
                        choice[i + 1][nd][nt] = k
    d, t = dobles, triples
    picks = [None] * n
    for i in range(n, 0, -1):
        k = choice[i][d][t]
        picks[i - 1] = k
        if k == 1:
            d -= 1
        elif k == 2:
            t -= 1
    signos = [cov[i][k][0] for i, k in enumerate(picks)]
    covered = [cov[i][k][1] for i, k in enumerate(picks)]
    apuestas = 2 ** dobles * 3 ** triples
    return {
        "signos": signos,
        "dobles": dobles,
        "triples": triples,
        "apuestas": apuestas,
        "p14": math.exp(sum(math.log(max(c, 1e-12)) for c in covered)),
        "aciertos_esperados": sum(covered),
        "p_cubierta": covered,
    }


PRESETS = [
    {"nombre": "Sencilla", "dobles": 0, "triples": 0},
    {"nombre": "4 dobles", "dobles": 4, "triples": 0},
    {"nombre": "2 triples + 3 dobles", "dobles": 3, "triples": 2},
    {"nombre": "7 dobles", "dobles": 7, "triples": 0},
    {"nombre": "4 triples + 4 dobles", "dobles": 4, "triples": 4},
]


def top_columns(probs, k=4):
    """Las `k` columnas sencillas (14 signos) más probables y distintas entre sí.

    Como las columnas son excluyentes, P(alguna acierta 14) = suma de sus probabilidades,
    así que elegir las k combinaciones más probables es lo óptimo para el pleno de 14.
    Búsqueda best-first: se parte de la columna con el signo favorito en cada partido y se
    van probando cambios al siguiente signo más probable."""
    import heapq

    opts = []
    for p in probs:
        order = sorted(zip(SIGNS, (p["p1"], p["px"], p["p2"])), key=lambda x: -x[1])
        opts.append([(s, max(v, 1e-12)) for s, v in order])

    def logp(idx):
        return sum(math.log(opts[i][j][1]) for i, j in enumerate(idx))

    start = tuple(0 for _ in opts)
    heap = [(-logp(start), start)]
    seen = {start}
    out = []
    while heap and len(out) < k:
        neg, idx = heapq.heappop(heap)
        signos = [opts[i][j][0] for i, j in enumerate(idx)]
        hits = sum(opts[i][j][1] for i, j in enumerate(idx))
        cambios = [i for i, j in enumerate(idx) if j > 0]
        out.append({"signos": signos, "p14": math.exp(-neg), "aciertos_esperados": hits, "cambios": cambios})
        for i in range(len(idx)):
            if idx[i] + 1 < len(opts[i]):
                nxt = idx[:i] + (idx[i] + 1,) + idx[i + 1:]
                if nxt not in seen:
                    seen.add(nxt)
                    heapq.heappush(heap, (-logp(nxt), nxt))
    return out
