# jma_hypocenter.py
# 気象庁の震源リスト(日ごと・暫定値)を取得し、日ごとのCSVとして jma_hypocenter フォルダに保存する
#   取得元: https://www.data.jma.go.jp/eqev/data/daily_map/YYYYMMDD.html(2025/1/1 以降)
#   ページ内の <pre> にある固定幅の一覧を読み取る。例:
#     2026  8 20 00:50 21.2  41°52.7'N 143° 3.0'E   46     4.0  浦河沖
#   出典: 気象庁 震源リスト。震源位置やマグニチュードは暫定値で、変更になる場合がある
#
# メイン画面の「メンテナンス」ボタンで開く画面からも取得できる(maintenance_window.py)
#
# 使い方: python3 scripts/jma_hypocenter.py [開始日 [終了日]] [--overwrite]
#   日付 ... 例: 2026/8/20。省略すると、観測の最初の日(daily_zips の最も古い日)から昨日まで
#   取得済みの日は取り直さない(--overwrite で取り直す)。ただし取得済みの最後の日は、
#   暫定値が更新されているかもしれないので取り直し、前回との違い(追加・削除・変更)を表示する
#   気象庁のサーバーに負担をかけないよう、1日ごとに REQUEST_INTERVAL 秒あける
import argparse
import csv
import html
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import app_paths
from log_common import set_console, log_message

URL = "https://www.data.jma.go.jp/eqev/data/daily_map/{:%Y%m%d}.html"
TIMEOUT = 30            # 1回の取得の待ち時間の上限 [秒]
REQUEST_INTERVAL = 2.0  # 取得の間隔 [秒]

# CSVの列
FIELDS = ["日時", "緯度", "経度", "深さ[km]", "M", "震央地名"]

# 一覧の1行(年 月 日 時:分 秒 緯度 経度 深さ M 震央地名)
#   緯度・経度は「41°52.7'N」のような度・分。M は「-」のこともある
LINE_RE = re.compile(
  r"(\d{4})\s+(\d+)\s+(\d+)\s+(\d+):(\d+)\s+([\d.]+)\s+"
  r"(\d+)°\s*([\d.]+)'([NS])\s+(\d+)°\s*([\d.]+)'([EW])\s+"
  r"(\S+)\s+(\S+)\s+(\S.*?)\s*$")


# 保存先のCSVのパス
def csv_path(day):
  return app_paths.JMA_HYPOCENTER_DIR / f"{day:%Y%m%d}.csv"

# 日ごとのページを取得し、<pre> の中の一覧のテキストを返す。ページが無ければ None
def fetch_list(day):
  req = urllib.request.Request(URL.format(day), headers={"User-Agent": "UsuMantenViewer"})
  try:
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
      page = res.read().decode("utf-8", errors="replace")
  except urllib.error.HTTPError as e:
    if e.code == 404:
      return None
    raise
  m = re.search(r"<pre>(.*?)</pre>", page, re.S)
  return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))) if m else None

# 一覧のテキストを読み取る。戻り値 ... (地震のリスト, 読み取れなかった行のリスト)
#   地震 ... {"日時": datetime, "緯度": 度, "経度": 度, "深さ[km]": 数値, "M": 数値 または None, "震央地名": 文字列}
def parse_list(text):
  quakes, skipped = [], []
  for line in text.splitlines():
    if not re.match(r"\s*\d{4}\s", line):
      continue   # 見出しや区切り線
    m = LINE_RE.match(line.strip())
    if not m:
      skipped.append(line)
      continue
    y, mo, d, h, mi, sec, la_d, la_m, ns, lo_d, lo_m, ew, depth, mag, name = m.groups()
    origin = datetime(int(y), int(mo), int(d), int(h), int(mi)) + timedelta(seconds=float(sec))
    lat = (int(la_d) + float(la_m) / 60) * (1 if ns == "N" else -1)
    lon = (int(lo_d) + float(lo_m) / 60) * (1 if ew == "E" else -1)
    quakes.append({"日時": origin, "緯度": lat, "経度": lon, "深さ[km]": float(depth),
                   "M": None if mag in ("-", "－") else float(mag), "震央地名": name})
  return quakes, skipped

