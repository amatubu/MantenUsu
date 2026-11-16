# cf_catalog.py
# CF カードの台帳(どのカードに何が入っているか)。UsuMantenCFTool.py の「カード一覧」で使う
#   カードには固有の番号が読めない(USB のカードリーダー越しには、リーダーの情報しか見えない)ので、
#   カードに貼ってあるラベルを、読み込み・初期化・登録のときに入れてもらい、それで分ける
#
#   <データフォルダ>/cf_cards/cf_cards.csv     ... ラベルごとに 1 行(いまの中身)。Excel で開けるように UTF-8(BOM 付き)
#   <データフォルダ>/cf_cards/cf_cards_log.csv ... したこと(登録・読み込み・初期化)の記録。初期化で消えた前の中身もここに残る
#
#   初期化して使い回してよいカード: 記録があり、その記録の最初と最後の分の WIN が、日ごとの ZIP(daily_zips)にあるもの
#   (Windows で読んだ前のカードは、Mac にイメージが無いので、イメージではなく WIN があるかで決める)
import csv
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
import zipfile

FIELDS = ["ラベル", "状態", "拠点ID", "読み替え", "拠点名", "枚目", "全枚数", "記録の始め", "記録の終わり", "いっぱい",
          "イメージ", "SHA-256", "WIN", "初期化した日", "初期化の拠点", "確かめた日時", "メモ"]
LOG_FIELDS = ["日時", "ラベル", "したこと", "内容"]

RECORDED = "記録あり"
BLANK = "初期化済み"
FOREIGN = "ロガーのカードではない"
EMPTY = "何も書かれていない"   # 先頭が全部 0xFF(設定値も無い。ロガーで使うには初期化する)

WIN_OK = "daily_zips にある"
WIN_PART = "daily_zips に一部だけ"
WIN_NONE = "daily_zips に無い"

TIME_FMT = "%Y-%m-%d %H:%M"


def catalog_paths(data_dir):
  d = Path(data_dir) / "cf_cards"
  return d / "cf_cards.csv", d / "cf_cards_log.csv"


# ラベルの並べ方: 数字の所は数として(CF-2 が CF-10 より前)
def label_key(label):
  return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.split(r"(\d+)", label) if t]



# ラベルの番号を 1 つ進める(桁はそろえる。満点10-009 → 満点10-010)。番号で終わらなければ、そのまま
def next_number(label):
  m = re.fullmatch(r"(.*?)(\d+)", label)
  if not m:
    return label
  return f"{m[1]}{int(m[2]) + 1:0{len(m[2])}d}"

def load(path):
  path = Path(path)
  if not path.exists():
    return {}
  with open(path, newline="", encoding="utf-8-sig") as f:
    return {r["ラベル"]: {k: r.get(k) or "" for k in FIELDS} for r in csv.DictReader(f) if r.get("ラベル")}


# 写しに書いてから置き換える(途中で止まっても壊さない)
def save(path, rows):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_name(path.name + ".part")
  with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, FIELDS)
    w.writeheader()
    for label in sorted(rows, key=label_key):
      w.writerow({k: rows[label].get(k, "") for k in FIELDS})
  os.replace(tmp, path)


def append_log(path, label, action, detail, now=None):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  new = not path.exists()
  with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
    w = csv.writer(f)
    if new:
      w.writerow(LOG_FIELDS)
    w.writerow([(now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), label, action, detail])


def new_row(label):
  return {k: "" for k in FIELDS} | {"ラベル": label}


# カードの中身(cf_card.card_info の結果と cf_card.card_range の結果)を行に書く
#   rng ... (最初の時刻, 最後の時刻, いっぱいか) か None
#   as_station ... 設定値が書かれないまま記録したカード(拠点ID 0000000000 など)を、どの拠点の分とみるか(拠点ID)。
#                  「読み替え」に書き、WIN はその拠点で探す。name はその拠点の名前にする
def set_content(row, info, name, rng, now=None, as_station=""):
  row["確かめた日時"] = (now or datetime.now()).strftime(TIME_FMT)
  row["読み替え"] = as_station or ""
  if not info.station:
    row.update({"状態": EMPTY if getattr(info, "empty", False) else FOREIGN, "拠点ID": "", "拠点名": "", "枚目": "", "全枚数": ""})
    clear_record(row)
    return row
  row.update({"拠点ID": info.station, "拠点名": name, "全枚数": str(info.cards)})
  if info.has_data and rng:
    a, b, full = rng
    row.update({"状態": RECORDED, "枚目": str(info.index or ""), "記録の始め": a.strftime(TIME_FMT),
                "記録の終わり": b.strftime(TIME_FMT), "いっぱい": "はい" if full else "いいえ"})
  else:
    row.update({"状態": BLANK, "枚目": ""})
    clear_record(row)
  return row


