# show_statistics.py
# daily_zips のZIPファイルの中身(WINファイル名)から、拠点ごとに記録がある期間を集計する
#   - 集計結果を output/data_availability_summary.xlsx / .csv に保存する
#   - 拠点ごとの記録期間と記録日数をグラフで表示する(--save で画像に保存)
#   使い方: python3 scripts/show_statistics.py [--save 画像ファイル]
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import zipfile

import app_paths                   # matplotlib より先に読み込む(ビルドしたアプリ向けの設定のため)
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from plot_common import setup_japanese_font

# ファイル間がこの時間より空いたら、別の記録期間とみなす
GAP_THRESHOLD = timedelta(hours=2)

# WINファイルは1分ごとに1ファイル
FILE_MINUTES = 1

# グラフの色(スライドに合わせる)
INK, SUB, BAR, GRID, BG = "#1F2A30", "#5B6670", "#2A6F95", "#DCD8CF", "#FCFBF8"

# 拠点IDから拠点名を引く表を作る
#   etc/拠点ID.ch の最初のチャンネル番号を、manten_ch.tbl の拠点名(4番目の項目)と突き合わせる
#   拠点名は manten_ch.tbl の制約で7文字まで(例: Nizaemon は Nizaemn)
def load_station_names(etc_dir):
  ch_to_name = {}
  with open(etc_dir / "manten_ch.tbl", encoding="utf-8") as f:
    for line in f:
      fields = line.split()
      if len(fields) >= 4 and not fields[0].startswith("#"):
        ch_to_name[fields[0].upper()] = fields[3]

  names = {}
  for ch_file in etc_dir.glob("*.ch"):
    first_ch = ch_file.read_text(encoding="utf-8").strip().split(",")[0].upper()
    if first_ch in ch_to_name:
      names[ch_file.stem] = ch_to_name[first_ch]
  return names

# ZIPの中のWINファイル名(拠点ID_YYMMDDHHMM.win)から、拠点IDと時刻を集める(解凍はしない)
def scan_zips(zips_dir):
  records = []
  for zip_path in sorted(zips_dir.glob("**/*.zip")):
    try:
      with zipfile.ZipFile(zip_path, "r") as zf:
        for filename in zf.namelist():
          fname = Path(filename).name
          if not fname.endswith(".win"):
            continue
          parts = fname[:-len(".win")].split("_")
          if len(parts) < 2 or len(parts[1]) != 10:
            continue
          try:
            dt = datetime.strptime(parts[1], "%y%m%d%H%M")
          except ValueError:
            continue
          records.append({"station": parts[0], "time": dt, "zip_name": zip_path.name})
    except Exception as e:
      print(f"警告: {zip_path.name} の読み込みに失敗しました ({e})")
  return records

# 拠点ごとに、連続して記録がある期間(ブロック)を取り出す
def summarize(df, names):
  summary_list = []
  for station, group in df.groupby("station"):
    times = group["time"].tolist()
    block_start = prev_t = times[0]
    count = 1
    for t in times[1:]:
      if t - prev_t > GAP_THRESHOLD:
        summary_list.append((station, block_start, prev_t, count))
        block_start, count = t, 0
      prev_t = t
      count += 1
    summary_list.append((station, block_start, prev_t, count))

  return pd.DataFrame([{
    "Station": station,
    "Name": names.get(station, station),
    "Start_Time": start,
    # 最後のファイルの1分間も含める
    "End_Time": end + timedelta(minutes=FILE_MINUTES),
    "File_Count": count,
  } for station, start, end, count in summary_list])

# 拠点ごとの記録期間を横棒で、右端に記録日数(ファイル数から計算)を表示する
def plot_availability(summary_df, save_path=None):
  setup_japanese_font()
  names = sorted(summary_df["Name"].unique())

  fig, ax = plt.subplots(figsize=(12, 3.2))
  fig.patch.set_facecolor(BG)
  ax.set_facecolor(BG)
  for i, name in enumerate(names):
    y = len(names) - 1 - i   # 上から名前順に並べる
    rows = summary_df[summary_df["Name"] == name]
    for _, row in rows.iterrows():
      start = mdates.date2num(row["Start_Time"])
      ax.barh(y, mdates.date2num(row["End_Time"]) - start, left=start, height=0.52, color=BAR)
    days = rows["File_Count"].sum() * FILE_MINUTES / (24 * 60)
    ax.text(1.005, (y + 0.5) / len(names), f"約{days:.0f}日", transform=ax.transAxes,
            va="center", ha="left", fontsize=11, color=INK)

  ax.set_yticks(range(len(names)))
  ax.set_yticklabels(list(reversed(names)), fontsize=12, color=INK, fontweight="bold")
  ax.set_ylim(-0.6, len(names) - 0.4)

  # 横軸は月ごと。最初の月と1月だけ年をつける
  first = summary_df["Start_Time"].min()
  last = summary_df["End_Time"].max()
  ax.set_xlim(mdates.date2num(datetime(first.year, first.month, 1)),
              mdates.date2num(datetime(last.year + last.month // 12, last.month % 12 + 1, 1)))
  def month_label(value, _):
    d = mdates.num2date(value)
    is_first = (d.year, d.month) == (first.year, first.month)
    return f"{d.year}年{d.month}月" if is_first or d.month == 1 else f"{d.month}月"
  ax.xaxis.set_major_locator(mdates.MonthLocator())
  ax.xaxis.set_major_formatter(plt.FuncFormatter(month_label))
  ax.tick_params(axis="x", labelsize=10, colors=SUB, length=0, pad=6)
  ax.tick_params(axis="y", length=0, pad=8)
  ax.grid(axis="x", color=GRID, linewidth=0.8)
  ax.set_axisbelow(True)
  for spine in ax.spines.values():
    spine.set_visible(False)

  ax.set_title(f"拠点ごとの記録期間 ({first:%Y/%m/%d}〜{last:%Y/%m/%d})", fontsize=13, color=INK)
  fig.subplots_adjust(left=0.1, right=0.93, top=0.86, bottom=0.14)

  if save_path:
    fig.savefig(save_path, dpi=220, facecolor=BG)
    print(f"[保存完了] グラフを '{save_path}' に保存しました。")
  else:
    plt.show()

def main():
  parser = argparse.ArgumentParser(description="daily_zips の拠点ごとの記録期間を集計する")
  parser.add_argument("--save", metavar="FILE", help="グラフを表示せず、画像ファイルに保存する")
  args = parser.parse_args()

  print("ZIPファイル群をスキャン中(解凍はしません)...")
  records = scan_zips(app_paths.ZIPS_DIR)
  if not records:
    print("有効なWINファイル名がZIP内で見つかりませんでした。パスを確認してください。")
    return

  df = pd.DataFrame(records).sort_values(["station", "time"]).reset_index(drop=True)
  summary_df = summarize(df, load_station_names(app_paths.ETC_DIR))

  # 結果をコンソール表示 ＆ Excel/CSVに保存(output フォルダ)
  print("\n=== データ可用性サマリー (ZIP直接スキャン) ===")
  print(summary_df)
  app_paths.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  out_base = app_paths.OUTPUT_DIR / "data_availability_summary"
  summary_df.to_excel(out_base.with_suffix(".xlsx"), index=False)
  summary_df.to_csv(out_base.with_suffix(".csv"), index=False)
  print(f"\n[保存完了] '{out_base}.xlsx' および .csv に出力しました。")

  plot_availability(summary_df, args.save)

if __name__ == "__main__":
  main()
