# pick_store.py
# 震源推定の画面で決めた P波・S波の到着時刻(手で直したものを含む)とパラメータを、地震ごとに JSON で保存・読み込みする
#   保存先: output/picks/<最初の P波の日時>.json(例: 20260520_235112.json)
#   震源推定の画面・P波・S波検出の画面・地震の候補の震源(画面とバッチ処理)で、同じ地震の範囲を開いたときに使う
#   (どの画面でも同じ到着時刻から震源を求め直せるようにするため)
#
#   JSON の中身:
#     {"version": 1, "saved": 保存した日時,
#      "stations": {拠点名: {"p": P波の日時, "p_manual": 手で指定したか, "s": S波の日時 または null, "s_manual": …}},
#      "excluded": [計算から外した拠点], "s_excluded": [S波を計算に使わない拠点],
#      "params": {震源推定の画面のパラメータ(vp, vp_vs, s_search, omori_k など)}}
import json
from datetime import datetime

import app_paths
from log_common import log_message

PICKS_DIR = app_paths.OUTPUT_DIR / "picks"
VERSION = 1
NAME_FORMAT = "%Y%m%d_%H%M%S"


def _iso(t):
  return t.isoformat(timespec="microseconds") if t is not None else None

def _parse(text):
  return datetime.fromisoformat(text) if text else None

# 保存した記録の、最初の P波の日時(計算から外した拠点を除く。全部外していれば全拠点)
def first_p(record):
  stations = record["stations"]
  used = [s["p"] for nm, s in stations.items() if s.get("p") and nm not in record.get("excluded", [])]
  times = used or [s["p"] for s in stations.values() if s.get("p")]
  return min(times) if times else None

# 到着時刻とパラメータを保存する
#   p_times, s_times ... {拠点名: 日時(datetime) または None}
#   p_manual, s_manual ... 手で指定した拠点の集合
#   excluded, s_excluded ... 計算から外した拠点・S波を計算に使わない拠点
#   params ... パラメータの辞書
#   replace ... この記録の元になった(読み込んだ)ファイル。名前が変わるときは消す
#   戻り値 ... 保存したファイルのパス(P波が1つもないときは保存せず None)
def save_record(p_times, s_times, p_manual, s_manual, excluded, s_excluded, params, replace=None):
  stations = {}
  for nm in sorted(set(p_times) | set(s_times)):
    p, s = p_times.get(nm), s_times.get(nm)
    if p is None and s is None:
      continue
    stations[nm] = {"p": _iso(p), "p_manual": nm in p_manual, "s": _iso(s), "s_manual": nm in s_manual}
  record = {"version": VERSION, "saved": datetime.now().isoformat(timespec="seconds"), "stations": stations,
            "excluded": sorted(excluded), "s_excluded": sorted(s_excluded),
            "params": {k: round(float(v), 3) for k, v in params.items()}}
  t = first_p(record)
  if t is None:
    return None
  PICKS_DIR.mkdir(parents=True, exist_ok=True)
  path = PICKS_DIR / f"{_parse(t):{NAME_FORMAT}}.json"
  with open(path, "w", encoding="utf-8") as f:
    json.dump(record, f, indent=2, ensure_ascii=False)
  if replace is not None and replace != path and replace.exists():
    replace.unlink()
  log_message(f"到着時刻を保存しました: {path}")
  return path

# 期間 [t0, t1] に最初の P波がある記録を探す(複数あれば、最後に保存したもの)
#   戻り値 ... (パス, 記録) または (None, None)
def find_record(t0, t1):
  if not PICKS_DIR.exists():
    return None, None
  found = []
  for path in PICKS_DIR.glob("*.json"):
    try:
      name_time = datetime.strptime(path.stem, NAME_FORMAT)
    except ValueError:
      continue
    # ファイル名の日時は秒未満を切り捨てているので、1秒の余裕をみる
    if not (t0.replace(microsecond=0) <= name_time <= t1):
      continue
    try:
      with open(path, encoding="utf-8") as f:
        record = json.load(f)
      t = _parse(first_p(record))
    except (OSError, ValueError, KeyError, TypeError) as e:
      log_message(f"到着時刻の記録を読めませんでした: {path}: {e}")
      continue
    if t is not None and t0 <= t <= t1:
      found.append((record.get("saved", ""), path, record))
  if not found:
    return None, None
  _saved, path, record = max(found, key=lambda x: (x[0], x[1].name))
  return path, record

# 記録の到着時刻を、基準の日時 ref からの秒にする
#   戻り値 ... (P波 {拠点名: 秒}, S波 {拠点名: 秒}, P波を手で指定した拠点, S波を手で指定した拠点)
def record_seconds(record, ref):
  p, s, p_manual, s_manual = {}, {}, set(), set()
  for nm, st in record["stations"].items():
    if st.get("p"):
      p[nm] = (_parse(st["p"]) - ref).total_seconds()
      if st.get("p_manual"):
        p_manual.add(nm)
    if st.get("s"):
      s[nm] = (_parse(st["s"]) - ref).total_seconds()
      if st.get("s_manual"):
        s_manual.add(nm)
  return p, s, p_manual, s_manual
