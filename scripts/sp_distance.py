# sp_distance.py
# S-P時間から震央距離を求める(走時表。ObsPy の TauP、速度構造 iasp91 で計算した表)
#   大森公式(距離 = k × S-P時間)の k は距離によって変わる(深さ 10 km で、150 km までは 8.0、300 km で 8.9、730 km で 9.6)ため、
#   遠い地震の距離は、この表から求める
#   深さは分からないので、ASSUMED_DEPTH を仮定する。震源リストと対応した遠い地震94件(震源の深さの中央値 46 km)で、
#   深さ 30〜40 km を仮定したときに震源リストの震央距離に最も近くなった
#   (自動の検出の S-P時間から求めた距離と震源リストの震央距離の差の中央値: k = 8.0 で -35 km、この表で -12 km)
#
# 表を作り直すとき(速度構造や深さを変えるとき。プロジェクトのフォルダで):
#   python3 scripts/sp_distance.py --make
import argparse

import numpy as np

# 仮定する震源の深さ [km]
ASSUMED_DEPTH = 30.0

# (震央距離 [km], S-P時間 [秒])。iasp91、深さ ASSUMED_DEPTH。最初に届く P波・S波(p・P・Pn、s・S・Sn)の差
#   S-P時間が距離 0 でも 3.6 秒あるのは、震源の深さ(30 km)の分
TABLE = [
  (0, 3.63), (10, 3.83), (20, 4.36), (30, 5.12), (40, 6.03), (50, 7.02),
  (60, 8.06), (70, 9.13), (80, 10.22), (90, 11.49), (100, 12.52), (110, 13.51),
  (120, 14.5), (130, 15.49), (140, 16.47), (150, 17.46), (160, 18.45), (170, 19.44),
  (180, 20.42), (190, 21.41), (200, 22.4), (225, 24.87), (250, 27.34), (275, 29.81),
  (300, 32.27), (325, 34.74), (350, 37.21), (375, 39.68), (400, 42.14), (425, 44.61),
  (450, 47.08), (475, 49.54), (500, 52.01), (525, 54.47), (550, 56.93), (575, 59.4),
  (600, 61.86), (625, 64.32), (650, 66.78), (675, 69.24), (700, 71.7), (725, 74.16),
  (750, 76.61), (775, 79.07), (800, 81.53), (825, 83.98), (850, 86.43), (875, 88.88),
  (900, 91.34), (925, 93.79), (950, 96.23), (975, 98.68), (1000, 101.13), (1050, 106.02),
  (1100, 110.9), (1150, 115.78), (1200, 120.65), (1250, 125.51), (1300, 130.37), (1350, 135.23),
  (1400, 140.07), (1450, 144.91), (1500, 149.74), (1550, 154.56), (1600, 159.38), (1650, 164.21),
  (1700, 169.22), (1750, 174.27), (1800, 179.39), (1850, 184.58), (1900, 189.83), (1950, 195.12),
  (2000, 200.45),
]
_DIST = np.array([d for d, _ in TABLE], dtype=float)
_SP = np.array([s for _, s in TABLE], dtype=float)

# S-P時間 [秒] から震央距離 [km] を求める
#   表の範囲より短い S-P時間は 0 km、長い S-P時間は表の最後の傾きで延ばす
def epicentral(sp):
  sp = float(sp)
  if sp >= _SP[-1]:
    slope = (_DIST[-1] - _DIST[-2]) / (_SP[-1] - _SP[-2])
    return float(_DIST[-1] + (sp - _SP[-1]) * slope)
  return float(np.interp(sp, _SP, _DIST))

# S-P時間 [秒] から震源距離 [km] を求める(震央距離と仮定した深さから)
def hypocentral(sp):
  return float(np.hypot(epicentral(sp), ASSUMED_DEPTH))

# 表を作り直して、そのまま貼れる形で出す
def make_table():
  from obspy.geodetics import kilometers2degrees
  from obspy.taup import TauPyModel
  model = TauPyModel("iasp91")
  dists = list(range(0, 201, 10)) + list(range(225, 1001, 25)) + list(range(1050, 2001, 50))
  rows = []
  for d in dists:
    arrivals = model.get_travel_times(ASSUMED_DEPTH, kilometers2degrees(max(d, 0.5)),
                                      phase_list=["p", "P", "Pn", "s", "S", "Sn"])
    p = min(a.time for a in arrivals if a.name[0] in "pP")
    s = min(a.time for a in arrivals if a.name[0] in "sS")
    rows.append(f"({d}, {round(s - p, 2)}),")
  for i in range(0, len(rows), 6):
    print("  " + " ".join(rows[i:i + 6]))

if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="S-P時間から震央距離を求める表")
  parser.add_argument("--make", action="store_true", help="表を作り直して出す")
  parser.add_argument("sp", nargs="*", type=float, help="S-P時間 [秒](震央距離を出す)")
  args = parser.parse_args()
  if args.make:
    make_table()
  for sp in args.sp:
    print(f"S-P {sp:.1f} 秒 → 震央距離 {epicentral(sp):.0f} km(深さ {ASSUMED_DEPTH:.0f} km を仮定)")