def save_csv(path, quakes):
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(FIELDS)
    for q in quakes:
      writer.writerow([q["日時"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-5], f"{q['緯度']:.4f}", f"{q['経度']:.4f}",
                       f"{q['深さ[km]']:g}", "" if q["M"] is None else f"{q['M']:g}", q["震央地名"]])

# 保存したCSVを読む(照合など、ほかのスクリプトから使う)。その日の分が無ければ空のリスト
def load_day(day):
  path = csv_path(day)
  if not path.exists():
    return []
  quakes = []
  with open(path, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
      quakes.append({"日時": datetime.strptime(row["日時"], "%Y-%m-%d %H:%M:%S.%f"),
                     "緯度": float(row["緯度"]), "経度": float(row["経度"]),
                     "深さ[km]": float(row["深さ[km]"]), "M": float(row["M"]) if row["M"] else None,
                     "震央地名": row["震央地名"]})
  return quakes

# daily_zips にあるZIPの日付(ファイル名の YYMMDD)
def zip_days():
  days = set()
  for p in app_paths.ZIPS_DIR.glob("*.zip"):
    if not p.name.startswith("._") and re.fullmatch(r"\d{6}", p.stem):
      days.add(datetime.strptime(p.stem, "%y%m%d"))
  return sorted(days)

# 取得済みの日(保存したCSVの日付)
def saved_days():
  days = []
  for p in app_paths.JMA_HYPOCENTER_DIR.glob("*.csv"):
    if not p.name.startswith("._") and re.fullmatch(r"\d{8}", p.stem):
      days.append(datetime.strptime(p.stem, "%Y%m%d"))
  return sorted(days)

# 前回保存した分(old)と取り直した分(new)を比べる
#   発震時刻(0.1秒単位)が同じものを同じ地震とみなし、位置・深さ・M・地名の違いを「変更」とする
#   戻り値 ... (追加, 削除, 変更) のリスト。変更は (前, 後) の組
def compare(old, new):
  key = lambda q: q["日時"].strftime("%H:%M:%S.%f")[:10]
  old_map, new_map = {key(q): q for q in old}, {key(q): q for q in new}
  added = [new_map[k] for k in new_map.keys() - old_map.keys()]
  removed = [old_map[k] for k in old_map.keys() - new_map.keys()]
  changed = [(old_map[k], new_map[k]) for k in old_map.keys() & new_map.keys()
             if any(round(old_map[k][f], 4) != round(new_map[k][f], 4) for f in ("緯度", "経度", "深さ[km]"))
             or old_map[k]["M"] != new_map[k]["M"] or old_map[k]["震央地名"] != new_map[k]["震央地名"]]
  return (sorted(added, key=key), sorted(removed, key=key), sorted(changed, key=lambda c: key(c[0])))

def describe(q):
  m = "-" if q["M"] is None else f"{q['M']:g}"
  return f"{q['日時']:%H:%M:%S} {q['震央地名']} M{m} 深さ{q['深さ[km]']:g}km"

# 昨日(震源リストの日ごとのページは、その日が終わってから載る)
def yesterday():
  return datetime.combine(datetime.now().date(), datetime.min.time()) - timedelta(days=1)

# 観測の最初の日(daily_zips の最も古い日。取得済みの CSV がそれより古ければその日)。どちらも無ければ None
def first_day():
  firsts = zip_days()[:1] + saved_days()[:1]
  return min(firsts) if firsts else None

# 取得の状況(メンテナンス画面の表示用)
#   戻り値 ... {"saved": 取得済みの日数, "first": 取得済みの最初の日, "last": 取得済みの最後の日,
#              "missing": 観測の最初の日から昨日までで、取得していない日のリスト}(取得済みが無ければ first・last は None)
def summary():
  saved = saved_days()
  start, end = first_day(), yesterday()
  saved_set = set(saved)
  missing = [] if start is None else [start + timedelta(days=i) for i in range((end - start).days + 1)
                                      if start + timedelta(days=i) not in saved_set]
  return {"saved": len(saved), "first": saved[0] if saved else None, "last": saved[-1] if saved else None,
          "missing": missing}

# 取得する日を決める
#   start, end ... 期間(start が None なら観測の最初の日から昨日まで、end が None なら start と同じ日)
#   overwrite  ... 取得済みの日も取り直す
#   取得するのは、まだ取得していない日と、取得済みの最後の日(暫定値の更新を確かめる)
#   戻り値 ... (期間の日のリスト, 取得する日のリスト, 取得済みの最後の日 または None)
def plan_days(start=None, end=None, overwrite=False):
  if start is None:
    start = first_day()
    if start is None:
      raise ValueError("daily_zips にZIPが無いため、開始日を指定してください(例: 2026/8/20)")
    end = yesterday()
  elif end is None:
    end = start
  days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
  saved_set = set(saved_days())
  last_saved = max((d for d in days if d in saved_set), default=None)
  todo = [d for d in days if overwrite or d not in saved_set or d == last_saved]
  return days, todo, last_saved

# 取得の前に表示する説明
def describe_plan(days, todo, last_saved):
  return (f"{days[0]:%Y-%m-%d}〜{days[-1]:%Y-%m-%d} の {len(days)}日のうち、取得するもの {len(todo)}日"
          + (f"(取得済みの最後の日 {last_saved:%Y-%m-%d} を含む)" if last_saved in todo else "")
          + f" → {app_paths.JMA_HYPOCENTER_DIR}")

# 日ごとに取得して CSV に保存する。取り直した日は前回との違いを log に書く
#   log         ... メッセージを受け取る関数(画面から使うときは、別スレッドから呼ばれる)
#   cancelled   ... 中止するときに True を返す関数(日の区切りで止める)
#   on_progress ... 済んだ日数を受け取る関数
#   戻り値 ... {"done": 保存した日数, "failed": 取得できなかった日数, "changed": 前回と違った日数}
def fetch_days(todo, log=log_message, cancelled=lambda: False, on_progress=None):
  saved_set = set(saved_days())
  result = {"done": 0, "failed": 0, "changed": 0}
  for i, day in enumerate(todo):
    if cancelled():
      break
    if i:
      time.sleep(REQUEST_INTERVAL)
    if on_progress:
      on_progress(i)
    try:
      text = fetch_list(day)
    except (urllib.error.URLError, TimeoutError) as e:
      log(f"  {day:%Y-%m-%d}: 取得できませんでした ({e})")
      result["failed"] += 1
      continue
    if text is None:
      log(f"  {day:%Y-%m-%d}: 震源リストのページがありません")
      result["failed"] += 1
      continue
    quakes, skipped = parse_list(text)
    old = load_day(day) if day in saved_set else None
    save_csv(csv_path(day), quakes)
    result["done"] += 1
    log(f"  {day:%Y-%m-%d}: {len(quakes)}件" + (f"(読み取れなかった行 {len(skipped)}行)" if skipped else ""))
    for line in skipped[:3]:
      log(f"    {line}")

    # 取り直した日は、前回との違いを表示する
    if old is not None:
      added, removed, changed = compare(old, quakes)
      if not (added or removed or changed):
        log(f"    前回({len(old)}件)と同じでした")
        continue
      result["changed"] += 1
      log(f"    前回({len(old)}件)との違い: 追加 {len(added)}件、削除 {len(removed)}件、変更 {len(changed)}件")
      for q in added[:5]:
        log(f"      + {describe(q)}")
      for q in removed[:5]:
        log(f"      - {describe(q)}")
      for a, b in changed[:5]:
        log(f"      * {describe(a)} → {describe(b)}")
  if on_progress:
    on_progress(result["done"] + result["failed"])
  return result

def main():
  parser = argparse.ArgumentParser(description="気象庁の震源リスト(日ごと)を取得してCSVに保存する")
  parser.add_argument("start", nargs="?", help="開始日(例: 2026/8/20)。省略すると観測の最初の日から昨日まで")
  parser.add_argument("end", nargs="?", help="終了日(省略すると開始日と同じ)")
  parser.add_argument("--overwrite", action="store_true", help="取得済みの日も取り直す")
  args = parser.parse_args()
  set_console(True)

  start = datetime.strptime(args.start, "%Y/%m/%d") if args.start else None
  end = datetime.strptime(args.end, "%Y/%m/%d") if args.end else None
  try:
    days, todo, last_saved = plan_days(start, end, args.overwrite)
  except ValueError as e:
    log_message(str(e))
    return 1
  log_message(describe_plan(days, todo, last_saved))
  fetch_days(todo)

if __name__ == "__main__":
  sys.exit(main())
