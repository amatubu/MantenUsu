# external_stations.py
# 満点地震計の4拠点に加えて使う、ほかの機関(気象庁・防災科研)の観測点の波形を読み込む(TODO 21)
#   観測点は etc/external_stations.txt に書く(画面で使う名前、WIN の観測点コード、緯度・経度・標高)
#   波形は、防災科研のページなどからダウンロードした ZIP を v-net フォルダにそのまま置く
#     ZIP の名前 ... <網の番号>_<開始 YYYYMMDDHHMM>_<分数>.zip(例: 03_02_07_202607081119_6.zip)
#     中身       ... 1分ごとの WIN32 形式のファイル(.cnt)と、チャンネル表(.euc.ch)
#   ZIP ごとに、使う観測点の上下動・水平動だけを SAC に変換し、output/vnet/<ZIPの名前> に保存して再利用する
#   (1分ごとのファイルを1つにつなぎ、win2sac_32 で変換する。win2sac_32 は満点地震計の変換と同じ tools のもの)
#   防災科研・気象庁のデータなので、v-net フォルダは Git の管理外にしている
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta

import obspy

import app_paths
import cache_manager
from win_pipeline import tool_path
from log_common import log_message

CONFIG_FILE = app_paths.ETC_DIR / "external_stations.txt"
DATA_DIR = app_paths.VNET_DIR
CACHE_DIR = app_paths.OUTPUT_DIR / "vnet"
# ZIP の名前(開始の日時と分数)
ZIP_RE = re.compile(r"_(\d{12})_(\d+)\.zip$")
# 使う成分と、チャンネル表の単位(加速度計(m/s/s)や、広帯域の別のチャンネル(wU など)は使わない)
COMPONENTS = ("U", "N", "E")
VELOCITY_UNIT = "m/s"
# WIN32 形式のファイルのヘッダーの長さ
WIN32_HEADER_SIZE = 4
# 防災科研のページで連続波形画像(1時間ごと。1段が1分)を見られる、有珠山の観測点
#   1時間スタックの画面のリンクと、外部の観測点で確かめる一覧(quake_loose.py)で使う
#   (WIN の観測点コード, 機関の番号, 網の番号, 地域の番号, 画面に出す説明)
#   気象庁の V.USHU(東有珠大平通)は傾斜計だけで、地震計の画像が無いので入れない
IMAGE_STATIONS = [
  ("N.USSV", "01", "05", "010503", "防災科研 有珠山壮瞥"),
  ("N.USOV", "01", "05", "010503", "防災科研 有珠山大平(地中)"),
  ("N.USIV", "01", "05", "010503", "防災科研 有珠山泉の沢"),
  ("V.USSW", "03", "02", "030207", "気象庁 昭和新山南麓"),
  ("V.USUA", "03", "02", "030207", "気象庁 有珠南山麓"),
  ("V.KBBY", "03", "02", "030207", "気象庁 北屏風山"),
  ("V.ATIK", "03", "02", "030207", "気象庁 虻田泉北"),
]
IMAGE_URL = ("https://www.vnet.bosai.go.jp/strace/view.php?orgid={org}&netid={net}&stcd={code}"
             "&tm={t:%Y%m%d%H}&comp={comp}&type=1H&area={area}")


# 観測点の一覧。戻り値 ... {画面で使う名前: {"code": WIN の観測点コード, "coords": (緯度, 経度, 標高[m])}}
def load_config(path=CONFIG_FILE):
  stations = {}
  if not path.exists():
    return stations
  for line in path.read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if not line:
      continue
    name, code, lat, lon, elev = line.split()[:5]
    stations[name] = {"code": code, "coords": (float(lat), float(lon), float(elev))}
  return stations

# 観測点の位置。戻り値 ... {画面で使う名前: (緯度, 経度, 標高[m])}(hypocenter.load_stations と同じ形)
def coords(path=CONFIG_FILE):
  return {name: st["coords"] for name, st in load_config(path).items()}

