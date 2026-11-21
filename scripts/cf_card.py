# cf_card.py
# ロガー(EDR-X7000)の CF カードを Mac で読み込む(イメージにする)・初期化する処理(Mac 専用)
#   画面は UsuMantenCFTool.py。ここは画面に触れない処理だけ
#
#   カードのデバイス(/dev/rdiskN)は root でないと開けないので、macOS の authopen を使う:
#     authopen が macOS の標準のパスワード(Touch ID)の画面を出して、デバイスを開き、開いたファイルを
#     -stdoutpipe でこちらに渡す(SCM_RIGHTS)。読み書き・SHA-256 の計算は、このプロセスが root でなく行う
#     (sudo や dd は使わない。1 枚ごとに 1 回、パスワードを聞かれる)
#   カードの一覧は diskutil(root は要らない)で、外付けで、大きさがいまの CF カードと同じものだけにする
#     (内蔵のディスクやほかの外付けのディスクは、選べないようにする)
#
#   カードの中身(raw_to_win.py・make_cf_image.py も参照):
#     先頭 1 KB がヘッダー(0x0e カードの枚数、0x16 カードの番号。初期化したカードは 00、記録するとロガーが 01・02 と書く)
#     その後ろに 1 KB ずつ記録('W' 波形、'G' GPS)。記録の無い所は 0xFF
import hashlib
import math
import os
import plistlib
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import make_cf_image
import raw_to_win
from make_cf_image import CARD_SIZE, SETTING_SIZE, FILL, SettingError

CHUNK = 4 << 20             # 読み書きの単位(512 の倍数)
AUTHOPEN = "/usr/libexec/authopen"
IDENTIFY_SIZE = 64 << 10    # 読み込んだイメージと同じカードかを確かめるのに比べる、先頭の大きさ
SETTING_DIR = Path("/Volumes/home/_満天地震計/set")   # 設定/データ変換ソフトが保存した設定値のファイル


class CardError(Exception):
  pass


class Cancelled(Exception):
  pass


# ---------------------------------------------------------------- カードの一覧(diskutil)

@dataclass
class Disk:
  node: str          # /dev/diskN
  size: int          # バイト
  media: str         # カードリーダーなどの名前
  internal: bool
  mounted: bool      # パーティションがマウントされている(CF カードならふつうは無い)

  @property
  def raw_node(self):
    return self.node.replace("/dev/disk", "/dev/rdisk")

  @property
  def is_card(self):
    return not self.internal and self.size == CARD_SIZE


DISKUTIL = "/usr/sbin/diskutil"


# 外のコマンドを fork せずに起動する(posix_spawn)
#   画面(Tk)を使っている間に fork すると、macOS が「The process has forked and you cannot use this
#   CoreFoundation functionality safely」と出す。subprocess は close_fds=True(既定)だと macOS では fork するので、
#   close_fds=False にし、コマンドを絶対パスで渡す(Python が開くファイルは既定で子に引き継がれないので、これで安全)
def spawn_run(cmd, **kw):
  return subprocess.run(cmd, close_fds=False, **kw)


def _diskutil_plist(verb, *args, run=spawn_run):
  r = run([DISKUTIL, verb, "-plist", *args], capture_output=True)
  if r.returncode:
    raise CardError(f"diskutil {verb} {' '.join(args)} が失敗しました: {r.stderr.decode(errors='replace').strip()}")
  return plistlib.loads(r.stdout)


# 外付けの物理ディスクの一覧
# /dev のディスクの名前(disk4 など。パーティションの disk4s1 は除く)。diskutil を呼ばずに、抜き差しを見るため
#   すばやく差し替えて同じ名前になっても分かるように、名前とファイルの番号(作り直すと変わる)の組にする
#   (時刻は入れない。アンマウントや開いただけでも変わり、差し替えたとみてしまうため)
def disk_nodes(dev_dir="/dev"):
  try:
    names = [n for n in os.listdir(dev_dir) if re.fullmatch(r"disk\d+", n)]
  except OSError:
    return None
  out = set()
  for n in names:
    try:
      st = os.stat(os.path.join(dev_dir, n))
      out.add((n, st.st_ino))
    except OSError:
      pass
  return frozenset(out)


