# external_stations.py
# 満点地震計の4拠点に加えて使う、ほかの機関(気象庁・防災科研)の観測点の波形を読み込む(TODO 21)
#   観測点は etc/external_stations.txt に書く(画面で使う名前、WIN の観測点コード、緯度・経度・標高)
#   波形は、防災科研のページなどからダウンロードした ZIP を v-net フォルダにそのまま置く
#     ZIP の名前 ... <網の番号>_<開始 YYYYMMDDHHMM>_<分数>.zip(例: 03_02_07_202607081119_6.zip)
#     中身       ... 1分ごとの WIN32 形式のファイル(.cnt)と、チャンネル表(.euc.ch)
#   ZIP ごとに、使う観測点の上下動・水平動だけを SAC に変換し、output/vnet/<ZIPの名前> に保存して再利用する
#   (catwin32 で1つにつなぎ、win2sac_32 で変換する。どちらも満点地震計の変換と同じ tools のもの)
#   防災科研・気象庁のデータなので、v-net フォルダは Git の管理外にしている
import re
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta

import obspy

import app_paths
from win_pipeline import tool_path
from log_common import log_message

CONFIG_FILE = app_paths.ETC_DIR / "external_stations.txt"
DATA_DIR = app_paths.PROJECT_DIR / "v-net"
CACHE_DIR = app_paths.OUTPUT_DIR / "vnet"
# ZIP の名前(開始の日時と分数)
ZIP_RE = re.compile(r"_(\d{12})_(\d+)\.zip$")
# 使う成分と、チャンネル表の単位(加速度計(m/s/s)や、広帯域の別のチャンネル(wU など)は使わない)
COMPONENTS = ("U", "N", "E")
VELOCITY_UNIT = "m/s"


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
def _convert(zip_path, stations):
  out_dir = CACHE_DIR / zip_path.stem
  done_path = out_dir / DONE_FILE
  done = set(done_path.read_text().split()) if done_path.exists() else set()
  if set(stations) <= done:
    return out_dir
  catwin = tool_path(app_paths.TOOLS_DIR, "catwin32")
  win2sac = tool_path(app_paths.TOOLS_DIR, "win2sac_32")
  if not catwin.exists() or not win2sac.exists():
    log_message(f"外部の観測点の変換に必要なツールがありません: {catwin.name}・{win2sac.name}")
    return None
  work = out_dir / "work"
  if work.exists():
    shutil.rmtree(work)
  work.mkdir(parents=True)
  try:
    with zipfile.ZipFile(zip_path) as z:
      cnts = sorted(n for n in z.namelist() if n.endswith(".cnt"))
      ch_names = [n for n in z.namelist() if n.endswith(".euc.ch")]
      if not cnts or not ch_names:
        log_message(f"ZIP に .cnt またはチャンネル表(.euc.ch)がありません: {zip_path.name}")
        return None
      for n in cnts + ch_names[:1]:
        (work / _base_name(n)).write_bytes(z.read(n))
    ch_text = (work / _base_name(ch_names[0])).read_bytes().decode("euc_jp", errors="replace")
    (work / "channels.tbl").write_text(ch_text, encoding="euc_jp", errors="replace")
    # win2sac_32 のパラメータファイル(2行目がチャンネル表)
    (work / "win.prm").write_text(".\nchannels.tbl\n.\n.\n")
    # 1分ごとのファイルを1つにつなぐ
    res = subprocess.run([str(catwin.resolve())] + [_base_name(n) for n in cnts] + ["-o", "all.cnt"],
                         cwd=work, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0 or not (work / "all.cnt").exists():
      log_message(f"外部の観測点の WIN32 ファイルをつなげませんでした({zip_path.name}): "
                  f"{res.stderr.decode('utf-8', errors='ignore')}")
      return None
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
          log_message(f"  {st['code']}.{c} を SAC に変換できませんでした({zip_path.name}): "
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
