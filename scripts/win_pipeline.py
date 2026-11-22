# win_pipeline.py
# WINファイルの抽出(日ごとのZIPから)・拠点ごとの結合・SACファイルへの変換
from bisect import bisect_left, bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import shutil
import subprocess
import sys
import threading
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
#   ファイルの数ではなく時刻で分ける(欠測があると、120個のファイルが8000秒を超える時間にまたがるため)
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

# days(datetime の日のリスト)のうち、その日の ZIP(find_zips と同じく、名前に年月日 yymmdd を含むもの)があるもの
#   データの無い日を一つずつ調べずに飛ばすために使う
def days_with_zips(zips_root, days):
  stems = [p.stem for p in Path(zips_root).glob("**/*.zip") if not p.name.startswith(APPLEDOUBLE_PREFIX)]
  return [d for d in days if any(d.strftime("%y%m%d") in s for s in stems)]

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


# ZIPの中のWINファイルの一覧(日時の順)。ZIPごとに覚えておき、同じZIPは読み直さない
#   1時間ずつ変換するとき(地震の候補の検出など)、同じ日のZIP(5760個のファイル)の名前を毎回調べ直さないため
#   ZIPが書き換えられたとき(更新日時・大きさが変わったとき)は読み直す
#   戻り値 ... ([日時, ...], [ZIP内のパス, ...])
ZIP_INDEX_CACHE_SIZE = 8   # 覚えておくZIPの数(古いものから忘れる)
_zip_index_cache = {}
_zip_index_lock = threading.Lock()

def zip_index(zip_path):
  st = zip_path.stat()
  key = (str(zip_path), st.st_mtime_ns, st.st_size)
  with _zip_index_lock:
    index = _zip_index_cache.get(key)
  if index is not None:
    return index
  entries = []
  with zipfile.ZipFile(zip_path, "r") as zf:
    for internal_path in zf.namelist():
      if Path(internal_path).name.startswith(APPLEDOUBLE_PREFIX):
        continue
      f_dt = win_datetime(internal_path)
      if f_dt is not None:
        entries.append((f_dt, internal_path))
  entries.sort()
  index = ([dt for dt, _ in entries], [name for _, name in entries])
  with _zip_index_lock:
    for old in [k for k in _zip_index_cache if k[0] == key[0]]:
      del _zip_index_cache[old]
    while len(_zip_index_cache) >= ZIP_INDEX_CACHE_SIZE:
      del _zip_index_cache[next(iter(_zip_index_cache))]
    _zip_index_cache[key] = index
  return index

# ZIPの中の、期間内のWINファイルを拠点IDごとに集める(取り出さない)
#   同じ名前のファイルが複数のZIPにあるときは、後のZIPのものを使う(取り出して上書きしたときと同じ)
#   戻り値 ... {拠点ID: [(日時, ZIPのパス, ZIP内のパス), ...](名前の順 = 古い順)}
def zip_wins(zip_paths, start_dt, end_dt):
  by_name = {}
  for zip_path in zip_paths:
    try:
      dts, names = zip_index(zip_path)
    except Exception as e:
      log_message(f"ZIP読み込みエラー ({zip_path.name}): {e}")
      continue
    for i in range(bisect_left(dts, start_dt), bisect_right(dts, end_dt)):
      by_name[Path(names[i]).name] = (dts[i], zip_path, names[i])
  station_items = defaultdict(list)
  for name in sorted(by_name):
    parts = name.split("_")
    if len(parts) >= 2:
      station_items[parts[0]].append(by_name[name])
  return dict(station_items)