def list_disks(run=spawn_run):
  disks = []
  for ident in _diskutil_plist("list", "external", "physical", run=run).get("WholeDisks", []):
    info = _diskutil_plist("info", ident, run=run)
    mounted = False
    try:
      parts = _diskutil_plist("list", ident, run=run).get("AllDisksAndPartitions", [])
      mounted = any(p.get("MountPoint") for d in parts for p in [d, *d.get("Partitions", [])])
    except CardError:
      pass
    disks.append(Disk(node=info.get("DeviceNode", f"/dev/{ident}"),
                      # ディスクの大きさ。TotalSize は、ファイルシステムがディスクに直接あると(パソコンで FAT32 に
                      # 初期化されたカードなど)その大きさ(4,068,257,792 バイト)になり、カードとみなせないので使わない
                      size=int(info.get("IOKitSize") or info.get("Size") or info.get("TotalSize") or 0),
                      media=str(info.get("MediaName") or info.get("IORegistryEntryName") or ""),
                      internal=bool(info.get("Internal", False)),
                      mounted=mounted))
  return disks


def unmount(disk, run=spawn_run):
  r = run([DISKUTIL, "unmountDisk", disk.node], capture_output=True)
  if r.returncode:
    raise CardError(f"{disk.node} をアンマウントできません: {r.stderr.decode(errors='replace').strip()}")


# カードを手放す(抜いてよい状態にする)
#   diskutil eject は使わない: eject はカードリーダーにメディアを止める命令を送り、USB 2.0 のリーダー
#   (Alcor Micro 058F:6366)では、そのあと別のカードを差しても macOS がカードを見つけなくなる
#   (リーダーの USB を抜き差しするまで。2026-11-17 に確かめた)。CF カードにはファイルシステムが無く、
#   ツールは読み書きのあとにデバイスを閉じているので、アンマウントだけで抜いてかまわない
def eject(disk, run=spawn_run):
  r = run([DISKUTIL, "unmountDisk", disk.node], capture_output=True)
  if r.returncode:
    raise CardError(f"{disk.node} を手放せません: {r.stderr.decode(errors='replace').strip()}")


# ---------------------------------------------------------------- デバイスを開く(authopen)

# authopen でデバイスを開き、開いたファイル(fd)を受け取る。write=True なら読み書き(O_RDWR)
def authopen(path, write=False):
  parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
  cmd = [AUTHOPEN, "-stdoutpipe"] + (["-o", str(os.O_RDWR)] if write else []) + [path]
  try:
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=child, stderr=subprocess.PIPE,
                            close_fds=False)   # fork しない(spawn_run を参照)
    child.close()
    fds = []
    try:
      _, fds, _, _ = socket.recv_fds(parent, 16, 1)
    except OSError:
      pass
    err = proc.stderr.read().decode(errors="replace").strip()
    code = proc.wait()
  finally:
    parent.close()
  if code or not fds:
    for fd in fds:
      os.close(fd)
    raise CardError(f"{path} を開けませんでした(パスワードの画面を取り消した?){': ' + err if err else ''}")
  return fds[0]


# 開いたデバイス(テストでは、ふつうのファイルを開いたもの)
class Device:
  def __init__(self, fd, size):
    self.fd = fd
    self.size = size

  @classmethod
  def open(cls, disk, write=False):
    if not disk.is_card:
      raise CardError(f"{disk.node} は CF カードではありません(大きさ {disk.size:,} バイト)")
    if write and disk.mounted:
      unmount(disk)
    return cls(authopen(disk.raw_node, write), disk.size)

  def close(self):
    if self.fd is not None:
      os.close(self.fd)
      self.fd = None

  def __enter__(self):
    return self

  def __exit__(self, *a):
    self.close()

  def pread(self, n, offset):
    out = bytearray()
    while len(out) < n:
      b = os.pread(self.fd, n - len(out), offset + len(out))
      if not b:
        raise CardError(f"{offset + len(out):,} バイト目から読めません(カードが抜けた?)")
      out += b
    return bytes(out)

  def pwrite(self, data, offset):
    view = memoryview(data)
    while view:
      n = os.pwrite(self.fd, view, offset)
      offset += n
      view = view[n:]

  def head(self):
    return self.pread(IDENTIFY_SIZE, 0)


# ---------------------------------------------------------------- カードの中身を調べる

@dataclass
class CardInfo:
  station: str        # 拠点ID
  cards: int          # カードの枚数(0x0e)
  index: int          # カードの番号(0x16。00 は初期化したまま)
  location: str
  has_data: bool      # 記録がある(2 つめのレコードが 0xFF でない)
  setting: dict       # make_cf_image.describe の結果(読めないときは None)
  unset: bool = False # 設定値が書かれないまま記録した(拠点ID 0000000000 など。raw_to_win.is_unset)
  empty: bool = False # 先頭が全部 0xFF(何も書かれていない。設定値も無い)
  pc_format: bool = False  # パソコンの形式(FAT など。先頭がブートセクタ)で初期化されている


