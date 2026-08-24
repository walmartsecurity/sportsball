"""Regenerate the bundled sample projections CSV.

The numbers here are ILLUSTRATIVE, not a real projection set. They exist so
that `sportsball` runs end to end out of the box and so the tests have a
realistically shaped board to work against. Player names and teams are a
plausible-looking 2026 snapshot and should not be trusted for an actual draft;
point `--projections` at a real export instead.
"""

import csv
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "sportsball" / "data" / "sample_projections.csv"

# name, team, pass_att, pass_yds, pass_td, int, rush_att, rush_yds, rush_td
QB = [
    ("Joe Burrow", "CIN", 620, 4700, 38, 11, 45, 180, 2),
    ("Josh Allen", "BUF", 560, 4100, 30, 11, 105, 560, 10),
    ("Lamar Jackson", "BAL", 480, 3900, 32, 7, 140, 800, 5),
    ("Jayden Daniels", "WAS", 530, 4000, 28, 9, 130, 750, 6),
    ("Patrick Mahomes", "KC", 590, 4300, 30, 10, 65, 320, 3),
    ("Jalen Hurts", "PHI", 480, 3500, 24, 9, 140, 620, 12),
    ("Jared Goff", "DET", 550, 4200, 30, 11, 25, 60, 1),
    ("Justin Herbert", "LAC", 560, 4000, 26, 10, 55, 260, 3),
    ("C.J. Stroud", "HOU", 550, 4100, 27, 11, 45, 220, 3),
    ("Baker Mayfield", "TB", 560, 4100, 30, 13, 50, 220, 3),
    ("Dak Prescott", "DAL", 570, 4200, 29, 11, 35, 130, 2),
    ("Bo Nix", "DEN", 540, 3800, 25, 11, 85, 420, 5),
    ("Drake Maye", "NE", 545, 3950, 26, 11, 70, 380, 4),
    ("Caleb Williams", "CHI", 540, 3900, 26, 12, 70, 350, 4),
    ("Brock Purdy", "SF", 520, 4000, 27, 10, 45, 200, 2),
    ("Kyler Murray", "ARI", 520, 3700, 23, 10, 85, 480, 5),
    ("Matthew Stafford", "LAR", 560, 4000, 28, 11, 25, 60, 1),
    ("Trevor Lawrence", "JAX", 540, 3800, 24, 11, 55, 250, 3),
    ("Tua Tagovailoa", "MIA", 520, 3800, 25, 11, 25, 50, 1),
    ("Michael Penix Jr.", "ATL", 540, 3800, 24, 12, 40, 150, 2),
    ("J.J. McCarthy", "MIN", 500, 3500, 23, 12, 55, 250, 3),
    ("Justin Fields", "NYJ", 440, 2900, 18, 9, 120, 650, 6),
]

