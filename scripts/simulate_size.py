# simulate_size.py
# 観測データの量を、日ごと・月ごとに集計する(Googleドライブやディスクの容量の見積もり用)
#   既定は、データフォルダの daily_zips の ZIP の中身(WIN ファイル)を、解凍せずに数える
#     ファイル数・WIN ファイルの大きさの合計・ZIP の大きさ(圧縮後)を出す
#   --dir で、展開した元データのフォルダ(拠点ID/YYMMDD/*.win の形)を数えることもできる(日ごとの ZIP にまとめる前の見積もり)
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/simulate_size.py                ... daily_zips の月ごとの集計
#   python3 scripts/simulate_size.py --daily        ... 日ごとの集計も出す
#   python3 scripts/simulate_size.py --dir <フォルダ> ... 展開した元データのフォルダを数える
import argparse
import os
import zipfile
from collections import defaultdict
from pathlib import Path

import app_paths


def new_stats():
  return {"count": 0, "size": 0, "zip": 0}

# daily_zips の ZIP の中身を数える(日付は ZIP の中のフォルダ名 拠点ID/YYMMDD/...)
def scan_daily_zips(zips_dir):
  daily = defaultdict(new_stats)
  for zip_path in sorted(zips_dir.glob("*.zip")):
    try:
      with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
          parts = info.filename.split("/")
          if not info.filename.endswith(".win") or len(parts) < 2:
            continue
          day = parts[-2]
          if not (day.isdigit() and len(day) == 6):
            continue
          daily[day]["count"] += 1
          daily[day]["size"] += info.file_size
          daily[day]["zip"] += info.compress_size
    except Exception as e:
      print(f"警告: {zip_path.name} を読めませんでした ({e})")
  return daily

# 展開した元データのフォルダ(拠点ID(10桁)/YYMMDD/*.win)を数える
def scan_folder(root):
  daily = defaultdict(new_stats)
  for id_folder in os.listdir(root):
    id_path = os.path.join(root, id_folder)
    if not os.path.isdir(id_path) or not id_folder.isdigit() or len(id_folder) != 10:
      continue
    for day in os.listdir(id_path):
      day_path = os.path.join(id_path, day)
      if not os.path.isdir(day_path) or not day.isdigit() or len(day) != 6:
        continue
      for filename in os.listdir(day_path):
        if not filename.endswith(".win"):
          continue
        try:
          size = os.path.getsize(os.path.join(day_path, filename))
        except OSError:
          continue
        daily[day]["count"] += 1
        daily[day]["size"] += size
  return daily

# 大きさを読みやすい単位にする
def format_size(size_bytes):
  for unit in ["B", "KB", "MB", "GB"]:
    if size_bytes < 1024.0:
      return f"{size_bytes:.1f} {unit}"
    size_bytes /= 1024.0
  return f"{size_bytes:.1f} TB"

def print_table(title, rows, label, with_zip):
  print(f"\n【{title}】")
  header = f"{label:<16} | {'ファイル数':>8} | {'WIN の大きさ':>12}" + (f" | {'ZIP(圧縮後)':>12}" if with_zip else "")
  print(header)
  print("-" * (len(header) + 6))
  for key, st in rows:
    line = f"{key:<16} | {st['count']:>10} | {format_size(st['size']):>14}"
    if with_zip:
      line += f" | {format_size(st['zip']):>14}"
    print(line)

def main():
  parser = argparse.ArgumentParser(description="観測データの量を、日ごと・月ごとに集計する")
  parser.add_argument("--dir", type=Path, help="展開した元データのフォルダ(拠点ID/YYMMDD/*.win)を数える")
  parser.add_argument("--daily", action="store_true", help="日ごとの集計も出す")
  args = parser.parse_args()

  if args.dir:
    if not args.dir.is_dir():
      print(f"エラー: フォルダが見つかりません: {args.dir}")
      return
    print(f"展開した元データのフォルダを数えています: {args.dir}")
    daily, with_zip = scan_folder(args.dir), False
  else:
    zips_dir = app_paths.ZIPS_DIR
    if not zips_dir.is_dir():
      print(f"エラー: daily_zips が見つかりません: {zips_dir}")
      return
    print(f"daily_zips の ZIP の中身を数えています(解凍はしません): {zips_dir}")
    daily, with_zip = scan_daily_zips(zips_dir), True
  if not daily:
    print("WIN ファイルが見つかりませんでした。")
    return

  monthly = defaultdict(new_stats)
  total = new_stats()
  for day, st in daily.items():
    for target in (monthly[day[:4]], total):
      for k in target:
        target[k] += st[k]

  print("=" * 60)
  print(f"全体: {len(daily)}日、ファイル数 {total['count']}、WIN の大きさ {format_size(total['size'])}"
        + (f"、ZIP(圧縮後) {format_size(total['zip'])}" if with_zip else ""))
  print(f"1日あたりの平均: WIN {format_size(total['size'] / len(daily))}"
        + (f"、ZIP {format_size(total['zip'] / len(daily))}" if with_zip else ""))
  print("=" * 60)
  print_table("月ごと", [(f"20{m[:2]}年{m[2:]}月", monthly[m]) for m in sorted(monthly)], "月", with_zip)
  if args.daily:
    print_table("日ごと", [(f"20{d[:2]}/{d[2:4]}/{d[4:]}", daily[d]) for d in sorted(daily)], "日付", with_zip)


if __name__ == "__main__":
  main()
