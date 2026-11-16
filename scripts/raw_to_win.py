# raw_to_win.py
# 地震計のロガーの CF カードの中身(raw)を、1分ごとの WIN ファイルに変換する(Windows の抽出・変換ツールの代わり)
#   入力は、抽出ツールが取り出した raw(例: 0970355225_2512060252_1.raw)か、CF カードをそのままイメージにしたもの
#   1回の回収の分(カード2枚など)は、まとめて指定する(順番は問わない。サンプルの通し番号で並べる)
#   出力は、変換ツールと同じ構成: <出力>/<YYMMDD>/<拠点ID>_<YYMMDDhhmm>.win(時刻は日本時間)
#   出力先を .zip にすると、ZIP に書く(中は <拠点ID>/<YYMMDD>/... 。変換ツールの ZIP と同じで、source_zips の元の ZIP に使える)
#   --daily を付けると、出力先を日ごとの ZIP のフォルダ(daily_zips)として、<YYMMDD>.zip に直接書き足す
#   (repackage_to_daily_zip.py でまとめ直したのと同じ形。もう入っている WIN ファイルは書かない)
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/raw_to_win.py <raw...> --info                  ... ヘッダー・記録の範囲・壊れた所を表示する
#   python3 scripts/raw_to_win.py <raw...> -o <フォルダ か .zip>   ... WIN に変換する
#   python3 scripts/raw_to_win.py <raw...> -o <daily_zips> --daily  ... WIN にして、日ごとの ZIP に直接書き足す
#   python3 scripts/raw_to_win.py <raw...> --verify <ZIP か フォルダ>   ... 書かずに、変換ツールの WIN と1バイトずつ比べる
#   python3 scripts/raw_to_win.py <raw...> --fit <ZIP か フォルダ> [--save-cal]
#                                          ... 変換ツールの WIN から、拠点の校正値(オフセット・ゲイン)を求める
#
# raw の形式(記念館 0970355225 の 2025/12〜2026/1 のデータで調べた。変換ツールの WIN と全ファイルが一致):
#   ファイルシステムは無く、1024 バイトのレコードが並ぶ。先頭のバイトがレコードの種類
#     先頭のレコード ... ヘッダー(拠点ID、緯度経度、チャンネル番号(0x3a〜)、校正値(0x40〜0x88))
#     'G' ... GPS。2〜15 バイト目に記録の開始時刻(UTC、YYYYMMDDhhmmss)。GPS の週番号の繰り返しのため、
#             2019/4 より前の日付は 1024 週(7168 日)を足す(例: 2006-04-22 → 2025-12-06)
#     'W' ... 波形。4〜5 バイト目がフレームの数、6〜9 バイト目が最初のフレームの通し番号、16 バイト目から
#             フレームが並ぶ。1 フレームは、制御の 1 バイトと 3 チャンネルの値
#               制御の bit 6: 1 = 値は 2 バイト(符号付き)、0 = 1 バイト
#               制御の bit 7: 1 = 細かい記録、0 = 粗い記録(大きな揺れのとき。値は 64 倍)
#               制御の下位 6 ビット: チャンネル 1〜3 の値の下 2 ビット(チャンネル 1 が最下位)
#             値 V = 4 × (値) + (下 2 ビット)。粗い記録は、その 64 倍
#     0xFF ... 書かれていない所(ここで記録が終わる)
#   サンプリングは 250 Hz で、通し番号 0 のフレームが記録の開始時刻
#   GPS による時刻の補正(マニュアルの gps3。変換ツールの「Execute the time correction automatically」と同じ):
#     ロガーは時計を直さず、GPS で測った内部時計のずれ('G' レコード)を記録するだけなので、変換のときに直す
#     ずれ(ms)を 4 ms(1 サンプル)で割り、0 の側へ切り捨てた数だけ、サンプルを足す(時計が遅れているとき)・減らす
#     (進んでいるとき)。前に測った所からの変化が 2 サンプル以上なら、前の測定から今の測定までを等分した所に
#     1 つずつ置く(秒の単位で、前の測定の秒 + 切り捨て(i × 間の秒数 / 数))
#     測定の時刻は、その 'G' レコードの前の 'W' レコードの最初のサンプルの時刻(それまでの補正を入れた時刻)
#     足すときは、その秒の 249 番目に 248 番目を写して入れ(もとの 249 番目からが 1 つ後ろへずれる)、
#     減らすときは、次の秒の頭になるはずのサンプルを捨てる(どの秒も 250 サンプルのまま)
#     (変換ツールの WIN と 1 サンプルずつ比べて確かめた)
#     馬場 1070432003 の 2026/8〜11 の回収(10 回の補正)で、変換ツールの gps3 と時刻・向きが全部一致した
#     --no-gps-correction で補正しない
#   WIN の値 = floor(ゲイン × V + オフセット)。細かい記録と粗い記録で別の値。オフセットは、ほぼ
#   -(ヘッダーのオフセット) × ゲイン(細かい記録はヘッダーの 4〜6 番目、粗い記録は 1〜3 番目)。ゲインの出どころは
#   わからないので、拠点ごとに変換ツールの WIN から求めて etc/logger_cal.txt に置く(--fit)
#   (V + a) × g ではわずかに合わない所がある(積がちょうど整数の付近。変換ツールの計算の順序の違いとみられる)
import argparse
import hashlib
import os
import shutil
import struct
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import app_paths

RECORD = 1024
SR = 250                   # サンプリング周波数(Hz)
JST = timedelta(hours=9)
GPS_ROLLOVER = timedelta(days=7168)
GPS_ROLLOVER_END = datetime(2019, 4, 7)
COARSE_SHIFT = 6           # 粗い記録の値の位置(V = (4 × 値 + 下 2 ビット) × 64)
CHUNK = 65536             # 一度に読むレコードの数(64 MB)
CAL_FILE = app_paths.PROJECT_DIR / "etc" / "logger_cal.txt"


class RawError(Exception):
  pass


# ---------------------------------------------------------------- 読み込み

