# fireworks_stack.py
# 洞爺湖の花火の最後の大きな1発を、日をまたいで重ね合わせ、拠点ごとの到着の遅れと発表用の図を作る
#   地震計の花火の揺れが、空気中の音波(約 340 m/s)で届いているかを確かめるためのもの
#
#   1. 日ごとの ZIP から 21:02〜21:07 の波形を取り出す(output/fireworks/final に保存し、次からはそれを使う)
#   2. 基準の拠点(Hokudai)の 20〜40 Hz の揺れが、21:03:30〜21:05:30 で一番大きい時刻を日ごとに求める
#      花火の期間で、その大きさが中央値の 30 倍以上で、時刻がそれらの日の中央値から 8 秒以内の日を使う
#      (21:04:48 ごろに終わるプログラムの日。21:03 ごろに終わるプログラムの日は含まれない)
#   3. その時刻をそろえて、拠点ごとの揺れの大きさ(0.1 秒ごとの RMS を中央値で割った値)の中央値をとる
#      花火の期間の外の日も同じように重ね、比べる
#   4. 遅れから、発生源の位置(湖面の高さ)と速さを求める。満点の4拠点だけで決め、
#      v-net の ZIP がある日は、ほかの機関の観測点の遅れを、その結果の確かめに使う
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/fireworks_stack.py [--start 2026-04-28] [--end 2026-10-31]
#       [--control 2026-04-20:2026-04-27,2026-11-01:2026-11-12] [--sample-days 2026-08-21,2026-08-22]
#       [--program-ranges 2025-07-08:2025-10-31,2026-04-28:2026-10-31]
#       [-o batch_plots/fireworks/slides] [--jobs 6] [--internal-names] [--no-map] [--manten-only]
#     --control        ... 比べる、花火のない日の範囲
#     --sample-days    ... 1日ごとの図と、v-net の確かめに使う日
#     --internal-names ... 図の拠点名を内部名のままにする(既定は etc/station_names.txt の発表用の名前)
#     --no-map         ... 地理院タイルを読まない(地図の図を作らない)
#     --program-ranges ... 07 の図に使う期間(花火の期間を年ごとに)
#     --manten-only    ... 満点地震計のデータだけを使う(v-net を読まない。05・06 は作らない。
#                          出力の既定は batch_plots/fireworks/slides_manten。地図の背景は地理院タイルのまま)
#
# 出力(-o のフォルダ。図はスライドの本文の幅に合わせた横長の PNG で、題名は入れない):
#   01_filter_effect.png ... 1日の波形の大きさ。フィルターなしと 20〜40 Hz の比較
#   02_stack.png         ... 満点の4拠点の重ね合わせ
#   03_travel_time.png   ... 発生源からの距離の差と、到着の遅れ
#   04_map.png           ... 拠点の配置と、推定した発生源
#   05_vnet_check.png    ... ほかの機関の観測点の1日ごとの揺れ
#   06_ussv_depth.png    ... 防災科研 USSV の地表付近と地中の地震計の比較(v-net に 01_05_03 の ZIP がある日)
#   07_program_calendar.png ... 日付と時刻で見た揺れの並び(月ごとの打ち上げプログラムの違い)
#   delays.csv / days.csv / ussv_depth.csv / program_days.csv / summary.json ... 数値
import argparse
import concurrent.futures
import csv
import json
import math
import shutil
import sys
import tempfile
import warnings
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.colors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker
import numpy as np
import obspy
from scipy import signal

import app_paths
import external_stations
import gsi_tiles
import plot_common
import station_names
import win_pipeline

REFERENCE = "Hokudai"
STATIONS = ["Hokudai", "Mimatsu", "Nizaemn", "Bamba"]
COMPONENTS = "UNE"
BANDS = [(20, 40), (10, 20), (5, 10)]
MAIN_BAND = (20, 40)
ENV_STEP = 0.1          # 揺れの大きさを求める間隔 [秒]
FINAL_WINDOW = ((21, 2), (21, 7))         # 取り出す時間(終わりの分も含む)
SEARCH_SECONDS = (90, 210)                # 21:02 からの秒。一番大きい揺れを探す範囲
MIN_RATIO = 30.0        # はっきり見えた日の基準(中央値に対する倍率)
MAX_TIME_OFFSET = 8.0   # 最大の揺れの時刻が、はっきり見えた日の中央値からこの秒数以内の日だけを重ねる
                        # (打ち上げのプログラムが違う日は、別の花火を最大として拾うため)
LAG_RANGE = (-20.0, 35.0)                 # 重ねる範囲 [秒]
BASELINE_END = -8.0     # これより前を、重ねた結果の背景とする [秒]
MIN_Z = 7.0             # 到着の山とみなす大きさ(背景のばらつきの倍数)
SHOW_WINDOW = ((20, 40), (21, 10))        # 1日の図の時間
FIREWORKS_TIME = ((20, 45), (21, 5))
LAKE_HEIGHT = 84.0      # 洞爺湖の水面の標高 [m]。発生源の高さに使う
SPEEDS = np.arange(300.0, 381.0, 1.0)     # 探す速さ [m/s]
GROUND_SPEED = 2000.0   # 図に比較として描く、地面の波の速さの目安 [m/s]
VNET_STATIONS = ["KBBY", "ATIK", "USSW", "USUA"]
USSV = "N.USSV"         # 防災科研 有珠山壮瞥。同じ場所に、地中の地震計と地表付近の広帯域地震計がある
USSV_SENSORS = {"U": "BU", "N": "BN", "E": "BE"}   # 地中の成分: 地表付近の成分
USSV_DEEP_HEIGHT, USSV_SHALLOW_HEIGHT = -100, 98   # チャンネル表の標高 [m]
USSV_WINDOW = (-1.0, 2.5)   # 比べる範囲(基準の拠点の最大からの秒)
PROGRAM_SHOW = (8.0, 25.5)      # 07 の図の範囲(20:40 からの分)
PROGRAM_HIT = 8.0               # 07 の図に点を打つ、ふだんの大きさとの比
PROGRAM_STRONG = 20.0           # 「強い揺れ」の基準(ふだんの大きさとの比)
PROGRAM_A_END = (24.45, 25.1)   # A の日の、最後の強い揺れの時刻の範囲(20:40 からの分)
PROGRAM_A_SHARE = 0.6           # 月の中でこの割合以上の日が A なら、A の月とする
MAP_BOUNDS = (42.522, 42.590, 140.765, 140.905)   # 南, 北, 西, 東

