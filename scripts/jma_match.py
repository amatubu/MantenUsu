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
import json
import threading

import numpy as np
from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
from obspy.taup import TauPyModel

import app_paths
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

# 計算した走時を保存するファイル(消えても作り直せるので、ユーザーのキャッシュフォルダに置く)
#   1回目は震源リスト 1万6千件の走時の計算に約2分かかるが、2回目からは新しく増えた分だけを計算する
TRAVEL_TIME_FILE = app_paths.USER_CACHE_DIR / f"travel_times_{VELOCITY_MODEL}.json"

_model = None
_travel_times = None       # {"震央距離[度],深さ[km]": [P波, S波]}(None ならまだファイルを読んでいない)
_travel_times_added = 0    # ファイルに保存していない数
_travel_times_lock = threading.Lock()   # メンテナンス画面の別スレッドと画面で同時に使うことがあるため

def model():
  global _model
  if _model is None:
    _model = TauPyModel(VELOCITY_MODEL)
  return _model

# P波・S波の最初の到達までの時間 [秒](震央距離 [度]、深さ [km])
#   同じような距離・深さの計算を繰り返さないよう、呼ぶ側で0.01度・1kmに丸め、計算した結果を覚えておく
#   (save_travel_times で TRAVEL_TIME_FILE に保存し、次に起動したときも使う)
def travel_times(dist_deg, depth_km):
  global _travel_times, _travel_times_added
  key = f"{dist_deg:g},{depth_km:g}"
  with _travel_times_lock:
    if _travel_times is None:
      _travel_times = _load_travel_times()
    if key in _travel_times:
      p, s = _travel_times[key]
      return p, s
  arrivals = model().get_travel_times(source_depth_in_km=max(depth_km, 0.0), distance_in_degree=dist_deg,
                                      phase_list=["p", "P", "s", "S"])
  p = min((a.time for a in arrivals if a.name.upper().startswith("P")), default=None)
  s = min((a.time for a in arrivals if a.name.upper().startswith("S")), default=None)
  with _travel_times_lock:
    _travel_times[key] = [p, s]
    _travel_times_added += 1
  return p, s

def _load_travel_times():
  try:
    return json.loads(TRAVEL_TIME_FILE.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}

# 覚えた走時のうち、まだ保存していない分があればファイルに書く
def save_travel_times():
  global _travel_times_added
  with _travel_times_lock:
    if not _travel_times_added:
      return
    text = json.dumps(_travel_times, separators=(",", ":"))
    _travel_times_added = 0
  try:
    TRAVEL_TIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRAVEL_TIME_FILE.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(TRAVEL_TIME_FILE)
  except OSError:
    pass   # 保存できなくても、次に計算し直すだけ

# 拠点の中心(緯度・経度の平均)
def array_center(stations):
  return (float(np.mean([c[0] for c in stations.values()])),
          float(np.mean([c[1] for c in stations.values()])))

