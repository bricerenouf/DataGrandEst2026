"""Compte les affluents de chaque cours d'eau.

Les tracés sont orientés de l'amont vers l'aval : le dernier point de chaque partie
est une embouchure possible. Le cours d'eau B est un affluent de A quand une
embouchure de B est à moins de TOL_M de A et que A passe par ce point : A ne fait
pas que s'y arrêter (B et A se jettent alors ailleurs) ni qu'en partir (A est alors
un bras ou une dérivation). Un bras dont le départ et l'arrivée sont tous
deux sur A n'est pas compté. Si plusieurs cours d'eau sont candidats, on garde le
plus proche puis le plus long.

  nb_affluents_directs : cours d'eau qui se jettent directement dans A
  nb_affluents_total   : tout le réseau amont (affluents des affluents, etc.)
"""
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_carte_cours_eau import KX, KY, SRC, simplify  # noqa: E402

OUT = ROOT / "output" / "affluents_cours_eau.csv"
TOL_M = 10.0
CELL = 500.0  # taille des cases de l'index spatial, en mètres


def seg_dist(p, a, b):
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    d2 = dx * dx + dy * dy
    t = 0 if d2 == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / d2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def near(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1]) <= TOL_M


def main():
    feats = []
    for f in json.loads(SRC.read_text())["features"]:
        g = f["geometry"]
        if not g:
            continue
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        parts = [simplify([(x * KX, y * KY) for x, y in line], 2.0, projected=True) for line in lines]
        parts = [p for p in parts if len(p) >= 2]
        feats.append({"c": f["properties"].get("CdOH"), "n": f["properties"].get("TopoOH") or "", "parts": parts,
                      "km": sum(math.hypot(b[0] - a[0], b[1] - a[1]) for p in parts for a, b in zip(p, p[1:])) / 1000})

    # Index spatial : case -> segments (feature, partie, indice)
    grid = defaultdict(list)
    for fi, f in enumerate(feats):
        for pi, part in enumerate(f["parts"]):
            for si, (a, b) in enumerate(zip(part, part[1:])):
                x0, x1 = sorted((a[0], b[0]))
                y0, y1 = sorted((a[1], b[1]))
                for cx in range(int((x0 - TOL_M) // CELL), int((x1 + TOL_M) // CELL) + 1):
                    for cy in range(int((y0 - TOL_M) // CELL), int((y1 + TOL_M) // CELL) + 1):
                        grid[(cx, cy)].append((fi, pi, si))

    def touching(p, exclude):
        """{feature: (distance, le tracé passe par p)} pour les tracés à moins de TOL_M de p."""
        found = {}
        for fi, pi, si in grid.get((int(p[0] // CELL), int(p[1] // CELL)), ()):
            if fi == exclude:
                continue
            part = feats[fi]["parts"][pi]
            d = seg_dist(p, part[si], part[si + 1])
            if d <= TOL_M:
                h = found.setdefault(fi, {"d": d, "interior": False, "start": False, "end": False})
                h["d"] = min(h["d"], d)
                starts, ends = near(p, part[0]), near(p, part[-1])
                h["start"] |= starts
                h["end"] |= ends
                h["interior"] |= not starts and not ends
        # passe par p : p est à l'intérieur d'une partie, ou une partie y arrive et une autre en repart
        return {fi: (h["d"], h["interior"] or (h["start"] and h["end"])) for fi, h in found.items()}

    parent = [None] * len(feats)
    for bi, f in enumerate(feats):
        cands = {}
        for part in f["parts"]:
            mouth = part[-1]
            # extrémité interne d'un tracé en plusieurs parties : le cours d'eau continue
            if any(near(mouth, q[0]) for q in f["parts"] if q is not part):
                continue
            src_hits = touching(part[0], bi)
            for ai, (d, passes) in touching(mouth, bi).items():
                if passes and ai not in src_hits:
                    cands[ai] = min(d, cands.get(ai, d))
        if cands:
            parent[bi] = min(cands, key=lambda ai: (round(cands[ai], 1), -feats[ai]["km"]))

    children = defaultdict(list)
    for bi, ai in enumerate(parent):
        if ai is not None:
            children[ai].append(bi)

    total = {}

    def network(ai):
        # parcours itératif du réseau amont, protégé contre les boucles (canaux, défluences)
        seen, stack = {ai}, list(children[ai])
        while stack:
            b = stack.pop()
            if b in seen:
                continue
            seen.add(b)
            stack.extend(children[b])
        return len(seen) - 1

    for ai in range(len(feats)):
        total[ai] = network(ai) if children[ai] else 0

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["CdOH", "nom", "se_jette_dans_CdOH", "se_jette_dans_nom", "nb_affluents_directs", "nb_affluents_total"])
        for i in sorted(range(len(feats)), key=lambda i: -total[i]):
            p = parent[i]
            w.writerow([feats[i]["c"], feats[i]["n"], feats[p]["c"] if p is not None else "",
                        feats[p]["n"] if p is not None else "", len(children[i]), total[i]])
    n_conf = sum(p is not None for p in parent)
    print(f"{len(feats)} cours d'eau, {n_conf} rattachés à un cours d'eau receveur -> {OUT}")


if __name__ == "__main__":
    main()
