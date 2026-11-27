# shake_oneday.py
# 「ある有珠満点地震計の1日」のページを作る(試作)
#   0 時から時計を進めながら、その日に起きた揺れ(地震・発破・花火など)が、どこで起きて、どう 4 拠点に届くかを見せる
#   揺れの無い時間は早送り(1 時間 ≒ 3 秒)、揺れの所では本当の速さ〜数倍に落とす。⏭ で次の揺れへ、下の 1 日の帯で好きな時刻へ
#   画面: 有珠山のまわりの地図(4 拠点・波の輪)、日本のまわりの小さな地図(遠い地震の震源と輪)、時刻、4 拠点の波形、説明
#   揺れの一覧は「1日の記録から地震を探す」の答え(etc/shake_day_<日>.csv)。このページだけの見どころは etc/oneday_<日>.csv
#   (番号, 追加の説明)に書く(クイズの答えは変えない)。震源はここで決める:
#     遠い地震・少し遠い地震 ... 気象庁の震源リスト(P の到着見込みが揺れの始まりに合うもの)。届く時刻は iasp91(jma_match)
#     有珠山の地震 ... 有珠山の震源リスト(batch_plots/quakes/locate/地震リスト_有珠山付近の地震_locations.csv)。
#                     無ければパターンマッチングの検出(推定の発生時刻)と、似ているひな形の震源。それも無ければ 4 拠点の P の読み取り(自動)
#     発破 ... 有珠山の震源リストの「発破」。無ければ場所は採石場(sp_locate.csv の正解の場所)、発生時刻は P の読み取りから
#     花火 ... 打ち上げの船は西から東へ動くので、東西に長い楕円で示す。東の端が 4 拠点の音の遅れから決めた場所(notes/fireworks_3comp)。
#             音(空気を伝わる音波、340 m/s。地震波の速さではない)の輪は楕円の中心から出し、Kawaminami の揺れの山(ふだんの 6 倍をこえたもの)の時刻から、音が届くまでの時間をさかのぼって出す
#   有珠山の震源リストにある地震・発破は、満点の拠点の P・S の印をリストの読み取り(<ユーザーの設定>/picks/<手直し>)にする(震源の計算から除いたものは計算の見込み)
#   続けて起きた揺れ(前の揺れの終わりから GROUP_GAP 秒以内)は 1 つの場面にまとめる(--group-gap で変えられる)
#   波形は、真ん中の縦線を今にして左右に描く。早送りの間は前後 30 分の揺れの強さで、揺れの場面に入ると真ん中を中心に拡大して本当の波形に替わる
#   有珠山のまわりの P波・S波の速さは、S-P 時間の教材(sp_locate)の大森係数 k = 3.7 と vp/vs = 1.74 に合わせる
#   入れるのは満点の拠点の波形だけ(再配布できない外部の観測点の波形は入れない)。列車の時刻表は入れない
#   書き出すもの(フォルダごとサーバーに置く。/usu/oneday/<日>/ の想定):
#     <出力>/index.html … ページ
#     <出力>/day.json … 揺れの一覧・震源・届く時刻、4 拠点の 1 日の揺れの強さ(10 秒ごと)
#     <出力>/wave/<番号>.json … 揺れごとの 4 拠点の上下動(50 Hz、8 ビット)
#     <出力>/map_usu.jpg・map_japan.jpg … 地理院タイル(淡色地図)をつないだ地図
#
#     python3 scripts/shake_oneday.py 2026-07-08 [-o 出力のフォルダ]
#   省略すると batch_plots/quiz/oneday/<日>/ に書き、日の一覧のページ batch_plots/quiz/oneday/index.html も書き直す

import argparse
import base64
import html
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import app_paths
import shake_day as sd
import shake_quiz as sq
import station_names
from hypocenter import LocalProjection, load_stations

OUTPUT_DIR = sq.OUTPUT_DIR / "oneday"
DAY_LINK = "../../day/{day}/"      # 拡大して聴くページ(サーバーの /usu/day/<日>/)
RATE = sd.RATE                     # 50 Hz
ENV_STEP = 10                      # 1 日の揺れの強さの刻み [秒]
OMORI_K, VP_VS = 3.7, 1.74
VP = OMORI_K * (VP_VS - 1)         # 有珠山のまわりの P波の速さ [km/s](k = 3.7 と vp/vs から。約 2.7)
VS = VP / VP_VS
SOUND_KMS = 0.34
QUARRY = (42.5274, 140.8093)       # 採石場(sp_locate.csv の問題 1 の正解の場所)
FIREWORKS = (42.57027, 140.83520)  # 花火の打ち上げ場所(notes/fireworks_3comp/bamba_path.py)
FIREWORKS_AREA = (0.7, 0.15)       # 打ち上げ場所の楕円の半径 [km](東西, 南北)。打ち上げの船が西から東へ動くため、東西に長く描く
                                   # 楕円の東の端を FIREWORKS(音の遅れから決めた場所)にし、中心はそこから西へ半径の分ずらす。音の輪は中心から出す
SUMMIT = (42.5436, 140.8394)
GROUP_GAP = 60.0                   # 前の揺れの終わりからこれ以内 [秒] に始まる揺れは、1 つの場面にまとめて見せる
PICKS_DIR = app_paths.PICKS_DIR    # 有珠山の震源リストの読み取り(「手直し」の列のファイル。震源推定の画面で保存したもの)
SCENE_WALL = 45.0                  # 1 つの場面にかける時間の目安 [秒](長い揺れは、種類ごとの速さより速くしてこれに収める)
USU_DEPTH = 1.5                    # 有珠山の地震の深さの仮の値 [km](4 拠点の P だけでは深さが決まらないため)
MAP_ZOOM_USU, MAP_ZOOM_JAPAN = 13, 6
MAP_MARGIN_KM = 2.5
# 揺れの種類ごと: フィルタ(帯域 Hz)、揺れの間の速さ、波形の見える幅 [秒]
KINDS = {
  "有珠山の地震": {"band": (1.0, 20.0), "speed": 1, "span": 30},
  "発破": {"band": (0.5, 2.0), "speed": 2, "span": 60},
  "少し遠い地震": {"band": (0.5, 10.0), "speed": 4, "span": 90},
  "遠くの地震": {"band": (0.5, 10.0), "speed": 6, "span": 120},
  "花火": {"band": (20.0, 40.0), "speed": 20, "span": 60},
  "地震ではない揺れ": {"band": (0.5, 20.0), "speed": 4, "span": 90},
  "列車": {"band": (0.5, 20.0), "speed": 4, "span": 90},
}
OTHER_KIND = {"band": (0.5, 20.0), "speed": 4, "span": 60}
FAR_KINDS = ("遠くの地震", "少し遠い地震")


def bandpass(x, band, fs=RATE):
  from scipy import signal
  gap = ~np.isfinite(x)
  y = signal.sosfiltfilt(signal.butter(4, band, "bandpass", fs=fs, output="sos"), np.where(gap, 0.0, x))
  y[gap] = np.nan
  return y

def b64_int8(a):
  return base64.b64encode(np.asarray(a, dtype=np.int8).tobytes()).decode("ascii")

def b64_uint8(a):
  return base64.b64encode(np.asarray(a, dtype=np.uint8).tobytes()).decode("ascii")


# --- P の読み取り(自動)と、有珠山のまわりの震源 ---

# P の読み取り: t_from〜t_to で、揺れの強さ(0.2 秒の RMS)が前の 16 秒のふだんの ratio 倍を最初にこえた時刻(日の始まりからの秒)
#   その間の最大が ふだんの min_peak 倍に届かない(揺れがはっきりしない)拠点は None
def pick_p(x, t_from, t_to, band=(5.0, 20.0), ratio=4.0, min_peak=6.0):
  i0, i1 = int((t_from - 20) * RATE), int((t_to + 5) * RATE)
  if i0 < 0 or i1 > len(x):
    return None
  y = x[i0:i1]
  if not np.isfinite(y).all():
    return None
  y = bandpass(y, band)
  n = RATE // 5
  e = np.sqrt(np.convolve(y ** 2, np.ones(n) / n, "same"))
  t = i0 / RATE + np.arange(len(e)) / RATE
  base = float(np.median(e[t < t_from - 2]))
  sel = (t >= t_from) & (t <= t_to)
  if base <= 0 or e[sel].max() < base * min_peak:
    return None
  hit = np.nonzero(sel & (e > base * ratio))[0]
  return float(t[hit[0]]) if hit.size else None

# 4 拠点の P から、深さを決めて震央(km)と発生時刻を格子で探す。fixed を渡すと場所は決めたまま、発生時刻だけ
#   center ... 探す範囲の中心(km)。範囲は ±GRID_KM(有珠山の地震なので、山頂のまわりに絞る。3 拠点だけでも外れないように)
#   start ... どの拠点も読めないときに使う揺れの始まり(いちばん近い拠点に P が届いた時刻とみる)
GRID_KM = 3.0
def locate_local(picks, xy, depth, fixed=None, center=(0.0, 0.0), start=None):
  names = [nm for nm in picks if picks[nm] is not None]
  if not names:
    if fixed is None or start is None:
      return None
    tt = min(np.hypot(np.hypot(xy[nm][0] - fixed[0], xy[nm][1] - fixed[1]), depth) for nm in xy) / VP
    return {"x": float(fixed[0]), "y": float(fixed[1]), "z": depth, "t0": float(start - tt), "res": 0.0}
  t = np.array([picks[nm] for nm in names])
  sx = np.array([xy[nm][0] for nm in names])
  sy = np.array([xy[nm][1] for nm in names])
  if fixed is not None:
    gx, gy = np.array([fixed[0]]), np.array([fixed[1]])
  else:
    g = np.arange(-GRID_KM, GRID_KM + 0.01, 0.05)
    gx, gy = [a.ravel() for a in np.meshgrid(g + center[0], g + center[1])]
  tt = np.hypot(np.hypot(gx[:, None] - sx, gy[:, None] - sy), depth) / VP
  t0 = np.median(t - tt, axis=1)
  res = np.abs(t - tt - t0[:, None]).sum(axis=1)
  k = int(np.argmin(res))
  return {"x": float(gx[k]), "y": float(gy[k]), "z": depth, "t0": float(t0[k]), "res": float(res[k] / len(names))}

def local_arrivals(src, xy, elev):
  out = {}
  for nm, (x, y) in xy.items():
    d = float(np.hypot(np.hypot(x - src["x"], y - src["y"]), src["z"] + elev[nm]))
    out[nm] = {"p": round(src["t0"] + d / VP, 2), "s": round(src["t0"] + d / VS, 2)}
  return out


