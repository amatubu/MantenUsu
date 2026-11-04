# pack_data.py
# 別のマシンに移すときに一緒にコピーするとよいデータ(Git の管理外のもの)を1つの zip にまとめる(--unpack で移す先に展開する)
#   アプリ本体のビルド(build_mac.sh・build_win.bat)とは別に使う。pack_data_mac.sh・pack_data_win.bat から呼ぶ
#   zip の中は、ユーザーの設定(user/)とデータフォルダ(data/)に分けて入れる(場所は app_paths.py)
#
# 使い方: python3 scripts/pack_data.py [--private] [--with-zips] [-o 出力先の zip]
#   --private   ... 再配布できないもの(洞爺駅の時刻表、防災科研・気象庁の波形の ZIP)も入れる
#                   自分のバックアップや、自分で別のマシンに環境を作るとき用。人には渡さない
#                   (ファイル名に -private を付け、UsuMantenViewer-data-private_YYYYMMDD.zip にする)
#   --with-zips ... daily_zips(観測の日ごとの ZIP)も入れる。とても大きい(数十 GB)ので、
#                   ふだんは入れず、移す先で Googleドライブから必要な日だけダウンロードする
#   出力先を省略すると、データフォルダに UsuMantenViewer-data_YYYYMMDD.zip を作る
#
#         python3 scripts/pack_data.py --unpack まとめた zip [--overwrite]
#   移す先のマシンで、zip の中身をユーザーの設定とデータフォルダに展開する(前の版で作った zip も読める)
#   同じ名前のファイルがあるときは残す(--overwrite を付けると上書きする)
#
# 入れないもの
#   output の期間ごと・1時間ごとのフォルダ(WIN・SAC) ... daily_zips から作り直せる。大きい
#   output/vnet ... v-net の ZIP から作り直せる
#   batch_plots の画像 ... 結果の画像。必要ならそのままコピーする(候補の一覧の CSV だけは入れる)
#   service_account ... Googleドライブの鍵。人に渡る zip には入れず、別に移す(メンテナンス画面の「鍵ファイルを取り込む」)
#   (--private を付けないとき)時刻表・v-net の ZIP ... 再配布できない(PRIVATE_ITEMS を参照)
#   tools・etc の初期値 ... Git やアプリのビルドに含まれる
#   ユーザーのキャッシュフォルダ(走時・地図のタイル・フォント一覧) ... 移す先で自動で作り直す
import argparse
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import app_paths

# zip の中の最初のフォルダと、展開する先
BASES = {"user": app_paths.USER_DIR, "data": app_paths.DATA_DIR}

# まとめるもの: (user か data, そこからのパス, ファイル名のパターン, 説明)
#   作り直せないもの(手で直した到着時刻・設定・手で用意したファイル)と、作り直すのに時間がかかるもの
ITEMS = [
  ("user", "picks", "**/*", "震源推定の画面で手で直した到着時刻(作り直せない)"),
  ("user", "", "history.json", "実行履歴"),
  ("user", "", "ps_picker_params.json", "P波・S波検出の画面の設定"),
  ("user", "", "hypocenter_params.json", "震源推定の画面の設定"),
  ("user", "etc", "*.tbl", "etc の上書き(チャンネル表)"),
  ("user", "etc", "*.ch", "etc の上書き(チャンネル)"),
  ("user", "etc", "*.prm", "etc の上書き(win.prm)"),
  ("user", "etc", "external_stations.txt", "etc の上書き(ほかの機関の観測点)"),
  ("data", "output", "picks_*/**/*", "前の版の到着時刻のバックアップ(picks_ で始まるフォルダ)"),
  ("data", "output/quake_rms", "*.npz", "地震の候補の検出に使う揺れの大きさ(RMS)。作り直すと1日あたり数十秒〜数分"),
  ("data", "output/quake_features", "*.json", "地震の候補ごとの波形の解析結果"),
  ("data", "jma_hypocenter", "*.csv", "気象庁の震源リスト(取り直すと1日あたり2秒以上)"),
  ("data", "batch_plots/quakes", "*.csv", "地震の候補の一覧・見逃し?の一覧"),
]
# 再配布できないもの(--private を付けたときだけ入れる)
#   時刻表 ... JR の時刻表から手で作ったもの
#   v-net の ZIP ... 防災科研のページからダウンロードした、防災科研・気象庁の観測データ
PRIVATE_ITEMS = [
  ("user", "etc", "train_timetable.csv", "洞爺駅の時刻表(手で作ったもの。再配布不可)"),
  ("data", "v-net", "*.zip", "ほかの機関の観測点の波形(防災科研のページからダウンロードしたもの。再配布不可)"),
]
ZIPS_ITEM = ("data", "daily_zips", "**/*.zip", "観測の日ごとの ZIP(Googleドライブからもダウンロードできる)")
# すでに圧縮されているファイル(圧縮し直しても小さくならないので、そのまま入れて時間を節約する)
STORED_SUFFIXES = {".zip", ".npz"}
# 前の版の zip(プロジェクトのフォルダからの相対パス)で、ユーザーの設定に移ったもの
LEGACY_TO_USER = {"output/picks/": "picks/", "output/history.json": "history.json",
                  "etc/ps_picker_params.json": "ps_picker_params.json",
                  "etc/hypocenter_params.json": "hypocenter_params.json",
                  "etc/train_timetable.csv": "etc/train_timetable.csv"}


