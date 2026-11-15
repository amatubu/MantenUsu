# cf_card.py
# ロガー(EDR-X7000)の CF カードを Mac で読み込む(イメージにする)・初期化する処理(Mac 専用)
#   画面は cf_card_window.py。ここは画面に触れない処理だけ
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
import os
import plistlib
import re
import socket
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import make_cf_image
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


def _diskutil_plist(verb, *args, run=subprocess.run):
  r = run(["diskutil", verb, "-plist", *args], capture_output=True)
  if r.returncode:
    raise CardError(f"diskutil {verb} {' '.join(args)} が失敗しました: {r.stderr.decode(errors='replace').strip()}")
  return plistlib.loads(r.stdout)


# 外付けの物理ディスクの一覧
def list_disks(run=subprocess.run):
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
                      size=int(info.get("TotalSize") or info.get("Size") or 0),
                      media=str(info.get("MediaName") or info.get("IORegistryEntryName") or ""),
                      internal=bool(info.get("Internal", False)),
                      mounted=mounted))
  return disks


def unmount(disk, run=subprocess.run):
  r = run(["diskutil", "unmountDisk", disk.node], capture_output=True)
  if r.returncode:
    raise CardError(f"{disk.node} をアンマウントできません: {r.stderr.decode(errors='replace').strip()}")


def eject(disk, run=subprocess.run):
  r = run(["diskutil", "eject", disk.node], capture_output=True)
  if r.returncode:
    raise CardError(f"{disk.node} を取り出せません: {r.stderr.decode(errors='replace').strip()}")


# ---------------------------------------------------------------- デバイスを開く(authopen)

# authopen でデバイスを開き、開いたファイル(fd)を受け取る。write=True なら読み書き(O_RDWR)
def authopen(path, write=False):
  parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
  cmd = [AUTHOPEN, "-stdoutpipe"] + (["-o", str(os.O_RDWR)] if write else []) + [path]
  try:
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=child, stderr=subprocess.PIPE)
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
                  setting=setting)


# カードの中身を言葉にする
def card_summary(info):
  if not info.station:
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
def read_card(dev, out, verify=True, progress=None, cancelled=None):
  out = Path(out)
  tmp = out.with_name(out.name + ".part")
  total = dev.size * (2 if verify else 1)
  sha = hashlib.sha256()
  try:
    with open(tmp, "wb") as f:
      for off in range(0, dev.size, CHUNK):
        _check(cancelled)
        b = dev.pread(min(CHUNK, dev.size - off), off)
        f.write(b)
        sha.update(b)
        _report(progress, off + len(b), total, "読み込み")
    digest = sha.hexdigest()
    if verify:
      sha2 = hashlib.sha256()
      for off in range(0, dev.size, CHUNK):
        _check(cancelled)
        b = dev.pread(min(CHUNK, dev.size - off), off)
        sha2.update(b)
        _report(progress, dev.size + off + len(b), total, "確認(もう一度読む)")
      if sha2.hexdigest() != digest:
        raise CardError("2 回読んだ中身が違います(カードかリーダーの不調?)。イメージは保存しません")
    tmp.replace(out)
  except BaseException:
    tmp.unlink(missing_ok=True)
    raise
  Path(str(out) + ".sha256").write_text(f"{digest}  {out.name}\n")
  return digest


# イメージの名前: <拠点名>_<日付>out_<カードの番号>.img(例: bamba_20261115out_1.img)
def image_name(name, index, day=None):
  day = day or date.today()
  return f"{name.lower()}_{day:%Y%m%d}out_{index}.img"


# ---------------------------------------------------------------- 初期化する

# 先頭に setting(1 KB)、残りを 0xFF で書き、読み直して確かめる
def write_card(dev, setting, verify=True, progress=None, cancelled=None):
  if len(setting) != SETTING_SIZE:
    raise CardError("設定値は 1024 バイトのはずです")
  total = dev.size * (2 if verify else 1)
  fill = bytes([FILL]) * CHUNK
  first = setting + fill[:CHUNK - SETTING_SIZE]
  for off in range(0, dev.size, CHUNK):
    _check(cancelled)
    n = min(CHUNK, dev.size - off)
    dev.pwrite((first if off == 0 else fill)[:n], off)
    _report(progress, off + n, total, "書き込み")
  try:
    os.fsync(dev.fd)
  except OSError:
    pass   # raw デバイスでは使えないことがある(書き込みはキャッシュを通らない)
  if verify:
    for off in range(0, dev.size, CHUNK):
      _check(cancelled)
      n = min(CHUNK, dev.size - off)
      if dev.pread(n, off) != (first if off == 0 else fill)[:n]:
        raise CardError(f"読み直した中身が書いたものと違います({off:,} バイト目から)")
      _report(progress, dev.size + off + n, total, "確認(読み直し)")


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
