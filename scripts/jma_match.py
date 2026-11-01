# jma_match.py
# 見つけた地震の候補を、気象庁の震源リスト(jma_hypocenter.py で保存したもの)と照合する
#   1. 震源リストの地震ごとに、拠点の中心までの震央距離・震源の方位と、P波・S波の到達見込みの時刻を計算する
#      (走時は ObsPy の TauP で、地球全体の標準的な速度構造 iasp91 を使う)
#   2. 候補の揺れの始まりが、P波の到達見込みの少し前から S波の到達見込みの少し後までに入る地震を、
#      その候補の揺れの元とみなす(複数あれば、見込みの振幅が最も大きいもの)
#   3. 見込みの振幅が大きいのに、どの候補とも対応しなかった地震を「見逃した地震」として挙げる
#   見込みの振幅は、坪井の式(M = log A + 1.73 log Δ - 0.83)を A について解いた log A に、
#   遠くほど高い周波数の揺れが弱まる分(ATTENUATION_PER_KM × Δ)を差し引いたもの
#   (A: 変位の最大振幅 [μm]、Δ: 震源距離 [km])。地震を選ぶための目安で、正確な振幅ではない
from datetime import timedelta
from functools import lru_cache

import numpy as np
from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
from obspy.taup import TauPyModel

import jma_hypocenter

# 走時の計算に使う速度構造
VELOCITY_MODEL = "iasp91"
# 遠くほど高い周波数(2〜20 Hz)の揺れが弱まる分 [log A / km]
#   坪井の式だけでは、遠い地震ほど揺れを大きく見込みすぎるため
#   (2025/7〜2026/8 の記録で、候補と対応した地震の割合が半分になる坪井の式の log A は、
#    震源距離 300km までで約1.0、300〜500km で約1.5、500〜1000km で2以上、1000km 以上では1件も対応しなかった)
ATTENUATION_PER_KM = 0.003
# 見込みの振幅がこれより小さい地震は照合に使わない(遠くの小さな地震は揺れが届かない)
#   走時の計算を減らすためでもある
#   (2025/7〜2026/8 の記録で、候補と対応した地震の見込みの振幅は -1.6 以上だった)
MIN_LOG_AMP = -2.5
# 揺れの始まりが、P波の到達見込みのこの秒数前から、S波の到達見込みのこの秒数後までなら対応させる
#   揺れの始まりは10秒ごとの窓で決めているので、その分の余裕を含める
MATCH_BEFORE_P = 40.0
MATCH_AFTER_S = 30.0
# 見込みの振幅がこれより小さい地震と対応したときは、たまたま時刻が合っただけかもしれないので「対応?」とする
#   (2025/7〜2026/8 の記録で、見込みの振幅が -1 より小さい地震が候補と対応したのは、
#    2025/7/18 03:43 の青森県東方沖 M2.9(震央距離 248km、-1.16)の1件だけだった)
DOUBTFUL_LOG_AMP = -1.0
# 見込みの振幅がこれ以上なのに対応する候補が無い地震を、見逃した地震として挙げる
#   (2025/7〜2026/8 の記録で、見込みの振幅が 0.5 以上の地震は、データのある拠点が3以上の時間なら約8割(72件中56件)が候補と対応した)
MISSED_LOG_AMP = 0.5
# 候補の揺れの始まりより、この分数前までに発生した地震を調べる(遠い地震の走時を含める)
SEARCH_MINUTES = 20

_model = None

def model():
  global _model
  if _model is None:
    _model = TauPyModel(VELOCITY_MODEL)
  return _model

# P波・S波の最初の到達までの時間 [秒](震央距離 [度]、深さ [km])
#   同じような距離・深さの計算を繰り返さないよう、0.01度・1kmに丸めて覚えておく
@lru_cache(maxsize=None)
def travel_times(dist_deg, depth_km):
  arrivals = model().get_travel_times(source_depth_in_km=max(depth_km, 0.0), distance_in_degree=dist_deg,
                                      phase_list=["p", "P", "s", "S"])
  p = min((a.time for a in arrivals if a.name.upper().startswith("P")), default=None)
  s = min((a.time for a in arrivals if a.name.upper().startswith("S")), default=None)
  return p, s

# 拠点の中心(緯度・経度の平均)
def array_center(stations):
  return (float(np.mean([c[0] for c in stations.values()])),
          float(np.mean([c[1] for c in stations.values()])))