# 有珠山の震源リスト(アプリで保存したもの): {候補の開始 "YYYYmmdd_HHMMSS": {"lat", "lon", "z"(海面下 km), "origin"(datetime), "blast"}}
#   ひな形(パターンマッチング)の震源も、ひな形の開始でここから引く
USU_LIST = app_paths.BATCH_PLOTS_DIR / "quakes" / "locate" / "地震リスト_有珠山付近の地震_locations.csv"
def usu_hypocenters(path=USU_LIST):
  import csv
  out = {}
  if not path.exists():
    print(f"  有珠山の震源リストがありません: {path}")
    return out
  with open(path, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
      try:
        start = datetime.strptime(row["開始"], "%Y-%m-%d %H:%M:%S")
        lat, lon, z = float(row["緯度"]), float(row["経度"]), float(row["深さ[km](海面下)"])
        hh, mm, ss = row["発生時刻"].split(":")
      except (KeyError, ValueError):
        continue
      origin = start.replace(hour=int(hh), minute=int(mm), second=0) + timedelta(seconds=float(ss))
      if (origin - start).total_seconds() < -43200:    # 日をまたいだ
        origin += timedelta(days=1)
      out[f"{start:%Y%m%d_%H%M%S}"] = {"lat": lat, "lon": lon, "z": z, "origin": origin, "start": start,
                                        "blast": row.get("種別", "") == "発破", "picks": row.get("手直し", "")}
  return out

# 震源リストの読み取り(<ユーザーの設定>/picks/<手直し>)で、満点の拠点の P・S の到着を置き換える(日の始まりからの秒)
#   震源の計算から除いた読み取り(excluded・s_excluded)は使わない。戻り値 ... 置き換えた (拠点, "p" または "s") の数
def observed_arrivals(arr, name, day):
  path = PICKS_DIR / name if name else None
  if not path or not path.exists():
    return 0
  data = json.loads(path.read_text(encoding="utf-8"))
  used = 0
  for nm, pk in data.get("stations", {}).items():
    if nm not in arr:
      continue
    for key, skip in (("p", data.get("excluded", [])), ("s", data.get("s_excluded", []))):
      if pk.get(key) and nm not in skip:
        arr[nm][key] = round((datetime.fromisoformat(pk[key]) - day).total_seconds(), 2)
        arr[nm][key + "_obs"] = True
        used += 1
  return used

# パターンマッチングで見つけた地震(batch_plots/quakes/template_match/detections_*.csv): [(推定の発生時刻, ひな形の開始), ...]
TEMPLATE_DIR = app_paths.BATCH_PLOTS_DIR / "quakes" / "template_match"
def template_detections():
  import csv
  out = {}
  for path in sorted(TEMPLATE_DIR.glob("detections_*.csv")):
    with open(path, encoding="utf-8-sig") as f:
      for row in csv.DictReader(f):
        try:
          t = datetime.strptime(row["時刻(推定の発生時刻)"][:19], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
          continue
        frac = row["時刻(推定の発生時刻)"][19:]
        t += timedelta(seconds=float("0" + frac) if frac else 0.0)
        out[round(t.timestamp())] = (t, row.get("似ているひな形", ""))
  return sorted(out.values())

# 有珠山の地震・発破の震源: 有珠山の震源リスト → パターンマッチング(地震だけ)のひな形の震源(発生時刻は検出の推定) の順に探す。無ければ None
def usu_source(ev, day, hypos, detections):
  start = day + timedelta(seconds=ev["start"])
  blast = ev["kind"] == "発破"
  for h in hypos.values():
    if h["blast"] == blast and -6 <= (start - h["origin"]).total_seconds() <= 4:
      return h, "有珠山の震源リスト"
  if blast:
    return None, ""
  for t, key in detections:
    if -6 <= (start - t).total_seconds() <= 3 and key in hypos:
      h = hypos[key]
      return dict(h, origin=t), f"ひな形 {key} の震源"
  return None, ""


# --- 遠い地震(気象庁の震源リスト) ---

# 揺れの始まり(P の到着)に合う地震。その揺れの終わりまでに届く、同じくらい大きな地震(続けて起きたもの)も入れる
def match_jma(ev, catalog, day):
  def sec(t):
    return (t - day).total_seconds()
  near = [q for q in catalog if abs(sec(q["p_time"]) - ev["start"]) <= 25 and q["dist_km"] > 30]
  if not near:
    return []
  main = max(near, key=lambda q: q["log_amp"])
  more = [q for q in catalog if q is not main and ev["start"] + 10 < sec(q["p_time"]) < ev["end"] - 10
          and q["dist_km"] > 30 and q["log_amp"] >= main["log_amp"] - 1.0]
  return [main] + more

# 震央距離 [km] → P・S が届くまでの秒の表(小さな地図の輪と、有珠山の地図の平らな波面に使う)
def far_tables(depth, max_km):
  import jma_match
  from obspy.geodetics import kilometers2degrees
  km = np.arange(0.0, max_km + 101, 10.0)
  p, s = [], []
  for d in km:
    a, b = jma_match.travel_times(round(kilometers2degrees(max(d, 1.0)), 2), round(depth))
    p.append(round(a, 2))
    s.append(round(b, 2))
  jma_match.save_travel_times()
  return {"km": km.tolist(), "p": p, "s": s}

def far_source(q, day, stations):
  import jma_match
  from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
  t0 = (q["日時"] - day).total_seconds()
  arr = {}
  for nm, (lat, lon, _) in stations.items():
    dm, _, _ = gps2dist_azimuth(lat, lon, q["緯度"], q["経度"])
    p, s = jma_match.travel_times(round(kilometers2degrees(dm / 1000), 2), round(q["深さ[km]"]))
    arr[nm] = {"p": round(t0 + p, 2), "s": round(t0 + s, 2)}
  return {"type": "far", "lat": q["緯度"], "lon": q["経度"], "z": q["深さ[km]"], "t0": round(t0, 2),
          "m": q["M"], "place": q["震央地名"], "km": round(q["dist_km"]), "az": round(q["azimuth"], 1),
          "arr": arr, "tt": far_tables(q["深さ[km]"], q["dist_km"] * 1.3)}


# --- 花火: Kawaminami の揺れの山ごとに、打ち上げの時刻を逆算する ---
# --- 花火: 20〜40 Hz(音で揺れる帯域。50 Hz に間引いた記録には無いので、元の 250 Hz の記録から作る) ---
FIREWORKS_BAND = (20.0, 40.0)
FIREWORKS_RATE = 125               # 花火の波形の間引き [Hz](250 Hz の 1/2。40 Hz まで描ける)
FIREWORKS_SHOW = 600.0             # 見せるのは最後の強い 1 発の前のこの秒数(はっきり見えるのは最後の 10 分ほど)
FIREWORKS_PROGRAM = app_paths.BATCH_PLOTS_DIR / "fireworks" / "program_days.csv"   # 日ごとの最後の強い揺れ(fireworks_stack.py)

# 4 拠点の 20〜40 Hz の上下動(物理量)を、t0〜t1(日の始まりからの秒)について FIREWORKS_RATE で返す
def fireworks_waveforms(day, t0, t1, stations):
  import warnings
  from scipy import signal
  import quake_wave_view as wv
  steps = wv.count_steps(app_paths.ETC_DIR)
  n = int(round((t1 - t0) * FIREWORKS_RATE))
  out = {nm: np.full(n, np.nan) for nm in stations}
  with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    st = wv.load_stream(day + timedelta(seconds=t0 - 10), day + timedelta(seconds=t1 + 10))
  for tr in st:
    nm = tr.stats.station
    if nm not in out or not tr.stats.channel.endswith("U"):
      continue
    fs = tr.stats.sampling_rate
    k = int(round(fs / FIREWORKS_RATE))
    if k < 1 or abs(fs / FIREWORKS_RATE - k) > 1e-6:
      continue
    x = np.ma.filled(np.ma.asarray(tr.data, dtype=float), np.nan) * steps.get((nm, "U"), 1.0)
    gap = np.isnan(x)
    x = np.where(gap, 0.0, x - np.nanmean(x))
    y = signal.sosfiltfilt(signal.butter(4, FIREWORKS_BAND, "bandpass", fs=fs, output="sos"), x)
    y[gap] = np.nan
    off = (tr.stats.starttime.datetime - day).total_seconds() - t0      # t0 からの秒
    idx = np.arange(0, len(y), k)
    j = np.round((off + idx / fs) * FIREWORKS_RATE).astype(int)
    ok = (j >= 0) & (j < n)
    out[nm][j[ok]] = y[idx[ok]]
  return out

# 最後の強い 1 発の時刻(日の始まりからの秒): 花火の分析の program_days.csv、無ければ Kawaminami の 20〜40 Hz がいちばん大きい所
def fireworks_last(day, ev, ys, t0):
  import csv
  if FIREWORKS_PROGRAM.exists():
    with open(FIREWORKS_PROGRAM, encoding="utf-8-sig") as f:
      for r in csv.DictReader(f):
        if r.get("日") == f"{day:%Y-%m-%d}" and r.get("最後の強い揺れ"):
          hh, mm, ss = r["最後の強い揺れ"].split(":")
          return int(hh) * 3600 + int(mm) * 60 + float(ss), "花火の分析(program_days.csv)"
  y = np.nan_to_num(ys["Hokudai"])
  return t0 + int(np.argmax(np.abs(y))) / FIREWORKS_RATE, "Kawaminami の 20〜40 Hz の最大"

# 1 発ずつ: Kawaminami の 20〜40 Hz の強さ(0.2 秒)の山のうち、ふだん(読んだ範囲の中央値)の 6 倍をこえたもの。
#   戻り値 ... [[打ち上げの時刻(音が Kawaminami に届くまでをさかのぼる), 倍率], ...]
def fireworks_bursts(y, t0, w0, w1, delay):
  from scipy import signal
  n = FIREWORKS_RATE // 5
  e = np.sqrt(np.convolve(np.nan_to_num(y) ** 2, np.ones(n) / n, "same"))
  base = float(np.median(e[e > 0])) if (e > 0).any() else 1.0
  pk, _ = signal.find_peaks(e, height=base * 6, distance=int(FIREWORKS_RATE * 1.5))
  out = []
  for i in pk:
    t = t0 + i / FIREWORKS_RATE
    if w0 <= t <= w1:
      out.append([round(t - delay, 2), round(float(e[i] / base), 1)])
  return out


# --- 波形と 1 日の揺れの強さ ---

# 揺れの窓の 4 拠点の上下動。拠点の間で共通の物差し(4 拠点の 99.9 % の最大)で 8 ビットにする
def event_waves(wf, stations, w0, w1, band):
  i0, i1 = int(w0 * RATE), int(w1 * RATE)
  ys = {}
  for nm in stations:
    a, b = max(i0 - 30 * RATE, 0), min(i1 + 30 * RATE, len(wf[nm]))
    y = bandpass(wf[nm][a:b], band)[i0 - a:i0 - a + (i1 - i0)]
    ys[nm] = y
  return encode_waves(ys, w0, RATE)

# 4 拠点の波形({拠点: 物理量の配列}、最初の点が t0 秒)を、拠点の間で共通の物差しで 8 ビットにする
def encode_waves(ys, t0, rate):
  peaks = [float(np.percentile(np.abs(y[np.isfinite(y)]), 99.9)) for y in ys.values() if np.isfinite(y).any()]
  scale = max(peaks) if peaks else 1.0
  out = {"t0": t0, "rate": rate, "scale": round(scale / 1000, 4), "st": {}}   # scale は µm/s
  for nm, y in ys.items():
    if not np.isfinite(y).any():
      continue
    v = np.round(np.clip(np.nan_to_num(y / scale), -1, 1) * 127).astype(int)
    v[~np.isfinite(y)] = -128
    out["st"][nm] = {"d": b64_int8(v), "peak": round(float(np.nanmax(np.abs(y)) / scale), 3)}
  return out

# 10 秒ごとの揺れの強さ(RMS)を、その日の中央値からの dB の 2 倍(0.5 dB 刻み)にして 8 ビットにする。記録の無い所は 0
def day_envelope(x):
  n = len(x) // (ENV_STEP * RATE)
  y = x[:n * ENV_STEP * RATE].reshape(n, -1)
  ok = np.isfinite(y).mean(axis=1) > 0.5
  rms = np.sqrt(np.nanmean(np.where(np.isfinite(y), y, np.nan) ** 2, axis=1)) if ok.any() else np.zeros(n)
  med = float(np.median(rms[ok])) if ok.any() else 1.0
  db = 20 * np.log10(np.maximum(rms, 1e-9) / med)
  v = np.clip(np.round(db * 2) + 40, 1, 255)     # 中央値 = 40(-20 dB まで)
  v[~ok] = 0
  return b64_uint8(v.astype(int))


# --- 地図 ---

def save_jpeg(img, path, quality=80):
  from PIL import Image
  Image.fromarray(img).save(path, format="JPEG", quality=quality, optimize=True)

def usu_map(out, stations, proj):
  import sp_locate
  pts = [proj.to_xy(*c[:2]) for c in stations.values()] + [proj.to_xy(*p) for p in (QUARRY, FIREWORKS, SUMMIT)]
  xs, ys = [p[0] for p in pts], [p[1] for p in pts]
  south, west = proj.to_latlon(min(xs) - MAP_MARGIN_KM, min(ys) - MAP_MARGIN_KM)
  north, east = proj.to_latlon(max(xs) + MAP_MARGIN_KM, max(ys) + MAP_MARGIN_KM)
  got = sp_locate.stitch_tiles(south, north, west, east, z=MAP_ZOOM_USU)
  if got is None:
    return None
  img, (s2, n2, w2, e2) = got
  save_jpeg(img, out / "map_usu.jpg")
  x0, y0 = proj.to_xy(s2, w2)
  x1, y1 = proj.to_xy(n2, e2)
  return {"src": "map_usu.jpg", "x0": round(x0, 4), "x1": round(x1, 4), "y0": round(y0, 4), "y1": round(y1, 4),
          "w": int(img.shape[1]), "h": int(img.shape[0])}

def japan_map(out, sources):
  import sp_locate
  lats = [SUMMIT[0]] + [s["lat"] for s in sources]
  lons = [SUMMIT[1]] + [s["lon"] for s in sources]
  south, north = min(lats) - 1.5, max(lats) + 1.5
  west, east = min(lons) - 2.0, max(lons) + 2.0
  # 狭すぎないように(北海道と東北が入るくらい)
  if north - south < 6:
    c = (north + south) / 2
    south, north = c - 3, c + 3
  if east - west < 8:
    c = (east + west) / 2
    west, east = c - 4, c + 4
  got = sp_locate.stitch_tiles(south, north, west, east, z=MAP_ZOOM_JAPAN)
  if got is None:
    return None
  img, (s2, n2, w2, e2) = got
  save_jpeg(img, out / "map_japan.jpg")
  return {"src": "map_japan.jpg", "south": s2, "north": n2, "west": w2, "east": e2,
          "w": int(img.shape[1]), "h": int(img.shape[0])}


# --- まとめ ---

# このページだけの追加の説明(etc/oneday_<日>.csv: 番号, 追加の説明)。クイズの答え(shake_day_<日>.csv)は変えずに、ここに足す
def read_extra(day):
  import csv
  path = app_paths.ETC_DIR / f"oneday_{day:%Y%m%d}.csv"
  if not path.exists():
    return {}
  with open(path, encoding="utf-8-sig") as f:
    return {int(r["番号"]): r["追加の説明"].strip() for r in csv.DictReader(f) if r.get("番号", "").strip().isdigit()}

# 続けて起きた揺れ(前の終わりから group_gap 秒以内に始まるもの)を 1 つの場面にまとめる
#   場面: 最初の揺れの番号・種類・色を持ち、parts にそれぞれの揺れ、src に全部の震源。速さはいちばん遅いもの
PART_KEYS = ("no", "kind", "color", "start", "end", "text", "vis", "note", "extra")
def group_scenes(items, group_gap):
  scenes = []
  for it in sorted(items, key=lambda e: e["start"]):
    part = {k: it[k] for k in PART_KEYS}
    if scenes and it["start"] - scenes[-1]["end"] <= group_gap:
      sc = scenes[-1]
      sc["parts"].append(part)
      sc["src"] += it["src"]
      sc["end"] = max(sc["end"], it["end"])
      sc["w0"], sc["w1"] = min(sc["w0"], it["w0"]), max(sc["w1"], it["w1"])
      sc["speed"], sc["span"] = min(sc["speed"], it["speed"]), max(sc["span"], it["span"])
      sc["fw"] = sc.get("fw") or it.get("fw")
    else:
      scenes.append({**it, "src": list(it["src"]), "parts": [part]})
  return scenes

def build(day, out, group_gap=GROUP_GAP):
  import jma_match
  stations = load_stations(app_paths.ETC_DIR)
  stations = {nm: stations[nm] for nm in sq.STATIONS if nm in stations}
  proj = LocalProjection(list(stations.values()))
  xy = {nm: tuple(float(v) for v in proj.to_xy(lat, lon)) for nm, (lat, lon, _) in stations.items()}
  elev = {nm: alt / 1000 for nm, (_, _, alt) in stations.items()}    # 拠点の標高 [km](震源の深さは海面下なので、上下の距離は 深さ + 標高)
  events = sd.read_answers(day)
  extra = read_extra(day)
  for ev in events:
    ev["extra"] = extra.get(ev["no"], "")
  print(f"揺れの一覧: {len(events)} 件(etc/shake_day_{day:%Y%m%d}.csv)、追加の説明: {len(extra)} 件")
  wf = sd.cached_waveforms(day, (0.05, 20.0))
  center = jma_match.array_center({nm: c[:2] for nm, c in stations.items()})
  catalog, missing = jma_match.load_catalog(day, day + timedelta(days=1), center)
  if missing:
    print(f"  気象庁の震源リストが無い日: {', '.join(f'{d:%Y-%m-%d}' for d in missing)}")
  (out / "wave").mkdir(parents=True, exist_ok=True)
  hypos, detections = {}, []
  if any(e["kind"] in ("有珠山の地震", "発破") for e in events):
    hypos, detections = usu_hypocenters(), template_detections()
  items = []
  for ev in events:
    kind = KINDS.get(ev["kind"], OTHER_KIND)
    srcs = []
    if ev["kind"] in FAR_KINDS:
      for q in match_jma(ev, catalog, day):
        srcs.append(far_source(q, day, stations))
    elif ev["kind"] in ("有珠山の地震", "発破"):
      known, how = usu_source(ev, day, hypos, detections)
      band = (1.0, 10.0) if ev["kind"] == "発破" else (5.0, 20.0)
      picks = {nm: pick_p(wf[nm], ev["start"] - 4, ev["start"] + 8, band=band) for nm in stations}
      if known:
        x, y = proj.to_xy(known["lat"], known["lon"])
        z = 0.0 if ev["kind"] == "発破" else known["z"]    # 発破は地表(震源リストの深さは計算のばらつき)
        loc = {"x": float(x), "y": float(y), "z": z, "t0": (known["origin"] - day).total_seconds(), "res": 0.0}
      elif ev["kind"] == "発破":
        loc = locate_local(picks, xy, 0.0, fixed=proj.to_xy(*QUARRY), start=ev["start"])
        how = "採石場"
      elif sum(p is not None for p in picks.values()) >= 3:
        loc = locate_local(picks, xy, USU_DEPTH, center=proj.to_xy(*SUMMIT))
      else:
        # 読めた拠点が 3 つより少ない(小さな地震): 10 分以内の有珠山の地震と同じ場所、無ければ山頂の下とみて、発生時刻だけ決める
        prev = [s for e in items[::-1] if e["kind"] == "有珠山の地震" and ev["start"] - e["start"] < 600
                for s in e["src"] if s["type"] == "local"]
        loc = locate_local(picks, xy, USU_DEPTH, fixed=(prev[0]["x"], prev[0]["y"]) if prev else proj.to_xy(*SUMMIT),
                           start=ev["start"])
      if loc:
        lat, lon = proj.to_latlon(loc["x"], loc["y"])
        arr = local_arrivals(loc, xy, elev)
        n_obs = observed_arrivals(arr, known["picks"], day) if known and how == "有珠山の震源リスト" else 0
        srcs.append({"type": "local", "x": round(loc["x"], 3), "y": round(loc["y"], 3), "z": loc["z"],
                     "lat": round(float(lat), 5), "lon": round(float(lon), 5),
                     "t0": round(loc["t0"], 2), "arr": arr, "res": round(loc["res"], 2), "obs": n_obs,
                     "how": how or "P の読み取り",
                     "picks": {nm: (round(p, 2) if p is not None else None) for nm, p in picks.items()}})
    elif ev["kind"] == "花火":
      fx, fy = fireworks_center(proj)
      d = {nm: float(np.hypot(xy[nm][0] - fx, xy[nm][1] - fy)) for nm in stations}
      delay = {nm: v / SOUND_KMS for nm, v in d.items()}
      # 見せるのは最後の強い 1 発の FIREWORKS_SHOW 秒前から(それより前は花火ではない揺れがまじり、花火の揺れも小さい)
      ft0 = max(ev["start"] - 60, 0.0)
      fys = fireworks_waveforms(day, ft0, ev["end"] + 120, stations)
      last, how_last = fireworks_last(day, ev, fys, ft0)
      show0, show1 = last - FIREWORKS_SHOW, last + max(delay.values()) + 3
      bursts = fireworks_bursts(fys["Hokudai"], ft0, show0, show1, delay["Hokudai"])
      fw = {"ys": fys, "t0": ft0}
      srcs.append({"type": "sound", "x": round(fx, 3), "y": round(fy, 3), "bursts": bursts,
                   "area": list(FIREWORKS_AREA), "last": round(last, 2), "show": [round(show0, 1), round(show1, 1)],
                   "delay": {nm: round(v, 2) for nm, v in delay.items()}})
      print(f"     花火: 最後の強い 1 発 {fmt(last)}({how_last})。{fmt(show0)}〜{fmt(show1)} を見せる")
    # 窓: 発生(遠い地震は震源での発生)の少し前から、揺れの終わりの少し後まで
    t_first = min([ev["start"]] + [s["t0"] for s in srcs if "t0" in s])
    pre, post = (3, 3) if ev["kind"] == "有珠山の地震" else (8, 10)   # 有珠山の地震は短いので、前後を短く
    w0 = float(max(0.0, np.floor(t_first - pre)))
    w1 = float(min(86400.0, np.ceil(ev["end"] + post)))
    sound = [s for s in srcs if s["type"] == "sound"]
    if sound:                                   # 花火は、はっきり見える最後の 10 分ほどだけ
      w0, w1 = float(np.floor(sound[0]["show"][0])), float(np.ceil(sound[0]["show"][1] + 5))
    items.append({**ev, "color": sd.KIND_COLORS.get(ev["kind"], sd.INK), "w0": w0, "w1": w1,
                  "band": list(kind["band"]), "speed": kind["speed"], "span": kind["span"], "src": srcs,
                  "fw": fw if sound else None})
    desc = []
    for s in srcs:
      if s["type"] == "far":
        desc.append(f"{s['place']} M{s['m']} {s['km']} km 発生 {fmt(s['t0'])}")
      elif s["type"] == "local":
        desc.append(f"{s['how']} ({s['lat']}, {s['lon']}, {s['z']:.1f} km) 発生 {fmt(s['t0'])} ずれ {s['res']} 秒 "
                    f"読み取りの到着 {s['obs']} "
                    f"P: {', '.join(f'{station_names.full(nm)} {fmt(p) if p else '-'}' for nm, p in s['picks'].items())}")
      elif s["type"] == "sound":
        desc.append(f"花火 {len(s['bursts'])} 発")
    print(f"  {ev['no']:>2} {ev['kind']} {fmt(ev['start'])}  " + (" / ".join(desc) or "震源なし"))
  scenes = group_scenes(items, group_gap)
  for sc in scenes:
    sc["speed"] = max(sc["speed"], round((sc["w1"] - sc["w0"]) / SCENE_WALL))   # 長い揺れ(大きな地震・花火)は速く
    # 波形は窓の前後に、見える幅の半分を足す(今の時刻を真ん中にして、左右いっぱいに描くため)
    a, b = max(0.0, sc["w0"] - sc["span"] / 2), min(86400.0, sc["w1"] + sc["span"] / 2)
    fw = sc.pop("fw", None)
    if fw:                                      # 花火: 20〜40 Hz の波形(元の 250 Hz の記録から)
      i0, i1 = int(round((a - fw["t0"]) * FIREWORKS_RATE)), int(round((b - fw["t0"]) * FIREWORKS_RATE))
      waves = encode_waves({nm: y[max(i0, 0):i1] for nm, y in fw["ys"].items()}, fw["t0"] + max(i0, 0) / FIREWORKS_RATE, FIREWORKS_RATE)
    else:
      waves = event_waves(wf, stations, a, b, sc["band"])
    (out / "wave" / f"{sc['no']}.json").write_text(json.dumps(waves, separators=(",", ":")), encoding="utf-8")
    if len(sc["parts"]) > 1:
      print(f"  場面 {sc['no']}: {'・'.join(str(q['no']) for q in sc['parts'])} をまとめる")
  far = [s for e in items for s in e["src"] if s["type"] == "far"]
  maps = {"usu": usu_map(out, stations, proj), "japan": japan_map(out, far) if far else None}
  marks = {"summit": xy_dict(proj, SUMMIT, "有珠山"), "quarry": xy_dict(proj, QUARRY, "採石場"),
           "fireworks": {**dict(zip(("x", "y"), (round(v, 3) for v in fireworks_center(proj)))),
                         "name": "花火の打ち上げ場所", "area": list(FIREWORKS_AREA)}}
  meta = {
    "day": f"{day:%Y-%m-%d}", "title": f"ある有珠満点地震計の1日 {day:%Y年%-m月%-d日}({'月火水木金土日'[day.weekday()]})",
    "dayLink": DAY_LINK.format(day=f"{day:%Y%m%d}"), "vp": round(VP, 3), "vs": round(VS, 3), "sound": SOUND_KMS,
    "center": {"lat": center[0], "lon": center[1]},
    "stations": [{"id": nm, "label": station_names.full(nm), "x": round(xy[nm][0], 3), "y": round(xy[nm][1], 3),
                  "env": day_envelope(bandpass_day(wf[nm]))} for nm in stations],
    "envStep": ENV_STEP, "maps": maps, "marks": marks, "events": scenes, "groupGap": group_gap,
  }
  (out / "day.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
  page = PAGE.replace("__TITLE__", html.escape(meta["title"]))
  (out / "index.html").write_text(page, encoding="utf-8")
  return out / "index.html"

def bandpass_day(x):
  # 1 日の揺れの強さは、紙の記録と同じ 0.5〜20 Hz(1 時間ずつ。端の乱れは小さいので気にしない)
  y = np.full_like(x, np.nan)
  n = 3600 * RATE
  for h in range(24):
    seg = x[h * n:(h + 1) * n]
    if np.isfinite(seg).any():
      y[h * n:(h + 1) * n] = bandpass(seg, (0.5, 20.0))
  return y

# 花火の楕円の中心(km): 音の遅れから決めた場所から、西へ楕円の東西の半径だけずらす
def fireworks_center(proj):
  x, y = proj.to_xy(*FIREWORKS)
  return float(x) - FIREWORKS_AREA[0], float(y)

def xy_dict(proj, p, name):
  x, y = proj.to_xy(*p)
  return {"x": round(float(x), 3), "y": round(float(y), 3), "name": name}

def fmt(sec):
  sec = float(sec)
  return f"{int(sec // 3600):02d}:{int(sec % 3600 // 60):02d}:{sec % 60:04.1f}"


PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__TITLE__</title>
<style>
:root{--now:#111;--extra:#fff8e1;--bg:#fafaf7;--fg:#222;--sub:#666;--line:#d4d4cc;--panel:#fff;--btn:#ecebe6;--btnon:#1565c0;--wave:#1f5f99;--p:#1565c0;--s:#c62828;--snd:#c2185b}
@media (prefers-color-scheme:dark){:root{--now:#fff;--extra:#3a3220;--bg:#161616;--fg:#e8e8e8;--sub:#aaa;--line:#3a3a3a;--panel:#202020;--btn:#2d2d2d;--wave:#8ab8e6;--p:#64a8f0;--s:#ef6b6b;--snd:#f06ba8}}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;-webkit-text-size-adjust:100%}
#app{display:grid;height:100vh;height:100dvh;gap:6px;padding:6px max(6px,env(safe-area-inset-right)) max(6px,env(safe-area-inset-bottom)) max(6px,env(safe-area-inset-left));
  grid-template-columns:100%;grid-template-rows:auto minmax(0,44fr) minmax(0,26fr) auto auto auto;
  grid-template-areas:"top" "map" "wave" "info" "strip" "ctl"}
#top{grid-area:top;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
#top h1{font-size:13px;font-weight:600;margin:0;color:var(--sub);flex:1 1 auto;min-width:0}
#clock{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:.02em}
#speed{font-size:12px;color:var(--sub);min-width:7em;text-align:right}
#speed.fast{color:#d84e00}
.box{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;min-height:0}
.box canvas{position:absolute;inset:0;width:100%;height:100%;touch-action:none}
#mapBox{grid-area:map}
#jpBox{grid-area:map;justify-self:end;align-self:start;width:30%;aspect-ratio:1/1;margin:5px;z-index:2;box-shadow:0 1px 4px #0004}
#jpBox.hide{display:none}
.credit{position:absolute;right:4px;bottom:2px;font-size:9px;color:#555;background:#fffc;padding:0 3px;border-radius:3px;z-index:3}
#waveBox{grid-area:wave}
#info{grid-area:info;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 10px;font-size:13px;line-height:1.5;max-height:22vh;overflow:auto}
#info .head{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
#info .no{display:inline-block;min-width:1.6em;text-align:center;border-radius:10px;color:#fff;font-weight:700;padding:0 6px}
#info .kind{font-weight:700}
#info .what{font-weight:600}
#info .src{color:var(--sub);font-size:12px}
#info a{color:var(--btnon)}
#info .extra{margin-top:3px;padding:3px 8px;border-left:3px solid #f9a825;background:var(--extra)}
#info .extra::before{content:"見どころ ";font-weight:700;color:#e68a00}
#stripBox{grid-area:strip;height:44px}
#ctl{grid-area:ctl;display:flex;gap:6px;align-items:center;justify-content:center;flex-wrap:wrap}
button,select{font:inherit;font-size:15px;border:1px solid var(--line);background:var(--btn);color:var(--fg);border-radius:8px;padding:6px 12px;min-height:38px}
button.main{background:var(--btnon);color:#fff;border-color:var(--btnon);min-width:5.5em;font-weight:700}
select{padding:6px 6px}
#links{font-size:11px;color:var(--sub);text-align:center;grid-area:ctl;align-self:end;display:none}
/* スマホの横 */
@media (orientation:landscape) and (max-height:560px){
  #app{grid-template-columns:52% 1fr;grid-template-rows:auto minmax(0,1fr) auto auto auto;
    grid-template-areas:"map top" "map wave" "map info" "map strip" "map ctl"}
  #clock{font-size:22px}
  #info{max-height:26vh}
  #stripBox{height:36px}
  button,select{min-height:32px;padding:3px 10px;font-size:14px}
}
/* パソコン・タブレット(広い画面) */
@media (min-width:900px) and (min-height:600px){
  #app{grid-template-columns:62% 1fr;grid-template-rows:auto minmax(0,30fr) minmax(0,22fr) minmax(0,30fr) auto auto;
    grid-template-areas:"top top" "map jp" "map info" "wave wave" "strip strip" "ctl ctl";max-width:1500px;margin:auto}
  #jpBox{grid-area:jp;width:auto;aspect-ratio:auto;margin:0;justify-self:stretch;align-self:stretch;box-shadow:none}
  #info{max-height:none;font-size:14px}
  #clock{font-size:30px}
  #top h1{font-size:15px}
}
</style></head><body>
<div id="app">
  <div id="top"><h1 id="title"></h1><span id="clock">00:00:00</span><span id="speed"></span></div>
  <div class="box" id="mapBox"><canvas id="map"></canvas><div class="credit">出典: 地理院タイル(淡色地図)</div></div>
  <div class="box" id="jpBox"><canvas id="jp"></canvas></div>
  <div class="box" id="waveBox"><canvas id="wave"></canvas></div>
  <div id="info">読み込み中…</div>
  <div class="box" id="stripBox"><canvas id="strip"></canvas></div>
  <div id="ctl">
    <button id="prev" title="前の揺れ">⏮</button>
    <button id="play" class="main">▶ 再生</button>
    <button id="next" title="次の揺れ">⏭</button>
    <select id="mult" title="速さ"><option value="0.5">ゆっくり</option><option value="1" selected>ふつう</option><option value="2">はやく</option></select>
  </div>
</div>
<script>
"use strict";
const $ = id => document.getElementById(id);
const FAST = 1200;              // 揺れの無い時間の速さ(1 時間 ≒ 3 秒)
const RAMP = 1.5;               // 速さを変える時間の目安 [秒](画面の上の時間)
const S = {t: 0, playing: false, mult: 1, last: null, waves: {}, img: {}, loaded: 0};
let D = null;                   // day.json
const dpr = () => window.devicePixelRatio || 1;
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const pad = n => String(n).padStart(2, "0");
const hms = t => { t = Math.max(0, Math.min(86399.9, t)); return `${pad(Math.floor(t / 3600))}:${pad(Math.floor(t % 3600 / 60))}:${pad(Math.floor(t % 60))}`; };
const hm = t => `${Math.floor(t / 3600)}:${pad(Math.floor(t % 3600 / 60))}`;
const circled = n => n <= 20 ? String.fromCharCode(0x2460 + n - 1) : `(${n})`;
const label = id => D.stations.find(s => s.id === id).label;

// --- データ ---
function b64(s, T) { const b = Uint8Array.from(atob(s), c => c.charCodeAt(0)); return new T(b.buffer); }
async function load() {
  D = await (await fetch("day.json")).json();
  document.title = D.title;
  $("title").textContent = D.title;
  for (const st of D.stations) st.lev = b64(st.env, Uint8Array);
  D.envMax = new Uint8Array(D.stations[0].lev.length);
  for (const st of D.stations) for (let i = 0; i < st.lev.length; i++) D.envMax[i] = Math.max(D.envMax[i], st.lev[i]);
  for (const key of ["usu", "japan"]) {
    const m = D.maps[key];
    if (!m) continue;
    const im = new Image(); im.onload = () => draw(); im.src = m.src; S.img[key] = im;
  }
  if (!D.maps.japan) $("jpBox").classList.add("hide");
  for (const ev of D.events) for (const s of ev.src) if (s.tt) s.tt.inv = true;
  resize();
  showInfo(true);
  // 揺れの波形は順に読む(はじめの揺れから)
  for (const ev of D.events) {
    try {
      const w = await (await fetch(`wave/${ev.no}.json`)).json();
      for (const k in w.st) w.st[k].v = b64(w.st[k].d, Int8Array);
      S.waves[ev.no] = w;
    } catch (e) { console.warn("波形を読めません", ev.no, e); }
    S.loaded++;
    showInfo(true);
  }
}

// --- 時間の進め方: 揺れの窓から離れるほど速く(窓の中は種類ごとの速さ) ---
function speedAt(t) {
  let v = FAST;
  for (const ev of D.events) {
    const d = t < ev.w0 ? ev.w0 - t : t > ev.w1 ? t - ev.w1 : 0;
    v = Math.min(v, ev.speed * Math.exp(d / (RAMP * ev.speed)));
  }
  return v;
}
const current = t => D.events.find(ev => t >= ev.w0 && t <= ev.w1);
const nextEvent = t => D.events.find(ev => ev.w0 > t + 0.5);
function prevEvent(t) {
  const cur = current(t);
  if (cur && t - cur.w0 > 3) return cur;
  let p = null;
  for (const ev of D.events) if (ev.w0 < t - 0.5 && (!cur || ev !== cur)) p = ev;
  return p;
}
function tick(now) {
  if (!S.playing) { S.last = null; return; }
  const dt = S.last == null ? 0 : Math.min(0.1, (now - S.last) / 1000);
  S.last = now;
  const v = speedAt(S.t) * S.mult;
  let t = S.t + v * dt;
  const nx = nextEvent(S.t);
  if (nx && t > nx.w0) t = nx.w0;         // 早送りで揺れを飛ばさない
  S.t = Math.min(t, 86400);
  if (S.t >= 86400) setPlaying(false);
  draw();
  requestAnimationFrame(tick);
}
function setPlaying(on) {
  S.playing = on;
  $("play").textContent = on ? "⏸ 止める" : (S.t >= 86400 ? "↺ はじめから" : "▶ 再生");
  if (on) { if (S.t >= 86400) S.t = 0; requestAnimationFrame(tick); }
}
function seek(t) { S.t = Math.max(0, Math.min(86400, t)); draw(); }

// --- 画面の大きさ ---
function fit(c) {
  const r = c.getBoundingClientRect(), k = dpr();
  const w = Math.max(1, Math.round(r.width * k)), h = Math.max(1, Math.round(r.height * k));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return [r.width, r.height];
}
function resize() { for (const id of ["map", "jp", "wave", "strip"]) fit($(id)); draw(); }
window.addEventListener("resize", resize);
if (window.ResizeObserver) new ResizeObserver(() => resize()).observe($("app"));

// --- 描く ---
let lastInfo = null;
function draw() {
  if (!D) return;
  $("clock").textContent = hms(S.t);
  const v = speedAt(S.t) * S.mult;
  const sp = $("speed");
  sp.textContent = v >= 40 ? `早送り ×${Math.round(v)}` : Math.abs(v - 1) < 0.05 ? "本当の速さ" : `×${v < 10 ? v.toFixed(1) : Math.round(v)}`;
  sp.classList.toggle("fast", v >= 40);
  updateZoom();
  drawMap(); drawJapan(); drawWave(); drawStrip();
  showInfo(false);
}

// 揺れの強さ(0〜1): 揺れの窓では波形の最近 0.3 秒の最大、それ以外は 1 日の揺れの強さ
function amp(st, t) {
  const ev = current(t), w = ev && S.waves[ev.no];
  if (w && w.st[st.id]) {
    const x = w.st[st.id].v, i1 = Math.floor((t - w.t0) * w.rate), i0 = Math.max(0, i1 - Math.round(0.3 * w.rate));
    let m = 0;
    for (let i = i0; i <= Math.min(i1, x.length - 1); i++) if (x[i] !== -128) m = Math.max(m, Math.abs(x[i]));
    return Math.min(1, m / 127 * 1.3);
  }
  const lv = st.lev[Math.floor(t / D.envStep)] || 0;
  return lv ? Math.max(0, Math.min(1, (lv - 44) / 60)) : 0;
}
// 震央距離 [km] → 届くまでの秒(表)の逆: 秒 → km
function ttInv(tab, key, sec) {
  const k = tab.km, a = tab[key];
  if (sec <= a[0]) return 0;
  for (let i = 1; i < a.length; i++) if (a[i] >= sec) return k[i - 1] + (k[i] - k[i - 1]) * (sec - a[i - 1]) / (a[i] - a[i - 1]);
  return Infinity;
}
// 窓の中、または少しあと(輪が出ていく所)の揺れ
const showing = t => D.events.filter(ev => t >= ev.w0 && t <= ev.w1 + 4);

function drawMap() {
  const c = $("map"), [W, H] = fit(c), g = c.getContext("2d"), k = dpr();
  g.setTransform(k, 0, 0, k, 0, 0);
  g.fillStyle = "#eef0ea"; g.fillRect(0, 0, W, H);
  const m = D.maps.usu;
  if (!m) return;
  // 拠点と目印が入る大きさで、画面いっぱいに描く(まわりは地図の余白が見える)
  const pts = D.stations.map(s => [s.x, s.y]).concat(Object.values(D.marks).map(q => [q.x, q.y]));
  const bx0 = Math.min(...pts.map(q => q[0])) - 1.0, bx1 = Math.max(...pts.map(q => q[0])) + 1.0;
  const by0 = Math.min(...pts.map(q => q[1])) - 0.9, by1 = Math.max(...pts.map(q => q[1])) + 0.9;
  // 小さな地図が上に重なって拠点や目印を隠すときは、重ならない左の部分に収める
  const fitTo = Wf => {
    const kx = Math.min(Wf / (bx1 - bx0), H / (by1 - by0));
    return [kx, Wf / 2 - ((bx0 + bx1) / 2 - m.x0) * kx, H / 2 - (m.y1 - (by0 + by1) / 2) * kx];
  };
  let [kx, ox, oy] = fitTo(W);
  const jb = $("jpBox"), r0 = c.getBoundingClientRect(), r1 = jb.getBoundingClientRect();
  let inset = null;                 // 小さな地図が重なる所(左の端, 下の端)[px]
  if (!jb.classList.contains("hide") && r1.left > r0.left + 10 && r1.left < r0.right && r1.top < r0.bottom && r1.bottom > r0.top) {
    const ix0 = r1.left - r0.left - 6, iy1 = r1.bottom - r0.top + 6;
    inset = [ix0, iy1];
    const hidden = pts.some(([x, y]) => ox + (x - m.x0) * kx > ix0 && oy + (m.y1 - y) * kx < iy1 + 14);
    if (hidden) [kx, ox, oy] = fitTo(ix0);
  }
  const P = (x, y) => [ox + (x - m.x0) * kx, oy + (m.y1 - y) * kx];
  if (S.img.usu && S.img.usu.complete) g.drawImage(S.img.usu, ox, oy, (m.x1 - m.x0) * kx, (m.y1 - m.y0) * kx);
  g.save(); g.beginPath(); g.rect(0, 0, W, H); g.clip();
  const t = S.t;
  const fs = Math.max(10, Math.min(13, W / 40));
  // 目印
  const mk = D.marks;
  const sm = P(mk.summit.x, mk.summit.y);
  g.fillStyle = "#6d4c41"; g.beginPath(); g.moveTo(sm[0], sm[1] - 7); g.lineTo(sm[0] - 6, sm[1] + 4); g.lineTo(sm[0] + 6, sm[1] + 4); g.fill();
  text(g, "有珠山", sm[0], sm[1] + 15, fs, "#6d4c41", "center");
  const evs = showing(t);
  // 採石場・花火の打ち上げ場所は、その揺れの場面が始まってから出す(先に何が起きるかわからないように)
  for (const key of ["quarry", "fireworks"]) {
    const kindOf = p => (key === "quarry" ? p.kind === "発破" : p.kind === "花火");
    if (!D.events.some(ev => ev.w0 <= t && ev.parts.some(kindOf))) continue;
    const on = evs.some(ev => ev.parts.some(kindOf));
    const q = P(mk[key].x, mk[key].y);
    g.globalAlpha = on ? 1 : 0.45;
    g.fillStyle = key === "quarry" ? "#d84e00" : "#c2185b";
    if (key === "fireworks" && mk.fireworks.area) {
      // 打ち上げ場所: 船が西から東へ動くので、東西に長い楕円
      const [ax, ay] = mk.fireworks.area;
      g.save(); g.globalAlpha = on ? 0.35 : 0.18;
      g.beginPath(); g.ellipse(q[0], q[1], ax * kx, ay * kx, 0, 0, 7); g.fill();
      g.globalAlpha = on ? 0.9 : 0.4; g.strokeStyle = g.fillStyle; g.lineWidth = 1.5; g.stroke(); g.restore();
    } else { g.beginPath(); g.arc(q[0], q[1], 3.5, 0, 7); g.fill(); }
    if (key === "fireworks" && mk.fireworks.area) { text(g, mk[key].name, q[0], q[1] - mk.fireworks.area[1] * kx - 5, fs - 1, g.fillStyle, "center"); g.globalAlpha = 1; continue; }
    text(g, mk[key].name, q[0] + 6, q[1] - 5, fs - 1, g.fillStyle, "left");
    g.globalAlpha = 1;
  }
  // 終わった揺れの震源(有珠山のまわり)は、番号を付けて薄く残す
  for (const ev of D.events) {
    if (ev.w1 + 4 >= t) continue;
    for (const s of ev.src) if (s.type === "local") {
      const e = P(s.x, s.y);
      g.globalAlpha = 0.6; cross(g, e[0], e[1], 4, ev.color);
      text(g, circled(ev.no), e[0] + 6, e[1] - 4, fs - 1, ev.color, "left", true); g.globalAlpha = 1;
    }
  }
  // 波
  const R = Math.hypot(W, H) / kx;    // 地図の対角 [km]
  for (const ev of evs) {
    for (const s of ev.src) {
      if (s.type === "local") {
        const e = P(s.x, s.y), dt = t - s.t0;
        cross(g, e[0], e[1], 7, ev.color);
        for (const [key, v, col, name] of [["p", D.vp, css("--p"), "P波"], ["s", D.vs, css("--s"), "S波"]]) {
          const r = Math.sqrt(Math.max(0, (v * dt) ** 2 - s.z ** 2));
          if (dt <= 0 || r > R) continue;
          g.strokeStyle = col; g.lineWidth = 2.5; g.globalAlpha = Math.max(0.15, 1 - r / R);
          g.beginPath(); g.arc(e[0], e[1], r * kx, 0, 7); g.stroke();
          g.globalAlpha = 1;
          if (r * kx > 14) text(g, name, e[0], e[1] - r * kx - 3, fs, col, "center");
        }
      } else if (s.type === "far") {
        const az = s.az * Math.PI / 180, ux = Math.sin(az), uy = Math.cos(az);   // 震源の向き(東・北)
        const cx = (m.x0 + m.x1) / 2, cy = (m.y0 + m.y1) / 2;
        for (const [key, col, name] of [["p", css("--p"), "P波"], ["s", css("--s"), "S波"]]) {
          const d = ttInv(s.tt, key, t - s.t0);         // 波の先が届いた震央距離 [km]
          const u = s.km - d;                            // 地図の中心から震源の向きへ
          if (!isFinite(d) || u > R || u < -R) continue;
          const bx = cx + ux * u, by = cy + uy * u;
          const a = P(bx - uy * R, by + ux * R), b = P(bx + uy * R, by - ux * R);
          g.strokeStyle = col; g.lineWidth = 3; g.globalAlpha = 0.85;
          g.beginPath(); g.moveTo(a[0], a[1]); g.lineTo(b[0], b[1]); g.stroke(); g.globalAlpha = 1;
          const lp = P(bx + ux * 0.3, by + uy * 0.3);
          text(g, name, lp[0], lp[1], fs, col, "center");
        }
        // 震源の向きの矢印(地図のふち)
        // 矢印の先は、地図の真ん中から震源の向きに進んで、ふち(18 px 手前)に当たる所
        const cc = [W / 2, H / 2], m18 = 18;
        const L = Math.min(Math.abs(ux) > 1e-6 ? (W / 2 - m18) / Math.abs(ux) : Infinity,
                           Math.abs(uy) > 1e-6 ? (H / 2 - m18) / Math.abs(uy) : Infinity);
        // 小さな地図が重なる所にかかるときは、矢印を手前で止める
        let Lt = L;
        while (inset && Lt > 60 && cc[0] + ux * Lt > inset[0] - 30 && cc[1] - uy * Lt < inset[1] + 30) Lt -= 4;
        const ex = cc[0] + ux * Lt, ey = cc[1] - uy * Lt;
        arrow(g, cc[0] + ux * (Lt - 40), cc[1] - uy * (Lt - 40), ex, ey, ev.color);
        // 震源の名前は、拠点の名前と重ならないように地図の左の角に(場面に震源が 2 つあれば 2 行)
        const dirs = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東", "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"];
        const row = ev.src.filter(q => q.type === "far").indexOf(s);
        const nFar = ev.src.filter(q => q.type === "far").length;
        // 矢印と反対側の角に置く(南の地震は左上、北の地震は左下)
        const ly = uy < 0 ? fs + 8 + row * (fs + 6) : H - 8 - (nFar - 1 - row) * (fs + 6);
        text(g, `${dirs[Math.round(s.az / 22.5) % 16]} ${s.km} km の ${s.place} M${s.m.toFixed(1)}`, 8, ly, fs + 1, ev.color, "left", true);
      } else if (s.type === "sound") {
        const e = P(s.x, s.y), life = Math.max(...Object.values(s.delay)) + 3;   // いちばん遠い拠点に届くまで
        for (const [tb, size] of s.bursts) {
          const dt = t - tb;
          if (dt < 0 || dt > life) continue;
          const r = dt * D.sound;
          g.strokeStyle = css("--snd"); g.lineWidth = 1.5 + Math.min(3, size / 15); g.globalAlpha = Math.max(0, 1 - dt / life);
          g.setLineDash([5, 4]); g.beginPath(); g.arc(e[0], e[1], r * kx, 0, 7); g.stroke(); g.setLineDash([]); g.globalAlpha = 1;
        }
        if (s.bursts.some(([tb]) => t - tb >= 0 && t - tb < 0.8)) { g.fillStyle = "#ff4081"; g.beginPath(); g.arc(e[0], e[1], 9, 0, 7); g.fill(); }
      }
    }
  }
  // 拠点
  for (const st of D.stations) {
    const p = P(st.x, st.y), a = amp(st, t);
    const flash = [];
    for (const ev of evs) for (const s of ev.src) {
      if (!s.arr || !s.arr[st.id]) continue;
      for (const [key, col] of [["p", css("--p")], ["s", css("--s")]]) {
        const d = t - s.arr[st.id][key];
        if (d >= 0 && d < 1.5) flash.push([d, col]);
      }
    }
    for (const [d, col] of flash) {
      g.strokeStyle = col; g.lineWidth = 3; g.globalAlpha = 1 - d / 1.5;
      g.beginPath(); g.arc(p[0], p[1], 8 + d * 18, 0, 7); g.stroke(); g.globalAlpha = 1;
    }
    const r = 5 + 11 * a;
    g.fillStyle = `hsl(${40 - 40 * a},100%,${55 - 10 * a}%)`; g.strokeStyle = "#333"; g.lineWidth = 1.2;
    g.beginPath(); g.arc(p[0], p[1], r, 0, 7); g.fill(); g.stroke();
    text(g, st.label, p[0], p[1] + r + fs + 1, fs, "#111", "center", true);
  }
  g.restore();
}
function text(g, s, x, y, size, col, align, box) {
  g.font = `600 ${size}px -apple-system,"Hiragino Sans",sans-serif`;
  g.textAlign = align; g.textBaseline = "alphabetic";
  if (box) { g.lineWidth = 3.5; g.strokeStyle = "#fffd"; g.strokeText(s, x, y); }
  g.fillStyle = col; g.fillText(s, x, y);
}
function cross(g, x, y, r, col) {
  g.strokeStyle = "#fff"; g.lineWidth = 5;
  for (const w of [5, 2.5]) {
    g.lineWidth = w; g.strokeStyle = w > 3 ? "#fff" : col;
    g.beginPath(); g.moveTo(x - r, y - r); g.lineTo(x + r, y + r); g.moveTo(x + r, y - r); g.lineTo(x - r, y + r); g.stroke();
  }
}
function arrow(g, x0, y0, x1, y1, col) {
  const a = Math.atan2(y1 - y0, x1 - x0);
  g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 4;
  g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1 - 8 * Math.cos(a), y1 - 8 * Math.sin(a)); g.stroke();
  g.beginPath(); g.moveTo(x1, y1); g.lineTo(x1 - 14 * Math.cos(a - 0.45), y1 - 14 * Math.sin(a - 0.45));
  g.lineTo(x1 - 14 * Math.cos(a + 0.45), y1 - 14 * Math.sin(a + 0.45)); g.fill();
}

// 小さな地図(日本のまわり、Web メルカトル)
function drawJapan() {
  const m = D.maps.japan;
  if (!m) return;
  const c = $("jp"), [W, H] = fit(c), g = c.getContext("2d"), k = dpr();
  g.setTransform(k, 0, 0, k, 0, 0);
  g.fillStyle = "#dfe8f3"; g.fillRect(0, 0, W, H);
  const my = lat => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  const sx = (m.east - m.west), sy = my(m.north) - my(m.south);
  const kk = Math.min(W / m.w, H / m.h);           // 地図の全体が入るように
  const iw = m.w * kk, ih = m.h * kk, ox = (W - iw) / 2, oy = (H - ih) / 2;
  const P = (lat, lon) => [ox + (lon - m.west) / sx * iw, oy + (my(m.north) - my(lat)) / sy * ih];
  if (S.img.japan && S.img.japan.complete) g.drawImage(S.img.japan, ox, oy, iw, ih);
  const t = S.t, fs = Math.max(9, Math.min(12, W / 22));
  const evs = showing(t);
  for (const ev of D.events) for (const s of ev.src) {
    if (s.type !== "far" || t < ev.w0) continue;       // まだ起きていない地震は出さない
    const on = evs.includes(ev);
    const p = P(s.lat, s.lon);
    g.globalAlpha = on ? 1 : 0.55;
    cross(g, p[0], p[1], on ? 6 : 4, ev.color);
    if (!on) text(g, circled(ev.no), p[0] + 6, p[1] - 4, fs - 1, ev.color, "left", true);
    g.globalAlpha = 1;
    if (!on) continue;
    for (const [key, col] of [["p", css("--p")], ["s", css("--s")]]) {
      const r = ttInv(s.tt, key, t - s.t0);
      if (!(r > 0) || !isFinite(r) || r > s.km * 1.25) continue;
      g.strokeStyle = col; g.lineWidth = 2.2; g.beginPath();
      for (let i = 0; i <= 72; i++) {
        const [la, lo] = dest(s.lat, s.lon, i * 5, r);
        const q = P(la, lo);
        if (i) g.lineTo(q[0], q[1]); else g.moveTo(q[0], q[1]);
      }
      g.stroke();
    }
    const lab = `${s.place} M${s.m.toFixed(1)}`;
    g.font = `600 ${fs}px -apple-system,"Hiragino Sans",sans-serif`;
    const hw = g.measureText(lab).width / 2 + 3;
    text(g, lab, Math.max(hw, Math.min(W - hw, p[0])), Math.max(fs + 2, Math.min(H - 14, p[1] + 16)), fs, ev.color, "center", true);
  }
  const u = P(D.center.lat, D.center.lon);
  g.fillStyle = "#ffb300"; g.strokeStyle = "#333"; g.lineWidth = 1;
  g.beginPath(); g.arc(u[0], u[1], 4.5, 0, 7); g.fill(); g.stroke();
  text(g, "有珠山", u[0] - 7, u[1] + 3, fs, "#333", "right", true);
  text(g, "出典: 地理院タイル", W - 3, H - 3, 8, "#555", "right", true);
}
// 緯度・経度から、方位 az [度] へ d [km] 進んだ所
function dest(lat, lon, az, d) {
  const R = 6371, r = Math.PI / 180, b = d / R, p1 = lat * r, l1 = lon * r, th = az * r;
  const p2 = Math.asin(Math.sin(p1) * Math.cos(b) + Math.cos(p1) * Math.sin(b) * Math.cos(th));
  const l2 = l1 + Math.atan2(Math.sin(th) * Math.sin(b) * Math.cos(p1), Math.cos(b) - Math.sin(p1) * Math.sin(p2));
  return [p2 / r, l2 / r];
}

// 4 拠点の波形。真ん中の縦線が今(P波・S波の印が、拠点に届く時刻にこの線を通る)
//   早送りの間は前後 30 分の揺れの強さ(ざっくり)。揺れの場面に入ると、真ん中を中心に拡大して本当の波形に替わる(出るときは逆)
const ZOOM_SEC = 0.9;           // 拡大・縮小にかける時間 [秒](画面の上の時間)
const COARSE = 3600;            // ざっくりの表示の幅 [秒]
function updateZoom() {
  const now = performance.now(), dt = S.zt == null ? 0 : Math.min(0.1, (now - S.zt) / 1000);
  S.zt = now;
  const cur = current(S.t), target = cur && S.waves[cur.no] ? 1 : 0;
  if (cur) S.zev = cur;
  if (S.zoom == null) S.zoom = target;
  const step = dt / ZOOM_SEC;
  S.zoom += Math.max(-step, Math.min(step, target - S.zoom));
  if (S.zoom !== target && !S.playing && !S.zraf) S.zraf = requestAnimationFrame(() => { S.zraf = 0; draw(); });
}
const ease = z => z * z * (3 - 2 * z);
function drawWave() {
  const c = $("wave"), [W, H] = fit(c), g = c.getContext("2d"), k = dpr();
  g.setTransform(k, 0, 0, k, 0, 0);
  g.clearRect(0, 0, W, H);
  const t = S.t, z = ease(S.zoom || 0), ev = S.zev, w = ev && S.waves[ev.no];
  const span = Math.exp(Math.log(COARSE) * (1 - z) + Math.log(ev ? ev.span : COARSE) * z);
  const top = 4, bot = 16, n = D.stations.length, rh = (H - top - bot) / n, Rm = 8;
  const fs = Math.max(10, Math.min(12, rh / 3));
  g.font = `600 ${fs}px -apple-system,"Hiragino Sans",sans-serif`;
  const L = Math.ceil(Math.max(...D.stations.map(s => g.measureText(s.label).width))) + 12;
  const pw = W - L - Rm, X = tt => L + (tt - (t - span / 2)) / span * pw, mx = L + pw / 2;
  const fine = z > 0.5 && w;
  text(g, fine ? `${ev.band[0]}〜${ev.band[1]} Hz の上下動(4 拠点で同じ倍率)` : "前後 30 分の揺れの強さ(10 秒ごと)", L, H - 4, 10, css("--sub"), "left");
  const sw = span >= 600 ? `${Math.round(span / 60)} 分` : `${Math.round(span)} 秒`;
  text(g, `幅 ${sw}`, W - Rm, H - 4, 10, css("--sub"), "right");
  D.stations.forEach((st, j) => {
    const y0 = top + rh * j, mid = y0 + rh / 2;
    g.strokeStyle = css("--line"); g.lineWidth = 1;
    g.beginPath(); g.moveTo(L, y0 + rh); g.lineTo(W - Rm, y0 + rh); g.stroke();
    text(g, st.label, L - 6, mid + 4, fs, css("--fg"), "right");
    g.save(); g.beginPath(); g.rect(L, y0, pw, rh); g.clip();
    // ざっくり: 揺れの強さ(拡大するにつれて薄くする)
    if (z < 1) {
      const lev = st.lev, step = D.envStep;
      g.globalAlpha = 1 - z;
      for (let tt = Math.floor((t - span / 2) / step) * step; tt <= t + span / 2; tt += step) {
        const v = lev[Math.floor(tt / step)];
        if (!v) continue;
        const hgt = Math.max(1, Math.min(1, (v - 20) / 120) * rh * 0.95);
        const e = tt <= t ? partAt(tt, step) : null;     // これから来る揺れには色を付けない
        g.fillStyle = e ? e.color : css("--wave");
        g.globalAlpha = (1 - z) * (e ? 0.95 : 0.55);
        g.fillRect(X(tt), mid + rh * 0.47 - hgt, Math.max(1, X(tt + step) - X(tt)), hgt);
      }
      g.globalAlpha = 1;
    }
    // 細かい: 本当の波形
    if (z > 0 && w && w.st[st.id]) {
      const x = w.st[st.id].v, half = rh / 2 * 0.95;
      g.globalAlpha = z;
      for (const s of ev.src) if (s.arr && s.arr[st.id]) {
        for (const [key, col] of [["p", css("--p")], ["s", css("--s")]]) {
          const xx = X(s.arr[st.id][key]);
          if (xx < L || xx > W) continue;
          g.strokeStyle = col; g.lineWidth = 1.5; g.setLineDash(s.arr[st.id][key + "_obs"] ? [] : [3, 3]);
          g.beginPath(); g.moveTo(xx, y0 + 2); g.lineTo(xx, y0 + rh - 2); g.stroke(); g.setLineDash([]);
          text(g, key.toUpperCase(), xx + 2, y0 + 11, 10, col, "left");
        }
      }
      g.strokeStyle = css("--wave"); g.lineWidth = 1; g.beginPath();
      const per = span * w.rate / pw;
      let started = false;
      for (let i = 0; i < pw; i++) {
        const a = Math.floor(((t - span / 2) + i / pw * span - w.t0) * w.rate), b = Math.max(a + 1, Math.floor(a + per));
        if (b <= 0 || a >= x.length) { started = false; continue; }
        let lo = 127, hi = -127, ok = false;
        for (let q = Math.max(0, a); q < Math.min(b, x.length); q++) { const v = x[q]; if (v === -128) continue; ok = true; if (v < lo) lo = v; if (v > hi) hi = v; }
        if (!ok) { started = false; continue; }
        const xx = L + i;
        if (!started) { g.moveTo(xx, mid - hi / 127 * half); started = true; } else g.lineTo(xx, mid - hi / 127 * half);
        g.lineTo(xx, mid - lo / 127 * half);
      }
      g.stroke();
      g.globalAlpha = 1;
    }
    g.restore();
  });
  // 今の線
  g.strokeStyle = css("--now"); g.lineWidth = 2;
  g.beginPath(); g.moveTo(mx, top + 12); g.lineTo(mx, H - bot); g.stroke();
  text(g, "今", mx, top + 10, 11, css("--now"), "center", true);
}
// tt〜tt+step にかかる揺れ(場面の中の 1 つ)
function partAt(tt, step) {
  for (const sc of D.events) for (const p of sc.parts) if (tt + step >= p.start && tt <= p.end) return p;
  return null;
}

// 1 日の帯(0〜24 時): 4 拠点のうち大きいほうの揺れの強さ、揺れの印、今の時刻
function drawStrip() {
  const c = $("strip"), [W, H] = fit(c), g = c.getContext("2d"), k = dpr();
  g.setTransform(k, 0, 0, k, 0, 0);
  g.clearRect(0, 0, W, H);
  const L = 6, R = 6, X = t => L + t / 86400 * (W - L - R), base = H - 12;
  const env = D.envMax, per = env.length / (W - L - R);
  g.fillStyle = css("--sub"); g.globalAlpha = 0.5;
  for (let i = 0; i < W - L - R; i++) {
    let m = 0;
    for (let q = Math.floor(i * per); q < Math.floor((i + 1) * per); q++) m = Math.max(m, env[q]);
    if (!m) continue;
    const h = Math.max(1, Math.min(1, (m - 30) / 120) * (base - 2));
    g.fillRect(L + i, base - h, 1, h);
  }
  g.globalAlpha = 1;
  for (const sc of D.events) for (const ev of sc.parts) {
    const a = X(ev.start), b = Math.max(a + 3, X(ev.end));
    g.fillStyle = ev.color; g.fillRect(a, 2, b - a, base - 2);
  }
  for (let h = 0; h <= 24; h += 3) {
    const x = X(h * 3600);
    g.fillStyle = css("--sub"); g.fillRect(x, base, 1, 3);
    text(g, String(h), Math.min(W - 8, Math.max(6, x)), H - 1, 9, css("--sub"), "center");
  }
  // 今: 揺れの印(赤や緑)と見分けやすいように、黒(暗い画面では白)の線と、上の三角
  const x = X(S.t), now = css("--now"), halo = css("--panel");
  g.fillStyle = halo; g.fillRect(x - 2.5, 0, 5, base + 2);
  g.fillStyle = now; g.fillRect(x - 1, 0, 2, base + 2);
  g.beginPath(); g.moveTo(x - 5, 0); g.lineTo(x + 5, 0); g.lineTo(x, 6); g.fill();
}
// 帯をなぞると、その時刻へ
(() => {
  const c = $("strip");
  let down = false;
  const go = e => { const r = c.getBoundingClientRect(); seek((e.clientX - r.left - 6) / (r.width - 12) * 86400); };
  c.addEventListener("pointerdown", e => { down = true; c.setPointerCapture(e.pointerId); go(e); });
  c.addEventListener("pointermove", e => { if (down) go(e); });
  c.addEventListener("pointerup", () => { down = false; });
})();

// --- 説明 ---
function srcText(ev) {
  const out = [];
  for (const s of ev.src) {
    if (s.type === "far") out.push(`震源: ${s.place} M${s.m.toFixed(1)}(深さ ${Math.round(s.z)} km、有珠山から ${s.km} km)。${hms(s.t0)} に起き、P波は ${Math.round(s.arr.Hokudai.p - s.t0)} 秒後、S波は ${Math.round(s.arr.Hokudai.s - s.t0)} 秒後に届く`);
    else if (s.type === "local") out.push(`震源: ${ev.kind === "発破" ? "採石場" : `有珠山の山頂から ${dirText(s)}(深さ ${s.z.toFixed(1)} km)`}。${hms(s.t0)} に起き、${(Math.min(...Object.values(s.arr).map(a => a.p)) - s.t0).toFixed(1)} 秒で最初の拠点に P波が届く${s.obs ? "(波形の P・S の印の実線は読み取った時刻、点線は計算の見込み)" : "(P・S の印は計算の見込み)"}`);
    else if (s.type === "sound") out.push(`打ち上げ場所から音(空気の振動、秒速 340 m)で届く。Kawaminami まで ${s.delay.Hokudai.toFixed(1)} 秒、Bamba まで ${s.delay.Bamba.toFixed(1)} 秒。地図の輪は、Kawaminami の揺れが大きくなった時刻から、音が届くまでの時間をさかのぼって描いたもの(打ち上げの船は西から東へ動くので、場所は楕円で示す)`);
  }
  return out.join("<br>");
}
function dirText(s) {
  const dx = s.x - D.marks.summit.x, dy = s.y - D.marks.summit.y, d = Math.hypot(dx, dy);
  if (d < 0.4) return "すぐ下";
  const names = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"];
  const a = (Math.atan2(dx, dy) * 180 / Math.PI + 360 + 22.5) % 360;
  return `${names[Math.floor(a / 45)]} ${d.toFixed(1)} km`;
}
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c])); }
function showInfo(force) {
  const t = S.t, cur = D.events.find(ev => t >= ev.w0 && t <= ev.w1 + 20);
  const nx = D.events.find(ev => ev.w0 > t);
  const key = cur ? `c${cur.no}` : `n${nx ? nx.no : "end"}`;
  const loading = S.loaded < D.events.length ? `<div class="src">波形を読み込み中 ${S.loaded}/${D.events.length}</div>` : "";
  if (!force && key === lastInfo) return;
  lastInfo = key;
  if (cur) {
    const best = Object.entries((S.waves[cur.no] || {st: {}}).st).sort((a, b) => b[1].peak - a[1].peak)[0];
    const lab = label(best ? best[0] : "Hokudai");
    const parts = cur.parts.map(p => `<div class="head"><span class="no" style="background:${p.color}">${p.no}</span>
      <span class="kind" style="color:${p.color}">${esc(p.kind)}</span><span class="src">${hms(p.start)}〜${hms(p.end)}・${esc(p.vis)}</span></div>
      <div class="what">${esc(p.text)}</div><div>${esc(p.note)}</div>${p.extra ? `<div class="extra">${esc(p.extra)}</div>` : ""}`).join("");
    const together = cur.parts.length > 1 ? `<div class="src">続けて起きた ${cur.parts.length} つの揺れを、まとめて見せています</div>` : "";
    $("info").innerHTML = `${parts}${together}<div class="src">${srcText(cur)}</div>
      <div class="src"><a href="${D.dayLink}#${encodeURIComponent(lab)}/z/${Math.round(cur.start)}" target="_blank">この揺れを拡大して聴く ›</a></div>${loading}`;
  } else if (nx) {
    // つぎの揺れは番号と時刻だけ(何の揺れかは、届いてからのお楽しみ)
    $("info").innerHTML = `<div class="head"><span class="src">つぎの揺れ</span><span class="no" style="background:#777">${nx.no}</span>
      <span class="src">${hm(nx.start)} ごろ。どこで起きた、何の揺れかな?</span></div>${t === 0 ? `<div class="src">▶ を押すと 0 時から時計が進みます。揺れの無い時間は早送り(1 時間 ≒ 3 秒)、揺れが来る所でゆっくりになります。⏭ で次の揺れへ、下の帯をなぞると好きな時刻へ。</div>` : ""}${loading}`;
  } else {
    $("info").innerHTML = `<div>この日の揺れは ${D.events.length} 件でした。下の帯の色の所をなぞると、もう一度見られます。</div>${loading}`;
  }
}