# name, team, rush_att, rush_yds, rush_td, targets, rec, rec_yds, rec_td
RB = [
    ("Bijan Robinson", "ATL", 300, 1450, 11, 85, 68, 570, 3),
    ("Saquon Barkley", "PHI", 305, 1500, 12, 55, 42, 320, 2),
    ("Jahmyr Gibbs", "DET", 250, 1250, 11, 80, 62, 520, 4),
    ("De'Von Achane", "MIA", 230, 1050, 8, 95, 76, 620, 4),
    ("Ashton Jeanty", "LV", 290, 1300, 10, 60, 46, 360, 2),
    ("Christian McCaffrey", "SF", 240, 1050, 8, 90, 72, 580, 3),
    ("Jonathan Taylor", "IND", 290, 1350, 11, 40, 30, 230, 1),
    ("Derrick Henry", "BAL", 280, 1350, 13, 25, 18, 140, 1),
    ("Bucky Irving", "TB", 250, 1150, 8, 60, 47, 380, 2),
    ("Josh Jacobs", "GB", 265, 1150, 10, 50, 38, 300, 2),
    ("Kyren Williams", "LAR", 265, 1100, 10, 45, 34, 260, 1),
    ("James Cook", "BUF", 235, 1050, 9, 45, 34, 270, 2),
    ("Chase Brown", "CIN", 245, 1050, 8, 65, 50, 400, 2),
    ("Omarion Hampton", "LAC", 240, 1000, 7, 45, 34, 260, 1),
    ("Kenneth Walker III", "SEA", 230, 980, 7, 45, 34, 260, 1),
    ("Breece Hall", "NYJ", 220, 920, 6, 60, 46, 360, 2),
    ("Alvin Kamara", "NO", 200, 820, 5, 80, 62, 490, 2),
    ("TreVeyon Henderson", "NE", 210, 900, 6, 50, 38, 300, 2),
    ("Chuba Hubbard", "CAR", 230, 950, 7, 45, 34, 250, 1),
    ("Tony Pollard", "TEN", 220, 900, 6, 50, 38, 290, 1),
    ("David Montgomery", "DET", 190, 800, 9, 30, 23, 180, 1),
    ("Aaron Jones", "MIN", 200, 830, 5, 55, 42, 330, 1),
    ("Quinshon Judkins", "CLE", 215, 880, 6, 35, 27, 200, 1),
    ("RJ Harvey", "DEN", 190, 800, 6, 45, 34, 270, 2),
    ("Kaleb Johnson", "PIT", 200, 840, 7, 30, 23, 170, 1),
    ("Isiah Pacheco", "KC", 190, 790, 6, 35, 27, 200, 1),
    ("Brian Robinson Jr.", "WAS", 175, 730, 6, 25, 19, 140, 1),
    ("Travis Etienne Jr.", "JAX", 165, 680, 4, 40, 31, 240, 1),
    ("Javonte Williams", "DAL", 170, 700, 4, 40, 30, 220, 1),
    ("Tyrone Tracy Jr.", "NYG", 165, 680, 4, 45, 34, 260, 1),
    ("Trey Benson", "ARI", 155, 650, 5, 25, 19, 140, 1),
    ("Rhamondre Stevenson", "NE", 150, 620, 3, 40, 31, 240, 1),
    ("Jaylen Warren", "PIT", 145, 610, 3, 45, 35, 280, 1),
    ("Rico Dowdle", "CAR", 150, 620, 3, 30, 23, 170, 1),
    ("Najee Harris", "LAC", 150, 600, 4, 25, 19, 140, 0),
    ("Cam Skattebo", "NYG", 145, 590, 4, 35, 27, 200, 1),
    ("Jordan Mason", "MIN", 140, 590, 4, 20, 15, 110, 0),
    ("Zach Charbonnet", "SEA", 130, 550, 5, 30, 23, 180, 1),
    ("Nick Chubb", "HOU", 130, 520, 4, 15, 11, 80, 0),
    ("Tank Bigsby", "JAX", 130, 550, 4, 15, 11, 80, 0),
    ("Braelon Allen", "NYJ", 130, 540, 4, 25, 19, 140, 1),
    ("Bhayshul Tuten", "JAX", 125, 530, 3, 25, 19, 150, 1),
    ("Dylan Sampson", "CLE", 120, 500, 3, 30, 23, 180, 1),
    ("Ray Davis", "BUF", 110, 460, 3, 25, 19, 140, 1),
    ("Tyjae Spears", "TEN", 105, 440, 2, 30, 23, 170, 1),
    ("Isaac Guerendo", "SF", 100, 430, 3, 25, 19, 140, 1),
    ("Blake Corum", "LAR", 100, 420, 2, 20, 15, 110, 0),
    ("Austin Ekeler", "WAS", 100, 400, 2, 50, 39, 300, 1),
    ("Jaydon Blue", "DAL", 90, 380, 2, 30, 23, 170, 1),
    ("Devin Neal", "NO", 95, 390, 2, 25, 19, 140, 0),
    ("Roschon Johnson", "CHI", 95, 390, 3, 20, 15, 110, 0),
    ("Will Shipley", "PHI", 90, 380, 2, 25, 19, 140, 0),
    ("Kimani Vidal", "LAC", 85, 350, 2, 20, 15, 110, 0),
    ("Jerome Ford", "CLE", 85, 350, 2, 25, 19, 140, 0),
    ("MarShawn Lloyd", "GB", 80, 340, 2, 20, 15, 110, 0),
]

