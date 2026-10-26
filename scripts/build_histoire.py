"""Génère index.html (la page « histoire des rivières »), en trois pages :
  1. la carte de la région, avec l'eau qui s'écoule dans les rivières ;
  2. l'exploration : la même carte, cliquable (région, département, cours d'eau), et le
     grand tableau semaine par semaine qui s'adapte à la sélection ;
  3. cinq cours d'eau au comportement à part, réunis sur une seule page.

Les rangs hebdomadaires viennent de build_tableau.ranks (rang de la semaine parmi les
mêmes semaines de l'année sur 2000-2026, à chaque station). Tous les chiffres cités dans
les textes sont calculés ici, à partir des seules données du projet.
"""
import base64
import csv
import json
import math
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from analyse_memoire import OUT as STATIONS_CSV, Network, fnum, load_stations, proj, seg_dist  # noqa: E402
from build_carte_cours_eau import DEPTS, Q, REGION, SRC, encode, length_km, order_parts, simplify  # noqa: E402
from build_parcours import Areas  # noqa: E402
from build_tableau import EVENTS, MISSING, VARS, mean_rows, pack, ranks  # noqa: E402

TEMPLATE = ROOT / "scripts" / "histoire_template.html"
OUT = ROOT / "index.html"
AFFLUENTS = ROOT / "output" / "affluents_cours_eau.csv"
COMMUNES = ROOT / "data" / "communes-grand-est.geojson"
# Quelques villes pour se repérer : les grandes villes et les préfectures (désignées par leur
# nom dans le fichier des communes ; aucune donnée de population n'est ajoutée)
VILLES = [("Strasbourg", "67"), ("Reims", "51"), ("Metz", "57"), ("Mulhouse", "68"), ("Nancy", "54"), ("Colmar", "68"),
          ("Troyes", "10"), ("Charleville-Mézières", "08"), ("Châlons-en-Champagne", "51"), ("Épinal", "88"),
          ("Chaumont", "52"), ("Bar-le-Duc", "55")]

# Les cinq cours d'eau, dans l'ordre du récit : du plus naturel au plus transformé par l'homme
CASES = [
    ("nerveux", "Le Rosselle à Forbach", "Le nerveux"),
    ("lente", "La Suippe à Pontfaverger-Moronvilliers et à Selles", "La lente"),
    ("montagnarde", "La Lauch à Linthal", "La montagnarde"),
    ("domptee", "La Plaine à Raon-l'Étape [La Trouche]", "La domptée"),
    ("rebelle", "La Fensch à Knutange", "La rebelle"),
]


def winters(rows):
    """Un point par hiver : neige et pluie tombées de décembre à mars (semaines 49 à 13),
    débit moyen du printemps qui suit (avril à juin, semaines 14 à 26)."""
    by_year = {}
    for r in rows:
        by_year.setdefault(int(r["annee"]), []).append(r)
    out = []
    for y in sorted(by_year)[1:]:
        win = [r for r in by_year.get(y - 1, []) if int(r["num_semaine"]) >= 49] + [r for r in by_year[y] if int(r["num_semaine"]) <= 13]
        spring = [v for r in by_year[y] if 14 <= int(r["num_semaine"]) <= 26 and (v := fnum(r["debit"])) is not None and v >= 0]
        if len(win) < 15 or len(spring) < 10:
            continue
        out.append([y, round(sum(fnum(r["neige"]) or 0 for r in win)), round(sum(fnum(r["pluie"]) or 0 for r in win)),
                    round(st.mean(spring) / 1000, 3)])
    return out


def season_profile(rows):
    """Débit moyen de chaque semaine de l'année (1 à 52), rapporté à la moyenne de l'année."""
    acc = {}
    for r in rows:
        v = fnum(r["debit"])
        if v is not None and v >= 0:
            acc.setdefault(int(r["num_semaine"]), []).append(v)
    if any(w not in acc for w in range(1, 53)):
        return None
    prof = [st.mean(acc[w]) for w in range(1, 53)]
    m = st.mean(prof)
    return [round(x / m, 3) for x in prof] if m > 0 else None