def card_info(head):
  if len(head) < 2 * SETTING_SIZE:
    raise CardError("カードの先頭が読めません")
  station = head[:10].decode("ascii", "replace")
  try:
    setting = make_cf_image.describe(head[:SETTING_SIZE])
  except SettingError:
    setting = None
  return CardInfo(station=station if station.isdigit() else "",
                  cards=head[0x0e], index=head[0x16],
                  location=head[0x18:0x38].split(b"\0")[0].decode("ascii", "replace"),
                  has_data=head[SETTING_SIZE] != FILL,
                  setting=setting,
                  unset=station.isdigit() and raw_to_win.is_unset(raw_to_win.read_header(head[:SETTING_SIZE])),
                  empty=set(head[:SETTING_SIZE]) == {FILL},
                  pc_format=is_pc_format(head))


# パソコンの形式で初期化されたか(先頭がブートセクタ: 終わりが 55 AA で、FAT か exFAT の印がある)
def is_pc_format(head):
  return (len(head) >= 512 and head[510:512] == b"\x55\xaa"
          and (head[3:11] == b"EXFAT   " or b"FAT" in head[0x36:0x3e] or b"FAT" in head[0x52:0x5a]))


# カードの中身を言葉にする
def card_summary(info):
  if not info.station:
    if info.empty:
      return "何も書かれていません(先頭が全部 0xFF。ロガーで使うには初期化します)"
    if info.pc_format:
      return "パソコンの形式(FAT32 など)で初期化されています(ロガーで使うには、このツールで初期化します)"
    return "ロガーのカードではないようです(先頭に拠点ID がありません)"
  state = "記録あり" if info.has_data else "記録なし(初期化したまま)"
  index = f"{info.index} 枚目" if info.index else "番号なし"
  return f"拠点ID {info.station}・{index}(全 {info.cards} 枚)・{state}・{location_text(info.location)}"


# 位置の文字列 "4233.6000,N,14050.6000,E000140.0" を、人が読める形にする(北緯 42°33.6000′・東経 140°50.6000′・標高 140.0 m)
def location_text(location):
  m = re.fullmatch(r"(\d{2})(\d{2}\.\d+),([NS]),(\d{3})(\d{2}\.\d+),([EW])(\d+\.\d)", location)
  if not m:
    return f"位置 {location}"
  lat = "北緯" if m[3] == "N" else "南緯"
  lon = "東経" if m[6] == "E" else "西経"
  return (f"{lat} {int(m[1])}°{m[2]}′・{lon} {int(m[4])}°{m[5]}′・標高 {float(m[7]):.1f} m")


# このカードを読み込んだイメージが folder にあれば、その名前(先頭 IDENTIFY_SIZE が同じもの)
def find_image(head, folder):
  folder = Path(folder)
  if not folder.is_dir():
    return []
  found = []
  for p in sorted(folder.glob("*.img")):
    try:
      if p.stat().st_size >= len(head):
        with open(p, "rb") as f:
          if f.read(len(head)) == head:
            found.append(p.name)
    except OSError:
      pass
  return found


# ---------------------------------------------------------------- 読み込む

# デバイスの全部を out に書き、SHA-256 を返す。verify なら、もう一度読んで同じか確かめる
#   progress(済んだバイト数, 全体のバイト数, 段階の名前) を呼ぶ。cancelled() が真なら Cancelled を出す
#   stats ... 辞書を渡すと、段階ごとにかかった秒を入れる({"読み込み": 秒, "確認": 秒})
def read_card(dev, out, verify=True, progress=None, cancelled=None, stats=None):
  stats = {} if stats is None else stats
  out = Path(out)
  tmp = out.with_name(out.name + ".part")
  total = dev.size * (2 if verify else 1)
  sha = hashlib.sha256()
  try:
    t0 = time.monotonic()
    with open(tmp, "wb") as f:
      for off in range(0, dev.size, CHUNK):
        _check(cancelled)
        b = dev.pread(min(CHUNK, dev.size - off), off)
        f.write(b)
        sha.update(b)
        _report(progress, off + len(b), total, "読み込み")
    digest = sha.hexdigest()
    stats["読み込み"] = time.monotonic() - t0
    if verify:
      t0 = time.monotonic()
      sha2 = hashlib.sha256()
      for off in range(0, dev.size, CHUNK):
        _check(cancelled)
        b = dev.pread(min(CHUNK, dev.size - off), off)
        sha2.update(b)
        _report(progress, dev.size + off + len(b), total, "確認(もう一度読む)")
      stats["確認"] = time.monotonic() - t0
      if sha2.hexdigest() != digest:
        raise CardError("2 回読んだ中身が違います(カードかリーダーの不調?)。イメージは保存しません")
    tmp.replace(out)
  except BaseException:
    tmp.unlink(missing_ok=True)
    raise
  Path(str(out) + ".sha256").write_text(f"{digest}  {out.name}\n")
  return digest


