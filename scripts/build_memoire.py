"""Génère memoire-rivieres.html : « L'énigme de la Champagne », trois pages sur la carte.

  1. L'énigme : la pluie repart de zéro chaque semaine, les rivières non ; celles de
     Champagne mettent bien plus longtemps à revenir à la normale.
  2. L'enquête : les suspects (taille, affluents, virages, neige, sol…), puis la preuve
     que l'eau vient d'en dessous : la rivière revient à la normale plus lentement que
     son propre sol.
  3. La preuve : l'automne 2022 rejoué semaine par semaine.

Prérequis : output/memoire_stations.csv (analyse_memoire.py). Tous les chiffres cités
dans la page sont calculés ici, uniquement à partir des données du projet (aucune donnée
externe).
"""
import csv
import datetime as dt
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from analyse_memoire import OUT as STATIONS_CSV, REPLAY_START, Network, acf, anomalies, load_stations  # noqa: E402
from build_carte_cours_eau import DEPTS, Q, REGION, encode, order_parts, render, simplify  # noqa: E402

TEMPLATE = ROOT / "scripts" / "memoire_template.html"
OUT = ROOT / "memoire-rivieres.html"

REACTIVE, INERTE = "La Doller à Reiningue", "Le Cubry à Pierry"
CHAMPAGNE = "MARNE"          # département-cœur de la Champagne, groupe comparé au reste
REPLAY_WEEKS = 66            # 27 juin 2022 -> fin septembre 2023
SUSPECTS = [  # (colonne, libellé, groupe)
    ("km_reseau_amont", "Longueur du réseau en amont", "taille"),
    ("affluents_amont", "Nombre d'affluents en amont", "taille"),
    ("rang_strahler", "Rang dans le réseau (Strahler)", "taille"),
    ("position_m", "Distance depuis la source", "taille"),
    ("affluents_par_km_riviere", "Affluents par km de rivière", "indice"),
    ("virages_par_km", "Virages par km", "indice"),
    ("part_neige", "Part de neige", "indice"),
    ("humidite_sol_moyenne", "Humidité moyenne du sol", "indice"),
    ("temperature_moyenne", "Température moyenne", "indice"),
]


def spearman(xs, ys):
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    return st.correlation(ranks(xs), ranks(ys))


def num(v):
    return float(v) if v not in ("", None) else None


def tapered(parts, tol=25):
    """Parties (lon/lat, amont -> aval) encodées + part de longueur de chacune, pour Leaflet.River."""
    ordered = order_parts(parts)
    return {"g": [encode(simplify(l, tol)) for l, _, _ in ordered],
            "rp": [[round(a, 4), round(b, 4)] for _, a, b in ordered]}


