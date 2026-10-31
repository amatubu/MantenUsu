import os
import zipfile
from pathlib import Path


def main():
  # --- 【設定エリア】 ---
  # チェック対象のZipファイルが集まっているフォルダのパス
  TARGET_DIR = "../source_zips"  # 適宜変更してください
  # --------------------

  target_path = Path(TARGET_DIR)
  zip_files = list(target_path.glob("*.zip"))

  if not zip_files:
    print(f"エラー: 指定されたフォルダにZipファイルが見つかりません: {TARGET_DIR}")
    return

  print(
    f"合計 {len(zip_files)} 個のZipファイルをスキャンして構造をチェックします...\n"
  )

  total_zips = 0
  total_files = 0
  sample_paths = []
  anomaly_count = 0

  for z_file in zip_files:
    total_zips += 1
    print(f"チェック中: {z_file.name}")
    try:
      with zipfile.ZipFile(z_file, "r") as zin:
        namelist = zin.namelist()
        for filename in namelist:
          if not filename.endswith(".win"):
            continue

          total_files += 1
          base_filename = Path(filename).name

          # パス構造のチェック
          # パスのパーツに分解 (例: "some_folder/260823/0970355225_2608232245.win")
          parts = filename.split("/")

          # サンプルとして最初の数件を保持
          if len(sample_paths) < 5:
            sample_paths.append(filename)

          # --- 厳密なバリデーション判定 ---
          is_valid = True
          reason = ""

          # 1. 階層チェック (最低2階層：日付フォルダ/ファイル)
          if len(parts) < 2:
            is_valid = False
            reason = "階層が不足しています"
          else:
            # 2. 日付フォルダの形式チェック (後ろから2番目が6桁の数字か)
            date_part = parts[-2]
            if not (date_part.isdigit() and len(date_part) == 6):
              is_valid = False
              reason = f"日付フォルダの形式が不正 ({date_part})"
            else:
              # 3. ファイル名の形式チェック (先頭10文字が10桁の数字＝IDか)
              file_id = base_filename[:10]
              if not (file_id.isdigit() and len(file_id) == 10):
                is_valid = False
                reason = f"ファイル名がID形式ではない ({base_filename})"

          # 想定外のファイルだった場合の集計
          if not is_valid:
            anomaly_count += 1
            if anomaly_count <= 5:
              print(f"  [警告] 想定外のファイルを発見 ({reason}): {filename}")

    except Exception as e:
      print(f"  -> Zipファイルを開けませんでした ({z_file.name}): {e}")

  print("\n" + "=" * 40)
  print("【チェック結果サマリー】")
  print(f"  - スキャンしたZip数: {total_zips} 個")
  print(f"  - 検出された .win ファイル総数: {total_files} 個")
  print(f"  - 想定外の構造だったファイル数: {anomaly_count} 個")
  print("=" * 40)

  if anomaly_count > 0:
    print(
      f"\n[注意] 想定外のファイルが {anomaly_count} 件検出されました。"
      "リパッケージ時にスキップまたはエラーになる可能性があります。"
    )
  else:
    print("\n[判定] すべてのファイルが想定通りの構造です！")

  if sample_paths:
    print("\n【格納パスのサンプル（上から5件）】")
    for p in sample_paths:
      print(f"  - {p}")
  print("\nチェックが完了しました。")


if __name__ == "__main__":
  main()