# name, team, targets, rec, rec_yds, rec_td, rush_att, rush_yds, rush_td
WR = [
    ("Ja'Marr Chase", "CIN", 175, 118, 1650, 13, 3, 15, 0),
    ("Justin Jefferson", "MIN", 165, 108, 1550, 10, 2, 10, 0),
    ("CeeDee Lamb", "DAL", 165, 110, 1500, 9, 6, 35, 0),
    ("Puka Nacua", "LAR", 155, 108, 1400, 7, 12, 70, 1),
    ("Malik Nabers", "NYG", 165, 105, 1400, 8, 4, 20, 0),
    ("Amon-Ra St. Brown", "DET", 150, 108, 1300, 10, 5, 25, 0),
    ("Brian Thomas Jr.", "JAX", 150, 95, 1350, 9, 5, 30, 0),
    ("Nico Collins", "HOU", 145, 92, 1350, 9, 2, 10, 0),
    ("Drake London", "ATL", 155, 100, 1300, 9, 2, 8, 0),
    ("A.J. Brown", "PHI", 140, 88, 1300, 9, 2, 10, 0),
    ("Tyreek Hill", "MIA", 145, 92, 1250, 8, 8, 50, 0),
    ("Jaxon Smith-Njigba", "SEA", 140, 95, 1200, 7, 5, 30, 0),
    ("Garrett Wilson", "NYJ", 150, 96, 1200, 7, 3, 15, 0),
    ("Ladd McConkey", "LAC", 140, 94, 1200, 7, 4, 25, 0),
    ("Marvin Harrison Jr.", "ARI", 140, 88, 1200, 9, 2, 10, 0),
    ("Xavier Worthy", "KC", 130, 85, 1100, 7, 20, 120, 1),
    ("Rome Odunze", "CHI", 135, 85, 1150, 8, 2, 10, 0),
    ("Terry McLaurin", "WAS", 130, 82, 1150, 9, 2, 10, 0),
    ("DK Metcalf", "PIT", 130, 80, 1150, 8, 2, 10, 0),
    ("Mike Evans", "TB", 125, 78, 1100, 10, 0, 0, 0),
    ("Davante Adams", "LAR", 130, 82, 1100, 9, 1, 5, 0),
    ("Jaylen Waddle", "MIA", 125, 82, 1050, 5, 4, 25, 0),
    ("Tee Higgins", "CIN", 120, 76, 1050, 8, 1, 5, 0),
    ("Courtland Sutton", "DEN", 125, 78, 1050, 7, 1, 5, 0),
    ("DJ Moore", "CHI", 130, 85, 1050, 6, 10, 60, 1),
    ("Zay Flowers", "BAL", 125, 84, 1050, 6, 8, 50, 0),
    ("Chris Olave", "NO", 130, 84, 1050, 6, 2, 10, 0),
    ("Tetairoa McMillan", "CAR", 130, 82, 1050, 6, 1, 5, 0),
    ("George Pickens", "DAL", 120, 74, 1050, 6, 2, 12, 0),
    ("Jameson Williams", "DET", 110, 68, 1050, 7, 12, 80, 1),
    ("Calvin Ridley", "TEN", 120, 72, 1000, 6, 1, 5, 0),
    ("Jerry Jeudy", "CLE", 125, 78, 1000, 5, 2, 10, 0),
    ("Rashee Rice", "KC", 115, 78, 950, 7, 6, 35, 0),
    ("Jordan Addison", "MIN", 110, 70, 950, 6, 2, 12, 0),
    ("Khalil Shakir", "BUF", 115, 82, 900, 5, 8, 45, 0),
    ("Emeka Egbuka", "TB", 110, 70, 900, 6, 3, 18, 0),
    ("Travis Hunter", "JAX", 110, 70, 900, 6, 4, 25, 0),
    ("Keon Coleman", "BUF", 105, 62, 900, 6, 1, 5, 0),
    ("Ricky Pearsall", "SF", 105, 68, 900, 5, 5, 30, 0),
    ("Deebo Samuel Sr.", "WAS", 105, 72, 850, 5, 30, 180, 2),
    ("Michael Pittman Jr.", "IND", 110, 72, 850, 5, 1, 5, 0),
    ("Josh Downs", "IND", 105, 72, 850, 5, 3, 18, 0),
    ("Chris Godwin", "TB", 105, 70, 850, 5, 4, 22, 0),
    ("Jauan Jennings", "SF", 100, 66, 850, 6, 2, 10, 0),
    ("Stefon Diggs", "NE", 105, 68, 850, 5, 1, 5, 0),
    ("Darnell Mooney", "ATL", 100, 60, 850, 5, 1, 5, 0),
    ("Rashid Shaheed", "NO", 95, 58, 850, 5, 6, 40, 0),
    ("Matthew Golden", "GB", 100, 62, 850, 5, 4, 25, 0),
    ("Jayden Reed", "GB", 95, 62, 800, 5, 10, 65, 1),
    ("Cooper Kupp", "SEA", 100, 68, 800, 4, 2, 10, 0),
    ("Marvin Mims Jr.", "DEN", 90, 58, 800, 5, 8, 55, 0),
    ("Brandon Aiyuk", "SF", 95, 60, 780, 4, 1, 5, 0),
    ("Jakobi Meyers", "LV", 100, 66, 780, 4, 2, 10, 0),
    ("Christian Kirk", "HOU", 95, 62, 750, 4, 2, 10, 0),
    ("Wan'Dale Robinson", "NYG", 105, 74, 750, 3, 6, 35, 0),
    ("Luther Burden III", "CHI", 90, 56, 720, 4, 8, 50, 0),
    ("Tre Tucker", "LV", 90, 56, 720, 4, 5, 32, 0),
    ("Quentin Johnston", "LAC", 90, 54, 720, 5, 1, 5, 0),
    ("Alec Pierce", "IND", 75, 42, 720, 4, 0, 0, 0),
    ("Jalen McMillan", "TB", 85, 52, 700, 5, 1, 5, 0),
    ("Adonai Mitchell", "IND", 85, 50, 700, 4, 3, 18, 0),
    ("Kyle Williams", "NE", 85, 52, 700, 4, 2, 12, 0),
    ("Romeo Doubs", "GB", 85, 54, 680, 4, 1, 5, 0),
    ("Xavier Legette", "CAR", 85, 50, 650, 4, 6, 38, 0),
    ("Cedric Tillman", "CLE", 85, 52, 650, 4, 1, 5, 0),
    ("Tank Dell", "HOU", 75, 46, 600, 4, 3, 18, 0),
    ("Dontayvion Wicks", "GB", 75, 46, 600, 4, 1, 5, 0),
    ("Joshua Palmer", "BUF", 80, 48, 620, 3, 0, 0, 0),
    ("Elic Ayomanor", "TEN", 80, 48, 620, 4, 1, 5, 0),
    ("DeMario Douglas", "NE", 80, 54, 620, 3, 4, 25, 0),
    ("Andrei Iosivas", "CIN", 75, 46, 580, 5, 1, 5, 0),
    ("Isaiah Bond", "CLE", 75, 44, 580, 3, 4, 25, 0),
    ("Roman Wilson", "PIT", 75, 46, 580, 3, 2, 12, 0),
    ("Jalen Coker", "CAR", 75, 46, 570, 3, 0, 0, 0),
    ("Kayshon Boutte", "NE", 70, 44, 560, 4, 0, 0, 0),
]

