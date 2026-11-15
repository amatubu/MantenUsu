# make_cf_image.py
# ロガー(EDR-X7000)の CF カードを初期化して設定値を書き込んだのと同じ、カードのイメージを作る
#   (Windows の設定/データ変換ソフトの「Setting」でカードに書き込むのの代わり。できたイメージを dd でカードに書く)
#
#   カードの中身は、先頭 1 KB が設定値で、残りはカードの最後まで 0xFF
#   (ツールで初期化したカードを Mac の dd で取ったもの raw/image/bamba_no_data.img で確かめた)
#   設定値の 1 KB は、ツールが保存する設定値のファイル(/Volumes/home/_満天地震計/set の 1 KB のファイル)と同じ形:
#     0x00〜0x09 ID(10 桁の数字)
#     0x0e      カードの枚数(Number of card)
#     0x0f      01(250sps などの設定とみられる。いまの設定のときの値)
#     0x10〜0x15 電圧の判定値(乾電池: 11.7・8.3・7.0 V)
#     0x16      カードの番号(ツールは 00。記録を始めるとロガーが 01・02 と書く)
#     0x17      01(GPS 補正間隔 1h・位置固定などの設定とみられる。いまの設定のときの値)
#     0x18〜0x37 位置 "4232.6000,N,14047.1000,E000020.0"(緯度 度2桁+分、経度 度3桁+分、標高 整数6桁+小数1桁)
#     0x38〜0x39 Sensor check level
#     0x3a〜0x3f チャンネル番号 3 つ
#     0x40 から後ろは 0(記録を始めるとロガーが校正値などを書く)
#   サンプリング(250sps)・電源(乾電池)・GPS 補正間隔(1h)・測位(位置固定)・平均処理(なし)は、いまの設定の値だけに
#   対応する(ほかの値のときにどのバイトがどう変わるかは、まだ調べていない)
#
# 使い方(プロジェクトのフォルダで):
#   設定値を指定して作る:
#     python3 scripts/make_cf_image.py -o bamba.img --id 1070432003 --lat 42 32.6 --lon 140 47.1 --alt 20 \
#       --channels 8D10 8D11 8D12 --cards 2 --sensor 1000
#   ツールが保存した設定値のファイルから作る:
#     python3 scripts/make_cf_image.py -o bamba.img --setting "/Volumes/home/_満天地震計/set/1070432003_2601232154_馬場邸(2枚)"
#   設定値の 1 KB のファイルだけを作る(ツールの「Use the formerly making sheet」で読み込める形):
#     python3 scripts/make_cf_image.py --setting-only -o 1070432003_bamba --id ...
#   できたイメージと同じ名前に .sha256 を付けたファイルに、SHA-256 を書く
#   カードへの書き込みは dd で(デバイスを必ず確かめる。間違えると別のディスクを消す):
#     diskutil list external physical          ... 容量が 4.1 GB(4,076,642,304 バイト)の /dev/diskN を探す
#     diskutil unmountDisk /dev/diskN
#     sudo dd if=bamba.img of=/dev/rdiskN bs=4m status=progress
#     sudo shasum -a 256 /dev/rdiskN           ... .sha256 の値と同じか確かめる
#   イメージのファイルを作らずに書くこともできる(-o - で標準出力に流す。4 GB の空きが要らない):
#     python3 scripts/make_cf_image.py -o - --setting <設定値のファイル> | sudo dd of=/dev/rdiskN bs=4m
#     (終わりに SHA-256 を表示するので、sudo shasum -a 256 /dev/rdiskN の値と比べる)
import argparse
import hashlib
import re
import sys
from pathlib import Path

CARD_SIZE = 4076642304          # いま使っている 4 GB の CF カードの大きさ(バイト)
SETTING_SIZE = 1024
SETTING_PART = 0x40             # 設定値の部分(この後ろは 0 にする)
FIXED = {0x0f: 0x01, 0x17: 0x01}
VOLTAGES = bytes.fromhex("027501ba0172")   # 乾電池: 11.7・8.3・7.0 V
FILL = 0xFF
CHUNK = 4 << 20


class SettingError(Exception):
  pass


