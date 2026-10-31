# screen_events.py
# 地震らしい波形が見られた期間の一覧から、近い地震の候補を絞り込む(単体実行用)
#   各期間について、4拠点の波形から次の値を計算し、一覧(CSV)と確認用の画像に出す
#     - 揺れの長さ      ... 包絡線が雑音の何倍かを超えている時間
#     - 卓越周波数      ... 揺れ始めの10秒間で最も強い周波数
#     - P→最大振幅      ... P波到着から水平動の振幅が最大になるまでの時間(S-P時間の目安)
#     - 振幅の比        ... 拠点ごとの最大振幅の、最大と最小の比
#     - 到来方向など    ... P波の時間差を平面波に当てはめた結果と、近い地震としての震源
#   近い地震は「短い・周波数が高い・P→最大振幅が短い」ので、それを満たすものに印を付ける
#
# 使い方: python3 scripts/screen_events.py <期間の一覧のテキストファイル>
#   一覧は1行に1期間。例: 2026/6/17 23:29-33、2026/6/23 12:58-13:01
#   SACファイルは output/<開始>_<終了> に作る(アプリの「指定期間の波形表示」と同じ場所)
import csv
import re
import sys
from datetime import datetime

import numpy as np

import app_paths
import win_pipeline
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import obspy

from hypocenter import load_stations, LocalProjection, pick_p_arrivals, fit_plane_wave, locate_grid
from plot_common import NM_TO_CM, setup_japanese_font
from log_common import set_console, log_message

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

# 出力先
CSV_FILE = app_paths.OUTPUT_DIR / "event_screening.csv"
IMAGE_DIR = app_paths.BATCH_PLOTS_DIR / "screening"

# 「2026/6/17 23:29-33」「2026/6/23 12:58-13:01」を (開始, 終了) の datetime にする
PERIOD_RE = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})\s*-\s*(?:(\d{1,2}):)?(\d{2})")

def parse_period(line):
  m = PERIOD_RE.search(line)
  if not m:
    return None
  y, mo, d, h1, mi1, h2, mi2 = m.groups()
  start = datetime(int(y), int(mo), int(d), int(h1), int(mi1))
  end = datetime(int(y), int(mo), int(d), int(h2 or h1), int(mi2))
  return start, end

# 期間の SAC ファイルを用意して読み込む(キャッシュがあればそれを使う)
def load_period(start, end):
  dest_dir = app_paths.OUTPUT_DIR / f"{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}"
  if not win_pipeline.find_sacs(dest_dir):
    if not win_pipeline.find_raw_wins(dest_dir):
      zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(start, end))
      if not zips:
        log_message(f"  ZIPファイルがありません: {start:%Y-%m-%d}")
        return None
      win_pipeline.extract_wins_from_zips(zips, start, end, dest_dir)
    win_pipeline.convert_wins_to_sacs(dest_dir, app_paths.TOOLS_DIR, app_paths.ETC_DIR)
  return obspy.read(str(dest_dir / win_pipeline.SAC_PATTERN))

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

# 確認用の画像: 4拠点の上下動(フィルター後)を、地震の中心の前後で並べる
def save_image(path, title, info):
  names = info["names"]
  fig, axes = plt.subplots(len(names), 1, figsize=(12, 2 * len(names)), sharex=True)
  axes = np.atleast_1d(axes)
  t0 = max(0.0, info["peak_t"] - 30)
  for ax, nm in zip(axes, names):
    tr = info["uf"][nm]
    t = tr.times() + (tr.stats.starttime - info["ref_time"])
    ax.plot(t, tr.data * NM_TO_CM, lw=0.5)
    ax.axvspan(*info["span"], color="gray", alpha=0.1)
    if info["picks"].get(nm) is not None:
      ax.axvline(info["picks"][nm], color="red", ls="--", lw=1)
    ax.text(0.01, 0.92, nm, transform=ax.transAxes, va="top", weight="bold")
    ax.set_ylabel("cm/s")
    ax.grid(True)
  axes[0].set_xlim(t0, info["peak_t"] + 60)
  axes[0].set_title(title, fontsize=10)
  axes[-1].set_xlabel(f"時刻 (s)  {info['ref_time'].strftime('%Y-%m-%d %H:%M:%S')} から"
                      f"  [{BAND[0]:g}-{BAND[1]:g} Hz]")
  fig.tight_layout()
  fig.savefig(path, dpi=80)
  plt.close(fig)

def fmt(v, spec):
  return "" if v is None else format(v, spec)

def main():
  if len(sys.argv) < 2:
    print("使い方: python3 scripts/screen_events.py <期間の一覧のテキストファイル>")
    sys.exit(1)
  set_console(True)
  setup_japanese_font()
  with open(sys.argv[1], encoding="utf-8") as f:
    periods = [p for p in (parse_period(line) for line in f) if p]
  stations = load_stations(app_paths.ETC_DIR)
  IMAGE_DIR.mkdir(parents=True, exist_ok=True)

  header = ["期間", "候補", "拠点数", "P検出", "揺れの中心", "揺れの長さ[秒]", "卓越周波数[Hz]",
            "P→最大振幅[秒]", "最大振幅[cm/s]", "最大の拠点", "振幅の比",
            "到来方向[度]", "見かけの速度[km/s]", "平面波の残差[秒]", "震源(近い地震として)"]
  rows = []
  for start, end in periods:
    label = f"{start:%Y-%m-%d %H:%M}-{end:%H:%M}"
    log_message(f"{label}: 解析中")
    try:
      st = load_period(start, end)
    except win_pipeline.PipelineError as e:
      log_message(f"  変換できませんでした: {e}")
      continue
    if st is None or len(st) == 0:
      continue
    names = sorted({tr.stats.station for tr in st})
    proj = LocalProjection([stations[nm] for nm in names if nm in stations])
    res, info = analyze(st, stations, proj)
    title = (f"{label}  揺れの長さ {res['duration']:.0f}秒  卓越 {fmt(res['freq'], '.1f')}Hz  "
             f"P→最大 {fmt(res['p_to_peak'], '.1f')}秒" + ("  ★近い地震の候補" if res["near"] else ""))
    save_image(IMAGE_DIR / f"{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}.png", title, info)
    rows.append([label, "★" if res["near"] else "", res["stations"], res["picked"],
                 res["peak_time"].strftime("%H:%M:%S"), f"{res['duration']:.1f}",
                 fmt(res["freq"], ".1f"), fmt(res["p_to_peak"], ".1f"),
                 f"{res['amp_max']:.5f}", res["loud"], f"{res['amp_ratio']:.1f}",
                 fmt(res["azimuth"], ".0f"), fmt(res["app_v"], ".1f"),
                 fmt(res["plane_rms"], ".3f"), res["location"]])
    log_message("  " + "  ".join(f"{h}={v}" for h, v in zip(header[1:], rows[-1][1:])))

  with open(CSV_FILE, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(header)
    writer.writerows(rows)
  log_message(f"一覧: {CSV_FILE}")
  log_message(f"画像: {IMAGE_DIR}")

if __name__ == "__main__":
  main()
