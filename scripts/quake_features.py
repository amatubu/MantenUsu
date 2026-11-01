# quake_features.py
# 地震らしい揺れの期間の波形から、近い地震かどうかを見分けるための値を計算する
# (screen_events.py と、UsuMantenViewer.py の地震の候補を探すバッチ処理から使う)
#   - 揺れの長さ      ... 包絡線が雑音の何倍かを超えている時間
#   - 卓越周波数      ... 揺れ始めの10秒間で最も強い周波数
#   - P→最大振幅      ... P波到着から水平動の振幅が最大になるまでの時間(S-P時間の目安)
#   - 振幅の比        ... 拠点ごとの最大振幅の、最大と最小の比
#   - 到来方向など    ... P波の時間差を平面波に当てはめた結果と、近い地震としての震源
import numpy as np

from hypocenter import pick_p_arrivals, fit_plane_wave, locate_grid
from plot_common import NM_TO_CM

# 近い地震の小さな揺れを見やすくするための帯域通過フィルター [Hz]
BAND = (2.0, 20.0)
# 包絡線(移動RMS)の窓の長さ [秒]
ENVELOPE_WINDOW = 1.0
# 揺れているとみなす、雑音(包絡線の下位20%の値)に対する倍率
DURATION_RATIO = 3.0
# P波の検出(近い地震向けに短い窓)
PICK_PARAMS = dict(p_sta=0.3, p_lta=3.0, p_thr=3.0, window=3.0)
# 震源の計算に使う P波の速度 [km/s]
VP = 5.5
# 近い地震の候補とする条件
NEAR_MAX_DURATION = 40.0   # 揺れの長さ [秒] 以下
NEAR_MAX_P_TO_PEAK = 4.0   # P→最大振幅 [秒] 以下
NEAR_MIN_FREQ = 5.0        # 卓越周波数 [Hz] 以上

# 移動RMSによる包絡線
def envelope(data, df):
  n = max(1, int(ENVELOPE_WINDOW * df))
  return np.sqrt(np.convolve(data.astype(float) ** 2, np.ones(n) / n, mode="same"))

# 帯域通過フィルターをかけたコピー
def band_filtered(tr):
  tr = tr.copy()
  tr.detrend("demean")
  tr.filter("bandpass", freqmin=BAND[0], freqmax=BAND[1], corners=4, zerophase=True)
  return tr

# 揺れ始めの10秒間の卓越周波数 [Hz](スペクトルを少しならしてから最大の周波数を取る)
def dominant_frequency(tr, start_offset):
  df = tr.stats.sampling_rate
  i0 = max(0, int(start_offset * df))
  seg = tr.data[i0:i0 + int(10 * df)].astype(float)
  if len(seg) < df:
    return None
  seg = (seg - seg.mean()) * np.hanning(len(seg))
  spec = np.abs(np.fft.rfft(seg))
  freqs = np.fft.rfftfreq(len(seg), 1 / df)
  spec = np.convolve(spec, np.ones(9) / 9, mode="same")
  band = (freqs >= 0.5) & (freqs <= 40)
  return float(freqs[band][np.argmax(spec[band])])

