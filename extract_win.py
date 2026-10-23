from datetime import datetime, timedelta
from pathlib import Path
import sys
import zipfile


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
  # 出力先の親フォルダ（指定がない場合は親ディレクトリの 'output'）
  dest_base = sys.argv[3] if len(sys.argv) > 3 else "../output"

  try:
    start_dt = datetime.strptime(start_str, "%Y%m%d%H%M")
    end_dt = datetime.strptime(end_str, "%Y%m%d%H%M")
  except ValueError:
    print(
        "エラー: 日時の形式は YYYYMMDDHHmm (例: 202608232245) で指定してください。"
    )
    sys.exit(1)

  if start_dt > end_dt:
    print("エラー: 開始日時は終了日時より前を指定してください。")
    sys.exit(1)

  # 起点（開始日時）のフォルダ名を作成 (例: output/202608232245)
  dest_dir = Path(dest_base) / start_str
  dest_dir.mkdir(parents=True, exist_ok=True)

  # ZIPファイルが格納されている親フォルダ
  zips_root = Path("../daily_zips")
  if not zips_root.exists():
    print(f"エラー: 'daily_zips' フォルダが見つかりません: {zips_root}")
    return

  # ★ 指定された期間に含まれる「日付（YYMMDD）」のリストを生成する
  # 例: 2026-08-23 なら '260823'
  target_dates = set()
  curr = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
  end_date_only = end_dt.replace(hour=0, minute=0, second=0, microsecond=0)

  while curr <= end_date_only:
    # YYMMDD 形式の文字列を作成（例: 2026年8月23日 -> 260823）
    yy_mm_dd = curr.strftime("%y%m%d")
    target_dates.add(yy_mm_dd)
    curr += timedelta(days=1)

  extracted_count = 0

  print(
      f"検索範囲: {start_dt.strftime('%Y-%m-%d %H:%M')} ～"
      f" {end_dt.strftime('%Y-%m-%d %H:%M')}"
  )
  print(f"ターゲット日付ZIP: {sorted(list(target_dates))}")
  print(f"出力先フォルダ: {dest_dir}\n")

  # ★ 対象となる日付のZIPファイルだけをピンポイントで探す
  target_zips = []
  for zip_path in zips_root.glob("**/*.zip"):
    # ZIPファイル名（拡張子除く）が target_dates に含まれるかチェック
    # （例: 260823.zip や 20260823.zip などの命名揺れにも対応できるように部分一致等で判定）
    zip_stem = zip_path.stem
    if any(d in zip_stem for d in target_dates):
      target_zips.append(zip_path)

  if not target_zips:
    print("警告: 条件に一致するZIPファイルが見つかりませんでした。")
    return

  # 該当するZIPファイルだけを処理
  for zip_path in sorted(target_zips):
    try:
      with zipfile.ZipFile(zip_path, "r") as zf:
        for internal_path in zf.namelist():
          filename = Path(internal_path).name
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

          # 指定された時間範囲内であれば抽出
          if start_dt <= file_dt <= end_dt:
            dest_file = dest_dir / filename
            # ZIP内の該当ファイルをバイナリで読み出して出力先へ書き出す
            with zf.open(internal_path) as src, open(dest_file, "wb") as dst:
              dst.write(src.read())

            print(f"Extracted from {zip_path.name}: {filename}")
            extracted_count += 1

    except Exception as e:
      print(f"警告: {zip_path.name} の処理中にエラーが発生しました ({e})")

  print(f"\n完了しました。合計 {extracted_count} 個のファイルを抽出しました。")


if __name__ == "__main__":
  main()