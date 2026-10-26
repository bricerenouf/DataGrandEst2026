"""Génère carte-sombre.html : les cours d'eau du Grand Est en bleu sur fond sombre.

Contour de la région avec un halo blanc vers l'extérieur, départements survolables,
20 grandes villes. Les rivières de 30 km et plus s'élargissent de la source vers
l'embouchure, d'autant plus que leur réseau d'affluents est grand.
Prérequis : output/affluents_cours_eau.csv (compter_affluents.py).
"""
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_carte_cours_eau import DEPTS, Q, REGION, SRC, encode, length_km, order_parts, simplify  # noqa: E402

COMMUNES = ROOT / "data" / "communes-grand-est.geojson"
AFFLUENTS = ROOT / "output" / "affluents_cours_eau.csv"
TEMPLATE = ROOT / "scripts" / "carte_sombre_template.html"
OUT = ROOT / "carte-sombre.html"

TAPER_KM = 30          # à partir de cette longueur : largeur croissante vers l'aval
LABELLED = ["le Rhin", "la Moselle", "la Meuse", "la Marne", "la Seine", "l'Aube", "l'Aisne", "l'Ill",
            "la Meurthe", "la Sarre", "la Saône"]
# Les 20 communes les plus peuplées de la région (hors communes de banlieue collées à une
# grande ville), désignées par leur nom et leur département. Aucune population n'est
# ajoutée aux données : seul le choix des noms vient de là.
VILLES = [("Strasbourg", "67"), ("Reims", "51"), ("Metz", "57"), ("Mulhouse", "68"), ("Nancy", "54"),
          ("Colmar", "68"), ("Troyes", "10"), ("Charleville-Mézières", "08"), ("Châlons-en-Champagne", "51"),
          ("Thionville", "57"), ("Haguenau", "67"), ("Épinal", "88"), ("Saint-Dizier", "52"), ("Épernay", "51"),
          ("Saint-Louis", "68"), ("Chaumont", "52"), ("Forbach", "57"), ("Sarreguemines", "57"),
          ("Saint-Dié-des-Vosges", "88"), ("Sélestat", "67")]


def rings_of(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [ring for poly in polys for ring in poly]


def centroid(ring):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        f = x0 * y1 - x1 * y0
        a += f
        cx += (x0 + x1) * f
        cy += (y0 + y1) * f
    return cx / (3 * a), cy / (3 * a)


def main():
    with AFFLUENTS.open(encoding="utf-8") as fh:
        network = {r["CdOH"]: int(r["nb_affluents_total"]) for r in csv.DictReader(fh, delimiter=";")}

    rivers, labels = [], {}
    for f in json.loads(SRC.read_text())["features"]:
        g = f["geometry"]
        if not g:
            continue
        lines = [l for l in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]) if len(l) >= 2]
        km = length_km(lines)
        name = f["properties"].get("TopoOH") or ""
        at = network.get(f["properties"]["CdOH"], 0)
        if km >= TAPER_KM:
            ordered = order_parts(lines)
            rivers.append({"k": round(km), "at": at, "g": [encode(simplify(l, 40)) for l, _, _ in ordered],
                           "rp": [[round(a, 4), round(b, 4)] for _, a, b in ordered]})
        else:
            rivers.append({"k": round(km, 1), "g": [encode(simplify(l, 60)) for l in lines]})
        # Étiquette : la plus longue partie, très simplifiée, pour un texte qui suit la rivière
        if name in LABELLED and km > labels.get(name, (0,))[0]:
            longest = max(lines, key=lambda l: length_km([l]))
            labels[name] = (km, encode(simplify(longest, 3000)))
    # Les plus longues en dernier : elles sont dessinées par-dessus
    rivers.sort(key=lambda r: r["k"])

    by_key = {}
    for f in json.loads(COMMUNES.read_text())["features"]:
        p = f["properties"]
        by_key[(p["nom"], p["code"][:2])] = f
    cities = []
    for rank, (nom, dep) in enumerate(VILLES):
        f = by_key.get((nom, dep))
        if f is None:
            sys.exit(f"Commune introuvable dans {COMMUNES.name} : {nom} ({dep})")
        ring = max(rings_of(f["geometry"]), key=len)
        lon, lat = centroid(ring)
        cities.append({"n": nom, "lon": round(lon, 4), "lat": round(lat, 4), "r": rank})

    region = json.loads(REGION.read_text())["geometry"]
    depts = [{"nom": f["properties"]["nom"], "code": f["properties"]["code"],
              "g": [encode(simplify(r, 100)) for r in rings_of(f["geometry"])]}
             for f in json.loads(DEPTS.read_text())["features"]]
    data = {"q": Q, "rivers": rivers, "cities": cities, "depts": depts,
            "region": [encode(simplify(r, 100)) for r in rings_of(region)],
            "labels": [{"n": n, "g": g} for n, (_, g) in labels.items()]}
    OUT.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
    n_pts = sum(len(g) // 2 for r in rivers for g in r["g"])
    print(f"{len(rivers)} cours d'eau ({n_pts} points), {len(cities)} villes, {len(labels)} noms de rivières -> {OUT} ({OUT.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
