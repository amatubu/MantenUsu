# convert_to_sac.py
# フォルダ内のWINファイルを拠点ごとに結合し、SACファイルに変換する(単体実行用。処理本体は win_pipeline)
from pathlib import Path
import sys

import win_pipeline

# プロジェクトのフォルダ(このスクリプトの親の親)。どこから実行しても同じ場所を使う
PROJECT_DIR = Path(__file__).resolve().parent.parent


def main():
  if len(sys.argv) < 2:
    print("使い方: python3 convert_to_sac.py <抽出された日時フォルダのパス>")
    print("例: python3 convert_to_sac.py ../output/202608232245")
    sys.exit(1)

  target_dir = Path(sys.argv[1])
  if not target_dir.exists():
    print(f"エラー: 指定されたフォルダが見つかりません: {target_dir}")
    sys.exit(1)

  # 変換対象(結合前)のWINファイル
  raw_wins = win_pipeline.find_raw_wins(target_dir)
  if not raw_wins:
    print("警告: 変換対象の .win ファイルが見つかりませんでした。")
    return
  stations = {p.name.split("_")[0] for p in raw_wins}
  print(f"対象拠点数: {len(stations)} 拠点 (計 {len(raw_wins)} ファイル)\n")

  # 結合・チャンネル読み替え・SAC変換(拠点ごとのエラーは表示して続行)
  try:
    win_pipeline.convert_wins_to_sacs(target_dir, PROJECT_DIR / "tools", PROJECT_DIR / "etc")
  except win_pipeline.PipelineError as e:
    print(f"エラー: {e}")
    sys.exit(1)

  sacs = sorted(p.name for p in win_pipeline.find_sacs(target_dir))
  print(f"SACファイル: {len(sacs)} 個")
  for name in sacs:
    print(f"  {name}")
  print("\nすべての拠点データの結合・変換処理が終了しました。")


if __name__ == "__main__":
  main()
