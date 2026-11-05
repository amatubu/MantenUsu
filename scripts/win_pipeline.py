# win_pipeline.py
# WINファイルの抽出(日ごとのZIPから)・拠点ごとの結合・SACファイルへの変換
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from log_common import log_message

# --- 出力フォルダ内のファイルの種類(ファイル名の決まり) ---
WIN_SUFFIX = ".win"              # WINファイルの拡張子
COMBINED_PREFIX = "combined_"    # 拠点ごとに結合したWINファイル(combined_拠点ID.win)
CORRECTED_PREFIX = "corrected_"  # チャンネル番号を読み替えたWINファイル(corrected_拠点ID.win)
SAC_PATTERN = "*_sac*"           # SACファイル(拠点名.成分.拠点ID_sac)
CHUNK_DIR_PREFIX = "chunks_"     # 長い期間を分けて変換するときの作業フォルダ(chunks_拠点ID)
APPLEDOUBLE_PREFIX = "._"        # Mac が外付けディスクなどに作る付随ファイル(中身は WIN や ZIP ではない)
CHANNEL_TABLE = "manten_ch.tbl"  # チャンネル表(etc にあるもの。変換するときに出力先フォルダへコピーする)

# win2sac_32 は約8000秒(2時間13分20秒)より長いWINファイルを、何も出力せずに終える。
# そのため、これより長い期間はこの分数ずつに分けて変換し、あとでつなぐ(WINファイルは1分に1つ)
CHUNK_MINUTES = 120


# 処理中に必要なファイルが無いなど、続行できないときのエラー
class PipelineError(Exception):
  pass


# ZIPから取り出したままの(結合前の)WINファイルか
#   例: 1070432002_2608260858.win (拠点ID_年月日時分.win)
def is_raw_win(path):
  return (path.suffix == WIN_SUFFIX
          and not path.name.startswith((COMBINED_PREFIX, CORRECTED_PREFIX, APPLEDOUBLE_PREFIX)))

# フォルダ内のWINファイル(結合後などを含むすべて)
def find_wins(dest_dir):
  return list(dest_dir.glob(f"*{WIN_SUFFIX}"))

# フォルダ内の結合前のWINファイル(SACファイルを作り直すのに必要)
def find_raw_wins(dest_dir):
  return [p for p in find_wins(dest_dir) if is_raw_win(p)]

# フォルダ内のSACファイル
def find_sacs(dest_dir):
  return [p for p in dest_dir.glob(SAC_PATTERN) if not p.name.startswith(APPLEDOUBLE_PREFIX)]

# フォルダ内のSACファイルのうち、今のチャンネル表で変換したもの
#   チャンネル表の感度・ゲインが変わると振幅の換算が変わるので、変換したときにコピーしたチャンネル表と
#   etc のチャンネル表を比べ、違えば(コピーが無いときも)SACと途中のファイルを消して空のリストを返す
#   (結合前のWINファイルが残っていればそれから、無ければ ZIP から取り出し直して変換し直す)
def find_current_sacs(dest_dir, etc_dir):
  sacs = find_sacs(dest_dir)
  if not sacs:
    return sacs
  used_tbl = dest_dir / CHANNEL_TABLE
  if used_tbl.exists() and used_tbl.read_bytes() == (etc_dir / CHANNEL_TABLE).read_bytes():
    return sacs
  log_message(f"チャンネル表が変わったため、SACファイルを変換し直します: {dest_dir.name}")
  for path in sacs + [p for p in find_wins(dest_dir) if not is_raw_win(p)]:
    path.unlink(missing_ok=True)
  for chunk_dir in dest_dir.glob(f"{CHUNK_DIR_PREFIX}*"):
    shutil.rmtree(chunk_dir, ignore_errors=True)
  return []