# ZIPの中の期間内のWINファイルを、取り出さずに拠点ごとにつないで SACファイルに変換する(SACファイルは dest_dir に出力)
#   1分ごとのWINファイルを書き出さないので速い(Mac では、書いたばかりのファイルを初めて開くのに 4〜5 ms かかり、
#   1時間分(4拠点で約250個)を書き出してからつなぐと、それだけで1秒ほどかかるため)
#   必要なツール・設定ファイルが無いときは PipelineError
#   戻り値 ... 使ったWINファイルの数(0 のときは何もしない)
def zips_to_sacs(zip_paths, start_dt, end_dt, dest_dir, tools_dir, etc_dir):
  station_items = zip_wins(zip_paths, start_dt, end_dt)
  count = sum(len(items) for items in station_items.values())
  if count == 0:
    return 0
  dest_dir.mkdir(parents=True, exist_ok=True)
  # ZIPは1回だけ開き、拠点ごとの処理(別スレッド)で使い回す(読むときは ZIPごとの鍵をかける)
  zip_files, locks = {}, {}
  try:
    for items in station_items.values():
      for _dt, zip_path, _name in items:
        if zip_path not in zip_files:
          zip_files[zip_path] = zipfile.ZipFile(zip_path, "r")
          locks[zip_path] = threading.Lock()

    def write_combined(items, combined_win):
      with open(combined_win, "wb") as out:
        for _dt, zip_path, internal_path in items:
          with locks[zip_path]:
            data = zip_files[zip_path].read(internal_path)
          out.write(data)

    convert_stations(station_items, write_combined, lambda item: item[0], dest_dir, tools_dir, etc_dir)
  finally:
    for zf in zip_files.values():
      zf.close()
  return count

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
    res = spawn_run(
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
  convert_stations(group_raw_wins(dest_dir), combine_wins, lambda f: win_datetime(f.name),
                   dest_dir, tools_dir, etc_dir)

# 拠点ごとに WINファイルをつないで SACファイルに変換する(拠点ごとに別スレッドで、同時に変換する)
#   station_items  ... {拠点ID: [WINファイル(またはZIPの中のWINファイル), ...](古い順)}
#   write_combined ... write_combined(WINファイルのリスト, 出力するファイル) で、つないだWINファイルを書く関数
#   when           ... WINファイルの日時を返す関数(time_chunks で分けるのに使う)
def convert_stations(station_items, write_combined, when, dest_dir, tools_dir, etc_dir):
  # win2sac_32(WINファイルをSACファイルに変換するツール)と、そのパラメータファイル
  win2sac_path = tool_path(tools_dir, "win2sac_32")
  win_prm_path = etc_dir / "win.prm"
  if not win2sac_path.exists() or not win_prm_path.exists():
    raise PipelineError("tools または etc 内に必要な実行ファイル/設定が不足しています。")

  # チャンネル定義ファイル(win.prm から参照される)を出力先フォルダにコピーする
  manten_tbl_path = etc_dir / CHANNEL_TABLE
  shutil.copy2(manten_tbl_path, dest_dir / manten_tbl_path.name)

  def convert_station(station_id, items):
    chunks = time_chunks(items, when)
    if len(chunks) == 1:
      combined_win = dest_dir / f"{COMBINED_PREFIX}{station_id}{WIN_SUFFIX}"
      write_combined(items, combined_win)
      target_win = correct_channels(combined_win, station_id, tools_dir, etc_dir)
      run_win2sac(win2sac_path, win_prm_path, target_win, station_id, f"{station_id}_sac", etc_dir)
    else:
      convert_in_chunks(win2sac_path, win_prm_path, chunks, station_id, dest_dir,
                        tools_dir, etc_dir, manten_tbl_path, write_combined)

  # 外部ツールを待つ時間がほとんどなので、スレッドで拠点の数だけ同時に動かす
  if not station_items:
    return
  with ThreadPoolExecutor(max_workers=len(station_items)) as pool:
    futures = [pool.submit(convert_station, sid, items) for sid, items in station_items.items()]
    for future in futures:
      future.result()   # 拠点の処理で起きたエラーはここで出す

# WINファイル(古い順)を、最初のファイルの時刻から CHUNK_MINUTES 分ずつの組に分ける
#   when   ... WINファイルの日時を返す関数(省略するとファイル名から求める)
#   戻り値 ... [[WINファイル, ...], ...](どの組も CHUNK_MINUTES 分より短い時間に収まる)
def time_chunks(files, when=lambda f: win_datetime(f.name)):
  chunks, chunk_start = [], None
  for f in files:
    t = when(f)
    if not chunks or t is None or chunk_start is None or (t - chunk_start).total_seconds() >= CHUNK_MINUTES * 60:
      chunks.append([])
      chunk_start = t
    chunks[-1].append(f)
  return chunks

# 外部ツールを fork せずに起動する(posix_spawn)
#   画面(Tk)を使っている間に fork すると、macOS が「The process has forked and you cannot use this
#   CoreFoundation functionality safely」と出す。subprocess は close_fds=True(既定)や cwd を渡したときは
#   macOS では fork するので、close_fds=False にし、コマンドは絶対パスで渡し、cwd は使わない
#   (Python が開くファイルは既定で子に引き継がれないので、close_fds=False でも安全。cf_card.spawn_run と同じ)
def spawn_run(cmd, **kw):
  return subprocess.run(cmd, close_fds=False, **kw)

# win2sac_32 を動かす(SACファイルは WINファイルと同じフォルダに出力)
#   win.prm の2行目のチャンネル表は、win2sac_32 を動かすフォルダからの相対パス(manten_ch.tbl)なので、
#   その行を絶対パスにした prm を一時的に作り、フォルダを移らずに(fork せずに)動かす
#   チャンネル表のパスに空白があるとき(prm の中で区切られてしまう)と Windows では、前のとおりフォルダを移って動かす
#   win_file ... 変換する WINファイル、ch_file ... チャンネルIDファイル、sac_name ... 出力するSACファイル名の末尾
#   戻り値 ... subprocess.CompletedProcess(stdout・stderr はバイト列)
# usage: win2sac_32 winfile ch_no sacfile [outdir] [-p(prmfile)]
#          [-Y] [-e] [-b[BIN]] [-a[ASC]] [-r(RATIO)] [-m(PMAX)]
def run_win2sac_tool(win2sac_path, win_file, ch_file, sac_name, prm_path):
  work = win_file.parent.resolve()
  lines = prm_path.read_text().split("\n")
  tbl = (work / lines[1].strip()) if len(lines) > 1 and lines[1].strip() else None
  if sys.platform == "win32" or tbl is None or any(c.isspace() for c in str(tbl)):
    return subprocess.run([str(win2sac_path.resolve()), win_file.name, str(ch_file.resolve()), sac_name, ".",
                           f"-p{prm_path.resolve()}"],
                          cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  lines[1] = str(tbl)
  tmp_prm = work / f"{sac_name}.prm"   # 拠点ごと・区切りごとに別の名前(同時に動かすため)
  tmp_prm.write_text("\n".join(lines))
  try:
    return spawn_run([str(win2sac_path.resolve()),   # win2sac_32 のフルパス
                      str(win_file.resolve()),       # 結合された(読み替え後の)WINファイル
                      str(ch_file.resolve()),        # チャンネル定義ファイルのフルパス
                      sac_name,                      # 出力するSACファイル
                      str(work),                     # 出力先フォルダ
                      f"-p{tmp_prm}"],               # prmファイル(チャンネル表を絶対パスにしたもの)
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  finally:
    tmp_prm.unlink(missing_ok=True)

# win2sac_32 で1つのWINファイルを変換する(SACファイルはWINファイルと同じフォルダに出力)
#   sac_name ... 出力するSACファイル名の末尾(拠点名.成分.<sac_name> になる)
def run_win2sac(win2sac_path, win_prm_path, target_win, station_id, sac_name, etc_dir):
  # 拠点IDごとのチャンネルIDファイル。無いときは全チャンネルを記載したファイルを使用
  station_ch_path = etc_dir / f"{station_id}.ch"
  if not station_ch_path.exists():
    station_ch_path = etc_dir / "manten.ch"

  result = run_win2sac_tool(win2sac_path, target_win, station_ch_path, sac_name, win_prm_path)
  # 点の数が上限を超えたときなどは、終了コード 0 のまま ERROR を出して何も出力しない
  if result.returncode != 0 or b"ERROR" in result.stderr:
    log_message(f"変換エラー (Station: {station_id}): "
                f"{result.stderr.decode('utf-8', errors='ignore')}")

# 長い期間を CHUNK_MINUTES 分ずつ作業フォルダで変換し、成分ごとに1つのSACファイルにつなぐ
#   chunks ... time_chunks で分けたWINファイルの組
#   出力するSACファイルの名前は、分けずに変換したときと同じ(拠点名.成分.拠点ID_sac)
#   write_combined ... つないだWINファイルを書く関数(省略するとファイルをつなぐ。convert_stations を参照)
def convert_in_chunks(win2sac_path, win_prm_path, chunks, station_id, dest_dir,
                      tools_dir, etc_dir, manten_tbl_path, write_combined=None):
  write_combined = write_combined or combine_wins
  import obspy  # 長い期間のときだけ必要なので、ここで読み込む

  work_dir = dest_dir / f"{CHUNK_DIR_PREFIX}{station_id}"
  shutil.rmtree(work_dir, ignore_errors=True)
  work_dir.mkdir()
  shutil.copy2(manten_tbl_path, work_dir / manten_tbl_path.name)
  try:
    for i, chunk in enumerate(chunks):
      combined_win = work_dir / f"{COMBINED_PREFIX}{station_id}{WIN_SUFFIX}"
      write_combined(chunk, combined_win)
      target_win = correct_channels(combined_win, station_id, tools_dir, etc_dir)
      run_win2sac(win2sac_path, win_prm_path, target_win, station_id,
                  f"{station_id}_sac{i:03d}", etc_dir)

    st = obspy.Stream()
    for sac in sorted(find_sacs(work_dir)):
      st += obspy.read(str(sac))
    # つなぎ目や欠測は0で埋める(SACファイルは欠測を持てないため)
    st.merge(method=1, fill_value=0)
    for tr in st:
      tr.write(str(dest_dir / f"{tr.stats.station}.{tr.stats.channel}.{station_id}_sac"), format="SAC")
  finally:
    shutil.rmtree(work_dir, ignore_errors=True)