# 1期間を解析して、結果の辞書と画像用の情報を返す
def analyze(st, stations, proj):
  names = sorted({tr.stats.station for tr in st.select(channel="*U*")})
  ref_time = min(tr.stats.starttime for tr in st)
  u = {nm: st.select(station=nm, channel="*U*")[0] for nm in names}
  uf = {nm: band_filtered(u[nm]) for nm in names}

  # --- 包絡線: 全拠点を雑音で割って足し、一番大きいところを地震の中心とする ---
  envs, noise = {}, {}
  for nm in names:
    df = uf[nm].stats.sampling_rate
    envs[nm] = envelope(uf[nm].data, df)
    noise[nm] = np.percentile(envs[nm], 20) or 1.0
  df = uf[names[0]].stats.sampling_rate
  npts = min(len(envs[nm]) for nm in names)
  ratio = np.median([envs[nm][:npts] / noise[nm] for nm in names], axis=0)
  peak_i = int(np.argmax(ratio))
  peak_t = peak_i / df + (uf[names[0]].stats.starttime - ref_time)

  # 揺れの長さ: 中心から前後に、雑音の DURATION_RATIO 倍を下回るまで
  above = ratio > DURATION_RATIO
  i0 = peak_i
  while i0 > 0 and above[i0 - 1]:
    i0 -= 1
  i1 = peak_i
  while i1 < npts - 1 and above[i1 + 1]:
    i1 += 1
  duration = (i1 - i0) / df

  # --- P波: 地震の中心の手前だけを使って、全拠点の同時検出 ---
  trimmed = {nm: uf[nm].slice(ref_time + max(0.0, peak_t - 30), ref_time + peak_t + 2)
             for nm in names}
  picks, _ = pick_p_arrivals(trimmed, ref_time, PICK_PARAMS["p_sta"], PICK_PARAMS["p_lta"],
                             PICK_PARAMS["p_thr"], PICK_PARAMS["window"])
  used = [nm for nm in names if picks.get(nm) is not None]
  p_first = min((picks[nm] for nm in used), default=None)

  # --- 拠点ごと: 最大振幅、P→水平動の最大振幅、卓越周波数 ---
  amps, p_to_peak, freqs = {}, [], []
  for nm in names:
    hor = [band_filtered(tr) for tr in st.select(station=nm) if tr.stats.channel[-1] in "NE"]
    amps[nm] = float(np.max(np.abs(u[nm].data))) * NM_TO_CM
    if picks.get(nm) is not None and hor:
      env_h = np.sqrt(sum(envelope(tr.data, df) ** 2 for tr in hor))
      off = hor[0].stats.starttime - ref_time
      j0 = int((picks[nm] - off) * df)
      # P波から60秒以内で水平動が最大になる時刻
      seg = env_h[j0:j0 + int(60 * df)]
      if len(seg):
        p_to_peak.append(np.argmax(seg) / df)
      f = dominant_frequency(u[nm], picks[nm] - (u[nm].stats.starttime - ref_time))
      if f is not None:
        freqs.append(f)

  res = dict(
    stations=len(names), picked=len(used),
    peak_time=ref_time + peak_t, duration=duration,
    p_time=ref_time + p_first if p_first is not None else None,
    p_to_peak=float(np.median(p_to_peak)) if p_to_peak else None,
    freq=float(np.median(freqs)) if freqs else None,
    amp_max=max(amps.values()), amp_ratio=max(amps.values()) / max(min(amps.values()), 1e-12),
    loud=max(amps, key=amps.get),
    azimuth=None, app_v=None, plane_rms=None, location="",
  )
  if len(used) >= 3:
    pts = np.array([proj.to_xy(*stations[nm][:2]) for nm in used])
    times = np.array([picks[nm] for nm in used])
    res["azimuth"], res["app_v"], res["plane_rms"] = fit_plane_wave(pts, times)
    if len(used) >= 4:
      loc = locate_grid(pts, times, VP)
      if loc["at_edge"]:
        res["location"] = "範囲外"
      else:
        lat, lon = proj.to_latlon(loc["x"], loc["y"])
        res["location"] = f"{lat:.3f},{lon:.3f} 深さ{loc['depth']:.1f}km"

  # 近い地震の候補か
  res["near"] = (res["duration"] <= NEAR_MAX_DURATION
                 and res["p_to_peak"] is not None and res["p_to_peak"] <= NEAR_MAX_P_TO_PEAK
                 and res["freq"] is not None and res["freq"] >= NEAR_MIN_FREQ)
  return res, dict(names=names, uf=uf, ref_time=ref_time, picks=picks,
                   peak_t=peak_t, span=(i0 / df, i1 / df))

# 有珠山の地震らしさ(震源リストに無い候補に使う)
#   有珠山周辺の地震は拠点のすぐ近くで起きるので、どの拠点にも P波がはっきり届き、S波もすぐ続き、
#   揺れも短い。次の条件をいくつ満たすかで「高」「中」「低」とする
#     - P波を USU_MIN_PICKS 拠点以上で検出できた(満たさなければ「低」)
#     - P→最大振幅が USU_MAX_P_TO_PEAK 秒以下(S-P時間が短い)
#     - 見かけの速度が USU_MIN_APP_V km/s 以上(これより遅いのは、拠点ごとの P波の検出がそろっていない)
#     - 揺れの長さが USU_MAX_DURATION 秒以下
#   すべて満たせば「高」、2つなら「中」、それ以外は「低」
#   (気象庁の震源リストで有珠山の地震と確かめられた 2026/2/2 02:45、3/4 22:58、5/20 23:51 と、
#    波形から有珠山の地震とみられる 2025/9/3 23:23、12/22 00:08 の5件は、P→最大振幅 1.6〜2.9秒、
#    見かけの速度 3.2〜13.6 km/s、揺れの長さ 14〜45秒で、すべて「高」になる。
#    震源リストに無いほかの10件は、P波を2拠点以下でしか検出できないか、P→最大振幅が5秒以上か、
#    見かけの速度が1.5 km/s 以下だった)
USU_MIN_PICKS = 3
USU_MAX_P_TO_PEAK = 3.5
USU_MIN_APP_V = 2.5
USU_MAX_DURATION = 60.0

# 有珠山の山頂(大有珠)の位置と、震源リストの地震を有珠山の地震とみなす震央の範囲 [km]・深さ [km]
USU_SUMMIT = (42.5442, 140.8392)
USU_RADIUS_KM = 10.0
USU_MAX_DEPTH_KM = 10.0
# 震源リストで有珠山の地震と確かめられたものに付ける有珠山の地震らしさ
USU_CONFIRMED = "確定(震源リスト)"

def usu_likeness(res):
  if res["picked"] < USU_MIN_PICKS or res["p_to_peak"] is None:
    return "低"
  met = sum([res["p_to_peak"] <= USU_MAX_P_TO_PEAK,
             res["app_v"] is not None and res["app_v"] >= USU_MIN_APP_V,
             res["duration"] <= USU_MAX_DURATION])
  return "高" if met == 3 else "中" if met == 2 else "低"

# 震源リストの地震が、有珠山の地震か(震央が山頂から USU_RADIUS_KM 以内で、USU_MAX_DEPTH_KM より浅い)
def is_usu_quake(q):
  from obspy.geodetics import gps2dist_azimuth
  dist_m, _, _ = gps2dist_azimuth(USU_SUMMIT[0], USU_SUMMIT[1], q["緯度"], q["経度"])
  return dist_m / 1000 <= USU_RADIUS_KM and q["深さ[km]"] <= USU_MAX_DEPTH_KM
