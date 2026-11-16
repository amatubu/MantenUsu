# batch_intensity.py
# 地震のリスト(CSV)の地震ごとに、拠点の3成分から計測震度を求め、比べる震度と並べた表にする
#   (波形の画面の「震度推定」を、リストの地震にまとめてかけるもの。計算は jma_intensity.calc_jma_intensity)
#   観測成果報告の「12個の地震と Bamba の計測震度」のような表を、感度などを直したあとに作り直すためのもの
#
# リストの形式(UTF-8 の CSV。1行目は項目名。「発生日時」のほかは無くてもよい):
#   発生日時,震央地名,M,深さ,比べる震度,開始,終了
#   2025-12-08 23:15,青森県東方沖,7.5,54 km,4,,
#     発生日時 ... 日本時間(秒は無くてもよい)
#     比べる震度 ... 洞爺湖町の震度計の震度など(0〜7、5弱・5強・6弱・6強)。あれば階級の差を出す
#     開始・終了 ... 計算に使う範囲(日本時間、分まで。終了の分も含む)。空なら、発生の2分前から9分後まで
#                   (アプリで地震を表示するときの範囲と同じくらい。遠い地震は S波・表面波が入るように延ばす)
#   範囲の中に列車などの雑音が入ると、その揺れで震度が決まることがあるので、最大の時刻も出す
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/batch_intensity.py <リスト.csv> [--station Bamba ...] [-o 結果.csv] [--etc <etc のフォルダ>]
#     --station ... 拠点(内部名。既定は Bamba。いくつか指定できる)
#     -o        ... 結果の CSV(既定は、リストと同じ所に <リスト>_result.csv)
#     --etc     ... チャンネル表などのフォルダ(既定はアプリと同じ。前のチャンネル表で計算し直して比べるとき)
#   SAC は output/ のキャッシュ(アプリと同じ <開始>_<終了> のフォルダ)を使い、無ければ日ごとの ZIP から作る
#   (--etc を指定したときは、キャッシュを使わず一時フォルダで変換する)
import argparse
import csv
import sys
import tempfile
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
from obspy import read

import app_paths
import win_pipeline
from jma_intensity import calc_jma_intensity

DEFAULT_STATION = "Bamba"
BEFORE = timedelta(minutes=2)   # 範囲の既定: 発生のこの前から
AFTER = timedelta(minutes=9)    # 発生のこの後の分まで(その分も含む)
SCALES = ["0", "1", "2", "3", "4", "5弱", "5強", "6弱", "6強", "7"]
TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M")


def parse_time(text):
  text = (text or "").strip()
  for fmt in TIME_FORMATS:
    try:
      return datetime.strptime(text, fmt)
    except ValueError:
      pass
  return None


# リストを読む。戻り値は 1 行ごとの辞書のリスト(発生日時は datetime、開始・終了は datetime か None)
def read_events(path):
  events = []
  with open(path, encoding="utf-8-sig", newline="") as f:
    for i, row in enumerate(csv.DictReader(f), start=2):
      row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
      origin = parse_time(row.get("発生日時"))
      if origin is None:
        if any(row.values()):
          print(f"{path.name} の {i} 行目: 発生日時が読めないので飛ばします: {row.get('発生日時')!r}")
        continue
      row["origin"] = origin
      start = parse_time(row.get("開始")) or (origin - BEFORE).replace(second=0)
      end = parse_time(row.get("終了")) or (origin + AFTER).replace(second=0)
      row["start"], row["end"] = start.replace(second=0), end.replace(second=0)
      events.append(row)
  return events