# SACファイルを作ったあとの WINファイル(結合前・結合後・読み替え後)を消す(キャッシュの容量を減らすため)
#   WINファイルは SACファイルを作る途中のもので、表示などには SACファイルだけを使う。
#   1時間分で SAC 約33 MB に対して WIN が約30 MB(結合前と結合後で同じ大きさ)あり、消すと容量がほぼ半分になる
#   SACファイルを作り直すとき(チャンネル表が変わったときなど)は、ZIP から取り出し直す(1時間分で1〜2秒)
#   戻り値 ... 消したファイルの数
def remove_wins(dest_dir):
  removed = 0
  for path in find_wins(dest_dir):
    try:
      path.unlink()
      removed += 1
    except OSError:
      pass
  return removed

# 作業用のフォルダで作った SACファイルと、変換に使ったチャンネル表のコピーを dest_dir に移す(同じ名前は置き換える)
#   (チャンネル表のコピーは、あとで find_current_sacs がチャンネル表の変更を確かめるのに使う)
def move_results(work_dir, dest_dir):
  dest_dir.mkdir(parents=True, exist_ok=True)
  for path in find_sacs(work_dir) + [work_dir / CHANNEL_TABLE]:
    if path.exists():
      shutil.move(str(path), str(dest_dir / path.name))

# OSに合わせた外部ツールのパス(Windowsなら .exe を付ける)
def tool_path(tools_dir, name):
  return tools_dir / (f"{name}.exe" if sys.platform.startswith("win") else name)


# 期間に含まれる日付を、ZIPファイル名の形式(YYMMDD)の集合で返す
def target_dates(start_dt, end_dt):
  dates = set()
  # 開始・終了日時から日付部分だけを取得(時分秒、ミリ秒をすべて0にしたもの)
  curr = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
  # 終了日時が翌日0:00の場合などに翌日ZIPまで取得してしまうのを防ぐため、
  # 1秒巻き戻した時刻を基準に日付を切り出す
  adjusted_end_dt = end_dt - timedelta(seconds=1)
  end_date_only = adjusted_end_dt.replace(hour=0, minute=0, second=0, microsecond=0)
  # 終了日まで繰り返す
  while curr <= end_date_only:
    dates.add(curr.strftime("%y%m%d"))
    curr += timedelta(days=1)
  return dates

# 指定した日付(YYMMDD)のどれかがファイル名に含まれるZIPファイルを、サブフォルダも含めて探す
def find_zips(zips_root, dates):
  return [zip_path for zip_path in zips_root.glob("**/*.zip")
          if any(d in zip_path.stem for d in dates)
          and not zip_path.name.startswith(APPLEDOUBLE_PREFIX)]

# ZIP内のWINファイル名から日時を取り出す。WINファイルでない・形式が違うときは None
#   例: 1070432002/260826/1070432002_2608260858.win
#       拠点ID    /年月日/拠点ID    _年月日時分.win
def win_datetime(internal_path):
  p = Path(internal_path)
  if p.suffix != WIN_SUFFIX:
    return None
  # ファイル名の「_」の後ろが日時(年月日時分の10桁)
  parts = p.stem.split("_")
  if len(parts) < 2:
    return None
  datetime_str = parts[1]
  if len(datetime_str) != 10 or not datetime_str.isdigit():
    return None
  # 20を補ってYYYYMMDDHHmmとみなし日付・時間形式に変換
  try:
    return datetime.strptime(f"20{datetime_str}", "%Y%m%d%H%M")
  except ValueError:
    return None