def findings(stations, all_ranks):
    """Page « ce que révèlent les données » : quatre tendances régionales, calculées sur les stations.

    Toutes les moyennes régionales sont des moyennes de stations (chaque station compte pareil)."""
    first = next(iter(stations.values()))
    yr = [int(r["annee"]) for r in first]
    wk = [int(r["num_semaine"]) for r in first]
    weeks_of = {}
    for i, y in enumerate(yr):
        weeks_of.setdefault(y, []).append(i)
    full = [y for y, w in weeks_of.items() if len(w) >= 52]
    early, late = range(2000, 2009), range(2017, 2026)

    def mean_over(rows, col, idx):
        v = [x for i in idx if (x := fnum(rows[i][col])) is not None]
        return st.mean(v) if v else None

    # 1. Température : moyenne des années, par station, puis région et départements
    t_year = {s: {y: mean_over(rows, "temp", w) for y, w in weeks_of.items()} for s, rows in stations.items()}
    def t_period(ids, ys):
        return st.mean(st.mean(v for y in ys if (v := t_year[s][y]) is not None) for s in ids)
    by_dept = {}
    for s, rows in stations.items():
        by_dept.setdefault(rows[0]["dept"].zfill(2), []).append(s)
    gains = [t_period(ids, late) - t_period(ids, early) for ids in by_dept.values()]
    region_t = {y: st.mean(v for s in stations if (v := t_year[s][y]) is not None) for y in full}
    temp = {"early": round(t_period(stations, early), 1), "late": round(t_period(stations, late), 1),
            "gainMin": round(min(gains), 1), "gainMax": round(max(gains), 1), "nDept": len(gains),
            "hot5": sorted(full, key=lambda y: -region_t[y])[:5]}

    # 2. Étés (juillet-août, semaines 27 à 35) : chaleur, sol, part des stations au débit d'été très bas
    def summer(y):
        idx = [i for i in weeks_of[y] if 27 <= wk[i] <= 35]
        low = [st.mean(v) for s in stations if len(v := [x for i in idx if (x := all_ranks[s]["debit"][i]) is not None]) >= 5]
        return {"temp": round(st.mean(v for rows in stations.values() if (v := mean_over(rows, "temp", idx)) is not None), 1),
                "soil": round(st.mean(v for rows in stations.values() if (v := mean_over(rows, "humidite", idx)) is not None), 2),
                "low": round(100 * sum(x < 20 for x in low) / len(low)), "from": idx[0], "to": idx[-1]}
    summers = {str(y): summer(y) for y in (2003, 2022)}

    # 3. Neige de l'hiver (décembre à mars, semaines 49 à 13), moyenne des stations
    def winter(y):
        idx = [i for i in weeks_of.get(y - 1, []) if wk[i] >= 49] + [i for i in weeks_of[y] if wk[i] <= 13]
        return st.mean(sum(fnum(rows[i]["neige"]) or 0 for i in idx) for rows in stations.values())
    win = {y: winter(y) for y in sorted(weeks_of)[1:]}
    w_early, w_late = st.mean(win[y] for y in range(2001, 2010)), st.mean(win[y] for y in range(2018, 2027) if y in win)
    snow = {"early": round(w_early), "late": round(w_late), "pct": round(100 * (w_late / w_early - 1)),
            "low5": sorted(win, key=win.get)[:5]}

    # 4. Une année sèche, une année humide : stations sous / au-dessus de leur normale sur l'année
    def year_balance(y):
        idx = weeks_of[y]
        m = [st.mean(v) for s in stations if len(v := [x for i in idx if (x := all_ranks[s]["debit"][i]) is not None]) >= len(idx) / 2]
        rain = st.mean(sum(fnum(rows[i]["pluie"]) or 0 for i in idx) for rows in stations.values())
        return {"n": len(m), "below": sum(x < 50 for x in m), "above": sum(x > 50 for x in m), "rain": round(rain),
                "from": idx[0], "to": idx[-1]}
    balance = {str(y): year_balance(y) for y in (2021, 2022, 2024, 2025)}
    return {"temp": temp, "summers": summers, "snow": snow, "balance": balance}


def corr(x, y, lag=0):
    p = [(x[i], y[i + lag]) for i in range(len(x) - lag) if x[i] is not None and y[i + lag] is not None]
    return st.correlation([a for a, _ in p], [b for _, b in p]) if len(p) > 200 else None


def switches_per_year(v):
    """Nombre de passages au-dessus / en dessous de la normale (rang 50), ramené à l'année."""
    pairs = [(a, b) for a, b in zip(v, v[1:]) if a is not None and b is not None]
    return 52 * sum((a >= 50) != (b >= 50) for a, b in pairs) / max(1, len(pairs))