# 期間の震源リストを読み、照合に使う地震に距離・方位・到達見込みを加える
#   start, end ... 期間(datetime)。遠い地震の走時を見込んで、前の日の分から読む
#   center     ... 拠点の中心 (緯度, 経度)
#   on_progress ... 進み具合 (何日目, 全体の日数) を受け取る関数(走時の計算に時間がかかるため。長い期間で数分)
#   戻り値 ... (地震のリスト, 震源リストの無かった日のリスト)
#     地震 ... jma_hypocenter.load_day の項目に加え、
#              "dist_km"(震央距離)、"azimuth"(拠点から見た震源の方位 [度])、"log_amp"(見込みの振幅)、
#              "p_time"・"s_time"(到達見込みの時刻)
def load_catalog(start, end, center, on_progress=None):
  quakes, missing = [], []
  first = (start - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
  total = (end - first).days + 1
  day = first
  while day <= end:
    if on_progress:
      on_progress((day - first).days + 1, total)
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
  save_travel_times()
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

# --- 画面表示用(P波・S波検出、震源推定の画面) ---

# 表示している波形の期間に P波が届く、震源リストの地震
#   t0, t1 ... 波形の期間(datetime)
#   site   ... 到達見込みを計算する位置 (緯度, 経度)(拠点、または拠点の中心)
#   戻り値 ... (地震のリスト, 震源リストがあるか)
#   震源リストは、jma_hypocenter.py で取得済みのものだけを使う(ここでは取得しない)
def quakes_in_window(t0, t1, site):
  catalog, missing = load_catalog(t0, t1, site)
  first_day = t0.replace(hour=0, minute=0, second=0, microsecond=0)
  days = (t1.replace(hour=0, minute=0, second=0, microsecond=0) - first_day).days + 1
  hits = [q for q in catalog if t0 - timedelta(seconds=MATCH_BEFORE_P) <= q["p_time"] <= t1]
  return hits, len(missing) < days

# 期間内の地震から、表示する地震を選ぶ
#   p_time ... 画面で検出した P波の到着時刻(datetime)または None
#   戻り値 ... (選んだ地震 または None, ほかの地震の数)
#   P波があれば、それが到達見込み(P波の MATCH_BEFORE_P 秒前〜S波の MATCH_AFTER_S 秒後)に入る地震のうち、
#   見込みの振幅が最も大きいもの(合う地震が無ければ None)。P波が無ければ、期間内で見込みの振幅が最も大きいもの
def choose_quake(hits, p_time=None):
  if p_time is not None:
    hits = [q for q in hits
            if q["p_time"] - timedelta(seconds=MATCH_BEFORE_P) <= p_time <= q["s_time"] + timedelta(seconds=MATCH_AFTER_S)]
  if not hits:
    return None, 0
  return max(hits, key=lambda q: q["log_amp"]), len(hits) - 1

# 画面に表示する文字列(複数行)
#   hits, available ... quakes_in_window の結果
#   t_ref  ... 画面の時刻の基準(datetime)。到達見込みを「基準からの秒」で表示する
#   p_time ... 画面で検出した P波の到着時刻(datetime)または None
#   wide   ... describe_lines を参照
#   戻り値 ... (文字列のリスト, 選んだ地震 または None)
def window_summary(hits, available, t_ref, p_time=None, wide=False):
  if not available:
    return ["気象庁の震源リスト: 未取得", " (scripts/jma_hypocenter.py で取得できます)"], None
  q, others = choose_quake(hits, p_time)
  if q is None and p_time is not None:
    # 画面の右の狭い欄に収まるよう、1行を短くする
    return (["気象庁の震源リスト:", " 検出した P波に合う地震なし"]
            + ([f" (この期間に揺れが届きそうな地震 {len(hits)}件)"] if hits else [])
            + [" 有珠山周辺の小さな地震かもしれません"]), None
  if q is None:
    return ["気象庁の震源リスト: この期間に揺れが届きそうな地震なし"], None
  return ["気象庁の震源リスト(暫定値):"] + describe_lines(q, others, t_ref, p_time, wide), q

# 画面に表示する地震の説明(複数行)
#   t_ref  ... 画面の時刻の基準(datetime)。到達見込みを「基準からの秒」で表示する
#   p_time ... 画面で検出した P波の到着時刻(datetime)。あれば到達見込みとの差を表示する
#   wide   ... 横に広い欄に表示する(行数を減らす)。False なら狭い欄に収まるよう1行を短くする
def describe_lines(q, others, t_ref, p_time=None, wide=False):
  sec = lambda t: (t - t_ref).total_seconds()
  origin = f"{q['日時']:%H:%M:%S.%f}"[:-5]
  where = f"震央距離 {q['dist_km']:.0f} km  方位 {q['azimuth']:.0f}° ({direction_name(q['azimuth'])})"
  arrival = f"到達見込み P {sec(q['p_time']):.1f} 秒 / S {sec(q['s_time']):.1f} 秒"
  diff = f"(検出した P波との差 {(p_time - q['p_time']).total_seconds():+.1f} 秒)" if p_time else ""
  if wide:
    lines = [f" {origin} {q['震央地名']}  M{q['M']:.1f}  深さ {q['深さ[km]']:g} km  {where}",
             f" {arrival}  {diff}"]
  else:
    lines = [f" {origin} {q['震央地名']}", f" M{q['M']:.1f}  深さ {q['深さ[km]']:g} km", f" {where}", f" {arrival}"]
    if diff:
      lines.append(f" {diff}")
  if q["log_amp"] < DOUBTFUL_LOG_AMP:
    lines.append(" ※遠いか小さい地震で、別の揺れかもしれません")
  if others:
    lines.append(f" ほかに合いそうな地震 {others}件")
  return lines