CACHE_ROOT = app_paths.OUTPUT_DIR / "fireworks"
INK = "#14213D"
ACCENT = "#C8501E"
BLUE = "#2F6F9F"
MUTED = "#8A8F99"
PAPER = "#FBFBF8"
FIG_SIZE = (16.64, 6.8)
DPI = 120


def parse_day(text):
  return datetime.strptime(text, "%Y-%m-%d").date()

def day_range(first, last):
  return [first + timedelta(days=i) for i in range((last - first).days + 1)]

def parse_ranges(text):
  days = []
  for part in text.split(","):
    first, last = part.split(":")
    days += day_range(parse_day(first), parse_day(last))
  return days

def at(day, hm):
  return datetime(day.year, day.month, day.day, hm[0], hm[1])

# 1日分の波形を取り出して npz に保存する(別のプロセスで動かす)。戻り値 ... 保存先(データが無ければ None)
def extract_day(day, window, folder):
  out = Path(folder) / f"{day:%Y%m%d}.npz"
  if out.exists():
    return out
  t0, t1 = at(day, window[0]), at(day, window[1])
  zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(t0, t1))
  if not zips:
    return None
  out.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(dir=str(out.parent)) as work:
    if win_pipeline.zips_to_sacs(zips, t0, t1, Path(work), app_paths.TOOLS_DIR, app_paths.ETC_DIR) == 0:
      return None
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      st = obspy.read(str(Path(work) / win_pipeline.SAC_PATTERN))
    st.merge(fill_value=0)
    data = {}
    for tr in st:
      key = f"{tr.stats.station}.{tr.stats.channel[-1]}"
      data[key] = tr.data.astype("f4")
      data[key + "_t0"] = np.array(tr.stats.starttime.timestamp)
      data[key + "_fs"] = np.array(tr.stats.sampling_rate)
    np.savez(out, **data)
  return out

def extract_days(days, window, folder, jobs):
  with concurrent.futures.ProcessPoolExecutor(jobs) as pool:
    paths = list(pool.map(extract_day, days, [window] * len(days), [str(folder)] * len(days)))
  return {d: p for d, p in zip(days, paths) if p is not None}

# 帯域を通した波形の、ENV_STEP 秒ごとの RMS
def envelope(x, fs, band, step=ENV_STEP):
  x = np.asarray(x, dtype=float)
  x = x - x.mean()
  if band is not None:
    x = signal.sosfiltfilt(signal.butter(4, band, "bandpass", fs=fs, output="sos"), x)
  width = int(round(step * fs))
  n = len(x) // width
  return np.sqrt((x[:n * width].reshape(n, width) ** 2).mean(axis=1))

def lag_axis():
  return np.arange(int(LAG_RANGE[0] / ENV_STEP), int(LAG_RANGE[1] / ENV_STEP)) * ENV_STEP

# 基準の拠点で一番大きい揺れの位置。戻り値 ... (ENV_STEP ごとの番号, 中央値に対する倍率)
def final_peak(z):
  key = f"{REFERENCE}.U"
  if key not in z.files or len(z[key]) < float(z[key + "_fs"]) * 300:
    return None
  e = envelope(z[key], float(z[key + "_fs"]), MAIN_BAND)
  med = np.median(e)
  if med <= 0:
    return None
  a, b = (int(s / ENV_STEP) for s in SEARCH_SECONDS)
  k = a + int(np.argmax(e[a:b]))
  return k, float(e[k] / med)

# 日ごとの揺れを、基準の拠点の最大にそろえて集める
#   戻り値 ... stacks {(群, 拠点, 成分, 帯域): 日数×時間の配列}, rows(日ごとの表)
def collect(files, season):
  i0, i1 = (int(round(v / ENV_STEP)) for v in LAG_RANGE)
  stacks, rows = {}, []
  peaks = {}
  for day, path in files.items():
    with np.load(path) as z:
      peaks[day] = final_peak(z)
  clear = [p[0] for d, p in peaks.items() if p and season[0] <= d <= season[1] and p[1] >= MIN_RATIO]
  center = float(np.median(clear))
  for day, path in sorted(files.items()):
    with np.load(path) as z:
      peak = peaks[day]
      if peak is None:
        continue
      k, ratio = peak
      in_season = season[0] <= day <= season[1]
      same_shot = abs(k - center) * ENV_STEP <= MAX_TIME_OFFSET
      group = "fireworks" if in_season and ratio >= MIN_RATIO and same_shot else "control" if not in_season else None
      when = at(day, FINAL_WINDOW[0]) + timedelta(seconds=(k + 0.5) * ENV_STEP)
      rows.append({"日": day.isoformat(), "花火の期間": int(in_season), "最大の時刻": f"{when:%H:%M:%S.%f}"[:10],
                   "中央値に対する倍率": round(ratio, 1), "重ねた": int(group == "fireworks")})
      if group is None or k + i0 < 0:
        continue
      for s in STATIONS:
        for c in COMPONENTS:
          key = f"{s}.{c}"
          if key not in z.files:
            continue
          fs = float(z[key + "_fs"])
          for band in BANDS:
            e = envelope(z[key], fs, band)
            med = np.median(e)
            if med > 0 and len(e) >= k + i1:
              stacks.setdefault((group, s, c, band), []).append(e[k + i0:k + i1] / med)
  return {k: np.array(v) for k, v in stacks.items()}, rows