# ZIPファイルから、期間内のWINファイルを dest_dir に取り出す
#   zip_paths        ... 対象のZIPファイルのリスト
#   start_dt, end_dt ... 期間(両端を含む)
#   戻り値            ... 取り出したWINファイルの数
def extract_wins_from_zips(zip_paths, start_dt, end_dt, dest_dir):
  dest_dir.mkdir(parents=True, exist_ok=True)
  extracted_count = 0
  for zip_path in zip_paths:
    try:
      with zipfile.ZipFile(zip_path, "r") as zf:
        for internal_path in zf.namelist():
          f_dt = win_datetime(internal_path)
          # WINファイルで、日時が期間内のものだけ取り出す
          if f_dt is None or not (start_dt <= f_dt <= end_dt):
            continue
          dest_file = dest_dir / Path(internal_path).name
          with zf.open(internal_path) as src, open(dest_file, "wb") as dst:
            dst.write(src.read())
          extracted_count += 1
    except Exception as e:
      # Zipファイルが開けなかったときはログに出して次へ
      log_message(f"ZIP読み込みエラー ({zip_path.name}): {e}")
  return extracted_count


# 結合前のWINファイルを拠点IDごとに集める
#   ファイル名の例: 1070432002_2608260858.win (拠点ID_年月日時分.win)
#   戻り値 ... {拠点ID: WINファイルのリスト(古い順)}
def group_raw_wins(dest_dir):
  station_files = defaultdict(list)
  for win_file in find_raw_wins(dest_dir):
    parts = win_file.name.split("_")
    if len(parts) >= 2:
      station_files[parts[0]].append(win_file)
  # ファイル名は日付・時刻で構成されているので、名前順で古いものから並ぶ
  return {sid: sorted(files, key=lambda x: x.name) for sid, files in station_files.items()}

# WINファイルを順につないで1つのファイルにする
def combine_wins(files, combined_win):
  with open(combined_win, "wb") as out:
    for f in files:
      with open(f, "rb") as rfd:
        out.write(rfd.read())

# 古いファイルなどでチャンネル番号の読み替えが必要な拠点は、wchch_32 で読み替える
#   変換ファイル(etc/拠点ID_conv.tbl)のフォーマット: 旧IDと新IDをタブで区切って記載
#     F010	8D04
#     F011	8D05
#   戻り値 ... 後続の変換に使うWINファイル(読み替え不要・失敗時は結合ファイルのまま)
def correct_channels(combined_win, station_id, tools_dir, etc_dir):
  conv_tbl_path = etc_dir / f"{station_id}_conv.tbl"
  wchch_path = tool_path(tools_dir, "wchch_32")
  if not conv_tbl_path.exists() or not wchch_path.exists():
    return combined_win

  corrected_win = combined_win.parent / f"{CORRECTED_PREFIX}{station_id}{WIN_SUFFIX}"
  # wchch_32 [ch conv table] < [in_file] > [out_file]
  with open(combined_win, "rb") as fin, open(corrected_win, "wb") as fout:
    res = subprocess.run(
      [str(wchch_path.resolve()), str(conv_tbl_path.resolve())],
      stdin=fin, stdout=fout, stderr=subprocess.PIPE)
  if res.returncode != 0:
    log_message(f"チャンネル変換エラー (Station: {station_id}): "
                f"{res.stderr.decode('utf-8', errors='ignore')}")
    return combined_win
  return corrected_win

# WINファイルを拠点ごとに結合し、SACファイルに変換する
#   dest_dir  ... WINファイルがあるフォルダ(SACファイルもここに出力)
#   tools_dir ... win2sac_32 などの外部ツールの格納場所
#   etc_dir   ... win.prm やチャンネル定義ファイルの格納場所
#   必要なツール・設定ファイルが無いときは PipelineError
def convert_wins_to_sacs(dest_dir, tools_dir, etc_dir):
  # win2sac_32(WINファイルをSACファイルに変換するツール)と、そのパラメータファイル
  win2sac_path = tool_path(tools_dir, "win2sac_32")
  win_prm_path = etc_dir / "win.prm"
  if not win2sac_path.exists() or not win_prm_path.exists():
    raise PipelineError("tools または etc 内に必要な実行ファイル/設定が不足しています。")

  # チャンネル定義ファイル(win.prm から参照される)を出力先フォルダにコピーする
  manten_tbl_path = etc_dir / CHANNEL_TABLE
  shutil.copy2(manten_tbl_path, dest_dir / manten_tbl_path.name)

  for station_id, files in group_raw_wins(dest_dir).items():
    if len(files) <= CHUNK_MINUTES:
      combined_win = dest_dir / f"{COMBINED_PREFIX}{station_id}{WIN_SUFFIX}"
      combine_wins(files, combined_win)
      target_win = correct_channels(combined_win, station_id, tools_dir, etc_dir)
      run_win2sac(win2sac_path, win_prm_path, target_win, station_id, f"{station_id}_sac", etc_dir)
    else:
      convert_in_chunks(win2sac_path, win_prm_path, files, station_id, dest_dir,
                        tools_dir, etc_dir, manten_tbl_path)

