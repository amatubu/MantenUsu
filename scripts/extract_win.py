# extract_win.py
# 指定期間のWINファイルを daily_zips のZIPから取り出す(単体実行用。処理本体は win_pipeline)
from datetime import datetime
from pathlib import Path
import sys

import win_pipeline

# プロジェクトのフォルダ(このスクリプトの親の親)。どこから実行しても同じ場所を使う
PROJECT_DIR = Path(__file__).resolve().parent.parent


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
  # 出力先の親フォルダ（指定がない場合はプロジェクトの 'output'）
  dest_base = Path(sys.argv[3]) if len(sys.argv) > 3 else PROJECT_DIR / "output"

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
  dest_dir = dest_base / start_str

  # ZIPファイルが格納されている親フォルダ
  zips_root = PROJECT_DIR / "daily_zips"
  if not zips_root.exists():
    print(f"エラー: 'daily_zips' フォルダが見つかりません: {zips_root}")
    return

  # 指定された期間に含まれる「日付（YYMMDD）」
  target_dates = win_pipeline.target_dates(start_dt, end_dt)

  print(
      f"検索範囲: {start_dt.strftime('%Y-%m-%d %H:%M')} ～"
      f" {end_dt.strftime('%Y-%m-%d %H:%M')}"
  )
  print(f"ターゲット日付ZIP: {sorted(target_dates)}")
  print(f"出力先フォルダ: {dest_dir}\n")

  # 対象となる日付のZIPファイルだけを探す
  target_zips = sorted(win_pipeline.find_zips(zips_root, target_dates))
  if not target_zips:
    print("警告: 条件に一致するZIPファイルが見つかりませんでした。")
    return
  print(f"対象ZIP: {', '.join(z.name for z in target_zips)}")

  extracted_count = win_pipeline.extract_wins_from_zips(target_zips, start_dt, end_dt, dest_dir)
  print(f"\n完了しました。合計 {extracted_count} 個のファイルを抽出しました。")


if __name__ == "__main__":
  main()