# 重ねた揺れから到着の遅れを読む(帯域・成分ごとの山の時刻の中央値)
#   center, half ... 探す範囲(None なら LAG の 1〜20 秒)
def read_delay(curves, lags, center=None, half=2.0):
  picks = []
  for curve in curves:
    base = curve[lags < BASELINE_END]
    z = (curve - base.mean()) / base.std()
    mask = (lags > 1) & (lags < 20) if center is None else np.abs(lags - center) <= half
    idx = np.where(mask)[0]
    j = idx[int(np.argmax(z[idx]))]
    if z[j] >= MIN_Z:
      picks.append(lags[j])
  if not picks:
    return None
  return float(np.median(picks)), float(np.std(picks)), len(picks)

def manten_coords():
  coords = {}
  for line in (app_paths.ETC_DIR / "manten_ch.tbl").read_text(encoding="utf-8").splitlines():
    cols = line.split()
    if len(cols) >= 16 and not cols[0].startswith("#") and cols[4] == "U":
      coords[cols[3]] = (float(cols[13]), float(cols[14]), float(cols[15]))
  return coords

# 緯度経度を、基準の拠点を原点にした東・北の距離 [m] にする
class Local:
  def __init__(self, lat, lon):
    self.lat, self.lon = lat, lon
    self.kx = 111320.0 * math.cos(math.radians(lat))
    self.ky = 111132.0

  def xy(self, lat, lon):
    return (lon - self.lon) * self.kx, (lat - self.lat) * self.ky

  def latlon(self, x, y):
    return self.lat + y / self.ky, self.lon + x / self.kx

# 発生源の位置と速さを、基準の拠点からの遅れに合わせて求める(湖面の高さの範囲を格子で調べる)
def fit_source(delays, coords, local):
  x = np.arange(-7000.0, 5001.0, 25.0)
  y = np.arange(-1000.0, 7001.0, 25.0)
  gx, gy = np.meshgrid(x, y)
  def dist(c):
    sx, sy = local.xy(c[0], c[1])
    return np.sqrt((gx - sx) ** 2 + (gy - sy) ** 2 + (LAKE_HEIGHT - c[2]) ** 2)
  d0 = dist(coords[REFERENCE])
  diffs = {s: dist(coords[s]) - d0 for s in delays}
  best = None
  for c in SPEEDS:
    err = sum((diffs[s] / c - delays[s]) ** 2 for s in delays)
    i = np.unravel_index(np.argmin(err), err.shape)
    rms = math.sqrt(err[i] / len(delays))
    if best is None or rms < best["rms"]:
      best = {"speed": float(c), "x": float(gx[i]), "y": float(gy[i]), "rms": rms}
  best["lat"], best["lon"] = local.latlon(best["x"], best["y"])
  return best

def source_distance(source, c, local):
  sx, sy = local.xy(c[0], c[1])
  return math.sqrt((source["x"] - sx) ** 2 + (source["y"] - sy) ** 2 + (LAKE_HEIGHT - c[2]) ** 2)

# ほかの機関の観測点の揺れを、その日の基準の拠点の最大にそろえて読む
def vnet_curves(sample_files):
  lags = lag_axis()
  curves = {}
  peaks = {}
  for day, path in sample_files.items():
    with np.load(path) as z:
      peak = final_peak(z)
    if peak is None:
      continue
    k, _ = peak
    when = at(day, FINAL_WINDOW[0]) + timedelta(seconds=k * ENV_STEP)
    peaks[day] = when
    t0, t1 = when - timedelta(minutes=5), when + timedelta(minutes=5)
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      st = external_stations.load(t0, t1, VNET_STATIONS)
    for tr in st:
      fs = tr.stats.sampling_rate
      for band in BANDS:
        if band[1] >= fs / 2:
          continue
        e = envelope(tr.data, fs, band)
        med = np.median(e)
        start = int(round((when - tr.stats.starttime.datetime).total_seconds() / ENV_STEP)) + int(round(LAG_RANGE[0] / ENV_STEP))
        if med > 0 and start >= 0 and start + len(lags) <= len(e):
          curves.setdefault((tr.stats.station, tr.stats.channel, band), {})[day] = e[start:start + len(lags)] / med
  return curves, peaks

# USSV の地中・地表付近の地震計の SAC を作る。戻り値 ... SAC のフォルダ(その時刻の ZIP が無ければ None)
def convert_ussv(when):
  for zip_path, start, end in external_stations.data_files():
    if zip_path.name.startswith("01_05_03_") and start <= when < end:
      break
  else:
    return None
  out = CACHE_ROOT / "ussv" / zip_path.stem
  done = out / "converted.txt"
  if done.exists():
    return out
  work = out / "work"
  shutil.rmtree(work, ignore_errors=True)
  work.mkdir(parents=True)
  try:
    with zipfile.ZipFile(zip_path) as z:
      names = z.namelist()
      external_stations.join_win32([z.read(n) for n in sorted(n for n in names if n.endswith(".cnt"))], work / "all.cnt")
      text = z.read(next(n for n in names if n.endswith(".euc.ch"))).decode("euc_jp", errors="replace")
    (work / "channels.tbl").write_text(text, encoding="euc_jp", errors="replace")
    (work / "win.prm").write_text(".\nchannels.tbl\n.\n.\n")
    wanted = set(USSV_SENSORS) | set(USSV_SENSORS.values())
    channels = {c[4]: c[0].upper() for c in (ln.split() for ln in text.splitlines())
                if len(c) > 8 and c[3] == USSV and c[4] in wanted}
    (work / "ussv.ch").write_text(",".join(channels.values()))
    win2sac = win_pipeline.tool_path(app_paths.TOOLS_DIR, "win2sac_32")
    win_pipeline.run_win2sac_tool(win2sac, work / "all.cnt", work / "ussv.ch", "out", work / "win.prm")
    for comp in channels:
      src = work / f"{USSV}.{comp}.out"
      if src.exists():
        src.replace(out / f"{comp}.sac")
    done.write_text("ok\n")
  finally:
    shutil.rmtree(work, ignore_errors=True)
  return out

