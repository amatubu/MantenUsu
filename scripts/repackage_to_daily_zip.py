# repackage_to_daily_zip.py
# 拠点ごと・回収ごとの元の ZIP を、日ごとの ZIP(YYMMDD.zip)にまとめ直す(データを用意する側が使う)
#   元の ZIP は、データフォルダの source_zips に置く(データフォルダはメンテナンス画面の「置き場所」で確かめられる)
#   まとめ直した ZIP は、データフォルダの daily_zips に書く。できた ZIP は、Googleドライブのフォルダに手で保存する
#   同じ日の ZIP がすでにあれば、無い WIN ファイルだけを書き足す(回収のたびに、新しい元の ZIP だけを入れて実行してよい)
#   書き足した日は、アプリが前の ZIP から作った結果(揺れの大きさ RMS、波形の SAC)が古いまま使われるので、
#   最後に、消すフォルダ・ファイルを表示する(消すのは手で。地震の候補の検出はその日を含めて探し直す)
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/repackage_to_daily_zip.py                         ... source_zips → daily_zips
#   python3 scripts/repackage_to_daily_zip.py --source <フォルダ> --output <フォルダ>   ... 場所を指定する
#   python3 scripts/repackage_to_daily_zip.py --dry-run                ... 書かずに、新しくできる日・書き足す日を表示する
import argparse
from datetime import datetime
import os                 # ディレクトリ操作など
import zipfile            # Zipファイルの読み書き
from pathlib import Path  # パス操作のためのクラス

import app_paths

# 元の ZIP を置くフォルダ(データフォルダの中)
SOURCE_ZIPS_DIR = app_paths.DATA_DIR / "source_zips"


# 書き足した時間について、アプリが前の ZIP から作った結果(キャッシュ)のパスの一覧
#   hours ... 書き足した WIN ファイルの時間(その時間の開始の datetime)の集まり
#   output_base ... アプリの出力データのキャッシュ(既定は app_paths.OUTPUT_DIR)
#   揺れの大きさ(quake_rms/YYYYMMDD_*.npz。1日ごと)、1時間スタック波形の SAC(stack_YYYYMMDD_HH)、
#   指定期間の波形の SAC(YYYYMMDDHHMM_YYYYMMDDHHMM。期間がその時間にかかるもの)
def stale_caches(hours, output_base=None):
  base = Path(output_base or app_paths.OUTPUT_DIR)
  if not base.exists() or not hours:
    return []
  hour_keys = {f"{h:%Y%m%d%H}" for h in hours}
  paths = []
  if (base / "quake_rms").exists():
    for ymd in sorted({k[:8] for k in hour_keys}):
      paths += sorted((base / "quake_rms").glob(f"{ymd}_*.npz"))
  for p in sorted(base.iterdir()):
    if not p.is_dir():
      continue
    if p.name.startswith("stack_"):
      if p.name[6:].replace("_", "") in hour_keys:
        paths.append(p)
    else:
      parts = p.name.split("_")
      if len(parts) == 2 and all(len(x) == 12 and x.isdigit() for x in parts):
        # 期間の始めの時間から終わりの時間までのどれかが、書き足した時間なら古い
        if any(parts[0][:10] <= k <= parts[1][:10] for k in hour_keys):
          paths.append(p)
  return paths


# WIN ファイルの名前(例: 0970355225_2608232245.win)から、その時間の開始の datetime の集まり
#   名前から時間が読めなければ、その日のすべての時間(0〜23時)
def win_hours(base_filename, date_folder):
  try:
    return {datetime.strptime(Path(base_filename).stem.split("_")[-1][:8], "%y%m%d%H")}
  except ValueError:
    day = datetime.strptime(date_folder, "%y%m%d")
    return {day.replace(hour=h) for h in range(24)}


# 書き足した日の、消すキャッシュを表示する
#   appended ... {日(YYMMDD): 書き足した時間(datetime)の集まり}
def print_stale_caches(appended, output_base=None, dry_run=False):
  if not appended:
    return
  print(f"\n※ すでにあった日の ZIP に{'書き足すことになります' if dry_run else '書き足しました'}: {', '.join(sorted(appended))}")
  stale = stale_caches(set().union(*appended.values()), output_base)
  if stale:
    print("  アプリが前の ZIP から作った次の結果は、古いまま使われます。"
          f"{'書いたあとに、' if dry_run else ''}アプリを閉じて、次を消してください:")
    for p in stale:
      print(f"    {p}{'/' if p.is_dir() else ''}")
  else:
    print("  この日の、アプリが作った結果(揺れの大きさ・波形の SAC)はありません。")
  print("  地震の候補の検出は、この日を含めて探し直してください(前の候補の一覧は消さなくてかまいません)。\n"
        "  メンテナンス画面の「地震の候補の検出」で期間を入れ、「期間のキャッシュ…を消して作り直す」を選べば、消すのもまとめてできます。")


