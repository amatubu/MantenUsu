# quake_loose.py
# 外部の観測点(気象庁・防災科研)の波形で確かめる時間を選ぶ(TODO 21)
#   今の基準(quake_detect.find_candidates)では候補にならないが、基準をゆるめると揺れていたところを「要確認」とし、
#   気象庁の震源リストと照合できたもの・列車の揺れとみられるものを除いて、v-net からダウンロードする60分の一覧にする
#   (外部の観測点の連続波形は、防災科研のページから手で60分ずつダウンロードするしかないため、数を絞る)
#
# ゆるめた基準(2026/6/1〜7/31 で試算し、外部の観測点で確かめた3つの揺れ(6/13 22:35、6/23 12:58、7/8 11:20)を
# すべて拾えて、ダウンロードが2か月で約50時間になるもの)
#   D ... 3拠点以上が雑音の LOOSE_RATIO 倍超で、どこかの拠点が LOOSE_MAX_RATIO 倍以上
#   H ... Hokudai が HOKUDAI_RATIO 倍以上で、ほかの LOOSE_HOKUDAI_OTHERS 拠点以上が LOOSE_HOKUDAI_OTHERS_RATIO 倍以上
#         (6/13 22:35 は Hokudai 57倍・Mimatsu 38倍で、Bamba・Nizaemn は2倍だった)
import csv
import html
import json
from datetime import timedelta

import jma_match
import quake_detect
import train_day_plot
import external_stations
from log_common import log_message

LOOSE_RATIO = 10.0
LOOSE_MAX_RATIO = 20.0
LOOSE_HOKUDAI_OTHERS_RATIO = 20.0
LOOSE_HOKUDAI_OTHERS = 1
# 今の候補とこの秒数より近いものは、同じ揺れとして除く
OVERLAP_MARGIN = 30.0
# 列車を調べる拠点(線路に近い拠点。stack_overlays.TrainOverlay と同じ)
TRAIN_STATION = "Bamba"
# ダウンロードする長さ [分] と、最初の候補の何分前から始めるか
DOWNLOAD_MINUTES = 60
DOWNLOAD_LEAD_MINUTES = 5
# 取得済みとみなすのに、候補の前後に何分の波形があればよいか
COVER_MARGIN_MINUTES = 1
# 列車かどうかの判定結果の保存先(波形を読み直さないため)。判定の方法を変えたら番号を上げる
TRAIN_CACHE_VERSION = 1
TRAIN_CACHE_FILE = quake_detect.FEATURES_CACHE_FILE.parent / f"loose_trains_v{TRAIN_CACHE_VERSION}.json"
# 判定の結果
TRAIN_TIMETABLE = "時刻表の列車"
TRAIN_FREIGHT = "貨物列車の候補"
# 毎日ほぼ同じ時刻の揺れは除く(地震は決まった時刻には起きないため)
#   残った候補のうち、時刻(日付を除く)が RECUR_MINUTES 分以内の候補が、ほかの RECUR_DAYS 日以上にもあるもの
#   (2025/7〜2026/8 では、21:14・21:42・01:51〜01:58・03:40〜03:48・04:00〜04:06 ごろの揺れが繰り返し残った。
#    Bamba や Hokudai が最大で Nizaemn はほぼ揺れておらず、時刻表に無い列車などとみられる。
#    外部の観測点で確かめた3つの揺れは、どれも当てはまらない)
#   期間の中の候補だけで数えるので、短い期間では除かれにくい
RECUR_MINUTES = 6
RECUR_DAYS = 3


# ゆるめた基準の候補のうち、今の候補と重ならないもの
#   candidates ... 今の基準の候補(quake_detect.find_candidates の結果)
def find(start_day, rms, candidates):
  loose, _info = quake_detect.find_candidates(
    start_day, rms, quake_ratio=LOOSE_RATIO, hokudai_others_ratio=LOOSE_HOKUDAI_OTHERS_RATIO,
    hokudai_others=LOOSE_HOKUDAI_OTHERS)
  margin = timedelta(seconds=OVERLAP_MARGIN)
  def overlaps(c):
    return any(c["start"] <= o["end"] + margin and o["start"] <= c["end"] + margin for o in candidates)
  return [c for c in loose if c["max_ratio"] >= LOOSE_MAX_RATIO and not overlaps(c)]