# 帯域を通した波形の、なめらかな包絡線(元のサンプリングのまま)
def smooth_envelope(x, fs, band, width=0.05):
  y = signal.sosfiltfilt(signal.butter(4, band, "bandpass", fs=fs, output="sos"), x - np.mean(x))
  n = max(1, int(round(width * fs)))
  return np.convolve(np.abs(signal.hilbert(y)), np.ones(n) / n, "same")

# 地中の地震計の遅れと振幅を、地表付近の地震計と比べる
#   戻り値 ... rows(日・成分ごとの表), curves {日: (時間, 地表付近, 地中)}(3成分の平均。最大を1にそろえた値)
def ussv_depth(sample_files):
  rows, curves = [], {}
  for day, path in sample_files.items():
    with np.load(path) as z:
      peak = final_peak(z)
    if peak is None:
      continue
    when = at(day, FINAL_WINDOW[0]) + timedelta(seconds=peak[0] * ENV_STEP)
    folder = convert_ussv(when)
    if folder is None:
      continue
    shallow_all, deep_all = [], []
    for deep_c, shallow_c in USSV_SENSORS.items():
      if not (folder / f"{deep_c}.sac").exists() or not (folder / f"{shallow_c}.sac").exists():
        continue
      with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        deep, shallow = obspy.read(str(folder / f"{deep_c}.sac"))[0], obspy.read(str(folder / f"{shallow_c}.sac"))[0]
      fs = deep.stats.sampling_rate
      e_deep, e_shallow = smooth_envelope(deep.data.astype(float), fs, MAIN_BAND), smooth_envelope(shallow.data.astype(float), fs, MAIN_BAND)
      i = int(round((when - deep.stats.starttime.datetime).total_seconds() * fs))
      a, b = i + int(USSV_WINDOW[0] * fs), i + int(USSV_WINDOW[1] * fs)
      d, s = e_deep[a:b], e_shallow[a:b]
      cc = signal.correlate(d - d.mean(), s - s.mean(), "full")
      lags = signal.correlation_lags(len(d), len(s), "full")
      use = np.abs(lags) <= fs
      j = np.where(use)[0][int(np.argmax(cc[use]))]
      corr = cc[j] / math.sqrt(((d - d.mean()) ** 2).sum() * ((s - s.mean()) ** 2).sum())
      quiet = int(600 * fs)
      event_ratio = s.max() / d.max()
      noise_ratio = np.median(e_shallow[:quiet]) / np.median(e_deep[:quiet])
      rows.append({"日": day.isoformat(), "成分": deep_c, "地中の遅れ_秒": round(lags[j] / fs, 2), "包絡線の相関": round(float(corr), 2),
                   "地表付近と地中の振幅比_花火": round(float(event_ratio), 1), "地表付近と地中の振幅比_背景": round(float(noise_ratio), 1),
                   "背景に対する倍率": round(float(event_ratio / noise_ratio), 1)})
      shallow_all.append(s / s.max())
      deep_all.append(d / d.max())
    if shallow_all:
      t = USSV_WINDOW[0] + np.arange(len(shallow_all[0])) / fs
      curves[day] = (t, np.mean(shallow_all, axis=0), np.mean(deep_all, axis=0))
  return rows, curves

def plot_ussv(rows, curves, out):
  days = sorted(curves)
  fig, axes = new_figure(len(days), 1, sharex=True, sharey=True)
  axes = np.atleast_1d(axes)
  for ax, day in zip(axes, days):
    t, shallow, deep = curves[day]
    ax.plot(t, shallow, color=ACCENT, lw=2.2, label=f"地表付近（標高 {USSV_SHALLOW_HEIGHT} m）")
    ax.plot(t, deep, color=BLUE, lw=2.2, label=f"地中（標高 {USSV_DEEP_HEIGHT} m）")
    lag = float(np.median([r["地中の遅れ_秒"] for r in rows if r["日"] == day.isoformat()]))
    ax.text(0.008, 0.78, f"{day.month}月{day.day}日", transform=ax.transAxes, fontsize=20, color=INK, weight="bold")
    ax.text(0.995, 0.5, f"地中が {lag:.2f} 秒遅い", transform=ax.transAxes, ha="right", fontsize=20, color=INK)
    ax.set_ylabel("最大を1とした大きさ", fontsize=15)
  axes[0].legend(loc="upper right", frameon=False, fontsize=16, labelcolor=INK, ncol=2)
  axes[-1].set_xlim(*USSV_WINDOW)
  axes[-1].set_ylim(0, 1.25)
  axes[-1].set_xlabel(f"{station_names.full(REFERENCE)} の最大の揺れからの時間 [秒]（USSV、20〜40 Hz、3成分の平均）", fontsize=16)
  fig.tight_layout(h_pad=0.6)
  save(fig, out)