# ヘッダー(先頭のレコード)
#   station ... 拠点ID、channels ... WIN のチャンネル番号 3 つ、cal_key ... 校正値の部分の識別(SHA-1 の先頭 8 桁)
#   cal_offsets・cal_gains ... 校正値の部分を 3 バイトずつ読んだもの(01xxxx・02xxxx がオフセット(符号付き)、03xxxx がゲインとみられる)
def read_header(rec0):
  station = rec0[:10].decode("ascii", "replace")
  if not station.isdigit():
    raise RawError(f"先頭のレコードが、ロガーのヘッダーではありません(拠点ID: {station!r})")
  channels = [int.from_bytes(rec0[0x3a + 2 * j:0x3c + 2 * j], "big") for j in range(3)]
  cal = rec0[0x40:0x88]
  offsets, gains = [], []
  for p in range(0, len(cal), 3):
    if cal[p] in (1, 2):   # 01 は負の値(例: 01 fff8 = -8)
      offsets.append(int.from_bytes(cal[p + 1:p + 3], "big", signed=True))
    elif cal[p] == 3:
      gains.append(int.from_bytes(cal[p:p + 3], "big"))  # 03 c1cb → 0x03c1cb として読む
  return {
    "station": station,
    "card": rec0[0x16],
    "location": rec0[0x18:0x38].split(b"\0")[0].decode("ascii", "replace"),
    "channels": channels,
    "cal_key": hashlib.sha1(cal).hexdigest()[:8],
    "cal_offsets": offsets,
    "cal_gains": gains,
  }


# 'G' レコードの記録の開始時刻(日本時間)
def gps_start_time(rec):
  t = datetime.strptime(rec[2:16].decode("ascii"), "%Y%m%d%H%M%S")
  while t < GPS_ROLLOVER_END:
    t += GPS_ROLLOVER
  return t + JST


