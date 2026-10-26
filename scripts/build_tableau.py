"""Génère tableau-semaines.html : un grand tableau de couleurs, semaine par semaine
(2000-2026), de la pluie, de la neige, de l'humidité du sol, de la température et du débit.

Chaque case est le rang de la semaine parmi les mêmes semaines de l'année sur toute la
période, pour la même station (0 = la plus basse, 100 = la plus haute ; les égalités
prennent le rang moyen). Fenêtre de ± 1 semaine autour de la semaine de l'année, pour
avoir assez de valeurs de comparaison. Ce rang retire le cycle des saisons et rend les
lignes comparables malgré leurs unités.

Vues : toute la région (moyenne des stations), chaque département, et chaque station
(débit seulement). Longueur du réseau amont, virages et affluents servent à trier les
stations (output/memoire_stations.csv, analyse_memoire.py).
"""
import base64
import bisect
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from analyse_memoire import OUT as STATIONS_CSV, fnum, load_stations  # noqa: E402
from build_carte_cours_eau import DEPTS, Q, REGION, SRC, encode, length_km, simplify  # noqa: E402

TEMPLATE = ROOT / "scripts" / "tableau_template.html"
OUT = ROOT / "tableau-semaines.html"
VARS = [("pluie", "Pluie"), ("neige", "Neige"), ("humidite", "Humidité du sol"), ("temp", "Température"), ("debit", "Débit")]
MISSING = 255
# Épisodes annotés sur le tableau : (début, fin, libellé). Chiffres vérifiés sur les rangs
# hebdomadaires de la région (rang < 30 = débit bas, > 70 = débit haut).
EVENTS = [
    ("2003-01-01", "2003-12-31", "2003 · sol très sec"),
    ("2011-01-31", "2011-06-13", "Printemps 2011 · 20 semaines de débit bas"),
    ("2016-04-04", "2016-08-01", "2016 · 18 semaines de débit haut"),
    ("2018-01-01", "2020-12-31", "2018-2020 · trois années sous la normale"),
    ("2022-01-01", "2022-12-31", "2022 · le sol le plus sec"),
    ("2024-04-01", "2024-08-19", "2024 · 21 semaines de débit haut, record"),
    ("2026-05-18", "2026-09-07", "Été 2026 · 17 semaines de débit bas, en cours"),
]


def woy(r):
    return min(int(r["num_semaine"]), 52)


def ranks(rows, col):
    """Rang (0-100) de chaque semaine parmi les mêmes semaines de l'année (± 1), ou None."""
    vals = [fnum(r[col]) for r in rows]
    if col == "debit":
        vals = [v if v is not None and v >= 0 else None for v in vals]
    pool = collections.defaultdict(list)
    for r, v in zip(rows, vals):
        if v is not None:
            w = woy(r)
            for d in (-1, 0, 1):
                pool[(w + d - 1) % 52 + 1].append(v)
    for p in pool.values():
        p.sort()
    out = []
    for r, v in zip(rows, vals):
        p = pool.get(woy(r))
        if v is None or not p or len(p) < 10:
            out.append(None)
            continue
        lo, hi = bisect.bisect_left(p, v), bisect.bisect_right(p, v)
        out.append(round(100 * (lo + (hi - lo) / 2) / len(p)))
    return out


def pack(values):
    """Liste de rangs (None = manquant) -> base64 d'octets."""
    return base64.b64encode(bytes(MISSING if v is None else v for v in values)).decode()


def mean_rows(series_list, n):
    out = []
    for i in range(n):
        v = [s[i] for s in series_list if s[i] is not None]
        out.append(round(sum(v) / len(v)) if v else None)
    return out


def main():
    stations = load_stations()
    with STATIONS_CSV.open(encoding="utf-8") as fh:
        meta = {r["site"]: r for r in csv.DictReader(fh, delimiter=";")}
    first = next(iter(stations.values()))
    dates = [r["date"] for r in first]
    n = len(dates)
    # Année et numéro de semaine de chaque colonne (format 2000W01 de la colonne semaine)
    years = [int(r["annee"]) for r in first]
    woys = [int(r["num_semaine"]) for r in first]

    per_station = {}
    for site, rows in stations.items():
        if [r["date"] for r in rows] != dates:
            sys.exit(f"Station {site} : semaines différentes des autres")
        per_station[site] = {col: ranks(rows, col) for col, _ in VARS}

    def group(sites):
        return {col: pack(mean_rows([per_station[s][col] for s in sites], n)) for col, _ in VARS}

    by_dept = collections.defaultdict(list)
    for site, rows in stations.items():
        by_dept[rows[0]["dept"].zfill(2)].append(site)

    st_out = []
    for site, rows in stations.items():
        m = meta.get(site)
        st_out.append({
            "id": site, "n": rows[0]["libelle_site"], "riv": rows[0]["libelle_cours_eau"], "dept": rows[0]["dept"].zfill(2),
            "lat": float(rows[0]["lat"]), "lon": float(rows[0]["lon"]),
            # attributs fixes pour le tri (absents si la station a trop peu de débits mesurés)
            "km": float(m["km_reseau_amont"]) if m else None, "vk": float(m["virages_par_km"]) if m else None,
            "aff": int(m["affluents_amont"]) if m else None, "t": int(m["inertie_sem"]) if m else None,
            "q": pack(per_station[site]["debit"]),
        })

    depts_geo = json.loads(DEPTS.read_text())["features"]
    rings = lambda g: [r for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]) for r in poly]
    background = []
    for f in json.loads(SRC.read_text())["features"]:
        g = f["geometry"]
        if not g:
            continue
        lines = [l for l in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]) if len(l) >= 2]
        km = length_km(lines)
        if km >= 15:
            background.append({"k": round(km), "g": [encode(simplify(l, 150)) for l in lines]})

    data = {
        "q": Q, "dates": [dates[0], n], "vars": VARS, "years": years, "woys": woys, "events": EVENTS,
        "region": group(list(stations)),
        "depts": [{"code": f["properties"]["code"], "nom": f["properties"]["nom"],
                   "g": [encode(simplify(r, 120)) for r in rings(f["geometry"])],
                   "n": len(by_dept[f["properties"]["code"]]), "rows": group(by_dept[f["properties"]["code"]])}
                  for f in depts_geo],
        "stations": st_out, "background": background,
        "outline": [encode(simplify(r, 120)) for r in rings(json.loads(REGION.read_text())["geometry"])],
    }
    OUT.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
    print(f"{len(st_out)} stations, {n} semaines ({dates[0]} -> {dates[-1]}), {len(depts_geo)} départements -> {OUT} ({OUT.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