# 保存した列車の判定。戻り値 ... {quake_detect.features_key: 判定(TRAIN_TIMETABLE・TRAIN_FREIGHT・"")}
def load_train_cache():
  try:
    return json.loads(TRAIN_CACHE_FILE.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}

def save_train_cache(cache):
  TRAIN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
  tmp = TRAIN_CACHE_FILE.with_suffix(".tmp")
  tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
  tmp.replace(TRAIN_CACHE_FILE)

# 候補が列車の揺れか(1時間スタックの「列車」の重ね表示と同じ解析で、候補と重なる Bamba の揺れを見る)
#   train ... stack_overlays.TrainOverlay(同じ時間の解析を使い回す)
#   戻り値 ... TRAIN_TIMETABLE・TRAIN_FREIGHT・""(列車ではない)。波形が無いなど判定できないときは例外
def train_verdict(cand, train):
  hour = cand["peak"].replace(minute=0, second=0, microsecond=0)
  events, _timetable = train.analyze_hour(hour, TRAIN_STATION)
  day = hour.replace(hour=0)
  t0, t1 = (cand["start"] - day).total_seconds(), (cand["end"] - day).total_seconds()
  for ev in events:
    if ev["end"] < t0 or ev["start"] > t1 or ev.get("quake"):
      continue
    if ev.get("trains"):
      return TRAIN_TIMETABLE
    if train_day_plot.is_freight_candidate(ev):
      return TRAIN_FREIGHT
  return ""

# 毎日ほぼ同じ時刻の揺れに、ほかに何日あるかを書いた理由(c["recur"])を付け、当てはまらないものを返す
def drop_recurring(cands):
  minute = lambda c: c["peak"].hour * 60 + c["peak"].minute + c["peak"].second / 60
  for c in cands:
    m = minute(c)
    days = {o["peak"].date() for o in cands if o["peak"].date() != c["peak"].date()
            and min(abs(m - minute(o)), 1440 - abs(m - minute(o))) <= RECUR_MINUTES}
    if len(days) >= RECUR_DAYS:
      c["recur"] = f"毎日同じ時刻の揺れ({c['peak']:%H:%M}±{RECUR_MINUTES}分がほかに{len(days)}日)"
  return [c for c in cands if not c.get("recur")]

# 残った候補を、ダウンロードする60分ごとにまとめる。戻り値 ... [{"start", "end", "candidates"}, ...]
def group_hours(cands):
  hours = []
  for c in sorted(cands, key=lambda c: c["start"]):
    if hours and c["end"] <= hours[-1]["end"]:
      hours[-1]["candidates"].append(c)
      continue
    start = (c["start"] - timedelta(minutes=DOWNLOAD_LEAD_MINUTES)).replace(second=0, microsecond=0)
    hours.append({"start": start, "end": start + timedelta(minutes=DOWNLOAD_MINUTES), "candidates": [c]})
  return hours

# v-net フォルダに、その時間を含む ZIP が網ごとにそろっているか
#   (網は、v-net フォルダにある ZIP の名前の網の番号(例: 01_05_03、03_02_07)から決める)
def covered(t0, t1, files=None):
  files = external_stations.data_files() if files is None else files
  nets = {_network(p.name) for p, _s, _e in files}
  if not nets:
    return False
  return all(any(_network(p.name) == net and s <= t0 and e >= t1 for p, s, e in files) for net in nets)

def _network(name):
  return "_".join(name.split("_")[:3])

# 候補の最大の時刻を含む1時間の、気象庁 V.KBBY の上下動の連続波形画像(防災科研のページ)の URL
#   ダウンロードする前に、揺れがありそうかをざっと見るのに使う(夜の KBBY はとても静かで、小さな揺れも見えやすい)
def image_url(c):
  return external_stations.image_url("V.KBBY", c["peak"])

# 候補の時刻と倍率の短い説明(一覧に書く)
def describe(c):
  return f"{c['peak']:%H:%M:%S} ×{c['max_ratio']:.0f}({c.get('rule', '')})"