# name, team, targets, rec, rec_yds, rec_td
TE = [
    ("Brock Bowers", "LV", 145, 105, 1200, 8),
    ("Trey McBride", "ARI", 140, 100, 1050, 7),
    ("George Kittle", "SF", 105, 78, 950, 7),
    ("T.J. Hockenson", "MIN", 105, 75, 800, 5),
    ("Sam LaPorta", "DET", 100, 72, 800, 6),
    ("Travis Kelce", "KC", 100, 72, 750, 5),
    ("David Njoku", "CLE", 100, 70, 720, 5),
    ("Tyler Warren", "IND", 95, 66, 720, 5),
    ("Tucker Kraft", "GB", 90, 62, 720, 6),
    ("Mark Andrews", "BAL", 90, 62, 700, 7),
    ("Colston Loveland", "CHI", 90, 62, 700, 5),
    ("Evan Engram", "DEN", 90, 66, 620, 4),
    ("Dallas Goedert", "PHI", 80, 56, 620, 5),
    ("Jonnu Smith", "PIT", 85, 62, 620, 4),
    ("Dalton Kincaid", "BUF", 85, 58, 620, 5),
    ("Kyle Pitts Sr.", "ATL", 85, 56, 620, 4),
    ("Jake Ferguson", "DAL", 85, 60, 580, 4),
    ("Hunter Henry", "NE", 80, 56, 560, 4),
    ("Zach Ertz", "WAS", 80, 58, 540, 5),
    ("Cade Otton", "TB", 80, 56, 540, 4),
    ("Isaiah Likely", "BAL", 70, 48, 520, 5),
    ("Brenton Strange", "JAX", 75, 52, 520, 3),
    ("Pat Freiermuth", "PIT", 75, 52, 520, 4),
    ("Juwan Johnson", "NO", 75, 52, 520, 4),
    ("Mike Gesicki", "CIN", 70, 50, 500, 3),
    ("Chig Okonkwo", "TEN", 70, 48, 500, 3),
    ("Harold Fannin Jr.", "CLE", 70, 48, 480, 3),
    ("Elijah Arroyo", "SEA", 65, 44, 480, 3),
    ("Theo Johnson", "NYG", 70, 46, 460, 4),
    ("Ja'Tavion Sanders", "CAR", 65, 44, 440, 2),
    ("Dalton Schultz", "HOU", 65, 46, 440, 3),
    ("Noah Fant", "SEA", 60, 42, 420, 2),
    ("Luke Musgrave", "GB", 55, 38, 400, 3),
    ("Michael Mayer", "LV", 55, 38, 380, 2),
    ("AJ Barner", "SEA", 55, 38, 380, 4),
]

