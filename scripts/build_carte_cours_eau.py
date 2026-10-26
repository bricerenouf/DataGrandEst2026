"""Génère carte-cours-eau.html : simplifie les cours d'eau et les injecte dans le gabarit."""
import csv
import json
import math
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "cours-eau-region_1791104972648.geojson"
DEPTS = ROOT / "data" / "departements-grand-est.geojson"
REGION = ROOT / "data" / "region-grand-est.geojson"
TEMPLATE = ROOT / "scripts" / "carte_cours_eau_template.html"
OUT = ROOT / "carte-cours-eau.html"
VIRAGES = ROOT / "output" / "virages_cours_eau.csv"  # produit par compter_virages.py
AFFLUENTS = ROOT / "output" / "affluents_cours_eau.csv"  # produit par compter_affluents.py
TOL_M = 12.0  # tolérance Douglas-Peucker en mètres (modifiable en argument : build_carte_cours_eau.py 8)
# Cours d'eau dessinés avec Leaflet.River (largeur croissante vers l'aval) : un segment
# devient une polyligne, d'où une simplification plus forte pour garder la carte fluide.
RIVER_MIN_KM = 20.0
RIVER_TOL_M = 30.0
Q = 1e5  # quantification : 5 décimales (~1 m)

KX = 111320 * math.cos(math.radians(48.7))
KY = 110570


def simplify(pts, tol, projected=False, indices=False):
    """Douglas-Peucker itératif, distances en mètres (projection locale si projected=False).
    Avec indices=True, renvoie les indices des points conservés."""
    n = len(pts)
    if n < 3:
        return list(range(n)) if indices else pts
    xy = pts if projected else [(p[0] * KX, p[1] * KY) for p in pts]
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        d2 = dx * dx + dy * dy
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if d2 == 0:
                dist = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / d2))
                dist = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if dist > best:
                best, idx = dist, i
        if best > tol2:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    if indices:
        return [i for i, k in enumerate(keep) if k]
    return [p for p, k in zip(pts, keep) if k]


def order_parts(lines):
    """Ordonne les parties d'un tracé de l'amont vers l'aval (Leaflet.River ne gère pas
    les tracés multiples) et renvoie [(partie, part_debut, part_fin)], où les parts
    (0 à 1) situent chaque partie le long de la rivière entière.

    La source est le début de partie le plus éloigné de toute fin de partie ; on enchaîne
    ensuite sur la partie dont le début est le plus proche de la fin courante."""
    lines = [l for l in lines if len(l) >= 2]
    if len(lines) == 1:
        return [(lines[0], 0.0, 1.0)]

    def d(a, b):
        return math.hypot((a[0] - b[0]) * KX, (a[1] - b[1]) * KY)

    lens = [length_km([l]) for l in lines]
    rest = set(range(len(lines)))
    first = max(rest, key=lambda i: min(d(lines[i][0], lines[j][-1]) for j in rest if j != i))
    order = [first]
    rest.remove(first)
    while rest:
        nxt = min(rest, key=lambda j: d(lines[order[-1]][-1], lines[j][0]))
        order.append(nxt)
        rest.remove(nxt)
    total, cum, out = sum(lens) or 1, 0.0, []
    for i in order:
        out.append((lines[i], cum / total, (cum + lens[i]) / total))
        cum += lens[i]
    return out


MAPBOX_STYLE = "brenouf/cmuxv0ou4000h01qr32z8byav"
MAPBOX_TOKEN_FILE = ROOT / ".mapbox_token"  # non versionné (voir .gitignore)


def basemap():
    """Fond de carte : style Mapbox si un jeton est disponible (variable d'environnement
    MAPBOX_TOKEN ou fichier .mapbox_token), sinon CARTO Positron. Le jeton public (pk.)
    est écrit dans la page générée, comme l'exige tout fond Mapbox côté navigateur."""
    token = os.environ.get("MAPBOX_TOKEN") or (MAPBOX_TOKEN_FILE.read_text().strip() if MAPBOX_TOKEN_FILE.exists() else "")
    if token:
        return {
            # {r} devient « @2x » sur les écrans haute densité seulement (tuiles 4 fois plus lourdes)
            "url": f"https://api.mapbox.com/styles/v1/{MAPBOX_STYLE}/tiles/512/{{z}}/{{x}}/{{y}}{{r}}?access_token={token}",
            "options": {"tileSize": 512, "zoomOffset": -1, "maxZoom": 19,
                        "attribution": '© <a href="https://www.mapbox.com/about/maps/">Mapbox</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'},
            "credit": "© Mapbox, © OpenStreetMap",
        }
    print("Pas de jeton Mapbox (MAPBOX_TOKEN ou .mapbox_token) : fond CARTO utilisé.")
    return {
        "url": "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        "options": {"subdomains": "abcd", "maxZoom": 19,
                    "attribution": '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> © <a href="https://carto.com/attributions">CARTO</a>'},
        "credit": "© OpenStreetMap, © CARTO",
    }


