# quake_list.py
# 地震リストの画面(quake_list_window.py)で見る一覧の保存と、CSV からの取り込み
#   一覧は2つ。同じ地震が両方に入ることもある(有珠山付近の地震で、気象庁の震源リストにもあるもの)
#     known ... 既知の地震(気象庁の震源リストと対応した地震の候補)
#     usu   ... 有珠山付近の地震(満点の観測点で震源を求めた地震)
#   ユーザーの設定(app_paths.QUAKE_LISTS_FILE)に JSON で保存する。1件は次を持つ
#     start, end ... 揺れの開始・終了(YYYY-MM-DD HH:MM:SS。この前後を波形で表示する)
#     time       ... 発生時刻(気象庁の発震時刻、または推定した発生時刻)
#     image      ... 確認用の画像のパス(無ければ空。データフォルダの中ならデータフォルダからの相対パスで持ち、
#                    データフォルダを変えたり別のマシンに移したりしても開けるようにする)
#   removed は「一覧から外す」で外した地震の開始({一覧: [開始, ...]})。地震の候補を探したときに自動で加える処理は、これを飛ばす
#     fields     ... 一覧の列に出す値({列の名前: 文字列})
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

import app_paths

KNOWN, USU = "known", "usu"
LIST_TITLES = {KNOWN: "既知の地震", USU: "有珠山付近の地震"}
# 一覧の列(名前, 幅, 右寄せか)
COLUMNS = {
  KNOWN: [("発震時刻", 165, False), ("震央地名", 130, False), ("M", 45, True), ("深さ[km]", 60, True),
          ("震央距離[km]", 80, True), ("満点の倍率", 75, True), ("対応", 135, False), ("有珠山付近", 70, False)],
  USU: [("発生時刻(推定)", 165, False), ("深さ[km]", 60, True), ("山頂から", 120, False), ("残差[秒]", 60, True),
        ("らしさ", 110, False), ("気象庁の M", 70, True), ("メモ", 185, False)],
}
# 波形を表示する範囲: 揺れの開始のこの秒数前から、終了のこの秒数後まで(分にそろえる。履歴への追加と同じ)
MARGIN_SECONDS = 30

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# 終了が分からないときの揺れの長さ [秒]
DEFAULT_LENGTH = 30
# 山頂から見た方位の名前(8方位)
DIRECTIONS = ("北", "北東", "東", "南東", "南", "南西", "西", "北西")


def load():
  try:
    with open(app_paths.QUAKE_LISTS_FILE, encoding="utf-8") as f:
      data = json.load(f)
  except (OSError, ValueError):
    data = {}
  lists = {KNOWN: data.get(KNOWN, []), USU: data.get(USU, [])}
  for kind in (KNOWN, USU):
    for e in lists[kind]:
      e["image"] = _relative_image(e.get("image", ""))   # 前の版はフルパスで持っていた
  removed = data.get("removed", {})
  lists["removed"] = {KNOWN: removed.get(KNOWN, []), USU: removed.get(USU, [])}
  return lists

def save(lists):
  path = app_paths.QUAKE_LISTS_FILE
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(".tmp")
  with open(tmp, "w", encoding="utf-8") as f:
    json.dump(lists, f, ensure_ascii=False, indent=1)
  tmp.replace(path)

# 確認用の画像のパス(データフォルダの中なら、データフォルダからの相対パスにする)
def _relative_image(path):
  if not path:
    return ""
  p = Path(path)
  if p.is_absolute():
    try:
      return p.relative_to(Path(app_paths.DATA_DIR).resolve()).as_posix()
    except ValueError:
      return str(p)
  return p.as_posix()

# 確認用の画像の今のパス(無ければ None)
def image_path(entry):
  text = entry.get("image", "")
  if not text:
    return None
  p = Path(text)
  if not p.is_absolute():
    p = Path(app_paths.DATA_DIR) / p
  return p if p.exists() else None

# 「一覧から外す」
def remove(lists, kind, entry):
  lists[kind].remove(entry)
  if entry["start"] not in lists["removed"][kind]:
    lists["removed"][kind].append(entry["start"])

# メモを書き換える(地震リストの画面の「メモを編集」。有珠山付近の地震の一覧だけがメモの列を持つ)
#   空にすればメモを消す
def set_memo(entry, memo):
  entry.setdefault("fields", {})["メモ"] = memo.strip()

# 波形を表示する範囲(メイン画面の日時の欄の形式 YYYYMMDDHHmm。終了の分も含む)
def display_range(entry):
  start = datetime.strptime(entry["start"], TIME_FORMAT)
  end = datetime.strptime(entry["end"], TIME_FORMAT)
  t0 = (start - timedelta(seconds=MARGIN_SECONDS)).replace(second=0)
  t1 = max((end + timedelta(seconds=MARGIN_SECONDS)).replace(second=0), t0 + timedelta(minutes=1))
  return t0.strftime("%Y%m%d%H%M"), t1.strftime("%Y%m%d%H%M")