# ゆるめた基準の候補の一覧(除いた理由とダウンロードする60分も書く)
def write_candidates_csv(path, loose, names):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["開始", "最大", "終了", "検出の基準", "最大の倍率"] + [f"{nm}の倍率" for nm in names]
                    + ["除いた理由", "ダウンロードする60分の開始", "KBBY の連続波形画像"])
    for c in loose:
      q = c.get("match")
      reason = f"震源リスト: {q['震央地名']} M{q['M']:g}" if q else c.get("train") or c.get("recur", "")
      writer.writerow([c["start"].strftime("%Y-%m-%d %H:%M:%S"), c["peak"].strftime("%H:%M:%S"),
                       c["end"].strftime("%H:%M:%S"), c.get("rule", ""), f"{c['max_ratio']:.1f}"]
                      + [f"{c['ratios'][nm]:.1f}" if nm in c["ratios"] else "" for nm in names]
                      + [reason, c["download"].strftime("%Y-%m-%d %H:%M") if c.get("download") else "", image_url(c)])

# ダウンロードする60分の一覧
def write_hours_csv(path, hours):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["開始", "終了", "分数", "取得済み", "候補の数", "最大の倍率", "候補", "KBBY の連続波形画像"])
    for h in hours:
      writer.writerow([h["start"].strftime("%Y-%m-%d %H:%M"), h["end"].strftime("%Y-%m-%d %H:%M"), DOWNLOAD_MINUTES,
                       "済" if h["covered"] else "", len(h["candidates"]),
                       f"{max(c['max_ratio'] for c in h['candidates']):.0f}",
                       " / ".join(describe(c) for c in h["candidates"]),
                       " ".join(dict.fromkeys(image_url(c) for c in h["candidates"]))])