# イメージの名前: <拠点名>_<記録の開始の日>in_<カードの番号>.img(例: bamba_20260826in_1.img)
#   記録の開始の日がわからないときは、<今日の日付>out(前の名前の付け方。例: bamba_20261115out_1.img)
#   記録の開始は 1 回の回収の分で同じなので、1 枚目と 2 枚目は同じ日の名前になる。前に読んだカードを読み直しても同じ名前
def image_name(name, index, started=None):
  if started:
    return f"{name.lower()}_{started:%Y%m%d}in_{index}.img"
  return f"{name.lower()}_{date.today():%Y%m%d}out_{index}.img"


# カードの記録の開始時刻(日本時間。最初の 'G' レコードの時刻)。limit までに見つからない・読めなければ None
#   1 枚目はヘッダーのすぐ後ろにある。2 枚目は 'G' レコードまで数百 KB 読むことがある
def record_start(dev, limit=64 << 20):
  step = 1 << 20
  for off in range(SETTING_SIZE, min(limit, dev.size), step):
    b = dev.pread(min(step, dev.size - off), off)
    for k in range(0, len(b) - SETTING_SIZE + 1, SETTING_SIZE):
      c = b[k]
      if c == ord("G"):
        try:
          return raw_to_win.gps_start_time(b[k:k + SETTING_SIZE])
        except ValueError:
          return None
      if c == FILL:
        return None
  return None



# カードの記録の範囲を、全部は読まずに調べる(台帳に書くため。数十回、1 KB ずつ読むだけ)
#   記録は先頭から詰めて書かれ、その後ろは 0xFF なので、0xFF になる所を二分探索で探す
#   返り値: (最初の波形の時刻, 最後の波形の終わりの時刻, カードがいっぱいか)。日本時間。記録が無ければ None
#   時刻は記録の開始(start)と、波形のサンプルの通し番号から求める(GPS による補正は入れない。分の単位の目安)
def card_range(dev, start):
  nrec = dev.size // SETTING_SIZE

  def rec(i):
    return dev.pread(SETTING_SIZE, i * SETTING_SIZE)

  if start is None or rec(1)[0] == FILL:
    return None
  lo, hi = 1, nrec          # lo は 0xFF でない、hi は 0xFF(か、カードの終わり)
  while hi - lo > 1:
    mid = (lo + hi) // 2
    if rec(mid)[0] == FILL:
      hi = mid
    else:
      lo = mid
  full = hi == nrec
  first = last = None
  for i in range(1, min(lo + 1, 4096)):        # 最初の 'W'(2 枚目は 'G' が先に来ることがある)
    r = rec(i)
    if r[0] == ord("W"):
      first = int.from_bytes(r[6:10], "big")
      break
  for i in range(lo, max(0, lo - 4096), -1):   # 最後の 'W'
    r = rec(i)
    if r[0] == ord("W"):
      last = int.from_bytes(r[6:10], "big") + int.from_bytes(r[4:6], "big")
      break
  if first is None or last is None:
    return None
  sr = raw_to_win.SR
  return start + timedelta(seconds=first / sr), start + timedelta(seconds=last / sr), full