HEADER = [
    "name", "position", "team", "games",
    "pass_att", "pass_cmp", "pass_yds", "pass_td", "int",
    "rush_att", "rush_yds", "rush_td",
    "targets", "rec", "rec_yds", "rec_td",
    "fumbles_lost", "two_point",
]


def row(name, pos, team, **kw):
    base = {key: 0 for key in HEADER}
    base.update({"name": name, "position": pos, "team": team, "games": 17})
    base.update(kw)
    return [base[key] for key in HEADER]


def main() -> None:
    rows = []
    for name, team, att, yds, td, ints, ra, ry, rtd in QB:
        rows.append(row(
            name, "QB", team, pass_att=att, pass_cmp=round(att * 0.655),
            pass_yds=yds, pass_td=td, **{"int": ints},
            rush_att=ra, rush_yds=ry, rush_td=rtd,
            fumbles_lost=round(att * 0.008, 1), two_point=0.4,
        ))
    for name, team, ra, ry, rtd, tgt, rec, ryds, rtds in RB:
        rows.append(row(
            name, "RB", team, rush_att=ra, rush_yds=ry, rush_td=rtd,
            targets=tgt, rec=rec, rec_yds=ryds, rec_td=rtds,
            fumbles_lost=round(ra * 0.006, 1), two_point=0.2,
        ))
    for name, team, tgt, rec, ryds, rtds, ra, ry, rtd in WR:
        rows.append(row(
            name, "WR", team, targets=tgt, rec=rec, rec_yds=ryds, rec_td=rtds,
            rush_att=ra, rush_yds=ry, rush_td=rtd,
            fumbles_lost=round(rec * 0.008, 1), two_point=0.2,
        ))
    for name, team, tgt, rec, ryds, rtds in TE:
        rows.append(row(
            name, "TE", team, targets=tgt, rec=rec, rec_yds=ryds, rec_td=rtds,
            fumbles_lost=round(rec * 0.005, 1), two_point=0.2,
        ))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"wrote {len(rows)} players to {OUT}")


if __name__ == "__main__":
    main()