# 残った候補の、KBBY の連続波形画像へのリンクの一覧(ブラウザで開き、上から順に見ていく)
def write_image_list(path, hours, names):
  rows = []
  for h in hours:
    for c in h["candidates"]:
      ratios = " ".join(f"{nm} {c['ratios'][nm]:.0f}" for nm in names if nm in c["ratios"])
      rows.append(f"<tr><td>{c['peak']:%Y-%m-%d %H:%M:%S}</td><td>{c['peak']:%M}分の段</td>"
                  f"<td>×{c['max_ratio']:.0f}</td><td>{html.escape(ratios)}</td>"
                  f"<td>{'済' if h['covered'] else ''}</td><td>{h['start']:%Y-%m-%d %H:%M}</td>"
                  f"<td><a href=\"{html.escape(image_url(c))}\" target=\"_blank\">KBBY {c['peak']:%m/%d %H}時</a></td></tr>")
  path.write_text(f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>KBBY の連続波形画像</title>
<style>
body {{ font-family: sans-serif; margin: 16px; color: #222; background: #fff; }}
table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #ccc; padding: 3px 8px; font-size: 13px; }}
th {{ background: #f0f0f0; }} a:visited {{ color: #888; }}
</style></head><body>
<p>外部の観測点で確かめる候補(ゆるめた基準で残ったもの {len(rows)}件)の時間の、気象庁 V.KBBY の上下動の連続波形画像です。
1時間の画像の、候補の分の段を見ます。開いたリンクは灰色になります。</p>
<table><tr><th>候補の最大</th><th>画像の段</th><th>最大の倍率</th><th>拠点ごとの倍率</th><th>取得済み</th>
<th>ダウンロードする60分の開始</th><th>画像</th></tr>
{chr(10).join(rows)}
</table></body></html>
""", encoding="utf-8")

# 一連の処理(地震の候補を探す処理の続きで呼ぶ)
#   train      ... stack_overlays.TrainOverlay(時刻表が無いときは None。列車の判定をしない)
#   out_dir, period ... 一覧の保存先と、ファイル名に付ける期間(YYYYMMDD_YYYYMMDD)
#     vnet_check_<期間>.csv    ... ゆるめた基準の候補すべて(除いた理由つき)
#     vnet_download_<期間>.csv ... ダウンロードする60分
#     vnet_locate_<期間>.csv   ... 残った候補のうち、外部の観測点の波形を取得済みのもの
#                                  (「候補の一覧(CSV)を選んで震源を表示」で開き、震源推定の画面で確かめる)
#   戻り値 ... (ゆるめた基準の候補, ダウンロードする60分のリスト)。中止したときは None
def run(start_day, rms, candidates, catalog, train, out_dir, period, names,
        cancelled=lambda: False, on_progress=None, log=log_message):
  loose = find(start_day, rms, candidates)
  log(f"外部の観測点で確かめる候補(ゆるめた基準: 3拠点が{LOOSE_RATIO:g}倍超で最大{LOOSE_MAX_RATIO:g}倍以上、"
      f"または {quake_detect.HOKUDAI} {quake_detect.HOKUDAI_RATIO:g}倍以上でほかの{LOOSE_HOKUDAI_OTHERS}拠点が"
      f"{LOOSE_HOKUDAI_OTHERS_RATIO:g}倍以上。今の候補と重なるものは除く): {len(loose)}件")
  jma_match.match_candidates(loose, catalog)
  rest = [c for c in loose if not c.get("match")]
  log(f"  震源リストと照合できたもの: {len(loose) - len(rest)}件")

  if train is None:
    log("  時刻表が無いため、列車の揺れは除いていません")
  else:
    cache = load_train_cache()
    checked = failed = 0
    for i, c in enumerate(rest):
      if cancelled():
        save_train_cache(cache)
        return None
      if on_progress:
        on_progress(i + 1, len(rest), "列車の揺れかどうかを調べています(件目)")
      key = quake_detect.features_key(c)
      if key not in cache:
        try:
          cache[key] = train_verdict(c, train)
          checked += 1
          if checked % 20 == 0:
            save_train_cache(cache)   # 中止しても、ここまでの分は次に使えるように
        except Exception as e:
          log(f"  [注意] 列車かどうか判定できませんでした({c['start']:%Y-%m-%d %H:%M:%S}): {e}")
          failed += 1
          continue
      c["train"] = cache[key]
    save_train_cache(cache)
    trains = [c for c in rest if c.get("train")]
    log(f"  列車の揺れとみられるもの: {len(trains)}件(時刻表の列車 "
        f"{sum(1 for c in trains if c['train'] == TRAIN_TIMETABLE)}件、貨物列車の候補 "
        f"{sum(1 for c in trains if c['train'] == TRAIN_FREIGHT)}件)"
        + (f"、判定できず残したもの {failed}件" if failed else ""))
    rest = [c for c in rest if not c.get("train")]

  kept = drop_recurring(rest)
  log(f"  毎日ほぼ同じ時刻(±{RECUR_MINUTES}分。ほかに{RECUR_DAYS}日以上)の揺れ: {len(rest) - len(kept)}件")
  rest = kept

  hours = group_hours(rest)
  files = external_stations.data_files()
  margin = timedelta(minutes=COVER_MARGIN_MINUTES)
  for h in hours:
    # 60分の中の候補が、どれも(前後を含めて)取得済みの ZIP に入っていれば取得済みとする
    h["covered"] = all(covered(c["start"] - margin, c["end"] + margin, files) for c in h["candidates"])
    for c in h["candidates"]:
      c["download"] = h["start"]
  todo = [h for h in hours if not h["covered"]]
  log(f"  残った候補 {len(rest)}件 → ダウンロードする60分 {len(hours)}個(取得済み {len(hours) - len(todo)}個、"
      f"未取得 {len(todo)}個。うち昼(6〜19時) {sum(1 for h in todo if 6 <= h['start'].hour < 19)}個)")
  ready = [c for h in hours if h["covered"] for c in h["candidates"]]
  paths = {kind: out_dir / f"vnet_{kind}_{period}.csv" for kind in ("check", "download", "locate")}
  write_candidates_csv(paths["check"], loose, names)
  write_hours_csv(paths["download"], hours)
  write_candidates_csv(paths["locate"], ready, names)
  paths["images"] = out_dir / f"vnet_kbby_{period}.html"
  write_image_list(paths["images"], hours, names)
  log(f"外部の観測点で確かめる候補の一覧: {paths['check']}")
  log(f"ダウンロードする60分の一覧: {paths['download']}")
  log(f"外部の観測点の波形を取得済みの候補 {len(ready)}件の一覧(候補の一覧(CSV)を選んで震源を表示 で開けます): "
      f"{paths['locate']}")
  log(f"KBBY の連続波形画像へのリンクの一覧(ブラウザで開きます): {paths['images']}")
  return loose, hours
