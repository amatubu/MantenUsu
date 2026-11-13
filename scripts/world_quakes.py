# world_quakes.py
# 世界の大きな地震(USGS の地震の一覧)を取得し、world_quakes フォルダに CSV で保存する(TODO 33)
#   気象庁の震源リスト(jma_hypocenter.py)は日本の周りの地震だけなので、海外の大きな地震の揺れは分からない。
#   M7 以上の地震は、満点の拠点にもはっきり揺れが届くことがある
#   (2026/2/23 02:04 のボルネオ島北部の深発地震 M7.1 は、近い地震と取り違えていた)。
#   1時間スタック波形の「地震」の重ね表示に、揺れが届く見込みの時刻を示すのに使う(stack_overlays.py)
#   取得元: USGS Earthquake Catalog(FDSN event web service) https://earthquake.usgs.gov/fdsnws/event/1/
#   出典: U.S. Geological Survey, Earthquake Hazards Program, ANSS Comprehensive Earthquake Catalog (ComCat)
#
# 保存の形: 月ごとの CSV(world_quakes/YYYYMM.csv)。時刻は日本時間(気象庁の震源リストと同じ)
#   列: 日時(日本時間), 緯度, 経度, 深さ[km], M, 震央地名, USGS の ID
#   取得するのは MIN_MAGNITUDE 以上。USGS の値は後から改訂されることがあるので、
#   取得済みの最後の月と、今月は取り直す
#
# メイン画面の「メンテナンス」の「震源リスト」タブからも取得できる(maintenance_window.py)
#
# 使い方: python3 scripts/world_quakes.py [開始の月 [終了の月]] [--overwrite]
#   月 ... 例: 2025/7。省略すると、観測の最初の月(daily_zips の最も古い日の月)から今月まで
import argparse
import csv
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from obspy.geodetics import gps2dist_azimuth, kilometers2degrees

import app_paths
import jma_hypocenter
import jma_match
from log_common import set_console, log_message

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
TIMEOUT = 60            # 1回の取得の待ち時間の上限 [秒]
REQUEST_INTERVAL = 2.0  # 取得の間隔 [秒]
MIN_MAGNITUDE = 7.0     # 取得する地震の大きさの下限
JST = timedelta(hours=9)

# 揺れの届く見込み(arrivals_in_window)
SURFACE_WAVE_KMS = 3.5   # 表面波の速さ [km/s](おおよそ。周期によって 3〜4 km/s ほど変わる)
FAR_DEG = 100.0          # これより遠いと、P波・S波が地球の核の陰に入り、揺れが見えにくい
                         #   (2025/7〜2026/8 の15件の波形で、100° を超える地震は見えなかった)
MAX_DELAY = timedelta(minutes=100)   # 起きてから揺れが届くまでの最大(地球の反対側の表面波 約 95 分)
# 最初に届く P波・S波として探す波(震源の上で反射した pP・sP などは除く)
P_PHASES = ["p", "P", "Pdiff", "PKP", "PKiKP", "PKIKP"]
S_PHASES = ["s", "S", "Sdiff", "SKS", "SKiKS", "SKIKS"]
# 気象庁の震源リストと同じ地震とみなす差(同じ地震なら、気象庁のほうを表示する)
SAME_AS_JMA_SEC = 30.0
SAME_AS_JMA_KM = 100.0

FIELDS = ["日時", "緯度", "経度", "深さ[km]", "M", "震央地名", "USGS の ID"]


# 保存先のCSVのパス(月ごと)
def csv_path(month):
  return app_paths.WORLD_QUAKES_DIR / f"{month:%Y%m}.csv"

# 月の初めの日
def month_start(day):
  return datetime(day.year, day.month, 1)

def next_month(month):
  return datetime(month.year + (month.month == 12), month.month % 12 + 1, 1)

# 1か月分を USGS から取得する(期間は日本時間の月。USGS は世界時なので9時間ずらして問い合わせる)
#   戻り値 ... USGS の CSV のテキスト
def fetch_month(month, min_magnitude=MIN_MAGNITUDE):
  start, end = month - JST, next_month(month) - JST
  query = urllib.parse.urlencode({"format": "csv", "starttime": f"{start:%Y-%m-%dT%H:%M:%S}",
                                  "endtime": f"{end:%Y-%m-%dT%H:%M:%S}", "minmagnitude": f"{min_magnitude:g}",
                                  "orderby": "time-asc", "eventtype": "earthquake"})
  req = urllib.request.Request(f"{URL}?{query}", headers={"User-Agent": "UsuMantenViewer"})
  with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
    return res.read().decode("utf-8", errors="replace")

# USGS の CSV を読み取る(時刻は日本時間にする)
def parse_usgs(text):
  quakes = []
  for row in csv.DictReader(text.splitlines()):
    try:
      t = datetime.strptime(row["time"][:23], "%Y-%m-%dT%H:%M:%S.%f") + JST
      quakes.append({"日時": t, "緯度": float(row["latitude"]), "経度": float(row["longitude"]),
                     "深さ[km]": float(row["depth"]) if row["depth"] else 0.0,
                     "M": float(row["mag"]) if row["mag"] else None,
                     "震央地名": row["place"] or "", "USGS の ID": row["id"]})
    except (KeyError, ValueError):
      continue
  return quakes

