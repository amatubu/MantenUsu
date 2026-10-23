from collections import defaultdict
from pathlib import Path
import shutil
import subprocess
import sys


def main():
  if len(sys.argv) < 2:
    print("使い方: python3 convert_to_sac.py <抽出された日時フォルダのパス>")
    print("例: python3 convert_to_sac.py ../output/202608232245")
    sys.exit(1)

  target_dir = Path(sys.argv[1])
  if not target_dir.exists():
    print(f"エラー: 指定されたフォルダが見つかりません: {target_dir}")
    sys.exit(1)

  tools_dir = Path("../tools")
  etc_dir = Path("../etc")

  win2sac_path = tools_dir / "win2sac_32"
  win_prm_path = etc_dir / "win.prm"
  manten_tbl_path = etc_dir / "manten_ch.tbl"

  if not win2sac_path.exists():
    print(f"エラー: win2sac_32 が見つかりません: {win2sac_path}")
    sys.exit(1)
  if not win_prm_path.exists():
    print(f"エラー: win.prm が見つかりません: {win_prm_path}")
    sys.exit(1)
  if not manten_tbl_path.exists():
    print(f"エラー: manten_ch.tbl が見つかりません: {manten_tbl_path}")
    sys.exit(1)

  # 1. 共通の manten_ch.tbl を実行ディレクトリにコピー（win.prm が参照するため必須）
  local_tbl = target_dir / "manten_ch.tbl"
  shutil.copy2(manten_tbl_path, local_tbl)

  # 2. フォルダ内のWINファイルを拠点IDごとにグルーピング
  # ※ 過去に作られた 'combined_' ファイルは除外する
  station_files = defaultdict(list)
  for win_file in target_dir.glob("*.win"):
    if win_file.name.startswith("combined_"):
      continue
    parts = win_file.name.split("_")
    if len(parts) >= 2:
      station_id = parts[0]
      station_files[station_id].append(win_file)

  if not station_files:
    print("警告: 変換対象の .win ファイルが見つかりませんでした。")
    return

  print(
      f"対象拠点数: {len(station_files)} 拠点 (計"
      f" {sum(len(v) for v in station_files.values())} ファイル)\n"
  )

  # 3. 拠点ごとにWINファイルを時系列でソートしてバイナリ結合 ＆ 変換
  for station_id, files in sorted(station_files.items()):
    sorted_files = sorted(files, key=lambda x: x.name)
    combined_win = target_dir / f"combined_{station_id}.win"

    print(f"[{station_id}] {len(sorted_files)} 個のファイルを結合中...")
    with open(combined_win, "wb") as wfd:
      for f in sorted_files:
        with open(f, "rb") as rfd:
          wfd.write(rfd.read())

    # 拠点ごとのチャンネルファイル（例: ../etc/0970355225.ch）を指定
    station_ch_path = etc_dir / f"{station_id}.ch"
    if not station_ch_path.exists():
      print(f"  -> 警告: 拠点用のチャンネルファイルが見つかりません: {station_ch_path}")
      print(f"     デフォルトの manten.ch を探します...")
      station_ch_path = etc_dir / "manten.ch"

    if not station_ch_path.exists():
      print(f"  -> エラー: チャンネルファイルが見つからないため {station_id} の変換をスキップします。")
      continue

    # 4. win2sac_32 の実行
    print(f"[{station_id}] win2sac_32 を実行中...")
    cmd = [
        str(win2sac_path.resolve()),
        str(combined_win.name),
        str(station_ch_path.resolve()),
        f"{station_id}_sac",
        ".",
        f"-p{win_prm_path.resolve()}",
    ]

    result = subprocess.run(
        cmd, cwd=target_dir, capture_output=True, text=True
    )

    if result.returncode == 0:
      print(f"  -> 成功: {station_id} のSAC変換が完了しました。")
    else:
      print(f"  -> エラー ({station_id}):")
      print(result.stdout)
      print(result.stderr)

  print("\nすべての拠点データの結合・変換処理が終了しました。")


if __name__ == "__main__":
  main()