# 範囲の SAC を読む。use_cache ならアプリと同じキャッシュ(output/<開始>_<終了>)を使い、無ければ作る
def load_stream(start, end, etc_dir, use_cache):
  zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(start, end))
  if use_cache:
    dest = app_paths.OUTPUT_DIR / f"{start:%Y%m%d%H%M}_{end:%Y%m%d%H%M}"
    if not win_pipeline.find_current_sacs(dest, etc_dir):
      if win_pipeline.find_raw_wins(dest):
        win_pipeline.convert_wins_to_sacs(dest, app_paths.TOOLS_DIR, etc_dir)
      else:
        if not zips:
          return None
        with tempfile.TemporaryDirectory(prefix="batch_intensity_") as work:
          if win_pipeline.zips_to_sacs(zips, start, end, Path(work), app_paths.TOOLS_DIR, etc_dir) == 0:
            return None
          dest.mkdir(parents=True, exist_ok=True)
          win_pipeline.move_results(Path(work), dest)
    return read_sacs(dest)
  if not zips:
    return None
  with tempfile.TemporaryDirectory(prefix="batch_intensity_") as work:
    if win_pipeline.zips_to_sacs(zips, start, end, Path(work), app_paths.TOOLS_DIR, etc_dir) == 0:
      return None
    return read_sacs(Path(work))


def read_sacs(folder):
  if not win_pipeline.find_sacs(folder):
    return None
  with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    return read(str(folder / win_pipeline.SAC_PATTERN))


# 拠点の計測震度・震度階級・最大の時刻(3成分合成加速度が最大になった時刻。日本時間)
def station_intensity(st, station):
  sub = st.select(station=station)
  if len({tr.stats.channel[-1:] for tr in sub} & {"U", "N", "E"}) < 3:
    sub = sub.merge(fill_value=0)
  if len(sub) < 3:
    return None
  res = calc_jma_intensity(sub)
  tr = res["st_filt"][0]
  t_peak = tr.stats.starttime + int(np.argmax(res["combined_acc"])) / tr.stats.sampling_rate
  return {"intensity": float(res["intensity"]), "scale": res["scale"], "peak": t_peak.datetime}


# 比べる震度との階級の差(拠点の方が大きければ正)。比べる震度が無ければ None
def scale_diff(scale, reference):
  ref = (reference or "").strip()
  ref = ref.split("(")[0].split("（")[0].strip()
  if ref not in SCALES or scale not in SCALES:
    return None
  return SCALES.index(scale) - SCALES.index(ref)


def judge(diff):
  if diff is None:
    return ""
  if diff == 0:
    return "一致"
  return f"{abs(diff)}つ{'上' if diff > 0 else '下'}"


def main():
  ap = argparse.ArgumentParser(description="地震のリストの地震ごとに、拠点の計測震度を求める")
  ap.add_argument("events", type=Path, help="地震のリスト(CSV)")
  ap.add_argument("--station", nargs="+", default=[DEFAULT_STATION], help="拠点(内部名。既定は Bamba)")
  ap.add_argument("-o", "--output", type=Path, help="結果の CSV")
  ap.add_argument("--etc", type=Path, help="チャンネル表などのフォルダ(既定はアプリと同じ)")
  args = ap.parse_args()

  etc_dir = args.etc.resolve() if args.etc else app_paths.ETC_DIR
  use_cache = args.etc is None
  events = read_events(args.events)
  if not events:
    sys.exit(f"{args.events} に地震がありません")
  out_path = args.output or args.events.with_name(f"{args.events.stem}_result.csv")

  head = ["発生日時", "震央地名", "M", "深さ", "比べる震度", "拠点", "計測震度", "震度", "判定", "最大の時刻", "範囲"]
  rows = []
  for ev in events:
    st = load_stream(ev["start"], ev["end"], etc_dir, use_cache)
    for station in args.station:
      r = station_intensity(st, station) if st is not None else None
      base = [f"{ev['origin']:%Y-%m-%d %H:%M}", ev.get("震央地名", ""), ev.get("M", ""), ev.get("深さ", ""),
              ev.get("比べる震度", ""), station]
      span = f"{ev['start']:%m-%d %H:%M}〜{ev['end']:%H:%M}"
      if r is None:
        rows.append(base + ["", "", "波形なし", "", span])
      else:
        diff = scale_diff(r["scale"], ev.get("比べる震度"))
        rows.append(base + [f"{r['intensity']:.2f}", r["scale"], judge(diff), f"{r['peak']:%H:%M:%S}", span])
      print("  ".join(str(v) for v in rows[-1]))

  with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(head)
    w.writerows(rows)
  print(f"結果: {out_path}")


if __name__ == "__main__":
  main()