# 位置の文字列 "4232.6000,N,14047.1000,E000020.0"
def position_text(lat_deg, lat_min, lon_deg, lon_min, alt, ns="N", ew="E"):
  if not (0 <= lat_deg <= 90 and 0 <= lat_min < 60 and 0 <= lon_deg <= 180 and 0 <= lon_min < 60):
    raise SettingError("緯度・経度の値が範囲の外です")
  if not (0 <= alt < 1000000):
    raise SettingError("標高は 0〜999999.9 m で指定してください")
  if ns not in "NS" or ew not in "EW":
    raise SettingError("北緯・南緯は N・S、東経・西経は E・W で指定してください")
  text = f"{lat_deg:02d}{lat_min:07.4f},{ns},{lon_deg:03d}{lon_min:07.4f},{ew}{alt:08.1f}"
  if len(text) != 32:
    raise SettingError(f"位置の文字列の長さが違います: {text!r}")
  return text


# 設定値の 1 KB を作る
def make_setting(id_, lat, lon, alt, channels, cards=2, sensor=500, ns="N", ew="E"):
  if not re.fullmatch(r"\d{10}", id_):
    raise SettingError(f"ID は 10 桁の数字で指定してください: {id_!r}")
  if len(channels) != 3 or not all(re.fullmatch(r"[0-9A-Fa-f]{4}", c) for c in channels):
    raise SettingError(f"チャンネル番号は 16 進 4 桁を 3 つ指定してください: {channels}")
  if not 1 <= cards <= 6:
    raise SettingError("カードの枚数は 1〜6 で指定してください")
  if not 0 <= sensor <= 0xFFFF:
    raise SettingError("Sensor check level は 0〜65535 で指定してください")
  h = bytearray(SETTING_SIZE)
  h[0:10] = id_.encode("ascii")
  h[0x0e] = cards
  for pos, val in FIXED.items():
    h[pos] = val
  h[0x10:0x16] = VOLTAGES
  h[0x16] = 0
  h[0x18:0x38] = position_text(int(lat[0]), float(lat[1]), int(lon[0]), float(lon[1]), alt, ns, ew).encode("ascii")
  h[0x38:0x3a] = sensor.to_bytes(2, "big")
  for j, c in enumerate(channels):
    h[0x3a + 2 * j:0x3c + 2 * j] = int(c, 16).to_bytes(2, "big")
  return bytes(h)


# ツールが保存した設定値のファイル(1 KB)を読む
#   0x40 から後ろ(前に記録したカードの残りが入っていることがある)は 0 にし、カードの番号は 00 にする
def read_setting(path):
  data = Path(path).read_bytes()
  if len(data) != SETTING_SIZE:
    raise SettingError(f"{path}: 設定値のファイルは 1024 バイトのはずですが、{len(data)} バイトです")
  h = bytearray(data[:SETTING_PART]) + bytes(SETTING_SIZE - SETTING_PART)
  h[0x16] = 0
  describe(bytes(h))   # 読めるか確かめる
  return bytes(h)


# 設定値の中身を、人が読める形にする
def describe(h):
  id_ = h[0:10].decode("ascii", "replace")
  if not id_.isdigit():
    raise SettingError(f"ID が読めません: {id_!r}")
  pos = h[0x18:0x38].decode("ascii", "replace")
  m = re.fullmatch(r"(\d{2})(\d{2}\.\d{4}),([NS]),(\d{3})(\d{2}\.\d{4}),([EW])(\d{6}\.\d)", pos)
  if not m:
    raise SettingError(f"位置が読めません: {pos!r}")
  unknown = {f"0x{k:02x}": f"{h[k]:02x}" for k, v in FIXED.items() if h[k] != v}
  if h[0x10:0x16] != VOLTAGES:
    unknown["0x10-0x15"] = h[0x10:0x16].hex()
  return {
    "ID": id_,
    "緯度": f"{m[3]} {int(m[1])}° {m[2]}′",
    "経度": f"{m[6]} {int(m[4])}° {m[5]}′",
    "標高": f"{float(m[7]):.1f} m",
    "カードの枚数": h[0x0e],
    "Sensor check level": int.from_bytes(h[0x38:0x3a], "big"),
    "チャンネル": " ".join(f"{int.from_bytes(h[0x3a + 2 * j:0x3c + 2 * j], 'big'):04X}" for j in range(3)),
    "知らない値": unknown or None,
  }


