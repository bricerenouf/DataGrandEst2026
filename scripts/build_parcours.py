"""Génère parcours-rivieres.html : récit défilant sur cinq rivières du Grand Est.

Pour chaque rivière : tracé, position de chaque virage (côté gauche/droite), passage
le plus sinueux (fenêtre de WINDOW_KM la plus riche en virages, avec la commune la
plus proche), kilomètres par département et affluents directs.
Prérequis : output/virages_cours_eau.csv et output/affluents_cours_eau.csv.
"""
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_carte_cours_eau import DEPTS, REGION, SRC, Q, encode, order_parts, render, simplify  # noqa: E402
from compter_virages import find_bends, project  # noqa: E402

COMMUNES = ROOT / "data" / "communes-grand-est.geojson"
TEMPLATE = ROOT / "scripts" / "parcours_template.html"
OUT = ROOT / "parcours-rivieres.html"
OUTPUT = ROOT / "output"

# D'ouest en est
RIVIERES = ["l'Aisne", "la Marne", "la Meuse", "la Moselle", "l'Ill"]
WINDOW_KM = 5.0
# Mêmes réglages que compter_virages.py
BEND_ARGS = dict(tol=10.0, seuil=45.0, neutre=5.0, droite=10.0, ecart=0.02)


def read_csv(name):
    with (OUTPUT / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


def km(line):
    return sum(math.hypot((b[0] - a[0]) * 111.32 * math.cos(math.radians(a[1])), (b[1] - a[1]) * 110.57)
               for a, b in zip(line, line[1:]))


def polygons(geom):
    return geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]


def in_ring(x, y, ring):
    inside = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def in_polys(x, y, polys):
    return any(in_ring(x, y, poly[0]) and not any(in_ring(x, y, h) for h in poly[1:]) for poly in polys)


class Areas:
    """Recherche du polygone (département, commune) contenant un point."""

    def __init__(self, features):
        self.items = []
        for f in features:
            polys = polygons(f["geometry"])
            xs = [p[0] for poly in polys for p in poly[0]]
            ys = [p[1] for poly in polys for p in poly[0]]
            self.items.append((min(xs), min(ys), max(xs), max(ys), polys, f["properties"]["nom"]))

    def find(self, x, y):
        for x0, y0, x1, y1, polys, nom in self.items:
            if x0 <= x <= x1 and y0 <= y <= y1 and in_polys(x, y, polys):
                return nom
        return None


def densest_window(parts_bends):
    """Fenêtre de WINDOW_KM la plus riche en virages, cherchée dans chaque partie du tracé."""
    best = None
    for part, bends in parts_bends:
        cum = [0.0]
        for a, b in zip(part, part[1:]):
            cum.append(cum[-1] + km([a, b]))
        pos = [(cum[i], i, side) for i, side in bends]
        j = 0
        for k in range(len(pos)):
            while pos[k][0] - pos[j][0] > WINDOW_KM:
                j += 1
            if best is None or k - j + 1 > best[0]:
                best = (k - j + 1, [part[p[1]] for p in pos[j:k + 1]])
    return best


def main():
    virages = {r["CdOH"]: r for r in read_csv("virages_cours_eau.csv")}
    affl_rows = read_csv("affluents_cours_eau.csv")
    affluents = {r["CdOH"]: r for r in affl_rows}
    src = [f for f in json.loads(SRC.read_text())["features"] if f["geometry"]]
    by_code = {f["properties"]["CdOH"]: f for f in src}
    depts = Areas(json.loads(DEPTS.read_text())["features"])
    communes = Areas(json.loads(COMMUNES.read_text())["features"])

    def lines_of(f):
        g = f["geometry"]
        return g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]

    out = []
    for name in RIVIERES:
        main_f = max((f for f in src if f["properties"]["TopoOH"] == name), key=lambda f: sum(km(l) for l in lines_of(f)))
        code = main_f["properties"]["CdOH"]
        parts = [l for l in lines_of(main_f) if len(l) >= 2]

        bends, parts_bends = [], []
        for part in parts:
            found = find_bends(project(part), **BEND_ARGS)
            parts_bends.append((part, found))
            bends += [(part[i], side) for i, side in found]
        n_win, win_pts = densest_window(parts_bends)
        cx = sum(p[0] for p in win_pts) / len(win_pts)
        cy = sum(p[1] for p in win_pts) / len(win_pts)

        dept_km = {}
        for part in parts:
            for a, b in zip(part, part[1:]):
                d = depts.find((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                if d:
                    dept_km[d] = dept_km.get(d, 0) + km([a, b])

        tribs = []
        for r in affl_rows:
            if r["se_jette_dans_CdOH"] != code:
                continue
            f = by_code[r["CdOH"]]
            v = virages.get(r["CdOH"], {})
            ordered = order_parts(lines_of(f))
            tribs.append({
                "n": r["nom"] or "Sans nom",
                "k": round(sum(km(l) for l in lines_of(f)), 1),
                "v": int(v.get("nb_virages", 0)),
                "a": int(r["nb_affluents_directs"]),
                "at": int(r["nb_affluents_total"]),
                "g": [encode(simplify(l, 20)) for l, _, _ in ordered],
                "rp": [[round(p0, 4), round(p1, 4)] for _, p0, p1 in ordered],
            })
        tribs.sort(key=lambda t: -t["k"])

        v, a = virages[code], affluents[code]
        out.append({
            "n": name,
            "k": float(v["longueur_km"]),
            "v": int(v["nb_virages"]),
            "vk": float(v["virages_par_km"]),
            "a": int(a["nb_affluents_directs"]),
            "at": int(a["nb_affluents_total"]),
            "recv": a["se_jette_dans_nom"],
            "g": [encode(simplify(l, 10)) for l, _, _ in order_parts(parts)],
            "rp": [[round(p0, 4), round(p1, 4)] for _, p0, p1 in order_parts(parts)],
            "b": [[round(p[0] * Q), round(p[1] * Q), side] for p, side in bends],
            "w": {"n": n_win, "km": WINDOW_KM, "commune": communes.find(cx, cy),
                  "pts": [[round(p[0] * Q), round(p[1] * Q)] for p in win_pts]},
            "depts": sorted(([d, round(x)] for d, x in dept_km.items() if x >= 1), key=lambda t: -t[1]),
            "tribs": tribs,
        })
        print(f"{name}: {len(bends)} virages (CSV {v['nb_virages']}), {len(tribs)} affluents, "
              f"fenêtre {n_win} virages près de {out[-1]['w']['commune']}, {out[-1]['depts']}")

    g = json.loads(REGION.read_text())["geometry"]
    region = [encode(simplify(r, 80)) for poly in polygons(g) for r in poly]
    dep_out = [[encode(simplify(r, 80)) for poly in polygons(f["geometry"]) for r in poly]
               for f in json.loads(DEPTS.read_text())["features"]]
    n_total = len(src)

    data = json.dumps({"q": Q, "total": n_total, "rivers": out, "region": region, "depts": dep_out},
                      ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(render(TEMPLATE, data))
    print(f"{OUT.stat().st_size / 1e6:.1f} Mo -> {OUT}")


if __name__ == "__main__":
    main()