# 1 つの入力ファイル(raw か カードのイメージ)
class RawFile:
  def __init__(self, path):
    self.path = Path(path)
    size = self.path.stat().st_size
    if size < 2 * RECORD:
      raise RawError(f"{self.path.name}: 小さすぎます({size} バイト)")
    self.mm = np.memmap(self.path, np.uint8, "r", shape=(size // RECORD, RECORD))
    self.header = read_header(bytes(self.mm[0]))
    types = np.array(self.mm[:, 0])
    types[0] = 0
    # 記録の終わり: 最初の 0xFF のレコード(その後ろに 0xFF でないものがあれば、知らせる)
    ff = np.nonzero(types == 0xFF)[0]
    self.end = int(ff[0]) if len(ff) else len(types)
    self.after_end = int(np.count_nonzero(types[self.end:] != 0xFF))
    t = types[1:self.end]
    self.w_recs = np.nonzero(t == ord("W"))[0] + 1
    self.g_recs = np.nonzero(t == ord("G"))[0] + 1
    self.bad_recs = np.nonzero((t != ord("W")) & (t != ord("G")))[0] + 1
    if len(self.w_recs) == 0:
      raise RawError(f"{self.path.name}: 波形のレコードがありません")
    self.start = gps_start_time(bytes(self.mm[self.g_recs[0]])) if len(self.g_recs) else None
    self.first_idx = self.frame_index(self.w_recs[0])

  def frame_index(self, rec):
    r = self.mm[rec]
    return int.from_bytes(bytes(r[6:10]), "big")

  # 波形のレコードの通し番号が続かない所: [(前の終わりの通し番号, 次の通し番号, 次のレコードの番号)]
  def gaps(self):
    h = np.array(self.mm[self.w_recs, 4:10]).astype(np.int64)
    n = (h[:, 0] << 8) | h[:, 1]
    idx = (h[:, 2] << 24) | (h[:, 3] << 16) | (h[:, 4] << 8) | h[:, 5]
    br = np.nonzero(idx[1:] != (idx + n)[:-1])[0]
    return [(int(idx[i] + n[i]), int(idx[i + 1]), int(self.w_recs[i + 1])) for i in br]

  # GPS 補正の記録(マニュアルの gps1 ファイルと同じ内容)から、内部時計と GPS の 1pps のずれ(ミリ秒)
  #   'G' レコードの 0x10 から: 起動からの秒、内部水晶の周波数(122880000 前後)、ずれ(4.096 MHz のカウント。
  #   2048000 より大きいと進み)、前回との差、…。周波数が 0xFFFFFFFF のものは、測っていない記録
  def gps_drift_ms(self):
    if len(self.g_recs) == 0:
      return np.zeros(0)
    g = np.array(self.mm[self.g_recs, 0x10:0x1c]).astype(np.int64)
    freq = (g[:, 4] << 24) | (g[:, 5] << 16) | (g[:, 6] << 8) | g[:, 7]
    drift = (g[:, 8] << 24) | (g[:, 9] << 16) | (g[:, 10] << 8) | g[:, 11]
    drift = drift[freq != 0xFFFFFFFF]
    return np.where(drift > 2048000, drift - 4096000, drift) / 4096

  # GPS で内部時計のずれを測った所: [(その前の 'W' レコードの最初のサンプルの通し番号, ずれ(4.096 MHz のカウント。
  #   負は進み))]。前に 'W' が無ければ後ろの 'W' を使う(どちらにも無ければ使わない)
  def gps_points(self):
    pts = []
    types = self.mm[:self.end, 0]
    for g in self.g_recs:
      g = int(g)
      rec = bytes(self.mm[g, 0x10:0x1c])
      if int.from_bytes(rec[4:8], "big") == 0xFFFFFFFF:
        continue
      d = int.from_bytes(rec[8:12], "big")
      if d > 2048000:
        d -= 4096000
      w = g - 1
      while w > 0 and types[w] != ord("W"):
        w -= 1
      if w == 0:
        w = g + 1
        while w < self.end and types[w] != ord("W"):
          w += 1
        if w >= self.end:
          continue
      pts.append((self.frame_index(w), d))
    return pts

  def last_frame(self):
    r = self.mm[self.w_recs[-1]]
    return self.frame_index(self.w_recs[-1]) + int.from_bytes(bytes(r[4:6]), "big")


# 'W' レコードをまとめて読む(numpy で、レコードの間はまとめて計算する)
#   recs ... (m, 1024) の uint8
#   返り値: (通し番号 (m,), フレームの数 (m,), 値 V (フレームの合計, 3) の int32, 粗い記録か (フレームの合計,))
#   フレームの長さ(4 か 7 バイト)は前のフレームを読まないと決まらないので、まずフレームの位置だけを順に求め、
#   値はあとでまとめて戻す。レコードは DECODE_BLOCK 個ずつ(大きいとメモリの読み書きで遅くなる)
DECODE_BLOCK = 512


def decode_w(recs, rec_numbers=None):
  recs = np.asarray(recs)
  m = len(recs)
  if m > DECODE_BLOCK:
    parts = [decode_w(recs[a:a + DECODE_BLOCK], None if rec_numbers is None else rec_numbers[a:a + DECODE_BLOCK])
             for a in range(0, m, DECODE_BLOCK)]
    return tuple(np.concatenate(x) for x in zip(*parts))
  n = (recs[:, 4].astype(np.int64) << 8) | recs[:, 5]
  idx = ((recs[:, 6].astype(np.int64) << 24) | (recs[:, 7].astype(np.int64) << 16)
         | (recs[:, 8].astype(np.int64) << 8) | recs[:, 9])
  maxn = int(n.max()) if m else 0
  # レコードを 1 列に並べ、それぞれの後ろに余白(0)を付ける(はみ出したフレームを読んでも、隣のレコードに届かない)
  width = RECORD + 8
  pad = np.zeros((m, width), np.uint8)
  pad[:, :RECORD] = recs
  flat = pad.ravel()
  first = np.arange(m, dtype=np.int32) * width
  # 1. フレームの位置。制御バイトの 0x40 が立っていれば 7 バイト、でなければ 4 バイト。
  #    レコードの終わりを越えたら、余白の頭に止める(そこにフレームがあれば、下で、はみ出しとして見つかる)
  where = np.empty((maxn, m), np.int32)
  pos, lim = first + 16, first + RECORD
  for k in range(maxn):
    where[k] = pos
    pos = np.minimum(pos + 4 + 3 * ((flat[pos] >> 6) & 1), lim)
  valid = np.arange(maxn)[None, :] < n[:, None]
  q = where.T[valid]                     # 本当にあるフレームの位置(レコードの順、レコードの中はフレームの順)
  # 2. 値をまとめて戻す
  c = flat[q].astype(np.int32)
  two = (c & 0x40) != 0                  # 値が 2 バイト
  over = q - first[q // width] + np.where(two, 7, 4) > RECORD
  if np.any(over):
    bad = int(q[over][0]) // width
    rec = rec_numbers[bad] if rec_numbers is not None else bad
    raise RawError(f"レコード {rec} のフレームが、レコードの外にはみ出します(壊れている?)")
  b = flat[q[:, None] + np.arange(1, 7, dtype=np.int32)]
  v1 = b[:, 0:3].view(np.int8).astype(np.int32)                            # 1 バイトのとき
  v2 = ((b[:, 0:6:2].astype(np.uint16) << 8) | b[:, 1:6:2]).view(np.int16).astype(np.int32)   # 2 バイトのとき
  v = np.where(two[:, None], v2, v1)
  rough = (c & 0x80) == 0                # 粗い記録(大きな揺れ)は 6 ビット上
  low = (c[:, None] >> np.array([0, 2, 4], np.int32)) & 3
  out = (4 * v + low) << np.where(rough, COARSE_SHIFT, 0)[:, None]
  return idx, n, out.astype(np.int32), rough


# 入力ファイル(1回の回収の分)の波形を、続いている区間ごとに順に返す
#   返り値: (最初のフレームの通し番号, 値 V (k, 3), 粗い記録か (k,)) を順に。区間の切れ目では report に書く
def iter_frames(files, report=print):
  expected = None
  for f in files:
    w = f.w_recs
    for a in range(0, len(w), CHUNK):
      rn = w[a:a + CHUNK]
      # 続いているレコードの番号はまとめて読む(memmap の切り出し)
      if rn[-1] - rn[0] == len(rn) - 1:
        recs = f.mm[rn[0]:rn[-1] + 1]
      else:
        recs = f.mm[rn]
      idx, n, v, coarse = decode_w(recs, rn)
      # レコードの間で通し番号が続かない所で分ける
      ends = idx + n
      breaks = np.nonzero(idx[1:] != ends[:-1])[0] + 1
      starts = np.concatenate([[0], breaks])
      cum = np.concatenate([[0], np.cumsum(n)])
      for s, e in zip(starts, np.concatenate([breaks, [len(idx)]])):
        i0 = int(idx[s])
        if expected is not None and i0 != expected:
          report(f"  ※ 波形が続いていません: 通し番号 {expected} の次が {i0}"
                 f"({(i0 - expected) / SR:+.3f} 秒。{f.path.name} のレコード {int(rn[s])})")
        yield i0, v[cum[s]:cum[e]], coarse[cum[s]:cum[e]]
        expected = int(ends[e - 1])


# ---------------------------------------------------------------- GPS による時刻の補正(gps3)

DRIFT_PER_SAMPLE = 4096 * 1000 // SR   # 1 サンプル(4 ms)の、ずれのカウント(4096 = 1 ms)


# 補正の一覧: [(記録の開始からの秒(補正を入れた時刻), +1 足す・-1 減らす)]
def gps_corrections(files):
  pts = [p for f in files for p in f.gps_points()]
  pts.sort()
  out = []
  if not pts:
    return out
  k = 0          # それまでに足した数(減らした数は負)
  target = 0     # それまでに合わせた、ずれのサンプル数
  a_idx = pts[0][0]
  for idx, d in pts[1:]:
    t = int(d / DRIFT_PER_SAMPLE)   # 0 の側へ切り捨て
    n = t - target
    if n:
      a_sec, b_sec = (a_idx + k) // SR, (idx + k) // SR
      step = 1 if n > 0 else -1
      for i in range(1, abs(n) + 1):
        out.append((a_sec + i * (b_sec - a_sec) // abs(n), step))
        k += step
      target = t
    a_idx = idx
  return out


# 補正を、サンプルの並びに入れる(iter_frames の出力を、補正した通し番号の並びにする)
def correct_frames(segments, corrections):
  # 補正ごとに、raw の通し番号で切る所: 足す ... その番号の前のサンプルを写して入れる、減らす ... その番号を捨てる
  cuts, k = [], 0
  for sec, d in corrections:
    if d > 0:
      cuts.append(((sec + 1) * SR - 1 - k, d))   # 秒の最後(249 番目)に、その前(248 番目)を写す
    else:
      cuts.append(((sec + 1) * SR - k, d))       # 次の秒の頭になるはずのサンプルを捨てる
    k += d
  k, ci, last = 0, 0, None
  for i0, v, coarse in segments:
    end = i0 + len(v)
    # 波形が抜けている所にある補正は、ずらすだけ
    while ci < len(cuts) and cuts[ci][0] < i0:
      k += cuts[ci][1]
      ci += 1
    pos = 0
    while ci < len(cuts):
      c, d = cuts[ci]
      if d > 0 and c > end or d < 0 and c >= end:
        break
      loc = c - i0
      if loc > pos:
        yield i0 + pos + k, v[pos:loc], coarse[pos:loc]
      if d > 0:
        src = (v[loc - 1:loc], coarse[loc - 1:loc]) if loc > 0 else last
        if src is not None:
          yield c + k, src[0], src[1]
        pos = loc
      else:
        pos = loc + 1
      k += d
      ci += 1
    if pos < len(v):
      yield i0 + pos + k, v[pos:], coarse[pos:]
    if len(v):
      last = (v[-1:], coarse[-1:])


def gps3_lines(corrections, start):
  return [f"{start + timedelta(seconds=sec):%y%m%d%H%M%S},{d}" for sec, d in corrections]


# ---------------------------------------------------------------- WIN の書き出し

def bcd(n):
  return ((n // 10) << 4) | (n % 10)


def win_time(t):
  return bytes([bcd(t.year % 100), bcd(t.month), bcd(t.day), bcd(t.hour), bcd(t.minute), bcd(t.second)])


# 1 秒・1 チャンネル分(サンプル s は int の配列)
#   差分の大きさで、いちばん小さい書き方を選ぶ(0: 4 ビット、1: 1 バイト、2: 2 バイト、3: 3 バイト、4: 4 バイト)
def encode_channel(ch, s):
  s = np.asarray(s, np.int64)
  d = np.diff(s)
  mn, mx = (int(d.min()), int(d.max())) if len(d) else (0, 0)
  if -8 <= mn and mx <= 7:
    code = 0
  elif -128 <= mn and mx <= 127:
    code = 1
  elif -32768 <= mn and mx <= 32767:
    code = 2
  elif -(1 << 23) <= mn and mx < (1 << 23):
    code = 3
  else:
    code = 4
  head = struct.pack(">HHi", ch, (code << 12) | len(s), int(s[0]))
  if code == 0:
    nib = (d & 15).astype(np.uint8)
    if len(nib) % 2:
      nib = np.append(nib, np.uint8(0))
    body = ((nib[0::2] << 4) | nib[1::2]).astype(np.uint8).tobytes()
  elif code == 3:
    body = d.astype(">i4").view(np.uint8).reshape(-1, 4)[:, 1:].tobytes()
  else:
    body = d.astype({1: ">i1", 2: ">i2", 4: ">i4"}[code]).tobytes()
  return head + body


# 1 秒分のブロック(t は日本時間、data は (250, 3))
def encode_second(t, channels, data):
  body = win_time(t) + b"".join(encode_channel(ch, data[:, j]) for j, ch in enumerate(channels))
  return struct.pack(">I", len(body) + 4) + body


# WIN を読む(照合・校正値を求めるため)。返り値: [(時刻, {チャンネル: サンプルの配列})]
def read_win(data):
  out, p = [], 0
  while p + 10 <= len(data):
    size = struct.unpack(">I", data[p:p + 4])[0]
    blk = data[p:p + size]
    t = datetime.strptime(blk[4:10].hex(), "%y%m%d%H%M%S")
    q, chans = 10, {}
    while q < size:
      ch, info = struct.unpack(">HH", blk[q:q + 4])
      code, sr = info >> 12, info & 0xFFF
      first = struct.unpack(">i", blk[q + 4:q + 8])[0]
      q += 8
      if code == 0:
        nb = sr // 2
        b = np.frombuffer(blk, np.uint8, nb, q).astype(np.int64)
        nib = np.stack([b >> 4, b & 15], 1).ravel()[:sr - 1]
        d = np.where(nib >= 8, nib - 16, nib)
        q += nb
      elif code == 3:
        b = np.frombuffer(blk, np.uint8, 3 * (sr - 1), q).reshape(-1, 3).astype(np.int64)
        d = (b[:, 0] << 16) | (b[:, 1] << 8) | b[:, 2]
        d = np.where(d >= 1 << 23, d - (1 << 24), d)
        q += 3 * (sr - 1)
      else:
        dt = {1: ">i1", 2: ">i2", 4: ">i4"}[code]
        d = np.frombuffer(blk, dt, sr - 1, q).astype(np.int64)
        q += np.dtype(dt).itemsize * (sr - 1)
      chans[ch] = np.concatenate([[first], first + np.cumsum(d)])
    out.append((t, chans))
    p += size
  return out


# 値 V を WIN の値にする: floor(ゲイン × V + オフセット)
#   offsets・gains ... (2, 3)。行 0 が細かい記録、行 1 が粗い記録の、チャンネルごとの値
def calibrate(v, coarse, offsets, gains):
  offsets, gains = np.asarray(offsets, np.float64), np.asarray(gains, np.float64)
  rows = coarse.astype(int)
  g = gains[rows]
  if np.isnan(g).any():
    raise RawError("粗い記録(大きな揺れ)の校正値がありません。大きな揺れを含む、変換ツールの WIN から --fit で求めてください")
  return np.floor(v * g + offsets[rows]).astype(np.int64)


# 区間ごとの値を、1 分ごとの WIN にまとめて sink に渡す
#   sink(相対パス, バイト列, 最初の秒の時刻, 値 (秒の数, 250, 3), 粗い記録か (秒の数, 250), 追記か)
#   区間の始めの端数(秒の途中から)と終わりの端数(1 秒に満たない分)は捨てる(変換ツールと同じ)
#   同じ分が区間の切れ目で 2 回に分かれたときは、追記する
def write_minutes(segments, start, header, offsets, gains, sink):
  station, channels = header["station"], header["channels"]
  written = set()
  carry = None   # 前の区間の終わりの 1 秒に満たない分(次の区間が続いていれば、前に付ける)
  for i0, v, coarse in segments:
    if carry is not None and carry[0] + len(carry[1]) == i0:
      i0, v, coarse = carry[0], np.concatenate([carry[1], v]), np.concatenate([carry[2], coarse])
    carry = None
    skip = (-i0) % SR
    v, coarse = v[skip:], coarse[skip:]
    sec0 = (i0 + skip) // SR
    nsec = len(v) // SR
    if len(v) > nsec * SR:
      carry = ((sec0 + nsec) * SR, v[nsec * SR:], coarse[nsec * SR:])
    if nsec == 0:
      continue
    c = coarse[:nsec * SR]
    w = calibrate(v[:nsec * SR], c, offsets, gains).reshape(nsec, SR, 3)
    c = c.reshape(nsec, SR)
    t0 = start + timedelta(seconds=sec0)
    k = 0
    while k < nsec:
      t = t0 + timedelta(seconds=k)
      m = min(nsec - k, 60 - t.second)
      name = f"{t:%y%m%d}/{station}_{t:%y%m%d%H%M}.win"
      blob = b"".join(encode_second(t + timedelta(seconds=s), channels, w[k + s]) for s in range(m))
      sink(name, blob, t, w[k:k + m], c[k:k + m], name in written)
      written.add(name)
      k += m
  return written


# ---------------------------------------------------------------- 校正値(etc/logger_cal.txt)

MODES = ("fine", "coarse")   # 細かい記録・粗い記録


# 拠点ID ごとに {(チャンネル, 記録の種類): (オフセット, ゲイン)} と、求めたときのヘッダーの校正値の識別
def load_cal(path=CAL_FILE):
  cal = {}
  path = Path(path)
  if not path.exists():
    return cal
  for line in path.read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if not line:
      continue
    station, ch, mode, gain, off, key = line.split()
    ent = cal.setdefault(station, {"channels": {}, "keys": set()})
    ent["channels"][(int(ch, 16), mode)] = (float(off), float(gain))
    ent["keys"].add(key)
  return cal


# offsets・gains は (2, 3)(行 0 が細かい記録、行 1 が粗い記録)。ゲインが nan なら、求められなかった
def save_cal(station, channels, offsets, gains, key, path=CAL_FILE):
  path = Path(path)
  lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
  lines = [l for l in lines if not (l.split() and l.split()[0] == station)]
  if not lines:
    lines = ["# ロガーの raw を WIN にするときの校正値(raw_to_win.py の --fit で求める)",
             "#   WIN の値 = floor(ゲイン × V + オフセット)。fine は細かい記録、coarse は粗い記録(大きな揺れ)",
             "#   オフセットは、ほぼ -(ヘッダーのオフセット) × ゲイン。識別は、求めたときの raw のヘッダーの校正値の部分",
             "# 拠点ID    チャンネル  記録    ゲイン        オフセット     識別"]
  for i, mode in enumerate(MODES):
    for j, ch in enumerate(channels):
      lines.append(f"{station}  {ch:04x}        {mode:<6}  {float(gains[i][j]):.10f}  "
                   f"{float(offsets[i][j]):+.8f}  {key}")
  path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def calibration_for(header, cal, report=print):
  ent = cal.get(header["station"])
  want = [(ch, mode) for mode in MODES for ch in header["channels"]]
  if ent is None or any(k not in ent["channels"] for k in want):
    raise RawError(f"拠点 {header['station']} の校正値が {CAL_FILE.name} にありません。"
                   "変換ツールで変換した WIN があれば、--fit で求めてください")
  if header["cal_key"] not in ent["keys"]:
    report(f"  ※ raw のヘッダーの校正値({header['cal_key']})が、校正値を求めたとき"
           f"({', '.join(sorted(ent['keys']))})と違います。ゲインが変わっているかもしれないので、"
           "変換ツールの WIN があれば --verify で確かめてください")
  offs = [[ent["channels"][(ch, mode)][0] for ch in header["channels"]] for mode in MODES]
  gains = [[ent["channels"][(ch, mode)][1] for ch in header["channels"]] for mode in MODES]
  return offs, gains


# 値 V と WIN の値の組から、floor(g × V + c) = W になるゲイン g とオフセット c を求める
#   g を決めると、c の範囲は [max(W - g V), min(W + 1 - g V)) で、その幅 h(g) は g について上に凸なので、
#   h が最大になる g を黄金分割で探し、c はその範囲の真ん中にする
#   返り値: (g, c, c の範囲の幅)。合うものが無ければ None
def fit_channel(v, w):
  x = np.asarray(v, np.float64)
  y = np.asarray(w, np.float64)
  u = np.unique(np.stack([x, y], 1), axis=0)
  x, y = u[:, 0], u[:, 1]

  def width(g):
    return np.min(y + 1 - g * x) - np.max(y - g * x)
  g0 = np.dot(x, y) / np.dot(x, x) if np.dot(x, x) else 1.0
  lo, hi = g0 - 1e-3, g0 + 1e-3
  r = (np.sqrt(5) - 1) / 2
  a, b = hi - r * (hi - lo), lo + r * (hi - lo)
  fa, fb = width(a), width(b)
  for _ in range(120):
    if fa < fb:
      lo, a, fa = a, b, fb
      b = lo + r * (hi - lo)
      fb = width(b)
    else:
      hi, b, fb = b, a, fa
      a = hi - r * (hi - lo)
      fa = width(a)
  g = (lo + hi) / 2
  h = width(g)
  if h <= 0:
    return None
  c = (np.max(y - g * x) + np.min(y + 1 - g * x)) / 2
  return g, c, h


# ---------------------------------------------------------------- 変換ツールの WIN(照合・校正値を求める)

class Reference:
  def __init__(self, path):
    self.path = Path(path)
    self.files = {}
    if self.path.is_dir():
      for p in self.path.rglob("*.win"):
        self.files[p.name] = p
      self.zip = None
    else:
      self.zip = zipfile.ZipFile(self.path)
      for info in self.zip.infolist():
        if info.filename.endswith(".win"):
          self.files[info.filename.rsplit("/", 1)[-1]] = info

  def read(self, name):
    ref = self.files.get(name)
    if ref is None:
      return None
    return self.zip.read(ref) if self.zip else ref.read_bytes()


# ---------------------------------------------------------------- まとめ

def open_inputs(paths, report=print):
  files = [RawFile(p) for p in paths]
  stations = {f.header["station"] for f in files}
  if len(stations) > 1:
    raise RawError(f"違う拠点のファイルが混ざっています: {', '.join(sorted(stations))}")
  files.sort(key=lambda f: f.first_idx)
  starts = {f.start for f in files if f.start is not None}
  if not starts:
    raise RawError("記録の開始時刻(GPS のレコード)が見つかりません")
  if len(starts) > 1:
    raise RawError("ファイルごとに記録の開始時刻が違います(別の回収のファイルが混ざっている?): "
                   + ", ".join(f"{t:%Y-%m-%d %H:%M:%S}" for t in sorted(starts)))
  return files, starts.pop()



# いくつかの拠点・回収の分をまとめて開き、回収(拠点と記録の開始時刻)ごとに分ける
#   返り値: [(files, start)](拠点ID・記録の開始の順)。files は open_inputs と同じく、カードの順
#   GPS のレコードが無いファイルは、同じ拠点の回収が 1 つだけならそこに入れる(決められなければエラー)
def open_groups(paths, report=print):
  files = [RawFile(p) for p in paths]
  groups = {}
  for f in files:
    if f.start is not None:
      groups.setdefault((f.header["station"], f.start), []).append(f)
  for f in files:
    if f.start is None:
      keys = [k for k in groups if k[0] == f.header["station"]]
      if len(keys) != 1:
        raise RawError(f"{f.path.name}: 記録の開始時刻(GPS のレコード)が無く、どの回収の分か決められません")
      groups[keys[0]].append(f)
  out = []
  for key in sorted(groups):
    fs = sorted(groups[key], key=lambda f: f.first_idx)
    seen = {}
    for f in fs:
      if f.header["card"] in seen:
        raise RawError(f"同じ回収の {f.header['card']} 枚目が 2 つあります: {seen[f.header['card']].path.name}, {f.path.name}")
      seen[f.header["card"]] = f
    out.append((fs, key[1]))
  return out

def print_info(files, start, report=print):
  h = files[0].header
  report(f"拠点ID: {h['station']}  位置: {h['location']}  チャンネル: "
         + " ".join(f"{c:04x}" for c in h["channels"]))
  report(f"ヘッダーの校正値({h['cal_key']}): オフセット {h['cal_offsets']}  ゲイン {h['cal_gains']}")
  report(f"記録の開始: {start:%Y-%m-%d %H:%M:%S}(日本時間)")
  for f in files:
    a, b = f.first_idx, f.last_frame()
    report(f"  {f.path.name}: カード {f.header['card']}  波形 {len(f.w_recs)} レコード・GPS {len(f.g_recs)} レコード  "
           f"{start + timedelta(seconds=a / SR):%Y-%m-%d %H:%M:%S} 〜 "
           f"{start + timedelta(seconds=b / SR):%Y-%m-%d %H:%M:%S}"
           f"(記録の終わり {f.end * RECORD / 2**20:.0f} MB / {len(f.mm) * RECORD / 2**20:.0f} MB)")
    for a_end, b_start, rec in f.gaps():
      report(f"    ※ 波形が {(b_start - a_end) / SR / 3600:.2f} 時間抜けています: "
             f"{start + timedelta(seconds=a_end / SR):%Y-%m-%d %H:%M:%S.%f}"[:-4] + " 〜 "
             f"{start + timedelta(seconds=b_start / SR):%Y-%m-%d %H:%M:%S.%f}"[:-4]
             + f"(レコード {rec}、{rec * RECORD / 2**20:.1f} MB の所)")
    drift = f.gps_drift_ms()
    if len(drift):
      worst = float(np.abs(drift).max())
      report(f"    GPS との時刻のずれ: 最大 {worst:.2f} ms({len(drift)} 回の測定)")
    if f.after_end:
      report(f"    ※ 記録の終わりの後ろに、書かれたレコードが {f.after_end} 個あります(前の記録の残り?。使いません)")
    if len(f.bad_recs):
      runs = np.split(f.bad_recs, np.nonzero(np.diff(f.bad_recs) != 1)[0] + 1)
      report(f"    ※ 波形でも GPS でもないレコードが {len(f.bad_recs)} 個あります(イメージの読み取りの失敗?):")
      for r in runs[:10]:
        report(f"       {r[0] * RECORD / 2**20:.1f} MB 〜 {(r[-1] + 1) * RECORD / 2**20:.1f} MB"
               f"(先頭のバイト 0x{int(f.mm[r[0], 0]):02x})")


def print_corrections(corrections, start, enabled, report=print):
  if not corrections:
    report("GPS による時刻の補正: なし(ずれが 4 ms を超えた所はありません)")
    return
  state = "" if enabled else "(--no-gps-correction のため、補正しません)"
  report(f"GPS による時刻の補正: {len(corrections)} 回{state}(変換ツールの gps3 と同じ形。+1 足す・-1 減らす)")
  for line in gps3_lines(corrections, start):
    report(f"  {line}")


def main(argv=None):
  ap = argparse.ArgumentParser(description="ロガーの raw(CF カードの中身)を 1 分ごとの WIN にする")
  ap.add_argument("raw", nargs="+", help="raw ファイルか、CF カードのイメージ(1 回の回収の分をまとめて)")
  ap.add_argument("-o", "--output", help="出力先のフォルダ(.zip にすると ZIP に書く)")
  ap.add_argument("--daily", action="store_true",
                  help="-o のフォルダを日ごとの ZIP(daily_zips)として、YYMMDD.zip に直接書き足す(もう入っている WIN は書かない)")
  ap.add_argument("--info", action="store_true", help="ヘッダーと記録の範囲を表示するだけ")
  ap.add_argument("--verify", metavar="REF", help="変換ツールの WIN(ZIP かフォルダ)と比べる(書かない)")
  ap.add_argument("--fit", metavar="REF", help="変換ツールの WIN(ZIP かフォルダ)から校正値を求める")
  ap.add_argument("--fit-minutes", type=int, default=200, help="--fit で使う分の数(揺れの大きい分から。既定 200)")
  ap.add_argument("--save-cal", action="store_true", help="--fit で求めた校正値を etc/logger_cal.txt に書く")
  ap.add_argument("--cal", help="校正値のファイル(既定は etc/logger_cal.txt)")
  ap.add_argument("--no-gps-correction", action="store_true",
                  help="GPS による時刻の補正(サンプルを足す・減らす。変換ツールの gps3)をしない")
  args = ap.parse_args(argv)

  try:
    files, start = open_inputs(args.raw)
    print_info(files, start)
    corrections = gps_corrections(files)
    print_corrections(corrections, start, not args.no_gps_correction)
    if args.info:
      return 0
    if args.no_gps_correction:
      corrections = []
    header = files[0].header
    if args.fit:
      return run_fit(files, start, header, Reference(args.fit), args.fit_minutes, args.save_cal,
                     Path(args.cal) if args.cal else CAL_FILE, corrections)
    offsets, gains = calibration_for(header, load_cal(args.cal or CAL_FILE))
    for mode, offs, gs in zip(("細かい記録", "粗い記録"), offsets, gains):
      print(f"校正値({mode}): " + "  ".join(f"{c:04x}: {g:.10f}, {o:+.6f}"
                                       for c, o, g in zip(header["channels"], offs, gs)))
    if args.verify:
      return run_verify(files, start, header, offsets, gains, Reference(args.verify), corrections)
    if not args.output:
      ap.error("-o(出力先)か --info・--verify・--fit を指定してください")
    return run_convert(files, start, header, offsets, gains, Path(args.output), corrections, args.daily)
  except RawError as e:
    print(f"エラー: {e}")
    return 1


# ---------------------------------------------------------------- 書き出し

# 記録の最初と最後の時刻(日本時間)
def record_range(files, start):
  return (start + timedelta(seconds=files[0].first_idx / SR),
          start + timedelta(seconds=max(f.last_frame() for f in files) / SR))


# 拠点ごとの ZIP の名前(例: 0970355243_Hokudai(260826-261115).zip。変換ツールの ZIP の名前に似せる)
def source_zip_name(station, name, files, start):
  a, b = record_range(files, start)
  return f"{station}_{name}({a:%y%m%d}-{b:%y%m%d}).zip"


# フォルダに書く: <フォルダ>/<YYMMDD>/<拠点ID>_<YYMMDDhhmm>.win
class FolderWriter:
  def __init__(self, folder):
    self.folder = Path(folder)
    self.count = 0

  def add(self, name, blob, append):
    p = self.folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "ab" if append else "wb") as fh:
      fh.write(blob)
    self.count += 0 if append else 1

  def close(self, ok):
    pass


# 1 つの ZIP に書く: 中は <拠点ID>/<YYMMDD>/<拠点ID>_<YYMMDDhhmm>.win(変換ツールの ZIP と同じ。source_zips に置ける)
#   書いている間は .part にし、終わったら名前を変える(中止・エラーのときは消す)
class ZipWriter:
  def __init__(self, path, top):
    self.path = Path(path)
    if self.path.exists():
      raise RawError(f"{self.path} はもうあります(上書きしません)")
    self.path.parent.mkdir(parents=True, exist_ok=True)
    self.part = self.path.with_name(self.path.name + ".part")
    self.zf = zipfile.ZipFile(self.part, "w", zipfile.ZIP_DEFLATED)
    self.top = top
    self.pending = {}     # ZIP は追記ができないので、分が分かれたときに備えて、1 つ前の分だけ持っておく
    self.count = 0

  def flush(self):
    for k in list(self.pending):
      self.zf.writestr(f"{self.top}/{k}", self.pending.pop(k))

  def add(self, name, blob, append):
    if append:
      self.pending[name] += blob
      return
    self.flush()
    self.pending[name] = blob
    self.count += 1

  def close(self, ok):
    try:
      if ok:
        self.flush()
    finally:
      self.zf.close()
    if ok:
      os.replace(self.part, self.path)
    else:
      self.part.unlink(missing_ok=True)


# 日ごとの ZIP(daily_zips/<YYMMDD>.zip。中は <拠点ID>/<YYMMDD>/<拠点ID>_<YYMMDDhhmm>.win)に、直接書き足す
#   repackage_to_daily_zip.py と同じく、同じ名前の WIN ファイルがもう入っていれば書かない
#   1 日分をためてから書く。前からある日の ZIP は、写しに書き足してから置き換える(途中で止まっても壊さない)
#   中止・エラーのときは、書き終えた日はそのまま残し、書いている途中の日は書かない
class DailyZipWriter:
  def __init__(self, folder):
    self.folder = Path(folder)
    self.day = None
    self.buf = {}
    self.added = {}       # {日(YYMMDD): 書いた WIN ファイルの数}
    self.appended = {}    # {前からあった日: 書き足した時間(datetime)の集まり}
    self.skipped = 0      # もう入っていたので書かなかった数
    self.count = 0

  def add(self, name, blob, append):
    if append:
      self.buf[name] += blob
      return
    day = name.split("/")[0]
    if day != self.day:
      self.flush()
      self.day = day
    self.buf[name] = blob

  def flush(self):
    if not self.buf:
      return
    path = self.folder / f"{self.day}.zip"
    existed = path.exists()
    have = set()
    if existed:
      with zipfile.ZipFile(path) as z:
        have = set(z.namelist())
    items = {}
    for name, blob in self.buf.items():
      base = name.split("/")[-1]
      key = f"{base[:10]}/{name}"
      if key not in have:
        items[key] = blob
    self.skipped += len(self.buf) - len(items)
    self.buf = {}
    if not items:
      return
    self.folder.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    if existed:
      shutil.copyfile(path, part)
    try:
      with zipfile.ZipFile(part, "a" if existed else "w", zipfile.ZIP_DEFLATED) as z:
        for key in sorted(items):
          z.writestr(key, items[key])
      os.replace(part, path)
    except BaseException:
      part.unlink(missing_ok=True)
      raise
    self.added[self.day] = len(items)
    self.count += len(items)
    if existed:
      self.appended[self.day] = {datetime.strptime(k.split("_")[-1][:8], "%y%m%d%H") for k in items}

  def close(self, ok):
    if ok:
      self.flush()
    self.buf = {}


# WIN にして writer に書く。progress(済んだ秒, 全体の秒) は 1 分ごとに呼ぶ(例外を投げると止まる)
def convert(files, start, header, offsets, gains, writer, corrections=(), progress=None, report=print):
  a, b = record_range(files, start)
  total = max((b - a).total_seconds(), 1)

  def sink(name, blob, t, w, coarse, append):
    writer.add(name, blob, append)
    if progress and not append:
      progress(min((t - a).total_seconds(), total), total)
  ok = False
  try:
    write_minutes(correct_frames(iter_frames(files, report), corrections), start, header, offsets, gains, sink)
    ok = True
  finally:
    writer.close(ok)
  return writer


def run_convert(files, start, header, offsets, gains, out, corrections=(), daily=False):
  if daily:
    writer = DailyZipWriter(out)
  elif out.suffix.lower() == ".zip":
    writer = ZipWriter(out, header["station"])
  else:
    writer = FolderWriter(out)
  last = [0]

  def progress(done, total):
    if writer.count // 10000 > last[0]:
      last[0] = writer.count // 10000
      print(f"  {writer.count} ファイル({start + timedelta(seconds=files[0].first_idx / SR + done):%Y-%m-%d %H:%M})")
  convert(files, start, header, offsets, gains, writer, corrections, progress)
  print(f"WIN ファイル: {writer.count} 個 → {out}")
  if daily:
    print_daily_result(writer)
  return 0


def print_daily_result(writer, report=print):
  new = sorted(d for d in writer.added if d not in writer.appended)
  if new:
    report(f"新しくできた日: {len(new)} 日({new[0]}〜{new[-1]})")
  for d in sorted(writer.appended):
    report(f"前からある日に書き足した: {d}.zip  {writer.added[d]} 個")
  if writer.skipped:
    report(f"もう入っていたので書かなかった WIN ファイル: {writer.skipped} 個")


def run_verify(files, start, header, offsets, gains, ref, corrections=()):
  made, same, diff, missing = {}, 0, [], []

  def sink(name, blob, append):
    base = name.rsplit("/", 1)[-1]
    made[base] = made.get(base, b"") + blob if append else blob

  # 分が切れ目で分かれることがあるので、1 つ前の分まで持ってから比べる
  def check(base):
    nonlocal same
    want = ref.read(base)
    if want is None:
      missing.append(base)
    elif want == made[base]:
      same += 1
    else:
      diff.append(base)

  done = [0]

  def sink2(name, blob, t, w, coarse, append):
    base = name.rsplit("/", 1)[-1]
    if not append:
      for k in [k for k in made if k != base]:
        check(k)
        del made[k]
        done[0] += 1
        if done[0] % 10000 == 0:
          print(f"  {done[0]} ファイル({t:%Y-%m-%d %H:%M}) 一致 {same}・違う {len(diff)}")
    sink(name, blob, append)

  written = write_minutes(correct_frames(iter_frames(files), corrections), start, header, offsets, gains, sink2)
  for k in list(made):
    check(k)
  names = {n.rsplit("/", 1)[-1] for n in written}
  only_ref = sorted(set(ref.files) - names)
  print(f"\n一致: {same}  違う: {len(diff)}  変換ツールに無い: {len(missing)}  こちらで作られない: {len(only_ref)}")
  for title, lst in (("違う", diff), ("変換ツールに無い", missing), ("こちらで作られない", only_ref)):
    if lst:
      print(f"  {title}: " + ", ".join(lst[:10]) + (" ..." if len(lst) > 10 else ""))
  return 0 if not (diff or missing or only_ref) else 2


def run_fit(files, start, header, ref, n_minutes, save, cal_path=CAL_FILE, corrections=()):
  # 校正の前の値 V のまま、揺れの大きい分を集める(ゲインの範囲が狭く決まり、粗い記録も入る)
  import heapq
  heap = []

  def sink(name, blob, t, w, coarse, append):
    base = name.rsplit("/", 1)[-1]
    if append or base not in ref.files:
      return
    amp = int(np.abs(w).max())
    item = (amp, base, w.copy(), coarse.copy())
    if len(heap) < n_minutes:
      heapq.heappush(heap, item)
    elif amp > heap[0][0]:
      heapq.heapreplace(heap, item)

  ident = [[0, 0, 0], [0, 0, 0]], [[1.0] * 3, [1.0] * 3]
  write_minutes(correct_frames(iter_frames(files), corrections), start, header, *ident, sink)
  if not heap:
    raise RawError("変換ツールの WIN に、raw と同じ時刻のファイルがありません")
  vs, ws, cs = [], [], []
  for amp, base, v, c in heap:
    blocks = read_win(ref.read(base))
    if len(blocks) != len(v):
      print(f"  ※ {base}: 秒の数が違うので使いません(こちら {len(v)}・変換ツール {len(blocks)})")
      continue
    vs.append(v.reshape(-1, 3))
    cs.append(c.ravel())
    ws.append(np.stack([np.concatenate([b[1][ch] for b in blocks]) for ch in header["channels"]], 1))
  v, w, c = np.concatenate(vs), np.concatenate(ws), np.concatenate(cs)
  print(f"校正値を求めます: {len(vs)} 分({len(v)} サンプル。うち粗い記録 {int(c.sum())}。揺れの大きい分から)")
  offsets, gains = [[], []], [[], []]
  for i, (mode, sel) in enumerate((("細かい記録", ~c), ("粗い記録", c))):
    for j, ch in enumerate(header["channels"]):
      if not sel.any():
        offsets[i].append(float("nan"))
        gains[i].append(float("nan"))
        print(f"  {ch:04x}({mode}): 変換ツールの WIN に無いので、求められません")
        continue
      res = fit_channel(v[sel, j], w[sel, j])
      if res is None:
        raise RawError(f"チャンネル {ch:04x}({mode}): floor(g × V + c) で合う g・c がありません(形式が違う?)")
      g, off, h = res
      offsets[i].append(off)
      gains[i].append(g)
      hdr = header["cal_offsets"]
      a = -hdr[(3 if i == 0 else 0) + j] if len(hdr) >= 6 else None
      note = f"。ヘッダーのオフセットから {a * g:+.6f}" if a is not None else ""
      print(f"  {ch:04x}({mode}): ゲイン {g:.10f}  オフセット {off:+.6f}(幅 {h:.2e}{note})")
  if save:
    save_cal(header["station"], header["channels"], offsets, gains, header["cal_key"], cal_path)
    print(f"{cal_path} に書きました。--verify で全ファイルを確かめてください")
  return 0


if __name__ == "__main__":
  sys.exit(main())
