# station_names.py
# 満点地震計の拠点名を、表示のときだけ読み替える(外部に発表するときの名前。オプション。既定は読み替えない)
#   読み替えるかどうかは、メイン画面のチェックボックスで切り替える(ユーザーの設定の settings.json の station_names)
#   読み替える名前は etc/station_names.txt に書く(ユーザーの設定の etc に同じ名前のファイルがあれば、そちらを使う)
#     1行に1拠点: 内部名  正式名  [略称]  # 説明
#     内部名 ... SAC・チャンネル表の観測点コード(Bamba・Hokudai・Mimatsu・Nizaemn)
#     正式名 ... 図のタイトル・拠点の選択欄・結果の文など、ふつうの表示に使う名前
#     略称   ... 波形の縦軸・配置図の注記など、狭い所に使う名前(省くと正式名)
#   内部名(picks の JSON、CSV の列名・キャッシュのキー、ファイル名、ログの文)は変えない。
#   ファイルに無い拠点(ほかの機関の観測点など)は、そのままの名前を表示する
import re

import app_paths
from log_common import log_message

CONFIG_FILE = app_paths.ETC_DIR / "station_names.txt"
# 名前に使える文字(「拠点名.成分」の形で使うので「.」は使えない。空白は区切りに使う)
NAME_RE = re.compile(r"^[^\s.]+$")

# 読み替えるかどうかの設定の名前(settings.json)
SETTING_KEY = "station_names"

# 読み込んだ表 {内部名: (正式名, 略称)}。最初に使うときに読む
_table = None
# 読み替えるか(None ならまだ設定を読んでいない)
_enabled = None


# チャンネル表(manten_ch.tbl)にある拠点の内部名(読めなければ空)
def _channel_table_stations():
  try:
    lines = (app_paths.ETC_DIR / "manten_ch.tbl").read_text(encoding="utf-8").splitlines()
  except (OSError, UnicodeDecodeError):
    return set()
  return {cols[3] for cols in (ln.split() for ln in lines) if len(cols) >= 4 and not cols[0].startswith("#")}

# 読み替えの表を読む。戻り値 ... {内部名: (正式名, 略称)}
#   形がおかしい行や、ほかの拠点の名前とぶつかる行は使わない(ログに書く)
def load(path=None):
  path = CONFIG_FILE if path is None else path
  table = {}
  if not path.exists():
    return table
  try:
    text = path.read_text(encoding="utf-8")
  except (OSError, UnicodeDecodeError) as e:
    log_message(f"拠点名の読み替えのファイルを読めません: {path} ({e})")
    return table
  used = {}   # 表示の名前 -> 内部名(ぶつかりを調べるため)
  for num, raw in enumerate(text.splitlines(), 1):
    line = raw.split("#", 1)[0].strip()
    if not line:
      continue
    cols = line.split()
    if len(cols) not in (2, 3) or not all(NAME_RE.match(c) for c in cols):
      log_message(f"拠点名の読み替え: {path.name} の {num} 行目を使いません(「内部名 正式名 [略称]」の形で、"
                  f"「.」を含まない名前にしてください): {raw.strip()}")
      continue
    internal, full = cols[0], cols[1]
    short = cols[2] if len(cols) == 3 else full
    clash = [n for n in {full, short} if used.get(n, internal) != internal]
    if internal in table or clash:
      log_message(f"拠点名の読み替え: {path.name} の {num} 行目を使いません(名前がほかの行とぶつかります): {raw.strip()}")
      continue
    table[internal] = (full, short)
    used[internal] = internal
    used[full] = used[short] = internal
  # 表示の名前が、ほかの拠点の内部名とぶつかる場合も使わない(どの拠点か分からなくなるため)
  known = set(table) | _channel_table_stations()
  for internal, (full, short) in list(table.items()):
    if any(n in known and n != internal for n in (full, short)):
      log_message(f"拠点名の読み替え: {internal} の名前が、ほかの拠点の内部名と同じなので使いません")
      del table[internal]
  return table

# 表を読み直す(テストや、ファイルを直したあと)
#   enabled ... 読み替えるか(None なら設定のまま)
def reload(path=None, enabled=None):
  global _table, _enabled
  _table = load(path)
  if enabled is not None:
    _enabled = bool(enabled)
  return _table

# ファイルに書いてある読み替えの表(切り替えがオフでも返す)
def names():
  if _table is None:
    reload()
  return _table

# 読み替えるか(初めて使うときに settings.json から読む)
def is_enabled():
  global _enabled
  if _enabled is None:
    _enabled = bool(app_paths.load_settings().get(SETTING_KEY, False))
  return _enabled

# 読み替えを切り替える(save なら settings.json に覚えておき、次に起動したときも使う)
#   開いている図は描き直さない。この後に開いたり描き直したりした図・画面から変わる
def set_enabled(on, save=True):
  global _enabled
  _enabled = bool(on)
  if save:
    app_paths.save_settings({**app_paths.load_settings(), SETTING_KEY: _enabled})

# 今使う読み替えの表(切り替えがオフなら空)
def table():
  return names() if is_enabled() else {}

# 正式名(読み替えが無ければ内部名のまま)
def full(name):
  return table().get(name, (name, name))[0]

# 略称(狭い所に使う)
def short(name):
  return table().get(name, (name, name))[1]

# 「拠点名.成分」の拠点名を読み替える(例: Bamba.U -> Irie.U)
def channel(label, short_name=False):
  station, dot, comp = label.partition(".")
  return (short if short_name else full)(station) + dot + comp

# 表示の名前(正式名・略称・内部名のどれでも)から内部名を引く。知らない名前はそのまま返す
def internal(name):
  for key, (f, s) in table().items():
    if name in (f, s):
      return key
  return name

# 「拠点名.成分」の表示から、内部名の「拠点名.成分」に戻す
def internal_channel(label):
  station, dot, comp = label.partition(".")
  return internal(station) + dot + comp