def streak(v, test, dates):
    best = cur = start = best_start = 0
    for i, x in enumerate(v):
        if x is not None and test(x):
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, best_start = cur, start
        else:
            cur = 0
    return best, dates[best_start] if best else None


# Valeurs mesurées : chaque série est ramenée sur un octet (0-254, 255 = manquant) entre
# son minimum et son maximum ; le débit, très étalé, l'est en échelle logarithmique.
RAW_COLS = {"pluie": "pluie", "neige": "neige", "humidite": "humidite", "temp": "temp", "debit": "debit"}


def qpack(values, log=False):
    """Liste de valeurs (None = manquant) -> (base64, [min, max, log]) pour le décodage."""
    t = [None if v is None else (math.log1p(v) if log else v) for v in values]
    known = [x for x in t if x is not None]
    if not known:
        return pack([None] * len(values)), [0, 1, int(log)]
    lo, hi = min(known), max(known)
    span = (hi - lo) or 1
    data = bytes(MISSING if x is None else round(254 * (x - lo) / span) for x in t)
    return base64.b64encode(data).decode(), [round(lo, 5), round(hi, 5), int(log)]


def raw_series(rows, col):
    vals = [fnum(r[col]) for r in rows]
    return [v if v is not None and (col != "debit" or v >= 0) else None for v in vals]