# win2sac_32 で1つのWINファイルを変換する(SACファイルはWINファイルと同じフォルダに出力)
#   sac_name ... 出力するSACファイル名の末尾(拠点名.成分.<sac_name> になる)
def run_win2sac(win2sac_path, win_prm_path, target_win, station_id, sac_name, etc_dir):
  # 拠点IDごとのチャンネルIDファイル。無いときは全チャンネルを記載したファイルを使用
  station_ch_path = etc_dir / f"{station_id}.ch"
  if not station_ch_path.exists():
    station_ch_path = etc_dir / "manten.ch"

  # usage: win2sac_32 winfile ch_no sacfile [outdir] [-p(prmfile)]
  #          [-Y] [-e] [-b[BIN]] [-a[ASC]] [-r(RATIO)] [-m(PMAX)]
  cmd = [
    str(win2sac_path.resolve()),     # win2sac_32 のフルパス
    target_win.name,                 # 結合された(読み替え後の)WINファイル
    str(station_ch_path.resolve()),  # チャンネル定義ファイルのフルパス
    sac_name,                        # 出力するSACファイル
    ".",                             # 出力先フォルダ(カレントディレクトリ)
    f"-p{win_prm_path.resolve()}",   # prmファイルのフルパス
  ]
  result = subprocess.run(cmd, cwd=target_win.parent,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  if result.returncode != 0:
    log_message(f"変換エラー (Station: {station_id}): "
                f"{result.stderr.decode('utf-8', errors='ignore')}")

# 長い期間を CHUNK_MINUTES 分ずつ作業フォルダで変換し、成分ごとに1つのSACファイルにつなぐ
#   出力するSACファイルの名前は、分けずに変換したときと同じ(拠点名.成分.拠点ID_sac)
def convert_in_chunks(win2sac_path, win_prm_path, files, station_id, dest_dir,
                      tools_dir, etc_dir, manten_tbl_path):
  import obspy  # 長い期間のときだけ必要なので、ここで読み込む

  work_dir = dest_dir / f"{CHUNK_DIR_PREFIX}{station_id}"
  shutil.rmtree(work_dir, ignore_errors=True)
  work_dir.mkdir()
  shutil.copy2(manten_tbl_path, work_dir / manten_tbl_path.name)
  try:
    for i in range(0, len(files), CHUNK_MINUTES):
      combined_win = work_dir / f"{COMBINED_PREFIX}{station_id}{WIN_SUFFIX}"
      combine_wins(files[i:i + CHUNK_MINUTES], combined_win)
      target_win = correct_channels(combined_win, station_id, tools_dir, etc_dir)
      run_win2sac(win2sac_path, win_prm_path, target_win, station_id,
                  f"{station_id}_sac{i // CHUNK_MINUTES:03d}", etc_dir)

    st = obspy.Stream()
    for sac in sorted(find_sacs(work_dir)):
      st += obspy.read(str(sac))
    # つなぎ目や欠測は0で埋める(SACファイルは欠測を持てないため)
    st.merge(method=1, fill_value=0)
    for tr in st:
      tr.write(str(dest_dir / f"{tr.stats.station}.{tr.stats.channel}.{station_id}_sac"), format="SAC")
  finally:
    shutil.rmtree(work_dir, ignore_errors=True)