# 花火の時間の基準の拠点の揺れの大きさ(3成分。ENV_STEP ごと)を保存する(別のプロセスで動かす)
def extract_program_day(day, folder):
  out = Path(folder) / f"{day:%Y%m%d}.npz"
  if out.exists():
    return out
  t0, t1 = at(day, SHOW_WINDOW[0]), at(day, SHOW_WINDOW[1])
  zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(t0, t1))
  if not zips:
    return None
  out.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(dir=str(out.parent)) as work:
    if win_pipeline.zips_to_sacs(zips, t0, t1, Path(work), app_paths.TOOLS_DIR, app_paths.ETC_DIR) == 0:
      return None
    with warnings.catch_warnings():
      warnings.simplefilter("ignore")
      st = obspy.read(str(Path(work) / win_pipeline.SAC_PATTERN))
    st.merge(fill_value=0)
    data = {tr.stats.channel[-1]: envelope(tr.data, tr.stats.sampling_rate, MAIN_BAND).astype("f4")
            for tr in st if tr.stats.station == REFERENCE}
    if "U" not in data:
      return None
    np.savez(out, **data)
  return out

def clock_text(minutes):
  if not np.isfinite(minutes):
    return ""
  total = 20 * 3600 + 40 * 60 + minutes * 60
  return f"{int(total // 3600)}:{int(total % 3600 // 60):02d}:{total % 60:04.1f}"

# 日ごとの揺れの並びを読み、A の日・A の月を判定する
def program_days(ranges, jobs):
  days = [d for first, last in ranges for d in day_range(first, last)]
  folder = CACHE_ROOT / "program"
  with concurrent.futures.ProcessPoolExecutor(jobs) as pool:
    paths = list(pool.map(extract_program_day, days, [str(folder)] * len(days)))
  size = int(round(31 * 60 / ENV_STEP))
  result = {}
  for day, path in zip(days, paths):
    if path is None:
      continue
    with np.load(path) as z:
      parts = [z[c] / np.median(z[c]) for c in COMPONENTS if c in z.files and np.median(z[c]) > 0]
    if len(parts) == 0 or len(parts[0]) < size * 0.8:
      continue
    ratio = np.full(size, np.nan)
    mean = np.mean(parts, axis=0)[:size]
    ratio[:len(mean)] = mean
    a, b = (int(v * 60 / ENV_STEP) for v in (4.0, 25.5))
    strong = np.where(ratio[a:b] >= PROGRAM_STRONG)[0]
    last = (a + strong[-1]) * ENV_STEP / 60 if len(strong) else np.nan
    is_a = PROGRAM_A_END[0] <= last <= PROGRAM_A_END[1]
    result[day] = {"ratio": ratio, "last": last, "A": bool(is_a), "max": float(np.nanmax(ratio[a:b]))}
  return result

def program_months(result):
  months = {}
  for day, r in result.items():
    months.setdefault((day.year, day.month), []).append(r["A"])
  return {m: np.mean(v) >= PROGRAM_A_SHARE and len(v) >= 5 for m, v in months.items()}