# パターンに合うファイル(macOS の ._ ファイルや .DS_Store は除く)
def collect(base, folder, pattern):
  root = BASES[base] / folder
  if not root.exists():
    return []
  return sorted(p for p in root.glob(pattern)
                if p.is_file() and not p.name.startswith("._") and p.name != ".DS_Store")

# zip の中の名前を、展開する先のパスにする(前の版の zip の名前も読む)
def unpack_target(name):
  base, _, rest = name.partition("/")
  if base in BASES and rest:
    return BASES[base] / rest
  for old, new in LEGACY_TO_USER.items():
    if name == old or (old.endswith("/") and name.startswith(old)):
      return app_paths.USER_DIR / (new + name[len(old):] if old.endswith("/") else new)
  return app_paths.DATA_DIR / name

def unpack(zip_path, overwrite):
  written = skipped = 0
  with zipfile.ZipFile(zip_path) as z:
    for info in z.infolist():
      if info.is_dir() or Path(info.filename).name.startswith("._"):
        continue
      target = unpack_target(info.filename)
      if target.exists() and not overwrite:
        skipped += 1
        continue
      target.parent.mkdir(parents=True, exist_ok=True)
      with z.open(info) as src, open(target, "wb") as dst:
        dst.write(src.read())
      written += 1
  print(f"展開しました: {written:,} 件(同じ名前があって残したもの {skipped:,} 件)")
  print(f"  ユーザーの設定: {app_paths.USER_DIR}")
  print(f"  データフォルダ: {app_paths.DATA_DIR}")
  return 0

def format_size(n):
  for unit in ("B", "KB", "MB", "GB"):
    if n < 1024:
      return f"{n:.1f} {unit}"
    n /= 1024
  return f"{n:.1f} TB"

def main():
  parser = argparse.ArgumentParser(description="別のマシンに移すデータを zip にまとめる")
  parser.add_argument("--private", action="store_true",
                      help="再配布できないもの(時刻表・v-net の ZIP)も入れる(自分のバックアップ・環境の移行用)")
  parser.add_argument("--with-zips", action="store_true", help="daily_zips(とても大きい)も入れる")
  parser.add_argument("-o", "--output", help="出力先の zip(省略するとデータフォルダに作る)")
  parser.add_argument("--unpack", metavar="ZIP", help="まとめた zip を、ユーザーの設定とデータフォルダに展開する")
  parser.add_argument("--overwrite", action="store_true", help="--unpack で、同じ名前のファイルを上書きする")
  args = parser.parse_args()
  if args.unpack:
    return unpack(Path(args.unpack), args.overwrite)

  items = ITEMS + (PRIVATE_ITEMS if args.private else []) + ([ZIPS_ITEM] if args.with_zips else [])
  kind = "data-private" if args.private else "data"
  out = app_paths.DATA_DIR / f"UsuMantenViewer-{kind}_{datetime.now():%Y%m%d}.zip" if not args.output else Path(args.output)
  print(f"ユーザーの設定: {app_paths.USER_DIR}")
  print(f"データフォルダ: {app_paths.DATA_DIR}")
  plan, seen = [], set()
  for base, folder, pattern, desc in items:
    files = [p for p in collect(base, folder, pattern) if p not in seen]
    seen.update(files)
    size = sum(p.stat().st_size for p in files)
    label = "/".join(x for x in (base, folder, pattern) if x)
    print(f"  {label:42s} {len(files):6,} 件 {format_size(size):>10s}  {desc}")
    plan += [(base, p) for p in files]
  if not plan:
    print("まとめるファイルがありません")
    return 1

  total = sum(p.stat().st_size for _base, p in plan)
  print(f"合計 {len(plan):,} 件 {format_size(total)} → {out}")
  tmp = out.with_name(out.name + ".part")
  with zipfile.ZipFile(tmp, "w") as z:
    for i, (base, path) in enumerate(plan, 1):
      method = zipfile.ZIP_STORED if path.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
      z.write(path, f"{base}/{path.relative_to(BASES[base]).as_posix()}", compress_type=method)
      if i % 500 == 0 or i == len(plan):
        print(f"  {i:,} / {len(plan):,} 件", flush=True)
  tmp.replace(out)
  print(f"できました: {out}({format_size(out.stat().st_size)})")
  print("移す先のマシンで pack_data_mac.sh(Windows は pack_data_win.bat)に --unpack と zip を付けて展開してください。")
  print("service_account(Googleドライブの鍵)は入れていないので、必要なら別に移してください。")
  if args.private:
    print("再配布できないもの(時刻表・v-net の ZIP)を含むので、人には渡さないでください。")
  else:
    print("時刻表・v-net の ZIP(再配布不可)は入れていません。自分用に入れるときは --private を付けます。")
  return 0

if __name__ == "__main__":
  sys.exit(main())
