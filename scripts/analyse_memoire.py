"""Inertie des rivières : indicateurs par station hydrométrique.

Pour chaque station de data/donnees.csv ayant au moins 50 % de débits renseignés :
  - relie la station à son cours d'eau (même nom à moins de 2 km, sinon le plus proche
    à moins de 150 m) ;
  - inertie_sem : temps de retour à la normale, en semaines. C'est l'« effet mémoire »
    de Mangin (1984) adapté au pas hebdomadaire : on retire au log du débit sa moyenne
    de chaque semaine de l'année (le cycle des saisons), puis on cherche le premier
    décalage où l'autocorrélation de cet écart passe sous 1/e ;
  - les « suspects » : taille du réseau en amont (km, affluents, rang de Strahler,
    distance depuis la source), affluents par km de rivière, virages par km, part de
    fossés et canaux en amont, inertie du sol (même méthode sur l'indice d'humidité),
    humidité moyenne du sol, part de neige, température moyenne ;
  - sol_retour_2022, riviere_retour_2022 : semaines, comptées depuis le 27 juin 2022,
    où le sol puis la rivière reviennent à la normale après la sécheresse de 2022
    (premier retour au-dessus de la normale de la semaine, deux semaines de suite pour
    la rivière, après le point le plus sec du sol) ;
  - evolution_ete : débit médian des semaines W25 à W38, 2014-2026 contre 2000-2012.

Écrit output/memoire_stations.csv. Les fonctions sont réutilisées par build_memoire.py.
"""
import collections
import csv
import json
import math
import re
import statistics as st
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_carte_cours_eau import KX, KY, SRC, order_parts  # noqa: E402

DONNEES = ROOT / "data" / "donnees.csv"
OUTPUT = ROOT / "output"
OUT = OUTPUT / "memoire_stations.csv"
SEUIL = 1 / math.e
P1, P2 = (2000, 2012), (2014, 2026)
REPLAY_START = "2022-06-27"          # début de la reconstitution de l'automne 2022
DRY_WINDOW = ("2022-06-20", "2022-11-15")   # où chercher le point le plus sec du sol


def fnum(v):
    return float(v) if v not in ("", None) else None


def load_stations():
    """{site: [lignes triées par date]}"""
    by = collections.defaultdict(list)
    with DONNEES.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            by[r["site"]].append(r)
    for rows in by.values():
        rows.sort(key=lambda r: r["date"])
    return by


def week(r):
    return min(int(r["num_semaine"]), 52)  # la semaine 53 rejoint la 52


def deseason(rows, values):
    """Écart de chaque valeur à la moyenne de la même semaine de l'année."""
    clim = collections.defaultdict(list)
    for r, x in zip(rows, values):
        if x is not None:
            clim[week(r)].append(x)
    mean = {w: st.mean(v) for w, v in clim.items()}
    return [x - mean[week(r)] if x is not None else None for r, x in zip(rows, values)]


def anomalies(rows):
    """Écart hebdomadaire du log du débit à sa moyenne pour la même semaine de l'année."""
    return deseason(rows, [math.log(q) if (q := fnum(r["debit"])) is not None and q > 0 else None for r in rows])


def soil_anomalies(rows):
    return deseason(rows, [fnum(r["humidite"]) for r in rows])


def recovery_2022(rows, qa, sa):
    """(semaine de retour du sol, semaine de retour de la rivière) depuis REPLAY_START, ou None."""
    dates = [r["date"] for r in rows]
    i0 = next(i for i, d in enumerate(dates) if d >= DRY_WINDOW[0])
    i1 = next(i for i, d in enumerate(dates) if d >= DRY_WINDOW[1])
    start = next(i for i, d in enumerate(dates) if d >= REPLAY_START)
    imin = min(range(i0, i1), key=lambda i: sa[i])
    soil = next((i for i in range(imin, min(imin + 52, len(sa))) if sa[i] >= 0), None)
    river = next((i for i in range(imin, min(imin + 60, len(qa) - 1))
                  if qa[i] is not None and qa[i] >= 0 and qa[i + 1] is not None and qa[i + 1] >= 0), None)
    return (soil - start if soil is not None else None, river - start if river is not None else None)


def acf(an, k):
    pairs = [(a, b) for a, b in zip(an, an[k:]) if a is not None and b is not None]
    if len(pairs) < 50:
        return None
    return st.correlation([a for a, _ in pairs], [b for _, b in pairs])


def memory(an, max_lag=60):
    """Temps de retour à la normale (semaines) d'une série d'écarts désaisonnalisés."""
    return next((k for k in range(1, max_lag) if (acf(an, k) or 0) < SEUIL), max_lag)


# ---------- Réseau hydrographique ----------

