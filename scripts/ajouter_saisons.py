"""Ajoute à data/donnees.csv les colonnes annee, num_semaine et saison, calculées à
partir de la colonne semaine (format 2000W01 : année 2000, semaine 01).

Saisons :
  Printemps : W12 à W24
  Ete       : W25 à W38
  Automne   : W39 à W50
  Hiver     : W51 à W53 et W01 à W11 (la semaine 53, présente certaines années,
              tombe fin décembre et rejoint l'hiver)

Les colonnes d'origine ne sont pas modifiées. Le script peut être relancé : les trois
colonnes sont recalculées si elles existent déjà.
"""
import csv
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "donnees.csv"
NEW = ["annee", "num_semaine", "saison"]


def saison(w):
    if 12 <= w <= 24:
        return "Printemps"
    if 25 <= w <= 38:
        return "Ete"
    if 39 <= w <= 50:
        return "Automne"
    return "Hiver"


def main():
    tmp = CSV.with_suffix(".csv.tmp")
    with CSV.open(newline="", encoding="utf-8") as fin, tmp.open("w", newline="", encoding="utf-8") as fout:
        reader = csv.reader(fin)
        header = next(reader)
        keep = [i for i, c in enumerate(header) if c not in NEW]
        header = [header[i] for i in keep]
        pos = header.index("semaine") + 1
        writer = csv.writer(fout, lineterminator="\n")
        writer.writerow(header[:pos] + NEW + header[pos:])
        n = 0
        for row in reader:
            row = [row[i] for i in keep]
            m = re.fullmatch(r"(\d{4})W(\d{2})", row[pos - 1])
            if not m:
                raise ValueError(f"ligne {n + 2} : semaine inattendue {row[pos - 1]!r}")
            annee, sem = m.groups()
            writer.writerow(row[:pos] + [annee, sem, saison(int(sem))] + row[pos:])
            n += 1
    os.replace(tmp, CSV)
    print(f"{n} lignes mises à jour -> {CSV}")


if __name__ == "__main__":
    main()