def plot_program(result, ranges, out):
  months = program_months(result)
  spans = []
  for first, last in ranges:
    have = [d for d in result if first <= d <= last]
    if have:
      spans.append((min(have), max(have)))
  fig, axes = plt.subplots(1, len(spans), figsize=FIG_SIZE, facecolor=PAPER, sharey=True, squeeze=False,
                           gridspec_kw={"width_ratios": [(b - a).days + 1 for a, b in spans]})
  step = int(round(1 / ENV_STEP))   # 1 秒ごと
  y0, y1 = (int(v * 60 / ENV_STEP) for v in PROGRAM_SHOW)
  for ax, (first, last) in zip(axes[0], spans):
    style(ax)
    ax.grid(False)
    n_days = (last - first).days + 1
    image = np.zeros(((y1 - y0) // step, n_days, 4))
    image[..., :3] = matplotlib.colors.to_rgb(INK)
    for day, r in result.items():
      if first <= day <= last:
        part = r["ratio"][y0:y1]
        n = len(part) // step
        hit = np.nanmax(part[:n * step].reshape(n, step), axis=1) >= PROGRAM_HIT
        image[:n, (day - first).days, 3] = hit
    ax.set_xlim(-0.5, n_days - 0.5)
    ax.set_ylim(PROGRAM_SHOW[1], PROGRAM_SHOW[0])
    ticks, labels = [], []
    day = first.replace(day=1)
    while day <= last:
      next_month = (day + timedelta(days=32)).replace(day=1)
      start, end = max((day - first).days, 0), min((next_month - first).days, n_days)
      if months.get((day.year, day.month)):
        ax.axvspan(start - 0.5, end - 0.5, color="#F3E7D8", zorder=0)
        ax.text((start + end - 1) / 2, PROGRAM_SHOW[0] + 0.15, "A", ha="center", va="top", fontsize=20, color=ACCENT, weight="bold", zorder=3)
      if end - start >= 7:
        ticks.append((start + end - 1) / 2)
        labels.append(f"{day.month}月")
      if day > first:
        ax.axvline(start - 0.5, color=MUTED, lw=0.8, zorder=1)
      day = next_month
    ax.imshow(image, extent=(-0.5, n_days - 0.5, PROGRAM_SHOW[1], PROGRAM_SHOW[0]), aspect="auto", interpolation="nearest", zorder=2)
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=16)
    ax.tick_params(axis="x", length=0)
    ax.set_title(f"{first.year}年", fontsize=20, color=INK, weight="bold")
  ticks = [10, 15, 20, 25]
  axes[0][0].set_yticks(ticks)
  axes[0][0].set_yticklabels([clock_text(v)[:5] for v in ticks], fontsize=16)
  fig.tight_layout()
  save(fig, out)

def style(ax):
  ax.set_facecolor(PAPER)
  for side in ("top", "right"):
    ax.spines[side].set_visible(False)
  for side in ("left", "bottom"):
    ax.spines[side].set_color(MUTED)
  ax.tick_params(colors=INK, labelsize=16)
  ax.yaxis.label.set_color(INK)
  ax.xaxis.label.set_color(INK)
  ax.grid(color="#E4E3DC", lw=0.8)
  ax.set_axisbelow(True)

def new_figure(*args, **kwargs):
  fig, axes = plt.subplots(*args, figsize=FIG_SIZE, facecolor=PAPER, **kwargs)
  for ax in np.atleast_1d(axes).ravel():
    style(ax)
  return fig, axes

def save(fig, path):
  fig.savefig(path, dpi=DPI, facecolor=PAPER)
  plt.close(fig)
  print(path)

# 1日の揺れの大きさ: フィルターなしと 20〜40 Hz
def plot_filter_effect(day, path, out, label):
  with np.load(path) as z:
    key = f"{REFERENCE}.U"
    x, fs = z[key], float(z[key + "_fs"])
  t0 = at(day, SHOW_WINDOW[0])
  fig, axes = new_figure(2, 1, sharex=True, sharey=True)
  quiet = int(((at(day, FIREWORKS_TIME[0]) - t0).total_seconds()) / 0.25)
  for ax, band, color, text in ((axes[0], None, MUTED, "フィルターなし"), (axes[1], MAIN_BAND, ACCENT, "20〜40 Hz")):
    e = envelope(x, fs, band, step=0.25)
    times = [t0 + timedelta(seconds=(i + 0.5) * 0.25) for i in range(len(e))]
    ax.plot(times, e / np.median(e[:quiet]), color=color, lw=0.8)
    ax.set_yscale("log")
    ax.axvspan(at(day, FIREWORKS_TIME[0]), at(day, FIREWORKS_TIME[1]), color="#F3E7D8", zorder=0)
    ax.text(0.012, 0.86, text, transform=ax.transAxes, fontsize=20, color=INK, weight="bold")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_ylabel("花火の前との比", fontsize=16)
  axes[1].set_ylim(0.3, 300)
  axes[1].set_xlim(t0, at(day, SHOW_WINDOW[1]))
  axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
  axes[1].set_xlabel(f"{day.month}月{day.day}日の時刻（{label}、上下動、色の帯が花火の時間）", fontsize=16)
  fig.tight_layout()
  save(fig, out)

def plot_stack(stacks, lags, delays, out):
  fig, axes = new_figure(len(STATIONS), 1, sharex=True)
  for ax, s in zip(axes, STATIONS):
    fw = stacks.get(("fireworks", s, "U", MAIN_BAND))
    ctl = stacks.get(("control", s, "U", MAIN_BAND))
    if ctl is not None:
      ax.plot(lags, np.median(ctl, axis=0), color=MUTED, lw=0.8)
    ax.plot(lags, np.median(fw, axis=0), color=ACCENT, lw=1.5)
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.text(0.008, 0.72, station_names.full(s), transform=ax.transAxes, fontsize=19, color=INK, weight="bold")
    if s in delays:
      ax.axvline(delays[s], color=INK, lw=1, ls=(0, (4, 3)))
      ax.text(delays[s] + 0.4, 0.72, f"+{delays[s]:.1f} 秒", transform=ax.get_xaxis_transform(), fontsize=19, color=INK)
  axes[0].text(0.995, 0.74, f"オレンジ: 花火の日（{len(stacks[('fireworks', REFERENCE, 'U', MAIN_BAND)])}日）", transform=axes[0].transAxes,
               ha="right", fontsize=15, color=ACCENT)
  axes[0].text(0.995, 0.5, f"灰色: 花火のない日（{len(stacks[('control', REFERENCE, 'U', MAIN_BAND)])}日）", transform=axes[0].transAxes,
               ha="right", fontsize=15, color="#5F6570")
  axes[-1].set_xlim(-10, 25)
  axes[-1].set_xlabel(f"{station_names.full(REFERENCE)} の最大の揺れからの時間 [秒]（20〜40 Hz、上下動。縦軸はふだんの大きさとの比）", fontsize=16)
  fig.tight_layout(h_pad=0.4)
  save(fig, out)

def plot_travel_time(table, source, out):
  fig, ax = new_figure()
  xs = np.array([0, max(r["距離の差_m"] for r in table) * 1.08]) / 1000
  ax.plot(xs, xs * 1000 / source["speed"], color=ACCENT, lw=2)
  ax.plot(xs, xs * 1000 / GROUND_SPEED, color=MUTED, lw=1.5, ls=(0, (5, 4)))
  ax.text(3.0, 3000 / source["speed"] - 1.0, f"{source['speed']:.0f} m/s", color=ACCENT, fontsize=21, weight="bold", ha="left", va="top")
  ax.text(xs[1], xs[1] * 1000 / GROUND_SPEED + 0.35, f"地面の波の目安 {GROUND_SPEED / 1000:.0f} km/s", color="#5F6570", fontsize=16, ha="right")
  offsets = {"KBBY": (-12, 10, "right"), "ATIK": (12, -20, "left"), REFERENCE: (14, 10, "left")}
  for r in table:
    used = r["使い方"] == "位置の推定"
    ax.plot(r["距離の差_m"] / 1000, r["遅れ_秒"], "o", ms=13, mfc=INK if used else PAPER, mec=INK, mew=2.2, zorder=4)
    dx, dy, ha = offsets.get(r["拠点"], (-12, 8, "right"))
    ax.annotate(r["表示名"], (r["距離の差_m"] / 1000, r["遅れ_秒"]), xytext=(dx, dy), textcoords="offset points", ha=ha, fontsize=18, color=INK)
  ax.plot([], [], "o", ms=12, color=INK, label="満点地震計（発生源の推定に使用）")
  if any(r["使い方"] == "確かめ" for r in table):
    ax.plot([], [], "o", ms=12, mfc=PAPER, mec=INK, mew=2.2, label="気象庁の観測点（確かめに使用、2日分）")
  ax.legend(loc="upper left", frameon=False, fontsize=16, labelcolor=INK)
  ax.set_xlim(0, xs[1])
  ax.set_ylim(0, None)
  ax.set_xlabel(f"発生源からの距離の、{station_names.full(REFERENCE)} との差 [km]", fontsize=17)
  ax.set_ylabel(f"{station_names.full(REFERENCE)} からの遅れ [秒]", fontsize=17)
  fig.tight_layout()
  save(fig, out)

def plot_map(table, source, coords, out):
  south, north, west, east = MAP_BOUNDS
  fig, ax = new_figure()
  for img, extent in gsi_tiles.tiles_for_bounds("std", south, north, west, east):
    ax.imshow(img, extent=extent, zorder=0, interpolation="bilinear", alpha=0.75)
  ax.grid(False)
  ax.set_xlim(west, east)
  ax.set_ylim(south, north)
  ax.set_aspect(1 / math.cos(math.radians((south + north) / 2)))
  ax.set_xticks([])
  ax.set_yticks([])
  box = dict(facecolor=PAPER, edgecolor="none", alpha=0.86, pad=2.5)
  ax.plot(source["lon"], source["lat"], "*", ms=30, color=ACCENT, mec=PAPER, mew=1.5, zorder=5)
  ax.annotate("推定した発生源", (source["lon"], source["lat"]), xytext=(14, 12), textcoords="offset points", fontsize=17, color=ACCENT, weight="bold", bbox=box)
  for r in table:
    c = coords[r["拠点"]]
    used = r["使い方"] == "位置の推定"
    ax.plot(c[1], c[0], "^", ms=15, mfc=INK if used else PAPER, mec=INK, mew=2, zorder=5)
    amount = f"+{r['遅れ_秒']:.1f}" if not r["遅れの範囲_秒"] else "+" + r["遅れの範囲_秒"].replace("〜", "〜+")
    text = r["表示名"] if r["遅れ_秒"] == 0 else f"{r['表示名']}  {amount} 秒"
    right_side = c[1] > east - 0.03   # 東の端の拠点は、名前を左に書く
    ax.annotate(text, (c[1], c[0]), xytext=(-11 if right_side else 11, -7), textcoords="offset points",
                ha="right" if right_side else "left", fontsize=16, color=INK, bbox=box)
  ax.text(0.995, 0.012, gsi_tiles.ATTRIBUTION, transform=ax.transAxes, ha="right", fontsize=11, color=INK, bbox=box)
  fig.tight_layout()
  fig.savefig(out, dpi=DPI, facecolor=PAPER, bbox_inches="tight", pad_inches=0.02)
  plt.close(fig)
  print(out)

def plot_vnet(curves, sample_files, table, lags, out):
  shown = [r["拠点"] for r in table if r["使い方"] == "確かめ"]
  if not shown:
    return
  days = sorted(sample_files)
  colors = dict(zip(days, (ACCENT, BLUE)))
  fig, axes = new_figure(len(shown) + 1, 1, sharex=True)
  i0, i1 = (int(round(v / ENV_STEP)) for v in LAG_RANGE)
  for day, path in sample_files.items():
    with np.load(path) as z:
      k, _ = final_peak(z)
      e = envelope(z[f"{REFERENCE}.U"], float(z[f"{REFERENCE}.U_fs"]), MAIN_BAND)
    axes[0].plot(lags, e[k + i0:k + i1] / np.median(e), color=colors[day], lw=1.2, label=f"{day.month}月{day.day}日")
  names = [REFERENCE] + shown
  for ax, s in zip(axes[1:], shown):
    for day, curve in curves.get((s, "U", MAIN_BAND), {}).items():
      ax.plot(lags, curve, color=colors[day], lw=1.2)
    row = next(r for r in table if r["拠点"] == s)
    ax.axvline(row["予測_秒"], color=INK, lw=1, ls=(0, (4, 3)))
    ax.text(0.995, 0.72, f"実測 +{row['遅れの範囲_秒'].replace('〜', '〜+')} 秒 / 予測 +{row['予測_秒']:.1f} 秒", transform=ax.transAxes, ha="right", fontsize=18, color=INK)
  for ax, s in zip(axes, names):
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.text(0.008, 0.72, station_names.full(s), transform=ax.transAxes, fontsize=19, color=INK, weight="bold")
  axes[0].legend(loc="upper right", frameon=False, fontsize=16, labelcolor=INK, ncol=2)
  axes[-1].set_xlim(-5, 12)
  axes[-1].set_xlabel(f"{station_names.full(REFERENCE)} の最大の揺れからの時間 [秒]（20〜40 Hz、上下動。縦軸はふだんの大きさとの比）", fontsize=16)
  fig.tight_layout(h_pad=0.4)
  save(fig, out)

def main():
  parser = argparse.ArgumentParser(description="洞爺湖の花火の揺れを重ね合わせ、到着の遅れと発表用の図を作る")
  parser.add_argument("--start", type=parse_day, default=date(2026, 4, 28))
  parser.add_argument("--end", type=parse_day, default=date(2026, 10, 31))
  parser.add_argument("--control", default="2026-04-20:2026-04-27,2026-11-01:2026-11-12")
  parser.add_argument("--sample-days", default="2026-08-21,2026-08-22")
  parser.add_argument("--program-ranges", default="2025-07-08:2025-10-31,2026-04-28:2026-10-31")
  parser.add_argument("-o", "--output", type=Path, default=None)
  parser.add_argument("--jobs", type=int, default=6)
  parser.add_argument("--internal-names", action="store_true")
  parser.add_argument("--no-map", action="store_true")
  parser.add_argument("--manten-only", action="store_true")
  args = parser.parse_args()
  if args.output is None:
    args.output = app_paths.DATA_DIR / "batch_plots" / "fireworks" / ("slides_manten" if args.manten_only else "slides")

  plot_common.setup_japanese_font()
  station_names.reload(enabled=not args.internal_names)
  args.output.mkdir(parents=True, exist_ok=True)
  season = (args.start, args.end)
  days = day_range(*season) + parse_ranges(args.control)
  samples = [parse_day(s) for s in args.sample_days.split(",") if s]

  files = extract_days(days, FINAL_WINDOW, CACHE_ROOT / "final", args.jobs)
  print(f"波形のある日: {len(files)} / {len(days)}")
  stacks, rows = collect(files, season)
  lags = lag_axis()

  delays = {}
  for s in STATIONS[1:]:
    curves = [np.median(stacks[("fireworks", s, c, b)], axis=0) for c in COMPONENTS for b in BANDS if ("fireworks", s, c, b) in stacks]
    found = read_delay(curves, lags)
    if found:
      delays[s] = found[0]
  coords = manten_coords()
  local = Local(*coords[REFERENCE][:2])
  source = fit_source(delays, coords, local)
  ref_dist = source_distance(source, coords[REFERENCE], local)

  table = [{"拠点": REFERENCE, "表示名": station_names.full(REFERENCE), "使い方": "位置の推定", "遅れ_秒": 0.0,
            "距離の差_m": 0.0, "予測_秒": 0.0, "読めた数": "", "遅れの範囲_秒": ""}]
  for s, d in delays.items():
    diff = source_distance(source, coords[s], local) - ref_dist
    table.append({"拠点": s, "表示名": station_names.full(s), "使い方": "位置の推定", "遅れ_秒": d,
                  "距離の差_m": diff, "予測_秒": diff / source["speed"], "読めた数": "", "遅れの範囲_秒": ""})

  sample_files = {d: files[d] for d in samples if d in files}
  curves = {}
  external = {}
  if not args.manten_only:
    curves, _ = vnet_curves(sample_files)
    external = external_stations.load_config()
  for s in VNET_STATIONS:
    if s not in external:
      continue
    coords[s] = tuple(external[s]["coords"])
    diff = source_distance(source, coords[s], local) - ref_dist
    expected = diff / source["speed"]
    # 日ごとに遅れを読む(帯域・成分の半分以上で山が読めた日だけを使う)
    per_day = []
    for day in sample_files:
      day_curves = [curves[(s, c, b)][day] for c in COMPONENTS for b in BANDS if day in curves.get((s, c, b), {})]
      found = read_delay(day_curves, lags, center=expected) if day_curves else None
      if found and found[2] >= max(3, len(day_curves) // 2):
        per_day.append(found[0])
    if per_day:
      table.append({"拠点": s, "表示名": s, "使い方": "確かめ", "遅れ_秒": float(np.mean(per_day)),
                    "距離の差_m": diff, "予測_秒": expected, "読めた数": f"{len(per_day)}日",
                    "遅れの範囲_秒": f"{min(per_day):.1f}〜{max(per_day):.1f}"})
    else:
      print(f"{s}: 予測の時刻に、はっきりした山がない")

  with open(args.output / "delays.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(table[0]))
    w.writeheader()
    w.writerows(table)
  with open(args.output / "days.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)
  in_season = [r for r in rows if r["花火の期間"]]
  summary = {"花火の期間の日数": len(in_season), "重ねた日数": sum(r["重ねた"] for r in rows),
             "花火のない日の日数": len(rows) - len(in_season), "発生源": source, "遅れ": table}
  (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
  print(json.dumps(summary, ensure_ascii=False, indent=1))

  sample_day = samples[0]
  show = extract_days([sample_day], SHOW_WINDOW, CACHE_ROOT / "sample", 1)
  if sample_day in show:
    plot_filter_effect(sample_day, show[sample_day], args.output / "01_filter_effect.png", station_names.full(REFERENCE))
  plot_stack(stacks, lags, delays, args.output / "02_stack.png")
  plot_travel_time(table, source, args.output / "03_travel_time.png")
  if not args.no_map:
    plot_map(table, source, coords, args.output / "04_map.png")
  if not args.manten_only:
    plot_vnet(curves, sample_files, table, lags, args.output / "05_vnet_check.png")
    depth_rows, depth_curves = ussv_depth(sample_files)
    if depth_rows:
      with open(args.output / "ussv_depth.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(depth_rows[0]))
        w.writeheader()
        w.writerows(depth_rows)
      plot_ussv(depth_rows, depth_curves, args.output / "06_ussv_depth.png")

  ranges = [tuple(parse_day(v) for v in part.split(":")) for part in args.program_ranges.split(",")]
  program = program_days(ranges, args.jobs)
  plot_program(program, ranges, args.output / "07_program_calendar.png")
  months = program_months(program)
  print("A の月:", [f"{y}-{m:02d}" for (y, m), a in sorted(months.items()) if a])
  with open(args.output / "program_days.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["日", "A の時刻に終わった", "最後の強い揺れ", "最大の倍率"])
    for day, r in sorted(program.items()):
      w.writerow([day.isoformat(), int(r["A"]), clock_text(r["last"]), round(r["max"])])
  return 0


if __name__ == "__main__":
  sys.exit(main())