def norm_name(s):
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(le|la|les|l|de|du|des|d|ruisseau|riviere|canal|bras)\b|'", " ", s)
    return " ".join(s.split())


# Premiers mots désignant un tracé secondaire (bras, canal…) plutôt que la rivière elle-même
SECONDARY = {"bras", "canal", "contre", "ancien", "ancienne", "fossé", "fosse", "noue", "rigole", "dérivation"}


def proj(p):
    return (p[0] * KX, p[1] * KY)


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = 0 if d2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy), t


class Network:
    def __init__(self):
        self.feats = {f["properties"]["CdOH"]: f for f in json.loads(SRC.read_text())["features"] if f["geometry"]}
        with (OUTPUT / "affluents_cours_eau.csv").open(encoding="utf-8") as fh:
            self.parent = {r["CdOH"]: r["se_jette_dans_CdOH"] for r in csv.DictReader(fh, delimiter=";")}
        with (OUTPUT / "virages_cours_eau.csv").open(encoding="utf-8") as fh:
            self.virages = {r["CdOH"]: r for r in csv.DictReader(fh, delimiter=";")}
        self.children = collections.defaultdict(list)
        for c, p in self.parent.items():
            if p:
                self.children[p].append(c)
        self._chain, self._net = {}, {}
        self.grid = collections.defaultdict(list)
        cell = 0.01
        for c, f in self.feats.items():
            for line in self.lines(c):
                for a, b in zip(line, line[1:]):
                    for cx in range(int(min(a[0], b[0]) / cell), int(max(a[0], b[0]) / cell) + 1):
                        for cy in range(int(min(a[1], b[1]) / cell), int(max(a[1], b[1]) / cell) + 1):
                            self.grid[(cx, cy)].append((c, a, b))

    def lines(self, c):
        g = self.feats[c]["geometry"]
        return g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]

    def link(self, lon, lat, name):
        """Cours d'eau de la station : même nom à moins de 2 km, sinon le plus proche à moins de 150 m.

        La comparaison des noms ignore « bras », « canal »… : à nom égal, on préfère donc un
        tracé de même nature que le cours d'eau de la station (la Seine plutôt que le Bras
        de la Seine, sauf si la station est elle-même sur un bras), puis le plus proche."""
        p, want, best = proj((lon, lat)), norm_name(name), {}
        secondary = lambda n: (n or "").split(" ")[0].lower() in SECONDARY
        station_secondary = secondary(name)
        for cx in range(int(lon / 0.01) - 2, int(lon / 0.01) + 3):
            for cy in range(int(lat / 0.01) - 2, int(lat / 0.01) + 3):
                for c, a, b in self.grid.get((cx, cy), ()):
                    d, _ = seg_dist(p, proj(a), proj(b))
                    if d < best.get(c, 1e18):
                        best[c] = d
        named = [(secondary(self.feats[c]["properties"]["TopoOH"]) != station_secondary, d, c)
                 for c, d in best.items() if norm_name(self.feats[c]["properties"]["TopoOH"]) == want and d < 2000]
        if named:
            return min(named)[2]
        near = min(best, key=best.get, default=None)
        return near if near and best[near] < 150 else None

    def chain(self, c):
        """Parties de l'amont vers l'aval : [(points projetés, points lon/lat, distances cumulées)], longueur."""
        if c not in self._chain:
            out, cum = [], 0.0
            for part, _, _ in order_parts(self.lines(c)):
                xy = [proj(p) for p in part]
                d = [cum]
                for a, b in zip(xy, xy[1:]):
                    d.append(d[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
                cum = d[-1]
                out.append((xy, part, d))
            self._chain[c] = (out, cum)
        return self._chain[c]

    def locate(self, c, lonlat):
        """Distance (m) depuis l'amont du point du cours d'eau le plus proche de lonlat."""
        p, best = proj(lonlat), (1e18, 0.0)
        for xy, _, d in self.chain(c)[0]:
            for i, (a, b) in enumerate(zip(xy, xy[1:])):
                e, t = seg_dist(p, a, b)
                if e < best[0]:
                    best = (e, d[i] + t * (d[i + 1] - d[i]))
        return best[1]

    def strahler(self, c, seen=()):
        """Rang de Strahler du cours d'eau entier (1 = ruisseau sans affluent)."""
        if not hasattr(self, "_order"):
            self._order = {}
        if c not in self._order:
            kids = [self.strahler(b, seen + (c,)) for b in self.children[c] if b not in seen]
            self._order[c] = 1 if not kids else max(kids) + (kids.count(max(kids)) >= 2)
        return self._order[c]

    def mouth(self, c):
        _, part, _ = self.chain(c)[0][-1]
        return part[-1]

    def network(self, c, seen=None):
        """(km, liste des cours d'eau) du réseau complet de c, c compris."""
        if c in self._net:
            return self._net[c]
        seen = seen if seen is not None else set()
        seen.add(c)
        km, members = self.chain(c)[1] / 1000, [c]
        for b in self.children[c]:
            if b not in seen:
                k2, m2 = self.network(b, seen)
                km += k2
                members += m2
        self._net[c] = (km, members)
        return self._net[c]

    def upstream(self, c, lonlat):
        """Réseau qui alimente le point : (km, cours d'eau affluents, position du point sur c en m)."""
        pos = self.locate(c, lonlat)
        km, members = pos / 1000, []
        for b in self.children[c]:
            if self.locate(c, self.mouth(b)) <= pos + 50:
                k2, m2 = self.network(b)
                km += k2
                members += m2
        return km, members, pos

    def cut_upstream(self, c, pos):
        """Géométrie (lon/lat) de c depuis sa source jusqu'à la position pos (m)."""
        out = []
        for xy, part, d in self.chain(c)[0]:
            if d[0] >= pos:
                break
            keep = [p for p, di in zip(part, d) if di <= pos]
            if len(keep) >= 2:
                out.append(keep)
        return out


def analyse(stations=None, net=None):
    stations = stations or load_stations()
    net = net or Network()
    res = []
    for site, rows in stations.items():
        q = [fnum(r["debit"]) for r in rows]
        valid = [x for x in q if x is not None and x > 0]
        if len(valid) < 0.5 * len(q):
            continue
        r0 = rows[0]
        lon, lat = float(r0["lon"]), float(r0["lat"])
        c = net.link(lon, lat, r0["libelle_cours_eau"])
        if c is None:
            continue
        up_km, up_members, pos = net.upstream(c, (lon, lat))
        med = st.median(valid)
        pos_km = pos / 1000

        ditch_km = sum(net.chain(m)[1] for m in up_members
                       if net.virages.get(m, {}).get("type") in ("fosse", "canal")) / 1000
        kids = [net.strahler(b) for b in net.children[c] if b in set(up_members)]
        order = 1 if not kids else max(kids) + (kids.count(max(kids)) >= 2)
        qa, sa = anomalies(rows), soil_anomalies(rows)
        soil_back, river_back = recovery_2022(rows, qa, sa)
        rain = sum(fnum(r["pluie"]) for r in rows)
        snow = sum(fnum(r["neige"]) for r in rows)

        def summer(y0, y1):
            xs = [x / med for x, r in zip(q, rows)
                  if x is not None and y0 <= int(r["annee"]) <= y1 and 25 <= int(r["num_semaine"]) <= 38]
            return st.median(xs) if xs else None

        s1, s2 = summer(*P1), summer(*P2)
        res.append({
            "site": site,
            "nom": r0["libelle_site"],
            "riviere": net.feats[c]["properties"]["TopoOH"],
            "CdOH": c,
            "dept": r0["libelle_dept"],
            "lat": lat,
            "lon": lon,
            "suivi_secheresse": int(r0["etiage"] == "1"),
            "inertie_sem": memory(qa),
            "affluents_amont": len(up_members),
            "km_reseau_amont": round(up_km, 1),
            "position_m": round(pos),
            "rang_strahler": order,
            "affluents_par_km_riviere": round(len(up_members) / pos_km, 3) if pos_km > 1 else "",
            "virages_par_km": float(net.virages[c]["virages_par_km"]),
            "part_fosses_canaux_amont": round(ditch_km / up_km, 4) if up_km > 0 else "",
            "inertie_sol_sem": memory(sa),
            "inertie_pluie_sem": memory(deseason(rows, [fnum(r["pluie"]) for r in rows])),
            "humidite_sol_moyenne": round(st.mean(fnum(r["humidite"]) for r in rows), 4),
            "part_neige": round(snow / (rain + snow), 4),
            "temperature_moyenne": round(st.mean(fnum(r["temp"]) for r in rows), 2),
            "sol_retour_2022": "" if soil_back is None else soil_back,
            "riviere_retour_2022": "" if river_back is None else river_back,
            "ete_2000_2012": round(s1, 4) if s1 is not None else "",
            "ete_2014_2026": round(s2, 4) if s2 is not None else "",
            "evolution_ete": round(s2 / s1 - 1, 4) if s1 and s2 else "",
        })
    return res


def main():
    res = analyse()
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=res[0].keys(), delimiter=";")
        w.writeheader()
        w.writerows(res)
    t = [r["inertie_sem"] for r in res]
    print(f"{len(res)} stations -> {OUT} ; retour à la normale en {min(t)} à {max(t)} semaines (médiane {st.median(t)})")


if __name__ == "__main__":
    main()