# 期間の震源リストを読み、照合に使う地震に距離・方位・到達見込みを加える
#   start, end ... 期間(datetime)。遠い地震の走時を見込んで、前の日の分から読む
#   center     ... 拠点の中心 (緯度, 経度)
#   戻り値 ... (地震のリスト, 震源リストの無かった日のリスト)
#     地震 ... jma_hypocenter.load_day の項目に加え、
#              "dist_km"(震央距離)、"azimuth"(拠点から見た震源の方位 [度])、"log_amp"(見込みの振幅)、
#              "p_time"・"s_time"(到達見込みの時刻)
def load_catalog(start, end, center):
  quakes, missing = [], []
  day = (start - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
  while day <= end:
    if not jma_hypocenter.csv_path(day).exists():
      if day >= start.replace(hour=0, minute=0, second=0, microsecond=0):
        missing.append(day)
    for q in jma_hypocenter.load_day(day):
      if q["M"] is None:
        continue
      dist_m, azimuth, _ = gps2dist_azimuth(center[0], center[1], q["緯度"], q["経度"])
      dist_km = dist_m / 1000
      hypo_km = max(np.hypot(dist_km, q["深さ[km]"]), 1.0)
      log_amp = q["M"] + 0.83 - 1.73 * np.log10(hypo_km) - ATTENUATION_PER_KM * hypo_km
      if log_amp < MIN_LOG_AMP:
        continue
      p, s = travel_times(round(kilometers2degrees(dist_km), 2), round(q["深さ[km]"]))
      if p is None or s is None:
        continue
      quakes.append(dict(q, dist_km=dist_km, azimuth=azimuth, log_amp=float(log_amp),
                         p_time=q["日時"] + timedelta(seconds=p), s_time=q["日時"] + timedelta(seconds=s)))
    day += timedelta(days=1)
  quakes.sort(key=lambda q: q["日時"])
  return quakes, missing

# 候補の揺れの始まりと、地震の到達見込みが合うか
def arrives_at(cand, q):
  return (q["p_time"] - timedelta(seconds=MATCH_BEFORE_P) <= cand["start"]
          <= q["s_time"] + timedelta(seconds=MATCH_AFTER_S))

# 候補ごとに、対応する震源リストの地震を探す
#   cand["match"]   ... 対応した地震(到達見込みが合うもののうち、見込みの振幅が最も大きいもの)または None
#   cand["doubtful"] ... 対応した地震の見込みの振幅が DOUBTFUL_LOG_AMP より小さい(対応が疑わしい)
#   cand["others"]  ... ほかに、P波の到達見込みが候補の揺れの間に入る地震の数(余震が続くときなど)
#   到達見込みが合う地震と、P波が候補の揺れの間に届く地震には q["matched"] = True を付ける
#   (揺れが重なって候補が1つになった地震を、見逃した地震に数えないため)
def match_candidates(candidates, catalog):
  for q in catalog:
    q["matched"] = False
  for cand in candidates:
    lo = cand["start"] - timedelta(minutes=SEARCH_MINUTES)
    near = [q for q in catalog if lo <= q["日時"] <= cand["end"]]
    hits = [q for q in near if arrives_at(cand, q)]
    during = [q for q in near
              if cand["start"] - timedelta(seconds=MATCH_BEFORE_P) <= q["p_time"] <= cand["end"]]
    cand["match"] = max(hits, key=lambda q: q["log_amp"]) if hits else None
    cand["doubtful"] = cand["match"] is not None and cand["match"]["log_amp"] < DOUBTFUL_LOG_AMP
    cand["others"] = len({id(q) for q in hits + during} - {id(cand["match"])})
    for q in hits + during:
      q["matched"] = True

# 見込みの振幅が MISSED_LOG_AMP 以上なのに、どの候補とも対応しなかった地震
#   ratios_between ... (開始, 終了) を渡すと (データのある拠点数, {拠点名: 最大の倍率}) を返す関数
#   戻り値 ... [(地震, データのある拠点数, {拠点名: P波〜S波の60秒後の最大の倍率}), ...]
#   データのある拠点が min_stations より少ない時間の地震は、候補を探していないので除く
def find_missed(catalog, ratios_between, min_stations, start, end):
  missed = []
  for q in catalog:
    if q["matched"] or q["log_amp"] < MISSED_LOG_AMP or not (start <= q["p_time"] < end):
      continue
    available, ratios = ratios_between(q["p_time"], q["s_time"] + timedelta(seconds=60))
    if available >= min_stations:
      missed.append((q, available, ratios))
  return missed

# 地震の短い説明。例: 22:45:12 浦河沖 M4.0 深さ50km
def describe(q):
  return f"{q['日時']:%H:%M:%S} {q['震央地名']} M{q['M']:.1f} 深さ{q['深さ[km]']:g}km"

# 方位の表示(16方位)。例: 95度 → 東
def direction_name(azimuth):
  names = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
           "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]
  return names[int((azimuth + 11.25) % 360 // 22.5)]