# 連続波形画像のページの URL(時刻は日本時間。その時刻を含む1時間の画像)
#   code ... WIN の観測点コード(IMAGE_STATIONS のもの) / t ... 日時(datetime) / comp ... 成分(U・N・E)
def image_url(code, t, comp="U"):
  for c, org, net, area, _desc in IMAGE_STATIONS:
    if c == code:
      return IMAGE_URL.format(org=org, net=net, code=code, t=t, comp=comp, area=area)
  raise ValueError(f"連続波形画像の観測点にありません: {code}")

# v-net フォルダの ZIP と、その期間。戻り値 ... [(ZIP のパス, 開始, 終了), ...]
def data_files():
  files = []
  if not DATA_DIR.exists():
    return files
  for path in sorted(DATA_DIR.glob("*.zip")):
    m = ZIP_RE.search(path.name)
    if m and not path.name.startswith("._"):
      start = datetime.strptime(m.group(1), "%Y%m%d%H%M")
      files.append((path, start, start + timedelta(minutes=int(m.group(2)))))
  return files

# ZIP の中のファイル名から、フォルダを除いた名前
def _base_name(name):
  return name.rsplit("/", 1)[-1]

# チャンネル表から、観測点の上下動・水平動のチャンネル番号を選ぶ。戻り値 ... {成分: チャンネル番号}
def _channels(ch_text, code):
  found = {}
  for line in ch_text.splitlines():
    cols = line.split()
    if len(cols) < 9 or line.startswith("#"):
      continue
    ch, station, comp, unit = cols[0], cols[3], cols[4], cols[8]
    if station == code and comp in COMPONENTS and unit == VELOCITY_UNIT and comp not in found:
      found[comp] = ch.upper()
  return found

# ZIP を SAC に変換する(変換済みならそれを使う)。戻り値 ... 変換結果のフォルダ(失敗したら None)
#   SAC ファイルの名前 ... <画面で使う名前>.<成分>.sac
#   変換した観測点は DONE_FILE に書いておく(その ZIP に無い観測点(別の網の ZIP など)も、変換し直さないため)
DONE_FILE = "converted.txt"
#   log ... メッセージを受け取る関数(メンテナンス画面からは別スレッドで呼ぶため、画面側で表示する)
# WIN32 形式のファイルを時刻の順に1つにつなぐ
#   WIN32 形式は、先頭4バイトのファイルのヘッダーのあとに1秒ごとのブロックが続くので、
#   2つ目以降はヘッダーを除いてつなげばよい(catwin32 の出力と同じ。Windows 用の catwin32 は用意していない)
def join_win32(blobs, out_path):
  with open(out_path, "wb") as f:
    for i, data in enumerate(blobs):
      f.write(data if i == 0 else data[WIN32_HEADER_SIZE:])