def clear_record(row):
  for k in ("記録の始め", "記録の終わり", "いっぱい", "イメージ", "SHA-256", "WIN"):
    row[k] = ""


# WIN を探す拠点ID(読み替えていれば、その拠点)
def station_of(row):
  return row.get("読み替え") or row.get("拠点ID", "")


def _station_text(row):
  if row.get("読み替え"):
    return f"{row['拠点名']}({row['拠点ID']} を {row['読み替え']} と読み替え)"
  return f"{row['拠点名']}({row['拠点ID']})"


# 中身を一言で(ログ・確かめるダイアログに出す)
def describe(row):
  if row.get("状態") == RECORDED:
    return (f"{_station_text(row)}の" + (f" {row['枚目']} 枚目、" if row.get("枚目") else "、")
            + f"{row['記録の始め']} 〜 {row['記録の終わり']}"
            + ("(いっぱい)" if row.get("いっぱい") == "はい" else "")
            + (f"、イメージ {row['イメージ']}" if row.get("イメージ") else "")
            + (f"、{row['WIN']}" if row.get("WIN") else ""))
  if row.get("状態") == BLANK:
    return f"初期化済み({row['拠点名'] or row['拠点ID']}" + (f"、{row['初期化した日']}" if row.get("初期化した日") else "") + ")"
  return row.get("状態") or "(未登録)"


# 同じ中身の行のラベル(カードを入れたとき、ラベルの候補にする)
def guess_label(rows, row):
  keys = ("拠点ID", "枚目", "記録の始め") if row.get("状態") == RECORDED else None
  if not keys:
    return None
  for label, r in rows.items():
    if r.get("状態") == RECORDED and all(r.get(k) == row.get(k) for k in keys):
      return label
  return None


# 記録の最初と最後の分の WIN が、日ごとの ZIP にあるか
#   端の分は途中から・途中までなので、1 分内側の分を見る(記録が 3 分に満たなければ、最初の分だけ)
def win_state(row, zips_dir):
  if row.get("状態") != RECORDED:
    return ""
  try:
    a = datetime.strptime(row["記録の始め"], TIME_FMT) + timedelta(minutes=1)
    b = datetime.strptime(row["記録の終わり"], TIME_FMT) - timedelta(minutes=1)
  except ValueError:
    return ""
  station = station_of(row)
  found = [_has_minute(zips_dir, station, t) for t in ([a, b] if b > a else [a])]
  return WIN_OK if all(found) else WIN_PART if any(found) else WIN_NONE


def _has_minute(zips_dir, station, t):
  p = Path(zips_dir) / f"{t:%y%m%d}.zip"
  if not p.exists():
    return False
  try:
    with zipfile.ZipFile(p) as z:
      return f"{station}/{t:%y%m%d}/{station}_{t:%y%m%d%H%M}.win" in set(z.namelist())
  except (OSError, zipfile.BadZipFile):
    return False


# 初期化して使い回してよいか
def reusable(row):
  return row.get("状態") == RECORDED and row.get("WIN") == WIN_OK


# 一覧の並べ方: 使い回せる古いものから。初期化済み、記録があってまだ WIN が無いもの、ほかの順
def list_key(row):
  if reusable(row):
    group = 0
  elif row.get("状態") in (BLANK, EMPTY):
    group = 1
  elif row.get("状態") == RECORDED:
    group = 2
  else:
    group = 3
  return (group, row.get("記録の始め") or row.get("初期化した日") or "", label_key(row["ラベル"]))


def summary(rows):
  rs = list(rows.values())
  rec = [r for r in rs if r["状態"] == RECORDED]
  return (f"台帳のカード {len(rs)} 枚: 記録あり {len(rec)} 枚(うち初期化してよい {sum(map(reusable, rec))} 枚)・"
          f"初期化済み {sum(r['状態'] == BLANK for r in rs)} 枚"
          + (f"・何も書かれていない {n} 枚" if (n := sum(r['状態'] == EMPTY for r in rs)) else ""))