def main():
    with STATIONS_CSV.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    series = load_stations()
    net = Network()

    stations = []
    for r in rows:
        srows = series[r["site"]]
        qa = anomalies(srows)
        start = next(i for i, x in enumerate(srows) if x["date"] >= REPLAY_START)
        champ = r["dept"] == CHAMPAGNE
        full_years = {x["annee"] for x in srows if int(x["annee"]) <= 2025}
        rain_year = sum(float(x["pluie"]) + float(x["neige"]) for x in srows if x["annee"] in full_years) / len(full_years)
        stations.append({
            "id": r["site"], "n": r["nom"], "riv": r["riviere"], "dept": r["dept"],
            "lat": float(r["lat"]), "lon": float(r["lon"]),
            "t": int(r["inertie_sem"]),
            "acf": [1.0] + [round(max(acf(qa, k) or 0, 0), 2) for k in range(1, 53)],
            "champ": champ, "ts": int(r["inertie_sol_sem"]), "rain": round(rain_year), "km": float(r["km_reseau_amont"]),
            "sus": {c: num(r[c]) for c, _, _ in SUSPECTS},
            "rs": num(r["sol_retour_2022"]), "rr": num(r["riviere_retour_2022"]),
            # écart du débit à la normale de la semaine (%), pendant la reconstitution
            "w": [None if x is None else round(100 * (math.exp(x) - 1)) for x in qa[start:start + REPLAY_WEEKS]],
        })

    # ---- Chiffres de la page ----
    t_all = [s["t"] for s in stations]
    champ = [s["t"] for s in stations if s["dept"] == CHAMPAGNE]
    other = [s["t"] for s in stations if s["dept"] != CHAMPAGNE]
    rain = {int(r["inertie_pluie_sem"]) for r in rows}
    suspects = []
    for col, label, group in SUSPECTS:
        pairs = [(s["sus"][col], s["t"]) for s in stations if s["sus"][col] is not None]
        suspects.append({"col": col, "label": label, "group": group,
                         "rho": round(spearman([a for a, _ in pairs], [b for _, b in pairs]), 2)})
    start = dt.date.fromisoformat(REPLAY_START)
    soil_back = [s["rs"] for s in stations if s["rs"] is not None]
    lag = lambda grp: [s["rr"] - s["rs"] for s in grp if s["rr"] is not None and s["rs"] is not None]
    on_champ = [s for s in stations if s["champ"]]
    off_champ = [s for s in stations if not s["champ"]]
    facts = {
        "n": len(stations), "rain": sorted(rain), "tMin": min(t_all), "tMax": max(t_all), "tMed": st.median(t_all),
        "champMed": st.median(champ), "champN": len(champ), "otherMed": st.median(other),
        "suspects": suspects,
        "champSoilMed": st.median(s["ts"] for s in on_champ), "offSoilMed": st.median(s["ts"] for s in off_champ),
        "champSlower": sum(s["t"] > s["ts"] for s in on_champ), "offSlower": sum(s["t"] > s["ts"] for s in off_champ), "offN": len(off_champ),
        "replayStart": REPLAY_START, "replayWeeks": REPLAY_WEEKS,
        "soilBackWeek": st.median(soil_back),
        "soilBackDate": (start + dt.timedelta(weeks=st.median(soil_back))).isoformat(),
        "lagOff": st.median(lag(off_champ)), "lagChamp": st.median(lag(on_champ)),
        "champNotBack": sum(1 for s in on_champ if s["rr"] is None), "offNotBack": sum(1 for s in off_champ if s["rr"] is None),
        "champRain": st.median(s["rain"] for s in on_champ), "offRain": st.median(s["rain"] for s in off_champ),
        "champKm": st.median(s["km"] for s in on_champ), "offKm": st.median(s["km"] for s in off_champ),
    }

    # ---- Géométries ----
    background = []
    for c in net.feats:
        km = net.chain(c)[1] / 1000
        if km < 10:
            continue
        if km >= 30:
            background.append({"k": round(km), **tapered(net.lines(c), 60)})
        else:
            background.append({"k": round(km), "g": [encode(simplify(l, 40)) for l in net.lines(c) if len(l) >= 2]})
    region_geom = json.loads(REGION.read_text())["geometry"]
    polys = region_geom["coordinates"] if region_geom["type"] == "MultiPolygon" else [region_geom["coordinates"]]

    data = {
        "q": Q, "stations": stations, "facts": facts, "background": background,
        "names": {"reactive": REACTIVE, "inerte": INERTE, "champagne": CHAMPAGNE},
        "region": [encode(simplify(r, 80)) for poly in polys for r in poly],
        "depts": [{"nom": f["properties"]["nom"],
                   "g": [encode(simplify(r, 80)) for poly in (f["geometry"]["coordinates"] if f["geometry"]["type"] == "MultiPolygon" else [f["geometry"]["coordinates"]]) for r in poly]}
                  for f in json.loads(DEPTS.read_text())["features"]],
    }
    OUT.write_text(render(TEMPLATE, json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
    print(json.dumps(facts, ensure_ascii=False))
    print(f"{OUT.stat().st_size / 1e6:.1f} Mo -> {OUT}")


if __name__ == "__main__":
    main()
