# screen_events.py
# 地震らしい波形が見られた期間の一覧から、近い地震の候補を絞り込む(単体実行用)
#   各期間について、4拠点の波形から揺れの長さ・卓越周波数・P→最大振幅などを計算し(quake_features.py)、
#   一覧(CSV)と確認用の画像に出す
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

from hypocenter import load_stations, LocalProjection
from quake_features import BAND, analyze
from plot_common import NM_TO_CM, setup_japanese_font
from log_common import set_console, log_message

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