# GPS の文の位置(raw_to_win.gga_position)。先頭の数十レコードの 'G' から(数 KB 読むだけ)
def gps_position(dev, n=64):
  for i in range(1, min(n, dev.size // SETTING_SIZE)):
    r = dev.pread(SETTING_SIZE, i * SETTING_SIZE)
    if r[0] == FILL:
      break
    if r[0] == ord("G"):
      p = raw_to_win.gga_position(r)
      if p:
        return p
  return None


# 位置からいちばん近い拠点と、その距離(km)。位置か拠点の位置が無ければ (None, None)
def nearest_station(stations, pos):
  if not pos:
    return None, None
  best = None
  for s in stations:
    if s.lat is None:
      continue
    dy = (s.lat - pos[0]) * 111.2
    dx = (s.lon - pos[1]) * 111.2 * math.cos(math.radians(pos[0]))
    d = math.hypot(dx, dy)
    if best is None or d < best[1]:
      best = (s, d)
  return best or (None, None)


# ---------------------------------------------------------------- 初期化する

# 先頭に setting(1 KB)、残りを 0xFF で書き、読み直して確かめる
#   stats ... 辞書を渡すと、段階ごとにかかった秒を入れる({"書き込み": 秒, "確認": 秒})
def write_card(dev, setting, verify=True, progress=None, cancelled=None, stats=None):
  stats = {} if stats is None else stats
  if len(setting) != SETTING_SIZE:
    raise CardError("設定値は 1024 バイトのはずです")
  total = dev.size * (2 if verify else 1)
  fill = bytes([FILL]) * CHUNK
  first = setting + fill[:CHUNK - SETTING_SIZE]
  t0 = time.monotonic()
  for off in range(0, dev.size, CHUNK):
    _check(cancelled)
    n = min(CHUNK, dev.size - off)
    dev.pwrite((first if off == 0 else fill)[:n], off)
    _report(progress, off + n, total, "書き込み")
  try:
    os.fsync(dev.fd)
  except OSError:
    pass   # raw デバイスでは使えないことがある(書き込みはキャッシュを通らない)
  stats["書き込み"] = time.monotonic() - t0
  if verify:
    t0 = time.monotonic()
    for off in range(0, dev.size, CHUNK):
      _check(cancelled)
      n = min(CHUNK, dev.size - off)
      if dev.pread(n, off) != (first if off == 0 else fill)[:n]:
        raise CardError(f"読み直した中身が書いたものと違います({off:,} バイト目から)")
      _report(progress, dev.size + off + n, total, "確認(読み直し)")
    stats["確認"] = time.monotonic() - t0


def _check(cancelled):
  if cancelled and cancelled():
    raise Cancelled()


def _report(progress, done, total, stage):
  if progress:
    progress(done, total, stage)


# ---------------------------------------------------------------- 拠点と設定値

@dataclass
class Station:
  id: str
  name: str               # チャンネル表の拠点名(例: Bamba)
  channels: list          # チャンネル番号 3 つ(例: ["8D10", "8D11", "8D12"])
  lat: float = None       # チャンネル表の位置・標高
  lon: float = None
  alt: float = None


# etc の <拠点ID>.ch とチャンネル表(manten_ch.tbl)から、拠点の一覧
def load_stations(etc_dir):
  etc_dir = Path(etc_dir)
  table = {}
  tbl = etc_dir / "manten_ch.tbl"
  if tbl.exists():
    for line in tbl.read_text(encoding="utf-8").splitlines():
      f = line.split()
      if len(f) >= 16 and not f[0].startswith("#"):
        table[f[0].upper()] = (f[3], float(f[13]), float(f[14]), float(f[15]))
  stations = []
  for ch in sorted(etc_dir.glob("[0-9]" * 10 + ".ch")):
    channels = [c.strip().upper() for c in ch.read_text().split("#")[0].strip().split(",") if c.strip()]
    if len(channels) != 3:
      continue
    name, lat, lon, alt = table.get(channels[0], (ch.stem, None, None, None))
    stations.append(Station(ch.stem, name, channels, lat, lon, alt))
  return stations


# 拠点ID の設定値のファイルのうち、いちばん新しいもの(名前の <ID>_<yymmddhhmm>_… の日時で)
def latest_setting_file(station_id, folder=SETTING_DIR):
  folder = Path(folder)
  try:
    files = [p for p in folder.iterdir() if p.name.startswith(station_id + "_") and p.is_file()
             and p.stat().st_size == SETTING_SIZE]
  except OSError:
    return None
  files.sort(key=lambda p: (p.name.split("_")[1] if p.name.count("_") >= 1 else "", "2枚" in p.name))
  return files[-1] if files else None


# 度(小数)を、度と分にする(設定値の形。分は小数 4 桁)
def deg_min(value):
  d = int(abs(value))
  m = round((abs(value) - d) * 60, 4)
  if m >= 60:
    d, m = d + 1, 0.0
  return d, m