def rings_of(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [ring for poly in polys for ring in poly]


def main():
    stations = load_stations()
    with STATIONS_CSV.open(encoding="utf-8") as fh:
        meta = {r["site"]: r for r in csv.DictReader(fh, delimiter=";")}
    first = next(iter(stations.values()))
    dates = [r["date"] for r in first]
    years = [int(r["annee"]) for r in first]
    n = len(dates)

    # Rangs de toutes les stations : moyenne régionale et indicateurs de comparaison
    all_ranks = {s: {c: ranks(rows, c) for c, _ in VARS} for s, rows in stations.items()}
    region = {c: pack(mean_rows([all_ranks[s][c] for s in stations], n)) for c, _ in VARS}

    def indicators(s):
        R = all_ranks[s]
        q = R["debit"]
        early = [x for x, y in zip(q, years) if x is not None and y <= 2008]
        late = [x for x, y in zip(q, years) if x is not None and y >= 2017]
        return {
            "rain": max(corr(R["pluie"], q, k) or -1 for k in (0, 1)),
            "soil": corr(R["humidite"], q), "snow": corr(R["neige"], q), "temp": corr(R["temp"], q),
            "sw": switches_per_year(q), "swRain": switches_per_year(R["pluie"]),
            "trend": st.mean(late) - st.mean(early) if early and late else None,
        }

    measured = [s for s in stations if s in meta]
    ind = {s: indicators(s) for s in measured}
    med = {k: st.median(v[k] for v in ind.values() if v[k] is not None) for k in ("rain", "soil", "snow", "sw", "trend")}
    ranked = lambda key, s, rev=True: sorted(measured, key=lambda x: ind[x][key] if ind[x][key] is not None else -9, reverse=rev).index(s) + 1

    # Neige d'hiver et débit de printemps (la montagnarde), année type du débit (la domptée)
    def wcorr(pts, i):
        return st.correlation([p[i] for p in pts], [p[3] for p in pts]) if len(pts) > 15 and len({p[i] for p in pts}) > 1 else None
    wint = {s: winters(stations[s]) for s in measured}
    w_snow = {s: wcorr(p, 1) for s, p in wint.items()}
    w_snow_ok = sorted((v, s) for s, v in w_snow.items() if v is not None)
    profiles = {s: p for s in measured if (p := season_profile(stations[s]))}
    amp = {s: max(p) / max(min(p), 1e-6) for s, p in profiles.items()}
    flat = sorted(profiles, key=amp.get)

    feats = {f["properties"]["CdOH"]: f for f in json.loads(SRC.read_text())["features"] if f["geometry"]}
    by_name = {rows[0]["libelle_site"]: s for s, rows in stations.items()}
    cases = []
    for key, name, title in CASES:
        s = by_name[name]
        rows, m, R = stations[s], meta[s], all_ranks[s]
        q = R["debit"]
        lo, lo_from = streak(q, lambda x: x < 30, dates)
        hi, hi_from = streak(q, lambda x: x > 70, dates)
        g = feats[m["CdOH"]]["geometry"]
        lines = [l for l in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]) if len(l) >= 2]
        i = ind[s]
        # Débit médian du second semestre (semaines 27 à 52), avant et après 2008
        def second_half(y0, y1):
            v = [float(r["debit"]) for r in rows if r["debit"] and y0 <= int(r["annee"]) <= y1 and int(r["num_semaine"]) >= 27]
            return st.median(v) if v else None
        before, after = second_half(2003, 2007), second_half(2008, 2012)

        def rain_year(y0, y1):
            tot = {}
            for r in rows:
                if y0 <= int(r["annee"]) <= y1:
                    tot[r["annee"]] = tot.get(r["annee"], 0) + float(r["pluie"]) + float(r["neige"])
            return st.mean(tot.values())
        cases.append({
            "key": key, "title": title, "name": name, "river": name.split(" à ")[0], "dept": rows[0]["libelle_dept"].title(), "c": m["CdOH"],
            "lat": float(m["lat"]), "lon": float(m["lon"]),
            "geom": [encode(simplify(l, 30)) for l in lines],
            "rows": {c: pack(R[c]) for c, _ in VARS},
            "facts": {**{k: (round(v, 2) if v is not None else None) for k, v in i.items()},
                      "inertie": int(m["inertie_sem"]), "km": float(m["km_reseau_amont"]), "aff": int(m["affluents_amont"]),
                      "vk": float(m["virages_par_km"]), "lo": lo, "loFrom": lo_from, "hi": hi, "hiFrom": hi_from,
                      "rankRain": ranked("rain", s), "rankRainLow": ranked("rain", s, False), "rankSw": ranked("sw", s, False),
                      "rankSnow": ranked("snow", s), "rankTrend": ranked("trend", s), "rankSoilLow": ranked("soil", s, False),
                      "ratio2008": round(after / before, 1) if before and after else None,
                      "rainBefore": round(rain_year(2003, 2007)), "rainAfter": round(rain_year(2008, 2012))},
        })
        f = cases[-1]["facts"]
        if key == "montagnarde":
            f.update({"wSnow": round(w_snow[s], 2), "wRain": round(wcorr(wint[s], 2), 2), "nW": len(w_snow_ok),
                      "rankWSnow": [x for _, x in reversed(w_snow_ok)].index(s) + 1,
                      "wSnowMed": round(st.median(v for v, _ in w_snow_ok), 2)})
            cases[-1]["chart"] = {"type": "hivers", "pts": wint[s]}
        if key == "domptee":
            rhin = [amp[x] for x in profiles if stations[x][0]["libelle_cours_eau"] == "Le Rhin"]
            f.update({"amp": round(amp[s], 1), "ampMed": round(st.median(amp.values()), 1), "rankFlat": flat.index(s) + 1, "nFlat": len(flat),
                      "ampRhin": round(min(rhin), 1) if rhin else None})
            cases[-1]["chart"] = {"type": "saisons", "hl": s, "ids": list(profiles), "lines": list(profiles.values())}
        print(f"{title:15} {name[:40]:40} pluie {i['rain']:+.2f} sol {i['soil']:+.2f} neige {i['snow']:+.2f} | {i['sw']:.0f} chgts/an "
              f"| évol. {i['trend']:+.0f} | retour {m['inertie_sem']} sem. | bas {lo} sem. ({lo_from}) haut {hi} sem. ({hi_from})")

    # Départements : rangs moyens de leurs stations, et zones pour savoir où passe chaque rivière
    dept_feats = json.loads(DEPTS.read_text())["features"]
    for f in dept_feats:
        f["properties"]["nom"] = f["properties"]["code"]          # Areas.find renvoie ce champ
    dept_areas = Areas(dept_feats)
    by_dept = {}
    for st_id, rows in stations.items():
        by_dept.setdefault(rows[0]["dept"].zfill(2), []).append(st_id)

    # Stations : rangs (écart à la normale) et valeurs mesurées des 5 lignes, cours d'eau relié
    st_out = []
    net = Network()

    def linked(st_id, rows):
        """Cours d'eau et position de la station. memoire_stations.csv ne garde que les stations
        dont le débit couvre au moins la moitié des semaines : les autres sont rattachées ici,
        avec la même règle, pour rester cliquables sur la carte."""
        m = meta.get(st_id)
        if m:
            return m["CdOH"], int(m["position_m"])
        lon, lat = float(rows[0]["lon"]), float(rows[0]["lat"])
        c = net.link(lon, lat, rows[0]["libelle_cours_eau"])
        return (c, round(net.locate(c, (lon, lat)))) if c else (None, None)

    raw = {st_id: {c: raw_series(rows, col) for c, col in RAW_COLS.items()} for st_id, rows in stations.items()}
    for st_id, rows in stations.items():
        c_st, pos = linked(st_id, rows)
        q = {c: qpack(raw[st_id][c], log=(c == "debit")) for c in RAW_COLS}
        st_out.append({"id": st_id, "n": rows[0]["libelle_site"], "dept": rows[0]["dept"].zfill(2),
                       "lat": float(rows[0]["lat"]), "lon": float(rows[0]["lon"]),
                       "c": c_st, "pos": pos,
                       "rows": {c: pack(all_ranks[st_id][c]) for c, _ in VARS},
                       "raw": {c: q[c][0] for c in q}, "rs": {c: q[c][1] for c in q}})
    with_station = {x["c"] for x in st_out if x["c"]}

    # Moyennes des valeurs mesurées pour la région et chaque département (sans le débit :
    # une moyenne de débits de rivières de tailles différentes n'aurait pas de sens)
    def raw_group(ids):
        out = {}
        for c in ("pluie", "neige", "humidite", "temp"):
            mean = []
            for i in range(n):
                v = [raw[x][c][i] for x in ids if raw[x][c][i] is not None]
                mean.append(sum(v) / len(v) if v else None)
            out[c] = qpack(mean)
        return {"raw": {c: v[0] for c, v in out.items()}, "rs": {c: v[1] for c, v in out.items()}}
    region_raw = raw_group(list(stations))

    # Arbre des affluents : pour chaque cours d'eau dont le réseau contient une station, ses
    # stations (de l'amont vers l'aval) et ses affluents « équipés », avec la position de
    # leur confluence le long de lui (même mesure que la position des stations).
    own = {}
    for x in st_out:
        if x["c"]:
            own.setdefault(x["c"], []).append(x)
    equipped = {}

    def has_station(c, seen=()):
        if c not in equipped:
            equipped[c] = c in own or any(has_station(b, seen + (c,)) for b in net.children[c] if b not in seen)
        return equipped[c]
    tree = {}

    def confluence(c, b):
        """Position (m) sur c de l'embouchure de b : la fin de partie de b la plus proche de c
        (pour un affluent en plusieurs morceaux, la dernière partie chaînée n'est pas forcément l'aval)."""
        ends = [proj(part[-1]) for part in net.lines(b) if len(part) >= 2]
        best = (1e18, None)
        for xy, _, d in net.chain(c)[0]:
            for i, (a, z) in enumerate(zip(xy, xy[1:])):
                for e in ends:
                    dist, t = seg_dist(e, a, z)
                    if dist < best[0]:
                        best = (dist, d[i] + t * (d[i + 1] - d[i]))
        return best[1] or 0

    def build(c, seen=()):
        if c in tree:
            return
        kids = [b for b in net.children[c] if b not in seen and has_station(b, seen + (c,))]
        tree[c] = {"n": net.feats[c]["properties"].get("TopoOH") or "",
                   "st": [x["id"] for x in sorted(own.get(c, []), key=lambda x: x["pos"] or 0)],
                   "kids": sorted([[b, round(confluence(c, b))] for b in kids], key=lambda k: k[1])}
        for b in kids:
            build(b, seen + (c,))
    for c in own:
        build(c)
    print(f"arbre des affluents : {len(tree)} cours d'eau, {sum(len(t['kids']) for t in tree.values())} confluences avec station")

    # Rivières : toutes ; largeur croissante vers l'aval à partir de 30 km ; départements traversés
    with AFFLUENTS.open(encoding="utf-8") as fh:
        aff_rows = list(csv.DictReader(fh, delimiter=";"))
    network = {r["CdOH"]: int(r["nb_affluents_total"]) for r in aff_rows}
    parent_of = {r["CdOH"]: r["se_jette_dans_CdOH"] for r in aff_rows if r["se_jette_dans_CdOH"]}
    main5 = set(sorted(network, key=lambda c: -network[c])[:5])
    case_rivers = {c["c"] for c in cases}
    rivers, km_by_c = [], {}
    for c, f in feats.items():
        g = f["geometry"]
        lines = [l for l in (g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]) if len(l) >= 2]
        km = km_by_c[c] = length_km(lines)
        if km >= 30:
            ordered = order_parts(lines)
            r = {"k": round(km), "at": network.get(c, 0), "g": [encode(simplify(l, 60)) for l, _, _ in ordered],
                 "rp": [[round(a, 4), round(b, 4)] for _, a, b in ordered]}
        else:
            r = {"k": round(km, 1), "at": network.get(c, 0), "g": [encode(simplify(l, 90)) for l in lines]}
        # Départements traversés : utiles au survol et au clic (cours d'eau de plus de 5 km, et
        # tous ceux qui ont une station, avec au moins le département de la station)
        if km >= 5 or c in with_station:
            d_set = {d for l in lines for p in l[::8] + [l[-1]] if (d := dept_areas.find(p[0], p[1]))}
            r["d"] = sorted(d_set | {x["dept"] for x in st_out if x["c"] == c})
        if c in with_station or c in main5 or c in case_rivers:
            r["c"], r["n"] = c, f["properties"].get("TopoOH") or ""
        if c in main5:
            r["main"] = 1
        r["cd"] = c
        rivers.append(r)
    rivers.sort(key=lambda r: r["k"])
    # Cours d'eau receveur de chacun (indice dans la liste) : pour faire couler un réseau entier
    index = {r["cd"]: i for i, r in enumerate(rivers)}
    for r in rivers:
        parent = parent_of.get(r.pop("cd"))
        if parent in index:
            r["p"] = index[parent]

    communes = {(f["properties"]["nom"], f["properties"]["code"][:2]): f for f in json.loads(COMMUNES.read_text())["features"]}
    cities = []
    for nom, dep in VILLES:
        ring = max(rings_of(communes[(nom, dep)]["geometry"]), key=len)
        cities.append({"n": nom, "lon": round(sum(p[0] for p in ring) / len(ring), 4), "lat": round(sum(p[1] for p in ring) / len(ring), 4)})

    # Page « vue d'ensemble » : quelques chiffres, tous tirés des données
    longest = max(km_by_c, key=km_by_c.get)
    biggest = max(network, key=network.get)
    overview = {
        "km": round(sum(km_by_c.values())), "nRivers": len(km_by_c),
        # le troisième élément : la position du cours d'eau dans la liste des rivières de la page
        "longest": [feats[longest]["properties"]["TopoOH"], round(km_by_c[longest]), index[longest]],
        "biggest": [feats[biggest]["properties"]["TopoOH"], network[biggest], index[biggest]],
    }
    print("vue d'ensemble :", overview)

    obs = findings(stations, all_ranks)
    print("tendances :", obs)
    data = {
        "overview": overview, "obs": obs,
        "q": Q, "dates": [dates[0], n], "years": years, "vars": VARS, "events": EVENTS, "region": region,
        "median": {k: round(v, 2) for k, v in med.items()}, "nMeasured": len(measured), "nTrend": sum(ind[x]["trend"] is not None for x in measured), "nStations": len(stations),
        "cases": cases, "rivers": rivers, "stations": st_out, "cities": cities, "tree": tree, "regionRaw": region_raw,
        "outline": [encode(simplify(r, 100)) for r in rings_of(json.loads(REGION.read_text())["geometry"])],
        "depts": [{"code": f["properties"]["code"], "nom": json.loads(DEPTS.read_text())["features"][i]["properties"]["nom"],
                   "g": [encode(simplify(r, 120)) for r in rings_of(f["geometry"])], "n": len(by_dept.get(f["properties"]["code"], [])),
                   "rows": {c: pack(mean_rows([all_ranks[x][c] for x in by_dept.get(f["properties"]["code"], [])], n)) for c, _ in VARS},
                   **raw_group(by_dept.get(f["properties"]["code"], []))}
                  for i, f in enumerate(dept_feats)],
    }
    print("5 plus grands cours d'eau :", [feats[c]["properties"]["TopoOH"] for c in main5])
    OUT.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, ensure_ascii=False, separators=(",", ":"))))
    print("médianes de comparaison :", data["median"])
    print(f"-> {OUT} ({OUT.stat().st_size / 1e6:.1f} Mo)")


if __name__ == "__main__":
    main()
