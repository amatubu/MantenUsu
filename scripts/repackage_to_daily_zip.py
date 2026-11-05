# repackage_to_daily_zip.py
# 拠点ごと・回収ごとの元の ZIP を、日ごとの ZIP(YYMMDD.zip)にまとめ直す(データを用意する側が使う)
#   元の ZIP は、データフォルダの source_zips に置く(データフォルダはメンテナンス画面の「置き場所」で確かめられる)
#   まとめ直した ZIP は、データフォルダの daily_zips に書く。できた ZIP は、Googleドライブのフォルダに手で保存する
#   同じ日の ZIP がすでにあれば、無い WIN ファイルだけを書き足す(回収のたびに、新しい元の ZIP だけを入れて実行してよい)
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/repackage_to_daily_zip.py                         ... source_zips → daily_zips
#   python3 scripts/repackage_to_daily_zip.py --source <フォルダ> --output <フォルダ>   ... 場所を指定する
import argparse
import os                 # ディレクトリ操作など
import zipfile            # Zipファイルの読み書き
from pathlib import Path  # パス操作のためのクラス

import app_paths

# 元の ZIP を置くフォルダ(データフォルダの中)
SOURCE_ZIPS_DIR = app_paths.DATA_DIR / "source_zips"


def main():
  parser = argparse.ArgumentParser(description="拠点ごとの元の ZIP を、日ごとの ZIP(YYMMDD.zip)にまとめ直す")
  parser.add_argument("--source", type=Path, default=SOURCE_ZIPS_DIR,
                      help=f"元の ZIP のフォルダ(既定: {SOURCE_ZIPS_DIR})")
  parser.add_argument("--output", type=Path, default=app_paths.ZIPS_DIR,
                      help=f"日ごとの ZIP を書くフォルダ(既定: {app_paths.ZIPS_DIR})")
  args = parser.parse_args()
  SOURCE_DIR = args.source
  OUTPUT_DIR = args.output

  # 出力先のフォルダを作成
  os.makedirs(OUTPUT_DIR, exist_ok=True)

  # 入力元・出力先のパスオブジェクト
  source_path = Path(SOURCE_DIR)
  output_path = Path(OUTPUT_DIR)

  # 入力元フォルダにあるZipファイルのリストを取得
  zip_files = list(source_path.glob("*.zip"))

  # Zipファイルが見つかったかどうかを確認
  if not zip_files:
    print(f"エラー: 指定されたフォルダにZipファイルが見つかりません: {SOURCE_DIR}")
    print("       元の ZIP をこのフォルダに置くか、--source でフォルダを指定してください")
    return

  # 処理前の情報表示(lenでファイル数を取得)
  print(
    f"合計 {len(zip_files)} 個のZipファイルを処理し、日単位のZipに再構築します...\n"
  )

  # 処理されたファイルの数を記録
  total_processed = 0

  # 各Zipファイルに対する処理
  for z_file in zip_files:
    # 処理するZipファイルのファイル名を表示
    print(f"処理中: {z_file.name}")
    try:
      # Zipファイルを開く(zinオブジェクトで扱い、ブロックが終了したら自動で破棄)
      with zipfile.ZipFile(z_file, "r") as zin:
        # ファイル一覧を取得し、それぞれについて処理する
        for filename in zin.namelist():
          # .win ファイル以外は無視する
          if not filename.endswith(".win"):
            continue

          # パス構造から確実に6桁の日付フォルダ（後ろから2番目）を特定
          parts = filename.split("/")
          # 階層が浅いファイルは無視する
          if len(parts) < 2:
            continue

          # 最後からふたつめのパート(ファイルの親フォルダ)から日付を取得
          date_folder = parts[-2]
          # YYMMDDの6桁構成でなければ無視する
          if not (date_folder.isdigit() and len(date_folder) == 6):
            continue

          # 日付ごとのZipファイルパス (例: daily_zips/260823.zip)
          daily_zip_path = output_path / f"{date_folder}.zip"

          # ファイル名だけを取り出す (例: 0970355225_2608232245.win)
          base_filename = Path(filename).name

          # ファイル名の先頭10文字をIDとして切り出す
          file_id = base_filename[:10]

          # 新しいZip内でのパスを構築 (例: 0970355225/260823/0970355225_2608232245.win)
          new_filename = f"{file_id}/{date_folder}/{base_filename}"

          # メモリ上でデータを読み込んで、新しい日単位Zipに直接追加・追記
          file_data = zin.read(filename)
          # 日単位Zipを追記(append)モードで開く。存在しなければ作成する
          with zipfile.ZipFile(
            daily_zip_path, "a", zipfile.ZIP_DEFLATED
          ) as zout:
            # 重複書き込みを防ぐため、既にZip内に存在しない場合のみ追加
            if new_filename not in zout.namelist():
              # Zipファイルに新しいパス構成で書き込み
              zout.writestr(new_filename, file_data)
              # 処理済みのファイル数を加算
              total_processed += 1

    # エラー発生時の処理
    except Exception as e:
      print(f"  -> エラーが発生しました ({z_file.name}): {e}")

  # 処理の出力
  print(
    f"\n完了しました！ 合計 {total_processed} 個のファイルを日単位Zipに整理しました。"
  )
  print(f"保存先: {OUTPUT_DIR}")
  print("できた日ごとの ZIP は、Googleドライブのフォルダに保存してください(使う側は --download-zips で受け取ります)")


if __name__ == "__main__":
  main()