// --- 操作 ---
$("play").addEventListener("click", () => setPlaying(!S.playing));
$("next").addEventListener("click", () => { const ev = nextEvent(S.t); if (ev) { seek(ev.w0); if (!S.playing) setPlaying(true); } else seek(86400); });
$("prev").addEventListener("click", () => { const ev = prevEvent(S.t); seek(ev ? ev.w0 : 0); });
$("mult").addEventListener("change", e => { S.mult = +e.target.value; draw(); });
document.addEventListener("keydown", e => {
  if (e.key === " ") { e.preventDefault(); setPlaying(!S.playing); }
  else if (e.key === "ArrowRight") $("next").click();
  else if (e.key === "ArrowLeft") $("prev").click();
});
load().catch(e => { $("info").textContent = "読み込めませんでした(フォルダごとサーバーに置いて開いてください): " + e; });
</script>
</body></html>
"""


# 日の一覧のページ(<出力の親>/index.html)。フォルダにある日(<日>/day.json)を全部並べる
def write_index(folder):
  folder = Path(folder)
  items = []
  for path in sorted(folder.glob("*/day.json")):
    meta = json.loads(path.read_text(encoding="utf-8"))
    kinds = {}
    for sc in meta["events"]:
      for part in sc.get("parts") or [sc]:
        kinds[part["kind"]] = kinds.get(part["kind"], 0) + 1
    note = "、".join(f"{k} {n}" for k, n in kinds.items())
    items.append(f'<li><a href="{path.parent.name}/">{html.escape(meta["title"])}</a>'
                 f'<div class="note">出てくる揺れ: {html.escape(note) or "なし"}</div></li>')
  page = INDEX.replace("__ITEMS__", "\n".join(items))
  (folder / "index.html").write_text(page, encoding="utf-8")
  return folder / "index.html"

INDEX = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ある有珠満点地震計の1日</title>
<style>
:root { color-scheme: light dark; --bg: #f6f7f9; --panel: #fff; --ink: #1c2126; --muted: #68727d; --line: #d5dae0; --accent: #1565c0; }
@media (prefers-color-scheme: dark) { :root { --bg: #15181b; --panel: #1f2327; --ink: #e8eaed; --muted: #9aa3ad; --line: #3a4046; --accent: #64a8f0; } }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 16px; }
h1 { font-size: 19px; margin: 0 0 8px; }
.note { color: var(--muted); font-size: 13px; }
ul { list-style: none; padding: 0; }
li { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin: 8px 0; }
a { color: var(--accent); font-weight: 600; font-size: 16px; text-decoration: none; }
</style>
</head>
<body>
<main>
<h1>ある有珠満点地震計の1日</h1>
<p class="note">有珠山のまわりの満点地震計 4 か所に、1 日のあいだにどんな揺れが、どこから、どう届くのかを、時計を進めながら見るページです。揺れの無い時間は早送りになります。日を選んでください。</p>
<ul>
__ITEMS__
</ul>
</main>
</body>
</html>
"""


def main():
  parser = argparse.ArgumentParser(description="「ある有珠満点地震計の1日」のページを作る(試作)")
  parser.add_argument("day", help="日(例: 2026-07-08)")
  parser.add_argument("-o", "--output", help="出力のフォルダ(省略すると batch_plots/quiz/oneday/<日>)")
  parser.add_argument("--group-gap", type=float, default=GROUP_GAP,
                      help=f"前の揺れの終わりからこの秒数以内に始まる揺れを 1 つの場面にまとめる(既定 {GROUP_GAP:g})")
  args = parser.parse_args()
  day = datetime.strptime(args.day, "%Y-%m-%d")
  out = Path(args.output) if args.output else OUTPUT_DIR / f"{day:%Y%m%d}"
  out.mkdir(parents=True, exist_ok=True)
  page = build(day, out, args.group_gap)
  size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
  print(f"書き出しました({size / 1e6:.1f} MB): {page}")
  if not args.output:
    print(f"日の一覧: {write_index(OUTPUT_DIR)}")

if __name__ == "__main__":
  main()