# 波形の画面のタイトル
def title_of(kind, entry):
  f = entry["fields"]
  if kind == KNOWN:
    return f"{entry['time'][:19]} {f.get('震央地名', '')} M{f.get('M', '')}(既知の地震)"
  return f"{entry['time'][:19]} 有珠山付近 深さ{f.get('深さ[km]', '')}km(有珠山付近の地震)"


# --- CSV からの取り込み ---

# 「終了」の列は時刻だけのことがある(開始の日付を使い、日をまたいでいれば翌日にする)
def _end_time(start, text):
  text = text.strip()
  if len(text) > 8:
    return datetime.strptime(text[:19], TIME_FORMAT)
  t = datetime.strptime(f"{start:%Y-%m-%d} {text}", TIME_FORMAT)
  return t + timedelta(days=1) if t < start else t

def _image(base, text):
  if not text:
    return ""
  p = (base / text).resolve()
  return _relative_image(p) if p.exists() else ""

def _entry(row, base, time, image_col, fields):
  start = datetime.strptime(row["開始"].strip()[:19], TIME_FORMAT)
  end = _end_time(start, row.get("終了", "") or row["開始"])
  return {"start": start.strftime(TIME_FORMAT), "end": end.strftime(TIME_FORMAT),
          "time": (time or row["開始"]).strip(), "image": _image(base, row.get(image_col, "")),
          "fields": fields}

def _known_entry(row, base):
  return _entry(row, base, row.get("気象庁: 発震時刻", ""), "画像", {
    "発震時刻": row.get("気象庁: 発震時刻", "")[:21], "震央地名": row.get("震央地名", ""), "M": row.get("M", ""),
    "深さ[km]": row.get("深さ[km]", ""), "震央距離[km]": row.get("震央距離[km]", ""),
    "満点の倍率": row.get("最大の倍率", ""), "対応": row.get("震源リストとの対応", "")})

# 山頂から見た震央の方位(例: 南西(222°))
def _azimuth_text(lat, lon):
  from obspy.geodetics import gps2dist_azimuth
  from quake_features import USU_SUMMIT
  dist_m, azimuth, _ = gps2dist_azimuth(USU_SUMMIT[0], USU_SUMMIT[1], lat, lon)
  return dist_m / 1000, f"{DIRECTIONS[int((azimuth + 22.5) % 360 // 45)]}({azimuth:.0f}°)"

# 震源の一覧の行に方位が無ければ、緯度・経度から求める
def _direction(row):
  try:
    return _azimuth_text(float(row["緯度"]), float(row["経度"]))[1]
  except (KeyError, ValueError):
    return ""

# 震源の一覧の行(2つの形式: 有珠山付近の地震の一覧 usu_quakes_*.csv と、--locate-quakes や
#   地震の候補の震源の画面の「一覧と図を保存」の *_locations.csv。列の名前が少し違う)
def _usu_entry(row, base):
  time = row.get("発生時刻(推定)", "")
  if not time and row.get("発生時刻", "").strip():
    # *_locations.csv の発生時刻は時刻だけ(開始の日付を付ける。日をまたいでいれば翌日)
    start = datetime.strptime(row["開始"].strip()[:19], TIME_FORMAT)
    t = datetime.strptime(f"{start:%Y-%m-%d} {row['発生時刻'].strip()[:8]}", TIME_FORMAT)
    if t < start - timedelta(hours=12):
      t += timedelta(days=1)
    time = f"{t:%Y-%m-%d} {row['発生時刻'].strip()}"
  image_col = "波形の画像" if "波形の画像" in row else "画像"
  e = _entry(row, base, time, image_col, {
    "発生時刻(推定)": time[:21],
    "深さ[km]": row.get("震源: 深さ[km](海面下)") or row.get("深さ[km](海面下)", ""),
    "山頂から": f"{row.get('山頂からの距離[km]', '')} km {row.get('山頂からの方位') or _direction(row)}".strip(),
    "残差[秒]": row.get("残差[秒]", ""), "らしさ": row.get("有珠山の地震らしさ", ""),
    "気象庁の M": row.get("M", ""), "メモ": row.get("メモ", "")})
  if not row.get("終了"):
    e["end"] = (datetime.strptime(e["start"], TIME_FORMAT) + timedelta(seconds=DEFAULT_LENGTH)).strftime(TIME_FORMAT)
  if not e["image"] and image_col == "画像" and row.get("画像"):
    # *_locations.csv の画像は、震源の確認用の画像のフォルダにある
    e["image"] = _image(base / "locate", row["画像"])
  return e