# エラーになった元の ZIP を、最後にもう一度表示する(途中のエラーは、長い一覧に埋もれて見落としやすいため)
def print_failed(failed):
  if not failed:
    return
  print(f"\n※ エラーで処理できなかった元の ZIP が {len(failed)} 個あります(この ZIP の WIN ファイルは入っていないか、途中までです):")
  for name, e in failed:
    print(f"    {name}: {e}")
  print("  直してから、もう一度実行してください(入っている WIN ファイルは二重に入りません)。")


def main():
  parser = argparse.ArgumentParser(description="拠点ごとの元の ZIP を、日ごとの ZIP(YYMMDD.zip)にまとめ直す")
  parser.add_argument("--source", type=Path, default=SOURCE_ZIPS_DIR,
                      help=f"元の ZIP のフォルダ(既定: {SOURCE_ZIPS_DIR})")
  parser.add_argument("--output", type=Path, default=app_paths.ZIPS_DIR,
                      help=f"日ごとの ZIP を書くフォルダ(既定: {app_paths.ZIPS_DIR})")
  parser.add_argument("--dry-run", action="store_true",
                      help="ZIP を書かずに、新しくできる日・前からある日に書き足す日と WIN ファイルの数を表示する")
  args = parser.parse_args()
  dry_run = args.dry_run
  SOURCE_DIR = args.source
  OUTPUT_DIR = args.output

  # 出力先のフォルダを作成
  if not dry_run:
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
  if dry_run:
    print(f"確認だけ(--dry-run): 合計 {len(zip_files)} 個のZipファイルを調べます。ZIP は書きません\n")
  else:
    print(
      f"合計 {len(zip_files)} 個のZipファイルを処理し、日単位のZipに再構築します...\n"
    )

  # 処理されたファイルの数を記録
  total_processed = 0
  # 実行の前からあった日の ZIP と、そこに書き足した日(YYMMDD)
  existed = {p.stem for p in output_path.glob("*.zip")}
  appended = {}
  # 日ごとの ZIP に入っている(入れる)WIN ファイルの名前(日ごとに1回だけ読む)と、日ごとに加える数
  daily_names = {}
  added = {}
  # 読めなかった・途中でエラーになった元の ZIP と、そのエラー(最後にもう一度表示する)
  failed = []

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

          # 日ごとの ZIP に入っている名前(初めてのときに読む)
          if date_folder not in daily_names:
            daily_names[date_folder] = set()
            if date_folder in existed:
              with zipfile.ZipFile(daily_zip_path, "r") as zd:
                daily_names[date_folder] = set(zd.namelist())
          # 重複書き込みを防ぐため、既にZip内に存在しない場合のみ追加
          if new_filename in daily_names[date_folder]:
            continue
          if not dry_run:
            # メモリ上でデータを読み込んで、日単位Zipを追記(append)モードで開いて書く。存在しなければ作成する
            file_data = zin.read(filename)
            with zipfile.ZipFile(daily_zip_path, "a", zipfile.ZIP_DEFLATED) as zout:
              zout.writestr(new_filename, file_data)
          daily_names[date_folder].add(new_filename)
          # 処理済みのファイル数を加算
          total_processed += 1
          added[date_folder] = added.get(date_folder, 0) + 1
          if date_folder in existed:
            appended.setdefault(date_folder, set()).update(win_hours(base_filename, date_folder))

    # エラー発生時の処理
    except Exception as e:
      print(f"  -> エラーが発生しました ({z_file.name}): {e}")
      failed.append((z_file.name, e))

  # 処理の出力
  new_days = sorted(d for d in added if d not in existed)
  if dry_run:
    print(f"\n確認だけ(--dry-run): 合計 {total_processed} 個の WIN ファイルを日ごとの ZIP に入れることになります(書いていません)。")
  else:
    print(
      f"\n完了しました！ 合計 {total_processed} 個のファイルを日単位Zipに整理しました。"
    )
  if new_days:
    print(f"新しくできる日: {len(new_days)} 日({new_days[0]}〜{new_days[-1]})")
    for d in new_days:
      print(f"  {d}.zip  {added[d]} 個")
  if appended:
    print(f"前からある日に書き足す: {len(appended)} 日")
    for d in sorted(appended):
      print(f"  {d}.zip  {added[d]} 個")
  if not added:
    print("新しく入れる WIN ファイルはありません(どれも日ごとの ZIP に入っています)。")
  print(f"保存先: {OUTPUT_DIR}")
  if dry_run:
    print("書くときは、--dry-run を付けずに実行してください")
  else:
    print("できた日ごとの ZIP は、Googleドライブのフォルダに保存してください(使う側は --download-zips で受け取ります)")
  print_stale_caches(appended, dry_run=dry_run)
  print_failed(failed)

if __name__ == "__main__":
  main()