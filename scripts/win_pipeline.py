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


# 処理中に必要なファイルが無いなど、続行できないときのエラー
class PipelineError(Exception):
  pass


# ZIPから取り出したままの(結合前の)WINファイルか
#   例: 1070432002_2608260858.win (拠点ID_年月日時分.win)
def is_raw_win(path):
  return (path.suffix == WIN_SUFFIX
          and not path.name.startswith((COMBINED_PREFIX, CORRECTED_PREFIX)))

# フォルダ内のWINファイル(結合後などを含むすべて)
def find_wins(dest_dir):
  return list(dest_dir.glob(f"*{WIN_SUFFIX}"))

# フォルダ内のSACファイル
def find_sacs(dest_dir):
  return list(dest_dir.glob(SAC_PATTERN))

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
          if any(d in zip_path.stem for d in dates)]

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


# 結合前のWINファイルを、拠点IDごとに結合する
#   戻り値 ... {拠点ID: 結合後のWINファイルのパス}
def combine_wins(dest_dir):
  # 拠点IDごとに結合前のWINファイルを集める
  #   ファイル名の例: 1070432002_2608260858.win (拠点ID_年月日時分.win)
  station_files = defaultdict(list)
  for win_file in find_wins(dest_dir):
    if not is_raw_win(win_file):
      continue
    parts = win_file.name.split("_")
    if len(parts) >= 2:
      station_files[parts[0]].append(win_file)

  combined = {}
  for station_id, files in station_files.items():
    # ファイル名は日付・時刻で構成されているので、名前順で古いものから並ぶ
    combined_win = dest_dir / f"{COMBINED_PREFIX}{station_id}{WIN_SUFFIX}"
    with open(combined_win, "wb") as out:
      for f in sorted(files, key=lambda x: x.name):
        with open(f, "rb") as rfd:
          out.write(rfd.read())
    combined[station_id] = combined_win
  return combined

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
  manten_tbl_path = etc_dir / "manten_ch.tbl"
  shutil.copy2(manten_tbl_path, dest_dir / manten_tbl_path.name)

  for station_id, combined_win in combine_wins(dest_dir).items():
    target_win = correct_channels(combined_win, station_id, tools_dir, etc_dir)

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
        f"{station_id}_sac",             # 出力するSACファイル
        ".",                             # 出力先フォルダ(カレントディレクトリ)
        f"-p{win_prm_path.resolve()}",   # prmファイルのフルパス
    ]
    result = subprocess.run(cmd, cwd=dest_dir,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
      log_message(f"変換エラー (Station: {station_id}): "
                  f"{result.stderr.decode('utf-8', errors='ignore')}")