# イメージを書く(先頭が設定値、残りは 0xFF)。返り値は SHA-256
#   path が "-" なら標準出力に流す(ファイルを作らずに、dd でカードへ書ける)
def write_image(path, setting, size=CARD_SIZE):
  if size < SETTING_SIZE or size % 512:
    raise SettingError(f"イメージの大きさが正しくありません: {size}")
  sha = hashlib.sha256()
  fill = bytes([FILL]) * CHUNK
  with (open(sys.stdout.fileno(), "wb", closefd=False) if str(path) == "-" else open(path, "wb")) as f:
    f.write(setting)
    sha.update(setting)
    left = size - len(setting)
    while left:
      n = min(left, CHUNK)
      f.write(fill[:n])
      sha.update(fill[:n])
      left -= n
  return sha.hexdigest()


# メッセージは標準エラーに出す(標準出力はイメージを流すのに使うことがある)
def log(*a):
  print(*a, file=sys.stderr)


def main(argv=None):
  ap = argparse.ArgumentParser(description="ロガーの CF カードを初期化・設定したのと同じイメージを作る")
  ap.add_argument("-o", "--output", required=True,
                  help="出力するイメージ(- で標準出力に流す。--setting-only のときは設定値のファイル)")
  ap.add_argument("--setting", help="ツールが保存した設定値のファイル(1 KB)から作る")
  ap.add_argument("--id", help="ID(10 桁)")
  ap.add_argument("--lat", nargs=2, metavar=("度", "分"), help="緯度(例: 42 32.6)")
  ap.add_argument("--lon", nargs=2, metavar=("度", "分"), help="経度(例: 140 47.1)")
  ap.add_argument("--south", action="store_true", help="南緯")
  ap.add_argument("--west", action="store_true", help="西経")
  ap.add_argument("--alt", type=float, help="標高(m)")
  ap.add_argument("--channels", nargs=3, metavar="CH", help="チャンネル番号 3 つ(例: 8D10 8D11 8D12)")
  ap.add_argument("--cards", type=int, default=2, help="カードの枚数(既定 2)")
  ap.add_argument("--sensor", type=int, default=500, help="Sensor check level(既定 500)")
  ap.add_argument("--size", type=int, default=CARD_SIZE, help=f"カードの大きさ(バイト。既定 {CARD_SIZE})")
  ap.add_argument("--setting-only", action="store_true", help="設定値の 1 KB のファイルだけを作る")
  ap.add_argument("--force", action="store_true", help="出力先がもうあっても上書きする")
  args = ap.parse_args(argv)

  try:
    if args.setting:
      setting = read_setting(args.setting)
    else:
      missing = [n for n in ("id", "lat", "lon", "alt", "channels") if getattr(args, n) is None]
      if missing:
        ap.error("--setting か、--id・--lat・--lon・--alt・--channels を指定してください(足りない: "
                 + ", ".join("--" + n for n in missing) + ")")
      setting = make_setting(args.id, args.lat, args.lon, args.alt, args.channels, args.cards, args.sensor,
                             "S" if args.south else "N", "W" if args.west else "E")
    info = describe(setting)
    for k, v in info.items():
      if v is not None:
        log(f"  {k}: {v}")
    if info["知らない値"]:
      log("※ いまの設定と違う値が入っています(サンプリング・電源などの設定を変えた?)。そのまま書きます")
    if args.output == "-":
      if args.setting_only:
        raise SettingError("--setting-only のときは、出力先にファイルを指定してください")
      if sys.stdout.isatty():
        raise SettingError("-o - は、イメージを標準出力に流します。dd などにパイプでつないでください")
      log(f"イメージを標準出力に流しています({args.size:,} バイト)...")
      digest = write_image("-", setting, args.size)
      log(f"SHA-256: {digest}(書き込んだカードの値と比べてください)")
      return 0
    out = Path(args.output)
    if out.exists() and not args.force:
      raise SettingError(f"{out} はもうあります(上書きするには --force)")
    if args.setting_only:
      out.write_bytes(setting)
      log(f"設定値のファイル: {out}")
      return 0
    log(f"イメージを書いています({args.size:,} バイト)...")
    digest = write_image(out, setting, args.size)
    Path(str(out) + ".sha256").write_text(f"{digest}  {out.name}\n")
    log(f"イメージ: {out}\nSHA-256: {digest}({out.name}.sha256 に保存)")
    return 0
  except SettingError as e:
    log(f"エラー: {e}")
    return 1


if __name__ == "__main__":
  sys.exit(main())