def render(template, data):
    """Injecte les données et le fond de carte dans un gabarit HTML."""
    return (template.read_text()
            .replace("/*__DATA__*/null", data)
            .replace("/*__BASEMAP__*/null", json.dumps(basemap(), ensure_ascii=False)))


def length_km(lines):
    s = 0.0
    for line in lines:
        for a, b in zip(line, line[1:]):
            s += math.hypot((b[0] - a[0]) * 111.32 * math.cos(math.radians(a[1])), (b[1] - a[1]) * 110.57)
    return s


def categorize(name):
    if not name:
        return "autre"
    first = name.split()[0].lower()
    low = name.lower()
    if re.match(r"^(ancien(ne)? |contre |grand )?(canal|rigole|aqueduc|conduite)", low):
        return "canal"
    if first in ("fossé", "fosse") or re.search(r"graben\b", low):
        return "fosse"
    if first in ("bras", "noue"):
        return "bras"
    if name[0].islower() or first == "rivière" or low.startswith("petite rivière"):
        return "riviere"
    if first in ("ruisseau", "ru", "rupt", "ruz", "goutte", "ravin", "combe", "fond", "fontaine",
                 "source", "val", "vallée", "gorge", "grand") or re.search(r"(bach|baech\w*|runz)\b", low):
        return "ruisseau"
    return "autre"


def encode(line):
    """Coordonnées entières delta-encodées : [x0, y0, dx1, dy1, ...]."""
    out, px, py = [], 0, 0
    for x, y in line:
        ix, iy = round(x * Q), round(y * Q)
        out += [ix - px, iy - py]
        px, py = ix, iy
    return out


def load_csv(path, script):
    if not path.exists():
        sys.exit(f"{path} introuvable : lancez d'abord scripts/{script}")
    with path.open(encoding="utf-8") as fh:
        return {r["CdOH"]: r for r in csv.DictReader(fh, delimiter=";")}


def main():
    tol = float(sys.argv[1]) if len(sys.argv) > 1 else TOL_M
    virages = load_csv(VIRAGES, "compter_virages.py")
    affluents = load_csv(AFFLUENTS, "compter_affluents.py")
    src = json.loads(SRC.read_text())
    feats, n_in, n_out = [], 0, 0
    for f in src["features"]:
        p, g = f["properties"], f["geometry"]
        if not g:
            continue
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        km = length_km(lines)
        enc, rp = [], None
        if km >= RIVER_MIN_KM:
            ordered = order_parts(lines)
            rp = [[round(p0, 4), round(p1, 4)] for _, p0, p1 in ordered]
            lines = [l for l, _, _ in ordered]
        for line in lines:
            n_in += len(line)
            s = simplify(line, RIVER_TOL_M if rp else tol)
            n_out += len(s)
            enc.append(encode(s))
        feats.append({
            "n": p.get("TopoOH") or "",
            "c": p.get("CdOH") or "",
            "s": p.get("SourceNomO") or "",
            "t": categorize(p.get("TopoOH")),
            "k": round(km, 2),
            "g": enc,
        })
        if rp:
            feats[-1]["rp"] = rp
        v = virages.get(p.get("CdOH"))
        if v:
            feats[-1].update(v=int(v["nb_virages"]), vk=float(v["virages_par_km"]), si=float(v["sinuosite"]))
        a = affluents.get(p.get("CdOH"))
        if a:
            feats[-1].update(a=int(a["nb_affluents_directs"]), at=int(a["nb_affluents_total"]), pc=a["se_jette_dans_CdOH"])

    # cours d'eau receveur : code -> indice dans la liste
    index = {f["c"]: i for i, f in enumerate(feats)}
    for f in feats:
        pc = f.pop("pc", "")
        if pc in index:
            f["p"] = index[pc]

    depts = json.loads(DEPTS.read_text())
    dep_out = []
    for f in depts["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        rings = [encode(simplify(r, 80)) for poly in polys for r in poly]
        dep_out.append({"code": f["properties"]["code"], "nom": f["properties"]["nom"], "g": rings})

    g = json.loads(REGION.read_text())["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    region = [encode(simplify(r, 80)) for poly in polys for r in poly]

    data = json.dumps({"q": Q, "rivers": feats, "depts": dep_out, "region": region}, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(render(TEMPLATE, data))
    print(f"{len(feats)} tronçons, points {n_in} -> {n_out}, {OUT.stat().st_size / 1e6:.1f} Mo -> {OUT}")


if __name__ == "__main__":
    main()
