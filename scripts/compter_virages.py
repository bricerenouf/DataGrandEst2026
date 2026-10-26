"""Compte les virages de chaque cours d'eau et calcule leur sinuosité.

Méthode, pour chaque partie du tracé :
  1. projection locale en mètres, puis simplification Douglas-Peucker (TOL_M) pour
     retirer le bruit de numérisation ;
  2. angle de changement de direction (signé) à chaque sommet ;
  3. les changements successifs dans le même sens forment un même tournant (à gauche
     ou à droite) ; les déviations inférieures à --neutre ne l'interrompent pas ;
  4. un tournant s'arrête quand le cours d'eau change de côté, ou quand il rencontre
     une ligne droite : un segment simplifié d'au moins --droite mètres dont le tracé
     d'origine s'écarte de moins de --ecart (2 %) de sa longueur. Un segment qui
     résume un morceau de courbe s'écarte davantage et ne coupe donc pas la courbe ;
  5. chaque tournant dont la rotation cumulée atteint --seuil compte pour un virage.
     Gauche puis droite = 2 virages ; gauche, ligne droite, gauche = 2 virages.
Avec une simplification à 10 m, une ligne droite n'est reconnue qu'à partir
d'environ 150 m : plus courte, elle se confond avec les courbes voisines.
Sinuosité = longueur du tracé / distance à vol d'oiseau entre ses extrémités.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_carte_cours_eau import SRC, categorize, simplify  # noqa: E402

OUT = ROOT / "output" / "virages_cours_eau.csv"


def project(line):
    lat0 = sum(p[1] for p in line) / len(line)
    kx, ky = 111320 * math.cos(math.radians(lat0)), 110570
    return [(x * kx, y * ky) for x, y in line]


def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    t = 0 if d2 == 0 else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def turn_angle(a, b, c):
    """Changement de direction en b, en degrés ; positif = à gauche."""
    h1 = math.atan2(b[1] - a[1], b[0] - a[0])
    h2 = math.atan2(c[1] - b[1], c[0] - b[0])
    return math.degrees((h2 - h1 + math.pi) % (2 * math.pi) - math.pi)


def find_bends(xy, tol, seuil, neutre, droite, ecart):
    """Virages du tracé (voir la méthode en tête de fichier).

    Renvoie une liste de (indice du sommet le plus marqué dans xy, côté) où côté vaut
    +1 pour un virage à gauche et -1 à droite, dans le sens du courant."""
    kept = simplify(xy, tol, projected=True, indices=True)
    straight = []
    for i, j in zip(kept, kept[1:]):
        L = math.hypot(xy[j][0] - xy[i][0], xy[j][1] - xy[i][1])
        dev = max((seg_dist(xy[k], xy[i], xy[j]) for k in range(i + 1, j)), default=0.0)
        straight.append(L >= droite and dev <= ecart * L)
    pts = [xy[i] for i in kept]
    bends, acc, apex, best = [], 0.0, None, 0.0

    def close():
        if abs(acc) >= seuil:
            bends.append((apex, 1 if acc > 0 else -1))

    for v in range(1, len(pts) - 1):
        a = turn_angle(pts[v - 1], pts[v], pts[v + 1])
        after_straight = straight[v - 1]
        if abs(a) < neutre and not after_straight:
            acc += a
            continue
        if acc and (after_straight or (a > 0) != (acc > 0)):
            close()
            acc, apex, best = 0.0, None, 0.0
        acc += a
        if abs(a) > best:
            best, apex = abs(a), kept[v]
    close()
    return bends


def count_bends(xy, tol, seuil, neutre, droite, ecart):
    return len(find_bends(xy, tol, seuil, neutre, droite, ecart))


def length(xy):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(xy, xy[1:]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tol", type=float, default=10.0, help="tolérance de simplification en m (défaut 10)")
    ap.add_argument("--seuil", type=float, default=45.0, help="rotation minimale d'un tournant en degrés (défaut 45)")
    ap.add_argument("--neutre", type=float, default=5.0, help="déviation ignorée en degrés (défaut 5)")
    ap.add_argument("--droite", type=float, default=10.0, help="longueur minimale d'une ligne droite en m (défaut 10)")
    ap.add_argument("--ecart", type=float, default=0.02, help="écart maximal d'une ligne droite, en part de sa longueur (défaut 0,02)")
    args = ap.parse_args()

    rows = []
    for f in json.loads(SRC.read_text())["features"]:
        p, g = f["properties"], f["geometry"]
        if not g:
            continue
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        n = tot_len = weighted_sin = 0
        for line in lines:
            xy = project(line)
            L = length(xy)
            if L == 0:
                continue
            n += count_bends(xy, args.tol, args.seuil, args.neutre, args.droite, args.ecart)
            chord = math.hypot(xy[-1][0] - xy[0][0], xy[-1][1] - xy[0][1])
            weighted_sin += L * (L / chord if chord > 0 else 1)
            tot_len += L
        km = tot_len / 1000
        rows.append({
            "CdOH": p.get("CdOH"),
            "nom": p.get("TopoOH") or "",
            "type": categorize(p.get("TopoOH")),
            "longueur_km": round(km, 3),
            "nb_parties": len(lines),
            "nb_virages": n,
            "virages_par_km": round(n / km, 2) if km else 0,
            "sinuosite": round(weighted_sin / tot_len, 3) if tot_len else 1,
        })

    rows.sort(key=lambda r: -r["nb_virages"])
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys(), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} cours d'eau, {sum(r['nb_virages'] for r in rows)} virages -> {OUT}")


if __name__ == "__main__":
    main()