# 気象庁の震源リストの地震として、一覧に足りない値を有珠山付近の地震の CSV の行から作る
def _known_from_usu(row, base):
  e = _known_entry(row, base)
  e["image"] = _image(base, row.get("波形の画像", ""))
  e["fields"]["震央距離[km]"] = row.get("気象庁の震央との距離[km]", "")
  e["fields"]["満点の倍率"] = ""
  e["fields"]["対応"] = row.get("有珠山の地震らしさ", "")
  return e

# CSV を読み、取り込む地震を一覧ごとに返す
#   地震の候補の一覧(--detect-quakes の quake_candidates_*.csv) ... 震源リストと対応した行を「既知の地震」へ
#   震源の一覧(usu_quakes_*.csv や --locate-quakes の *_locations.csv) ... すべての行を「有珠山付近の地震」へ。
#     気象庁の発震時刻がある行は「既知の地震」にも入れる
#   戻り値 ... {KNOWN: [...], USU: [...]}(どちらの形式でもなければ None)
def read_csv(path):
  path = Path(path)
  with open(path, encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))
  if not rows or "開始" not in rows[0]:
    return None
  base = path.parent
  out = {KNOWN: [], USU: []}
  if "震源リストとの対応" in rows[0]:
    out[KNOWN] = [_known_entry(r, base) for r in rows if r["震源リストとの対応"].startswith("対応")]
  elif "山頂からの距離[km]" in rows[0]:
    out[USU] = [_usu_entry(r, base) for r in rows]
    out[KNOWN] = [_known_from_usu(r, base) for r in rows if r.get("気象庁: 発震時刻", "").strip()]
  else:
    return None
  return out

# 同じ地震を置き換えるとき、新しい方で空の値(メモ・らしさ・画像など)は前の値を残す
def _combine(old, new):
  e = dict(new)
  e["image"] = new.get("image") or old.get("image", "")
  e["fields"] = {**old.get("fields", {}), **{k: v for k, v in new["fields"].items() if v}}
  return e

# 取り込む地震を一覧に加える(同じ開始の地震は、取り込むもので置き換える。
#   ただし、有珠山付近の地震の CSV から作った既知の地震は、すでにあれば置き換えない)
#   skip_removed ... 「一覧から外す」で外した地震は加えない(自動で加えるとき)。
#                    False なら加え、外した印を消す(自分で CSV を取り込んだとき)
#   戻り値 ... {一覧: (加えた数, 置き換えた数)}
def merge(lists, new, keep_existing_known=False, skip_removed=False):
  result = {}
  for kind in (KNOWN, USU):
    by_start = {e["start"]: e for e in lists[kind]}
    removed = lists["removed"][kind]
    added = replaced = 0
    for e in new[kind]:
      if e["start"] in removed:
        if skip_removed:
          continue
        removed.remove(e["start"])
      if e["start"] in by_start:
        if kind == KNOWN and keep_existing_known:
          continue
        replaced += 1
        e = _combine(by_start[e["start"]], e)
      else:
        added += 1
      by_start[e["start"]] = e
    lists[kind] = sorted(by_start.values(), key=lambda e: e["start"])
    result[kind] = (added, replaced)
  return result

# 地震の候補を探したあとに、震源リストと対応した候補を「既知の地震」に加える
#   csv_path ... 保存した地震の候補の一覧(quake_candidates_*.csv)
#   戻り値 ... (加えた数, 置き換えた数)
def add_from_detection(csv_path):
  new = read_csv(csv_path)
  if not new or not new[KNOWN]:
    return 0, 0
  lists = load()
  result = merge(lists, {KNOWN: new[KNOWN], USU: []}, skip_removed=True)
  save(lists)
  return result[KNOWN]

# 地震の候補の震源の画面の1行(候補と、震源を求めた結果)から、有珠山付近の地震を作る
def usu_entry_from_location(cand, r):
  import quake_detect
  import quake_locate
  loc, q = r["loc"], cand["jma"]
  local = r["kind"] in quake_locate.LOCAL_KINDS
  time = (r["ref"] + timedelta(seconds=loc["origin"])) if local and loc else cand["start"]
  dist_km, direction = _azimuth_text(r["lat"], r["lon"])
  end = cand["end"] if cand["end"] > cand["start"] else cand["start"] + timedelta(seconds=DEFAULT_LENGTH)
  # 確認用の画像: 震源の確認用の画像があればそれを、無ければ地震の候補の画像
  image = ""
  if r.get("image") and (quake_locate.OUTPUT_DIR / r["image"]).exists():
    image = _relative_image(quake_locate.OUTPUT_DIR / r["image"])
  elif (quake_detect.OUTPUT_DIR / f"{cand['start']:%Y%m%d_%H%M%S}.png").exists():
    image = _relative_image(quake_detect.OUTPUT_DIR / f"{cand['start']:%Y%m%d_%H%M%S}.png")
  row = cand.get("row", {})
  return {"start": cand["start"].strftime(TIME_FORMAT), "end": end.strftime(TIME_FORMAT),
          "time": time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-4], "image": image,
          "fields": {"発生時刻(推定)": time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-5],
                     "深さ[km]": quake_locate.depth_short(loc) if local else "",
                     "山頂から": f"{dist_km:.1f} km {direction}",
                     "残差[秒]": f"{loc['rms']:.3f}" if loc else "",
                     "らしさ": row.get("有珠山の地震らしさ", ""),
                     "気象庁の M": f"{q['M']:g}" if q and q.get("M") is not None else "",
                     "メモ": row.get("メモ", "")}}

