import os
import shutil
import sys
from datetime import datetime


def main():
  # 引数のチェック (開始日時, 終了日時, 出力先親フォルダ)
  if len(sys.argv) < 3:
    print(
        "使い方: python3 extract_win.py <開始YYYYMMDDHHmm>"
        " <終了YYYYMMDDHHmm> [出力先親ディレクトリ]"
    )
    sys.exit(1)

  start_str = sys.argv[1]
  end_str = sys.argv[2]
  # 出力先の親フォルダ（指定がない場合はカレントディレクトリの 'output'）
  dest_base = sys.argv[3] if len(sys.argv) > 3 else "./output"

  try:
    start_dt = datetime.strptime(start_str, "%Y%m%d%H%M")
    end_dt = datetime.strptime(end_str, "%Y%m%d%H%M")
  except ValueError:
    print(
        "エラー: 日時の形式は YYYYMMDDHHmm (例: 202608232245) で指定してください。"
    )
    sys.exit(1)

  # 起点（開始日時）のフォルダ名を作成 (例: output/202608232245)
  dest_dir = os.path.join(dest_base, start_str)
  os.makedirs(dest_dir, exist_ok=True)

  source_root = "."
  copied_count = 0

  print(
      f"検索範囲: {start_dt.strftime('%Y-%m-%d %H:%M')} ～"
      f" {end_dt.strftime('%Y-%m-%d %H:%M')}"
  )
  print(f"出力先フォルダ: {dest_dir}\n")

  if not os.path.exists(source_root):
    print(f"エラー: コピー元フォルダが見つかりません: {source_root}")
    return

  for id_folder in os.listdir(source_root):
    id_path = os.path.join(source_root, id_folder)
    if not os.path.isdir(id_path) or not id_folder.isdigit() or len(id_folder) != 10:
      continue

    for date_folder in os.listdir(id_path):
      date_path = os.path.join(id_path, date_folder)
      if (
          not os.path.isdir(date_path)
          or not date_folder.isdigit()
          or len(date_folder) != 6
      ):
        continue

      for filename in os.listdir(date_path):
        if not filename.endswith(".win"):
          continue

        parts = filename.split("_")
        if len(parts) < 2:
          continue

        datetime_str = parts[1].replace(".win", "")
        if len(datetime_str) != 10 or not datetime_str.isdigit():
          continue

        try:
          f_year = 2000 + int(datetime_str[0:2])
          f_month = int(datetime_str[2:4])
          f_day = int(datetime_str[4:6])
          f_hour = int(datetime_str[6:8])
          f_minute = int(datetime_str[8:10])

          file_dt = datetime(f_year, f_month, f_day, f_hour, f_minute)
        except ValueError:
          continue

        if start_dt <= file_dt <= end_dt:
          src_file = os.path.join(date_path, filename)
          dest_file = os.path.join(dest_dir, filename)

          shutil.copy2(src_file, dest_file)
          print(f"Copied: {id_folder}/{date_folder}/{filename}")
          copied_content_count = copied_count + 1
          copied_count += 1

  print(f"\n完了しました。合計 {copied_count} 個のファイルをコピーしました。")


if __name__ == "__main__":
  main()