def _convert(zip_path, stations, log=log_message):
  out_dir = CACHE_DIR / zip_path.stem
  done_path = out_dir / DONE_FILE
  done = set(done_path.read_text().split()) if done_path.exists() else set()
  if set(stations) <= done:
    return out_dir
  win2sac = tool_path(app_paths.TOOLS_DIR, "win2sac_32")
  if not win2sac.exists():
    log(f"外部の観測点の変換に必要なツールがありません: {win2sac.name}")
    return None
  work = out_dir / "work"
  if work.exists():
    cache_manager.remove_tree(work)
  work.mkdir(parents=True)
  try:
    with zipfile.ZipFile(zip_path) as z:
      cnts = sorted(n for n in z.namelist() if n.endswith(".cnt"))
      ch_names = [n for n in z.namelist() if n.endswith(".euc.ch")]
      if not cnts or not ch_names:
        log(f"ZIP に .cnt またはチャンネル表(.euc.ch)がありません: {zip_path.name}")
        return None
      # 1分ごとのファイルを1つにつなぐ(catwin32 と同じ結果になる)
      join_win32([z.read(n) for n in cnts], work / "all.cnt")
      (work / _base_name(ch_names[0])).write_bytes(z.read(ch_names[0]))
    ch_text = (work / _base_name(ch_names[0])).read_bytes().decode("euc_jp", errors="replace")
    (work / "channels.tbl").write_text(ch_text, encoding="euc_jp", errors="replace")
    # win2sac_32 のパラメータファイル(2行目がチャンネル表)
    (work / "win.prm").write_text(".\nchannels.tbl\n.\n.\n")
    for name, st in stations.items():
      chans = _channels(ch_text, st["code"])
      if not chans:
        continue   # 別の網の ZIP など
      (work / f"{name}.ch").write_text(",".join(chans[c] for c in COMPONENTS if c in chans))
      res = subprocess.run([str(win2sac.resolve()), "all.cnt", f"{name}.ch", "out", ".", "-pwin.prm"],
                           cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
      for c in chans:
        src = work / f"{st['code']}.{c}.out"
        if src.exists():
          src.replace(out_dir / f"{name}.{c}.sac")
        else:
          log(f"  {st['code']}.{c} を SAC に変換できませんでした({zip_path.name}): "
                      f"{res.stderr.decode('utf-8', errors='ignore')[-200:]}")
    done_path.write_text("\n".join(sorted(done | set(stations))) + "\n")
  finally:
    shutil.rmtree(work, ignore_errors=True)
  return out_dir

# 期間 [t0, t1] の外部の観測点の波形(期間を含む ZIP があれば)
#   names ... 読み込む観測点の名前(None なら設定ファイルの全部)
#   戻り値 ... Stream(拠点名は「画面で使う名前」、チャンネルは U・N・E。満点地震計の SAC と同じ単位)
def load(t0, t1, names=None):
  stations = {n: s for n, s in load_config().items() if names is None or n in names}
  st = obspy.Stream()
  if not stations:
    return st
  for zip_path, start, end in data_files():
    if end <= t0 or start >= t1:
      continue
    out_dir = _convert(zip_path, stations)
    if out_dir is None:
      continue
    for name in stations:
      for c in COMPONENTS:
        path = out_dir / f"{name}.{c}.sac"
        if path.exists():
          tr = obspy.read(str(path))[0]
          tr.stats.network, tr.stats.station, tr.stats.channel = "", name, c
          st += tr
  if st:
    st.merge(method=1, fill_value=0)
    st.trim(obspy.UTCDateTime(t0), obspy.UTCDateTime(t1))
  return st

# --- メンテナンス画面(maintenance_window.py)から使う ---

# v-net フォルダの ZIP ごとの状況
#   戻り値 ... [{"path": ZIP のパス, "start": 開始, "end": 終了, "size": ZIP の大きさ[バイト],
#               "converted": 設定の観測点をすべて変換済みか, "stations": 変換できた設定の観測点の名前のリスト}, ...]
def zip_status():
  names = set(load_config())
  rows = []
  for path, start, end in data_files():
    out_dir = CACHE_DIR / path.stem
    done_path = out_dir / DONE_FILE
    done = set(done_path.read_text().split()) if done_path.exists() else set()
    found = sorted({f.name.split(".")[0] for f in out_dir.glob("*.sac")} & names) if out_dir.exists() else []
    rows.append({"path": path, "start": start, "end": end, "size": path.stat().st_size,
                 "converted": bool(names) and names <= done, "stations": found})
  return rows

# 変換済みの SAC(output/vnet)の (ファイル数, 合計の大きさ[バイト])
def cache_size():
  files = ([f for f in CACHE_DIR.glob("**/*") if f.is_file() and not cache_manager.is_appledouble(f.name)]
           if CACHE_DIR.exists() else [])
  return len(files), sum(f.stat().st_size for f in files)

# 変換済みの SAC を消す(ZIP から変換し直せる)
def clear_cache():
  if CACHE_DIR.exists():
    cache_manager.remove_tree(CACHE_DIR)   # 外付けドライブの ._ ファイルが先に消えてもエラーにしない

# まだ変換していない ZIP を変換する
#   log・cancelled・on_progress ... jma_hypocenter.fetch_days と同じ
#   戻り値 ... 変換した ZIP の数
def convert_all(log=log_message, cancelled=lambda: False, on_progress=None):
  stations = load_config()
  todo = [r["path"] for r in zip_status() if not r["converted"]]
  for i, path in enumerate(todo):
    if cancelled():
      return i
    if on_progress:
      on_progress(i)
    log(f"  {path.name} を変換しています")
    _convert(path, stations, log)
  return len(todo)