# 気象庁の震源リストの地震として作る(有珠山付近の地震に加えるとき、震源リストと対応していれば既知の地震にも入れる)
def known_entry_from_location(cand, usu):
  q = cand["jma"]
  if not q:
    return None
  return {"start": usu["start"], "end": usu["end"], "time": f"{q['日時']:%Y-%m-%d %H:%M:%S.%f}"[:-5],
          "image": usu["image"],
          "fields": {"発震時刻": f"{q['日時']:%Y-%m-%d %H:%M:%S.%f}"[:-5], "震央地名": q["震央地名"],
                     "M": f"{q['M']:g}" if q.get("M") is not None else "", "深さ[km]": f"{q['深さ[km]']:g}",
                     "震央距離[km]": "", "満点の倍率": "", "対応": "有珠山付近の地震から"}}

# 有珠山付近の地震の一覧にある地震の開始(地震の候補の震源の画面の印に使う)
def usu_starts(lists=None):
  return {e["start"] for e in (lists or load())[USU]}

# 地震の候補の震源の画面から、有珠山付近の地震に加える・外す
#   戻り値 ... 加えたら True、外したら False
def toggle_usu(cand, r, add):
  lists = load()
  start = cand["start"].strftime(TIME_FORMAT)
  if not add:
    entry = next((e for e in lists[USU] if e["start"] == start), None)
    if entry:
      remove(lists, USU, entry)
      save(lists)
    return False
  usu = usu_entry_from_location(cand, r)
  known = known_entry_from_location(cand, usu)
  merge(lists, {USU: [usu], KNOWN: [known] if known else []}, keep_existing_known=True)
  save(lists)
  return True

# 有珠山付近の地震の一覧を、地震の候補の一覧の形式(quake_locate.read_candidates で読める CSV)で書き出す
#   地震リストの画面の「地図にプロット」で、地震の候補の震源の画面に渡すために使う
#   気象庁の震源は、既知の地震の一覧に同じ開始の地震があれば、その発震時刻で取得済みの震源リストから引く
#   (震源リストが無い日や見つからないときは、気象庁の列を空にする)
#   戻り値 ... 書き出した件数
USU_CANDIDATES_FIELDS = ["開始", "終了", "メモ", "有珠山の地震らしさ",
                         "気象庁: 発震時刻", "緯度", "経度", "深さ[km]", "M", "震央地名"]

def _jma_quake(time_text):
  import jma_hypocenter
  try:
    t = datetime.strptime(time_text.strip()[:21], "%Y-%m-%d %H:%M:%S.%f")
  except ValueError:
    try:
      t = datetime.strptime(time_text.strip()[:19], TIME_FORMAT)
    except ValueError:
      return None
  day = t.replace(hour=0, minute=0, second=0, microsecond=0)
  hits = [q for q in jma_hypocenter.load_day(day) if abs((q["日時"] - t).total_seconds()) < 1.0]
  return min(hits, key=lambda q: abs((q["日時"] - t).total_seconds())) if hits else None

def write_usu_candidates(path, lists=None):
  lists = lists or load()
  known = {e["start"]: e for e in lists[KNOWN]}
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=USU_CANDIDATES_FIELDS)
    w.writeheader()
    for e in sorted(lists[USU], key=lambda e: e["start"]):
      fields = e.get("fields", {})
      row = {"開始": e["start"], "終了": e["end"][11:19], "メモ": fields.get("メモ", ""),
             "有珠山の地震らしさ": fields.get("らしさ", "")}
      k = known.get(e["start"])
      q = _jma_quake(k.get("time") or k["fields"].get("発震時刻", "")) if k else None
      if q:
        row.update({"気象庁: 発震時刻": f"{q['日時']:%Y-%m-%d %H:%M:%S.%f}"[:-5], "緯度": f"{q['緯度']:.4f}",
                    "経度": f"{q['経度']:.4f}", "深さ[km]": f"{q['深さ[km]']:g}",
                    "M": f"{q['M']:g}" if q["M"] is not None else "", "震央地名": q["震央地名"]})
      w.writerow(row)
  return len(lists[USU])