def save_csv(path, quakes):
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(path, "w", encoding="utf-8", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(FIELDS)
    for q in quakes:
      writer.writerow([q["日時"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-4], f"{q['緯度']:.4f}", f"{q['経度']:.4f}",
                       f"{q['深さ[km]']:g}", "" if q["M"] is None else f"{q['M']:g}", q["震央地名"], q["USGS の ID"]])

def load_month(month):
  path = csv_path(month)
  if not path.exists():
    return []
  out = []
  with open(path, encoding="utf-8") as f:
    for row in csv.DictReader(f):
      out.append({"日時": datetime.strptime(row["日時"], "%Y-%m-%d %H:%M:%S.%f"), "緯度": float(row["緯度"]),
                  "経度": float(row["経度"]), "深さ[km]": float(row["深さ[km]"]),
                  "M": float(row["M"]) if row["M"] else None, "震央地名": row["震央地名"],
                  "USGS の ID": row["USGS の ID"]})
  return out

# 期間 [t0, t1] に起きた地震(日本時間)。取得済みの月だけを見る
#   (遠い地震の揺れは起きてから20分ほど遅れて届くので、表示に使うときは t0 を早めに取る)
def quakes_between(t0, t1):
  out, m = [], month_start(t0)
  while m <= t1:
    out += [q for q in load_month(m) if t0 <= q["日時"] <= t1]
    m = next_month(m)
  return out

# 気象庁の震源リストに同じ地震があるか(発生時刻が SAME_AS_JMA_SEC 秒以内、震央が SAME_AS_JMA_KM 以内)
#   震源リストを取得していない日は無いとみなす(USGS のほうを表示する)
def in_jma_list(q):
  day = q["日時"].replace(hour=0, minute=0, second=0, microsecond=0)
  for d in (day - timedelta(days=1), day, day + timedelta(days=1)):
    for j in jma_hypocenter.load_day(d):
      if (abs((j["日時"] - q["日時"]).total_seconds()) <= SAME_AS_JMA_SEC
          and gps2dist_azimuth(j["緯度"], j["経度"], q["緯度"], q["経度"])[0] / 1000 <= SAME_AS_JMA_KM):
        return True
  return False

_arrival_cache = {}   # (USGS の ID, 拠点の緯度, 経度) → 揺れの届く見込み

# 拠点に揺れが届く見込み(走時表 iasp91。気象庁の震源リストの到達見込み(jma_match)と同じ)
#   戻り値 ... 地震の dict に次を加えたもの
#     dist_km, deg ... 震央距離 [km]・[度] / azimuth ... 拠点から見た震央の方位 [度] / far ... FAR_DEG より遠い
#     waves ... [(名前, 時刻)]。最初に届く P波(遠いと Pdiff・PKP など)・S波(同じく SKS など)・表面波
def arrivals(q, site):
  key = (q["USGS の ID"], round(site[0], 3), round(site[1], 3))
  if key not in _arrival_cache:
    dist_m, azimuth, _ = gps2dist_azimuth(site[0], site[1], q["緯度"], q["経度"])
    dist_km = dist_m / 1000
    deg = kilometers2degrees(dist_km)
    waves = []
    for phases in (P_PHASES, S_PHASES):
      arr = jma_match.model().get_travel_times(source_depth_in_km=max(q["深さ[km]"], 0.0), distance_in_degree=deg,
                                               phase_list=phases)
      a = min(arr, key=lambda a: a.time, default=None)
      if a is not None:
        waves.append((a.name, q["日時"] + timedelta(seconds=a.time)))
    waves.append(("表面波", q["日時"] + timedelta(seconds=dist_km / SURFACE_WAVE_KMS)))
    _arrival_cache[key] = dict(dist_km=dist_km, deg=deg, azimuth=azimuth, far=deg > FAR_DEG, waves=waves)
  return dict(q, **_arrival_cache[key])

# 期間 [t0, t1] に揺れ(P波・S波・表面波のどれか)が届く見込みの地震(気象庁の震源リストにある地震は除く)
#   site ... 拠点の (緯度, 経度)
def arrivals_in_window(t0, t1, site):
  out = []
  for q in quakes_between(t0 - MAX_DELAY, t1):
    a = arrivals(q, site)
    if any(t0 <= t <= t1 for _name, t in a["waves"]) and not in_jma_list(q):
      out.append(a)
  return out

# 画面に書く地震の説明(地名・M・深さ・震央距離の順。震源リストの地震の書き方とそろえる)
def describe_arrival(a):
  m = "" if a["M"] is None else f" M{a['M']:g}"
  return f"{a['震央地名']}{m} 深さ {a['深さ[km]']:.0f} km 震央距離 {a['deg']:.0f}°"

# 取得済みの月
def saved_months():
  months = []
  for p in app_paths.WORLD_QUAKES_DIR.glob("*.csv"):
    if not p.name.startswith("._") and re.fullmatch(r"\d{6}", p.stem):
      months.append(datetime.strptime(p.stem, "%Y%m"))
  return sorted(months)

def this_month():
  return month_start(datetime.now())

# 観測の最初の月(震源リストと同じく、daily_zips の最も古い日。取得済みの CSV がそれより古ければその月)
def first_month():
  firsts = [month_start(d) for d in jma_hypocenter.zip_days()[:1]] + saved_months()[:1]
  return min(firsts) if firsts else None

# 取得の状況(メンテナンス画面の表示用)
#   戻り値 ... {"saved": 取得済みの月の数, "first", "last": 取得済みの最初・最後の月, "count": 地震の数,
#              "missing": 観測の最初の月から今月までで、取得していない月のリスト}
def summary():
  saved = saved_months()
  start, end = first_month(), this_month()
  missing, m = [], start
  while m is not None and m <= end:
    if m not in saved:
      missing.append(m)
    m = next_month(m)
  return {"saved": len(saved), "first": saved[0] if saved else None, "last": saved[-1] if saved else None,
          "count": sum(len(load_month(m)) for m in saved), "missing": missing}

# 取得する月を決める
#   start, end ... 期間(start が None なら観測の最初の月から今月まで、end が None なら start と同じ月)
#   取得するのは、まだ取得していない月と、取得済みの最後の月・今月(値の改訂や、新しい地震を取り込む)
#   戻り値 ... (期間の月のリスト, 取得する月のリスト)
def plan_months(start=None, end=None, overwrite=False):
  if start is None:
    start = first_month()
    if start is None:
      raise ValueError("daily_zips にZIPが無いため、開始の月を指定してください(例: 2025/7)")
    end = this_month()
  elif end is None:
    end = start
  start, end = month_start(start), month_start(min(end, this_month()))
  months, m = [], start
  while m <= end:
    months.append(m)
    m = next_month(m)
  saved = set(saved_months())
  last_saved = max((m for m in months if m in saved), default=None)
  todo = [m for m in months if overwrite or m not in saved or m == last_saved or m == this_month()]
  return months, todo

def describe_plan(months, todo):
  return (f"{months[0]:%Y-%m}〜{months[-1]:%Y-%m} の {len(months)}か月のうち、取得するもの {len(todo)}か月"
          f"(M{MIN_MAGNITUDE:g} 以上) → {app_paths.WORLD_QUAKES_DIR}")

def describe(q):
  m = "-" if q["M"] is None else f"{q['M']:g}"
  return f"{q['日時']:%Y-%m-%d %H:%M} M{m} 深さ{q['深さ[km]']:g}km {q['震央地名']}"

# 月ごとに取得して CSV に保存する。取り直した月は、前回から増えた地震を log に書く
#   log・cancelled・on_progress ... jma_hypocenter.fetch_days と同じ
#   戻り値 ... {"done": 保存した月の数, "failed": 取得できなかった月の数, "count": 保存した地震の数}
def fetch_months(todo, log=log_message, cancelled=lambda: False, on_progress=None):
  result = {"done": 0, "failed": 0, "count": 0}
  for i, month in enumerate(todo):
    if cancelled():
      break
    if i:
      time.sleep(REQUEST_INTERVAL)
    if on_progress:
      on_progress(i)
    try:
      quakes = parse_usgs(fetch_month(month))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
      log(f"  {month:%Y-%m}: 取得できませんでした ({e})")
      result["failed"] += 1
      continue
    old_ids = {q["USGS の ID"] for q in load_month(month)}
    save_csv(csv_path(month), quakes)
    result["done"] += 1
    result["count"] += len(quakes)
    new = [q for q in quakes if q["USGS の ID"] not in old_ids]
    log(f"  {month:%Y-%m}: {len(quakes)}件" + (f"(新しく {len(new)}件)" if old_ids and new else ""))
    for q in quakes if not old_ids else new:
      log(f"    {describe(q)}")
  if on_progress:
    on_progress(result["done"] + result["failed"])
  return result

def main():
  parser = argparse.ArgumentParser(description="世界の大きな地震(USGS)を取得してCSVに保存する")
  parser.add_argument("start", nargs="?", help="開始の月(例: 2025/7)。省略すると観測の最初の月から今月まで")
  parser.add_argument("end", nargs="?", help="終了の月(省略すると開始の月と同じ)")
  parser.add_argument("--overwrite", action="store_true", help="取得済みの月も取り直す")
  args = parser.parse_args()
  set_console(True)
  parse = lambda s: datetime.strptime(s, "%Y/%m") if s else None
  try:
    months, todo = plan_months(parse(args.start), parse(args.end), args.overwrite)
  except ValueError as e:
    log_message(str(e))
    return 1
  log_message(describe_plan(months, todo))
  fetch_months(todo)

if __name__ == "__main__":
  sys.exit(main())
