# train_day_plot.py
# 1日分の波形から列車らしい揺れを拾い出し、時刻表の時刻と並べたグラフにする(単体実行用)
#   1. 指定した拠点・成分の1日分の波形を、帯域通過フィルターに通して10秒ごとのRMSにする
#   2. RMS が周りの雑音(前後30分の中央値)の何倍かを超えた区間を「揺れ」として拾い出す
#      ほかの拠点でも同じころに大きく揺れていたものは、地震として列車の対応から除く
#   3. 6時間ずつ4段のグラフに、RMS・拾い出した揺れ・時刻表の時刻(指定したとき)を描く
#   4. --stack を付けると、1時間スタック波形(アプリと同じ図)に揺れと時刻表を重ねた画像も作る
#      1日のグラフと24時間分のスタック波形は、1つのPDF(<日付>_<拠点>.<成分>_trains.pdf)にもまとめる
#   5. --format svg / pdf を付けると、画像を拡大しても粗くならない形式(ベクター)で保存する(スライド用)
#      --hours でスタック波形を作る時間を絞れる(例: --hours 18,22。このときはまとめたPDFは作らない)
#
# 使い方: python3 scripts/train_day_plot.py <日付> [時刻表のCSV] [--station 拠点名] [--comp 成分] [--stack]
#                                        [--format png|svg|pdf] [--hours 時,時,...]
#   日付 ... 例: 2026/8/23
#   時刻表のCSV(任意) ... 1行に1本。「時刻,ラベル」(例: 06:12,北斗2号 上り)
#                       1行目が見出しでもよい。ラベルが「貨」で始まる行は別の色で描く
#   SACファイルは1時間ずつ output/<開始>_<終了> に作る(アプリの1時間ごとの表示と同じ単位)
import argparse
import csv
from datetime import datetime, timedelta

import numpy as np

import app_paths
import win_pipeline
import matplotlib
# 単体で実行したときだけ画面を使わない描画にする(アプリの画面から関数を使うときは切り替えない)
if __name__ == "__main__":
  matplotlib.use("Agg")
import matplotlib.pyplot as plt
import obspy

from matplotlib.backends.backend_pdf import PdfPages

from screen_events import load_period
from quake_detect import QUAKE_BAND, QUAKE_RATIO, QUAKE_MIN_STATIONS, QUAKE_MARGIN
from stack_plot import StackFigure, STACK_ROWS, add_span
from plot_common import NM_TO_CM, setup_japanese_font
from log_common import set_console, log_message

# 列車の振動を見やすくするための帯域通過フィルター [Hz]
#   Bamba では日中を中心に 3〜6 Hz の揺れ(設置場所の固有振動とみられる)が強いので、下限を 8 Hz にしている
#   (2026/8/21 の昼間の普通列車は、5〜20 Hz では雑音の6〜9倍だったが、8〜25 Hz では11〜20倍になった)
BAND = (8.0, 25.0)
# RMS を計算する窓の長さ [秒]
RMS_WINDOW = 10.0
# 雑音の基準(RMS の移動中央値)を取る窓の長さ [秒]
NOISE_WINDOW = 1800.0
# 揺れとみなす、雑音に対する倍率
#   (2026/8/23 の Bamba.U では、列車らしい揺れは雑音の20〜400倍、車などの小さな揺れは3〜10倍だった)
EVENT_RATIO = 10.0
# この秒数より近い揺れは1つにまとめる
MERGE_GAP = 30.0
# 時刻表の時刻(洞爺駅)から、拠点の横を通る時刻までのずれ [分]。ラベルに含まれる方面で決める
#   Bamba は洞爺駅の東南東(札幌側)にあるので、函館方面は駅に着く前、札幌方面は駅を出たあとに通る
#   (2026/8/23 の Bamba.U で、函館方面は1〜3分前、札幌方面は2〜3分後だった)
DIRECTION_OFFSETS = {"函館方面": -2.0, "札幌方面": 2.5}
# 見込んだ時刻と揺れを対応させるときに許すずれ [分]。この範囲でいちばん大きな揺れを選ぶ
MATCH_MINUTES = 2.5
# 遅れを見込んで、見込んだ時刻のこの分数あとまで探す [分]
#   (2026/8/23 では、函館方面の普通 8:12・13:12 と北斗 16:38 が、見込みより3〜4分遅れて通っていた)
DELAY_MINUTES = 5.0
# 時刻どおりの範囲で選んだ揺れより、遅れの範囲に方面の形が一致してこの倍以上大きい揺れがあれば、そちらを選ぶ
#   (2026/8/22 20:40 の北斗 札幌方面は、時刻どおりの範囲に小さな揺れ(雑音の14倍)があり、
#    本当の揺れ(142倍)は 3.6分遅れの 20:46 だった)
STRONGER_FACTOR = 3.0
# 遅れ・形?の段階では、この長さ [秒] 以上の揺れ(貨物列車らしい長い揺れ)は対応させない
#   (2026/8/21 19:47 の北斗 札幌方面は、4.9分遅れの長い揺れ(8〜25 Hz で33秒、貨物とみられる)を
#    取っていたが、本当は10分遅れの 19:59(20秒)だった。遅れた普通列車には30秒のもの(8/21 17:29)が
#    あるので、その間にしている。余裕が小さいので、ほかの日で合わなければ見直す)
DELAY_MAX_DURATION = 32.0
# 揺れの形(最大の前後の大きさの比)から方面を見分ける
#   函館方面は洞爺駅に近づいて減速するので後半が小さく、札幌方面は通過後に加速するので後半が大きい
#   (2026/8/23 の Bamba.U で、後/前 は函館方面 0.23〜0.60(17本中16本)、札幌方面 1.35〜5.2(20本中19本))
#   拠点と駅の位置関係で決まるので、Bamba 以外の拠点では見直しが必要
#   洞爺駅に停まらない貨物列車には当てはまらない(長い編成の通過が続くため、後半が大きく出やすい)
#   ので、一覧の「揺れの形から見た方面」は時刻表の列車と対応した揺れにだけ書く
ASYM_DIRECTIONS = {"after_smaller": "函館方面", "after_larger": "札幌方面"}
ASYM_LOW, ASYM_HIGH = 0.8, 1.25   # 後/前 がこの間なら、どちらとも言えない
ASYM_SPAN = (10, 60)              # 最大の前後、何秒から何秒までを比べるか
# 時刻表と対応しなかった揺れのうち、この倍率以上を貨物列車の候補とする
FREIGHT_MIN_RATIO = 20.0
# 地震の見分け方: 列車の揺れは線路に近い拠点にしか出ないが、地震はどの拠点でも大きく揺れる
#   列車用の帯域とは別に QUAKE_BAND で全拠点の揺れを調べ、同じころに QUAKE_MIN_STATIONS 拠点以上で
#   雑音の QUAKE_RATIO 倍以上揺れていたら地震とする(データのある拠点がそれより少ない時間は、
#   データのある全拠点(2拠点以上)で超えていること)
#   地震は列車より周波数が低く、列車用の 8〜25 Hz では Bamba で小さく出るため、帯域を分けている
#   基準は記録全体から地震の候補を探す処理(quake_detect.py)と共通で、決めた経緯もそちらに書いている
# 地震の揺れがいちばん大きい時刻からこの秒数以内に最大がある揺れを、地震とみなす
#   地震の揺れは数分続くので、そのあいだに通った列車まで地震にしないため
#   (2026/8/23 22:40 の普通 函館方面は、22:45:45 の地震の最中の 22:47:35 に通っていた)
QUAKE_PEAK_SECONDS = 60.0
# 大幅な遅れを見込む上限 [分]。時刻どおり・ふつうの遅れ・形?で見つからなかった列車だけに使う
#   (2026/8/22 16:38 の北斗 函館方面は、単線の行き違いなどで約19分遅れて 16:55 に通っていた。
#    2026/8/23 22:40 の普通 函館方面は、22:45 の地震のあと 22:47 に通っていた)
#   取り違えを防ぐため、揺れの形から見た方面が一致し(どちらとも言えないものは不可)、
#   貨物列車らしい長い揺れ(FREIGHT_MIN_DURATION 秒以上)でないものに限る
LONG_DELAY_MINUTES = 30.0
# ラベルにこの言葉を含む行は、線路を通らないので揺れと対応させない(時刻表の線だけ引く)
NOT_RAIL_WORDS = ("バス",)
# どの段階でも見つからなかった列車は、見込んだ時刻の前後(MATCH_MINUTES 分前〜DELAY_MINUTES 分後)で、
# 雑音のこの倍率以上の揺れを探し直す(「弱い揺れ?」とする)
#   (2026/8/21 は昼間の雑音が深夜の約4倍あり、普通列車6本が雑音の6〜9倍にしかならず拾えていなかった)
WEAK_RATIO = 5.0
# 揺れの長さ: 1秒ごとの RMS が最大から DURATION_DB [dB] 下がるまでの秒数
#   (2026/8/23 の Bamba.U で、北斗 12〜28秒、普通 12〜33秒、深夜の貨物列車は13本中11本が34〜45秒)
DURATION_DB = 20.0
# この長さ [秒] 未満の揺れは、貨物列車の候補にしない
FREIGHT_MIN_DURATION = 30.0

# 出力先
OUTPUT_DIR = app_paths.BATCH_PLOTS_DIR / "trains"
# 画像の形式(png のほか、拡大しても粗くならない svg・pdf)
IMAGE_FORMATS = ("png", "svg", "pdf")


# 1時間ずつ SAC ファイルを用意して読み込み、全拠点の指定した成分の1日分の波形にする
#   戻り値 ... {拠点名: トレース}
def load_day(day, comp):
  st = obspy.Stream()
  for hour in range(24):
    start = day + timedelta(hours=hour)
    log_message(f"  {start:%H}時台を読み込み中")
    try:
      hour_st = load_period(start, start + timedelta(minutes=59))
    except win_pipeline.PipelineError as e:
      log_message(f"    変換できませんでした: {e}")
      continue
    if hour_st:
      st += hour_st.select(channel=comp)
  st.merge(method=1, fill_value=0)
  return {tr.stats.station: tr for tr in st}

# 帯域通過フィルターをかけたコピー
def band_filtered(tr):
  tr = tr.copy()
  tr.detrend("demean")
  tr.filter("bandpass", freqmin=BAND[0], freqmax=BAND[1], corners=4, zerophase=True)
  return tr

# (フィルター後の)波形を、窓ごとの RMS [cm/s] にする
#   戻り値 ... (窓の中心の時刻[日の始まりからの秒], RMS)
def windowed_rms(tr, day, window=RMS_WINDOW):
  n = int(window * tr.stats.sampling_rate)
  count = len(tr.data) // n
  blocks = tr.data[:count * n].astype(float).reshape(count, n) * NM_TO_CM
  rms = np.sqrt(np.mean(blocks ** 2, axis=1))
  offset = (tr.stats.starttime.datetime - day).total_seconds()
  times = offset + (np.arange(count) + 0.5) * window
  return times, rms

# 揺れごとに、揺れの長さと、最大の前後の揺れの大きさの比(後/前)を求め、揺れの形から見た方面を決める
#   ev["duration"] ... 揺れの長さ [秒](DURATION_DB を参照)
#   1秒ごとの RMS の最大を中心に、後 ASYM_SPAN 秒の平均 ÷ 前 ASYM_SPAN 秒の平均
#   ev["asym"] ... 比(求められないときは None)
#   ev["dir"]  ... ASYM_DIRECTIONS の方面、またはどちらとも言えないとき None
def classify_shape(filtered, day, events):
  t1, env = windowed_rms(filtered, day, window=1.0)
  skip, span = ASYM_SPAN
  for ev in events:
    ev["asym"], ev["dir"], ev["duration"] = None, None, ev["end"] - ev["start"]
    sel = np.flatnonzero((t1 >= ev["start"]) & (t1 <= ev["end"]))
    if len(sel) == 0:
      continue
    p = sel[np.argmax(env[sel])]
    # 最大から前後に、DURATION_DB 下がるところまでたどる
    threshold = env[p] / 10 ** (DURATION_DB / 20)
    i, j = p, p
    while i > 0 and env[i - 1] > threshold:
      i -= 1
    while j < len(env) - 1 and env[j + 1] > threshold:
      j += 1
    ev["duration"] = t1[j] - t1[i] + 1.0
    if p - span < 0 or p + span >= len(env):
      continue
    before = env[p - span:p - skip].mean()
    after = env[p + skip:p + span].mean()
    if before <= 0:
      continue
    ev["asym"] = after / before
    if ev["asym"] < ASYM_LOW:
      ev["dir"] = ASYM_DIRECTIONS["after_smaller"]
    elif ev["asym"] > ASYM_HIGH:
      ev["dir"] = ASYM_DIRECTIONS["after_larger"]

# 全拠点の揺れから地震を探し、揺れの一覧に印を付ける
#   Bamba で拾った揺れと重なる地震は、その揺れに ev["quake"] = True を付ける
#   Bamba で拾えていない地震(Bamba では小さかったもの)は、地震だけの揺れとして events に加える
#   ev["others"] ... {拠点名: 揺れの前後 QUAKE_MARGIN 秒での、QUAKE_BAND の雑音に対する最大の倍率}
#   times, ratio ... 拠点(station)の列車用の帯域での窓ごとの時刻と倍率(地震だけの揺れの大きさに使う)
def mark_earthquakes(traces, station, day, events, times, ratio):
  series = {}
  for name, tr in traces.items():
    f = tr.copy()
    f.detrend("demean")
    f.filter("bandpass", freqmin=QUAKE_BAND[0], freqmax=QUAKE_BAND[1], corners=4, zerophase=True)
    t, rms = windowed_rms(f, day)
    series[name] = (t, np.where(rms > 0, rms / noise_level(rms), np.nan))

  # 拠点ごとに、前後 QUAKE_MARGIN 秒での最大の倍率を、窓(RMS_WINDOW 秒ごと)にそろえる
  #   窓の並びは、いちばん早い窓を含む RMS_WINDOW の倍数の秒から(1日分なら日の始まりから)
  origin = min(np.floor(t[0] / RMS_WINDOW) * RMS_WINDOW for t, _ in series.values())
  count = int((max(t[-1] for t, _ in series.values()) - origin) // RMS_WINDOW) + 1
  half = int(QUAKE_MARGIN / RMS_WINDOW)
  peaks, grids = [], []
  for t, r in series.values():
    grid = np.full(count, np.nan)
    idx = ((t - origin) // RMS_WINDOW).astype(int)
    ok = (idx >= 0) & (idx < count)
    grid[idx[ok]] = r[ok]
    grids.append(grid)
    peaks.append([np.nanmax(grid[max(0, k - half):k + half + 1]) if np.isfinite(grid[max(0, k - half):k + half + 1]).any()
                  else np.nan for k in range(count)])
  peaks = np.array(peaks)
  available = np.isfinite(peaks).sum(axis=0)
  need = np.maximum(2, np.minimum(QUAKE_MIN_STATIONS, available))
  is_quake = (available >= 2) & ((peaks >= QUAKE_RATIO).sum(axis=0) >= need)

  # 続いている窓をまとめて、地震の区間 [始まり, 終わり, いちばん大きい時刻] にする
  #   いちばん大きい時刻は、全拠点の倍率(対数)の合計が最大の窓
  score = np.nansum(np.log10(np.clip(np.array(grids), 1, None)), axis=0)
  quakes = []
  for k in np.flatnonzero(is_quake):
    start, end = origin + k * RMS_WINDOW, origin + (k + 1) * RMS_WINDOW
    if quakes and start - quakes[-1][1] <= MERGE_GAP:
      quakes[-1][1] = end
    else:
      quakes.append([start, end])
  for q in quakes:
    k0, k1 = int((q[0] - origin) // RMS_WINDOW), int((q[1] - origin) // RMS_WINDOW)
    q.append(origin + (k0 + int(np.argmax(score[k0:k1])) + 0.5) * RMS_WINDOW)

  for ev in events:
    ev["others"] = {}
    for name, (t, r) in series.items():
      if name == station:
        continue
      sel = (t >= ev["start"] - QUAKE_MARGIN) & (t <= ev["end"] + QUAKE_MARGIN)
      ev["others"][name] = float(np.nanmax(r[sel])) if sel.any() and np.isfinite(r[sel]).any() else 0.0
    ev["quake"] = any(abs(ev["peak"] - qp) <= QUAKE_PEAK_SECONDS for _, _, qp in quakes)

  # Bamba で拾えていない地震を加える
  for q0, q1, qp in quakes:
    if any(ev["quake"] and abs(ev["peak"] - qp) <= QUAKE_PEAK_SECONDS for ev in events):
      continue
    sel = np.flatnonzero((times >= q0) & (times <= q1))
    sel = sel[np.isfinite(ratio[sel])] if len(sel) else sel
    peak = qp
    ev = {"start": q0, "end": q1, "peak": peak, "ratio": float(np.nanmax(ratio[sel])) if len(sel) else 0.0,
          "quake": True, "asym": None, "dir": None, "duration": q1 - q0, "others": {}}
    for name, (t, r) in series.items():
      if name != station:
        sel2 = (t >= q0 - QUAKE_MARGIN) & (t <= q1 + QUAKE_MARGIN)
        ev["others"][name] = float(np.nanmax(r[sel2])) if sel2.any() and np.isfinite(r[sel2]).any() else 0.0
    events.append(ev)
  events.sort(key=lambda ev: ev["peak"])

# 揺れの形から見た方面が、時刻表のラベルの方面と食い違っていないか
def direction_agrees(ev, label):
  return ev.get("dir") is None or not any(d in label for d in DIRECTION_OFFSETS) or ev["dir"] in label

# 雑音の基準(RMS の移動中央値)。欠測(RMS が0)の窓は除いて計算する
def noise_level(rms):
  half = int(NOISE_WINDOW / RMS_WINDOW / 2)
  noise = np.empty_like(rms)
  for i in range(len(rms)):
    seg = rms[max(0, i - half):i + half + 1]
    seg = seg[seg > 0]
    noise[i] = np.median(seg) if len(seg) else np.nan
  return noise

# RMS が雑音の EVENT_RATIO 倍を超えた区間を拾い出す
#   戻り値 ... [{"start", "end", "peak"(いずれも秒), "ratio"(最大の倍率)}, ...]
def detect_events(times, rms, noise):
  ratio = rms / noise
  events = []
  for i in np.flatnonzero(ratio > EVENT_RATIO):
    t = times[i]
    if events and t - events[-1]["end"] <= MERGE_GAP:
      ev = events[-1]
      ev["end"] = t
    else:
      ev = {"start": t, "end": t, "peak": t, "ratio": 0.0}
      events.append(ev)
    if ratio[i] > ev["ratio"]:
      ev["peak"], ev["ratio"] = t, ratio[i]
  for ev in events:
    ev["start"] -= RMS_WINDOW / 2
    ev["end"] += RMS_WINDOW / 2
  return events

# 時刻表の有効期間の列の見出しと、見出しの行が無いときの列の位置(時刻,ラベル,種別,列車名,方面,メモ のあと)
#   ダイヤ改正で時刻が変わった列車は、前の時刻の行に終了日を、新しい時刻の行に開始日を入れる
#   空欄は「その日まで(から)ずっと有効」とみなす(両方空欄なら全期間で有効)
VALID_FROM_HEADER, VALID_UNTIL_HEADER = "有効開始日", "有効終了日"
VALID_FROM_COLUMN, VALID_UNTIL_COLUMN = 6, 7

# 時刻表のCSVの各行を読む
#   戻り値 ... [(日の始まりからの秒, ラベル, 有効開始日(date または None), 有効終了日(date または None)), ...]
#   3〜6列目(etc/train_timetable_template.xlsx の種別・列車名・方面・メモ)は使わない
#   Excel の「CSV UTF-8」と、ふつうの「CSV」(Shift_JIS)のどちらでも読める
def load_timetable_rows(path):
  try:
    with open(path, encoding="utf-8-sig") as f:
      rows = list(csv.reader(f))
  except UnicodeDecodeError:
    with open(path, encoding="cp932") as f:
      rows = list(csv.reader(f))
  col_from, col_until = VALID_FROM_COLUMN, VALID_UNTIL_COLUMN
  entries = []
  for row in rows:
    if not row or not row[0].strip():
      continue
    t = parse_time(row[0].strip())
    if t is None:
      # 見出しの行: 有効期間の列の位置を見出しの名前から決める
      cells = [c.strip() for c in row]
      if VALID_FROM_HEADER in cells:
        col_from = cells.index(VALID_FROM_HEADER)
      if VALID_UNTIL_HEADER in cells:
        col_until = cells.index(VALID_UNTIL_HEADER)
      continue
    label = row[1].strip() if len(row) > 1 else ""
    valid_from = parse_valid_date(row[col_from]) if len(row) > col_from else None
    valid_until = parse_valid_date(row[col_until]) if len(row) > col_until else None
    entries.append((t.hour * 3600 + t.minute * 60 + t.second, label, valid_from, valid_until))
  return entries

# その日に有効な時刻表。戻り値 ... [(日の始まりからの秒, ラベル), ...]
#   day ... 日(date または datetime)
def timetable_on(rows, day):
  d = day.date() if isinstance(day, datetime) else day
  return [(t, label) for t, label, valid_from, valid_until in rows
          if (valid_from is None or valid_from <= d) and (valid_until is None or d <= valid_until)]

# 時刻表のCSVを読む。戻り値 ... [(日の始まりからの秒, ラベル), ...]
#   day ... その日に有効な行だけにする(None なら有効期間を見ずに全部)
def load_timetable(path, day=None):
  rows = load_timetable_rows(path)
  if day is None:
    return [(t, label) for t, label, _from, _until in rows]
  return timetable_on(rows, day)

# 「2026/3/14」「2026-03-14」「2026/03/14 0:00:00」(Excel の表示)を日付にする。空欄や日付でなければ None
def parse_valid_date(text):
  text = (text or "").strip()
  if not text:
    return None
  for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
    try:
      return datetime.strptime(text, fmt).date()
    except ValueError:
      pass
  return None

# 「6:12」「06:12」「06:12:00」を時刻にする。時刻でなければ None
def parse_time(text):
  for fmt in ("%H:%M", "%H:%M:%S"):
    try:
      return datetime.strptime(text, fmt)
    except ValueError:
      pass
  return None

# 時刻表の時刻から、拠点の横を通ると見込まれる時刻 [秒] にする(方面がわからなければそのまま)
def expected_time(t, label):
  for direction, offset in DIRECTION_OFFSETS.items():
    if direction in label:
      return t + offset * 60
  return t

# 時刻表の各列車に揺れを対応させる。すべての列車で1つの段階を終えてから、次の段階に進む
#   1. 時刻どおり ... 見込んだ時刻の前後 MATCH_MINUTES 分でいちばん大きな揺れ
#                    (揺れの形から見た方面が列車の方面と食い違う揺れは使わない)
#                    ただし DELAY_MINUTES 分あとまでに、方面の形が一致して STRONGER_FACTOR 倍以上
#                    大きい揺れがあれば、そちらを遅れとして選ぶ
#   2. 遅れ       ... 見込んだ時刻の DELAY_MINUTES 分あとまでで、まだ対応していない最も早い揺れ
#                    (DELAY_MAX_DURATION 秒以上の長い揺れは使わない)
#   3. 形?        ... 方面の形が食い違っていても、見込んだ時刻の前後 MATCH_MINUTES 分にある揺れ
#                    (DELAY_MAX_DURATION 秒以上の長い揺れは使わない)
#                    (2026/8/23 11:06 の普通 函館方面は、すぐあとに別の揺れが続いたため後半が大きく見えていた)
#   4. 大幅な遅れ ... LONG_DELAY_MINUTES 分あとまでで、方面の形が一致する短い揺れのうち最も早いもの
#                    (間に地震があれば「地震後の遅れ」とする)
#   5. 見直し     ... 1・3 で方面の形がはっきり一致しない揺れを選んだ列車は、LONG_DELAY_MINUTES 分あと
#                    までに、形がはっきり一致する短い揺れで STRONGER_FACTOR 倍以上大きいものがあれば、
#                    そちらに替える
#                    (2026/8/21・22 16:38 の北斗 函館方面は、時刻どおりの範囲の小さな揺れ(雑音の11〜14倍)を
#                    取っていたが、本当は14〜19分遅れの大きな揺れ(88〜118倍)だった)
#   ラベルに NOT_RAIL_WORDS を含む行(バスなど)は対応させない
#   戻り値 ... [[時刻表の時刻, ラベル, 見込んだ時刻, 揺れ または None, 備考], ...]
#   対応した揺れには ev["trains"] にラベルを入れる
def match_timetable(timetable, events):
  for ev in events:
    ev["trains"] = []
  matches = []
  for t, label in timetable:
    note = "対象外(線路を通らない)" if any(w in label for w in NOT_RAIL_WORDS) else ""
    matches.append([t, label, expected_time(t, label), None, note])
  pending = lambda: [m for m in matches if m[3] is None and not m[4]]
  free = lambda ev: not ev["trains"] and not ev.get("quake")

  def assign(m, ev, suffix, note):
    ev["trains"].append(train_name(m[0], m[1] + suffix))
    m[3], m[4] = ev, note

  # 1. 時刻どおり
  for m in pending():
    t, label, expected = m[:3]
    near = [ev for ev in events if not ev.get("quake") and direction_agrees(ev, label)
            and abs(ev["peak"] - expected) <= MATCH_MINUTES * 60]
    if near:
      best = max(near, key=lambda ev: ev["ratio"])
      stronger = [ev for ev in events if free(ev) and ev.get("dir") and ev["dir"] in label
                  and ev["ratio"] >= best["ratio"] * STRONGER_FACTOR
                  and MATCH_MINUTES * 60 < ev["peak"] - expected <= DELAY_MINUTES * 60]
      if stronger:
        assign(m, max(stronger, key=lambda ev: ev["ratio"]), "(遅れ)", "遅れ?")
      else:
        assign(m, best, "", "")

  # 2. 遅れ
  for m in pending():
    t, label, expected = m[:3]
    later = [ev for ev in events if free(ev) and direction_agrees(ev, label)
             and ev["duration"] < DELAY_MAX_DURATION
             and MATCH_MINUTES * 60 < ev["peak"] - expected <= DELAY_MINUTES * 60]
    if later:
      assign(m, min(later, key=lambda ev: ev["peak"]), "(遅れ)", "遅れ?")

  # 3. 形?
  for m in pending():
    t, label, expected = m[:3]
    near = [ev for ev in events if free(ev) and ev["duration"] < DELAY_MAX_DURATION
            and abs(ev["peak"] - expected) <= MATCH_MINUTES * 60]
    if near:
      assign(m, max(near, key=lambda ev: ev["ratio"]), "(形?)", "形?")

  # 4. 大幅な遅れ
  for m in pending():
    t, label, expected = m[:3]
    later = [ev for ev in events if free(ev) and ev.get("dir") and ev["dir"] in label
             and ev["duration"] < FREIGHT_MIN_DURATION
             and DELAY_MINUTES * 60 < ev["peak"] - expected <= LONG_DELAY_MINUTES * 60]
    if later:
      best = min(later, key=lambda ev: ev["peak"])
      after_quake = any(ev.get("quake") and expected <= ev["peak"] <= best["peak"] for ev in events)
      if after_quake:
        assign(m, best, "(地震後の遅れ)", "地震後の遅れ?")
      else:
        assign(m, best, "(大幅な遅れ)", "大幅な遅れ?")

  # 5. 見直し
  for m in matches:
    t, label, expected, best, note = m
    if not best or note not in ("", "形?") or (best.get("dir") and best["dir"] in label):
      continue
    later = [ev for ev in events if free(ev) and ev.get("dir") and ev["dir"] in label
             and ev["duration"] < FREIGHT_MIN_DURATION and ev["ratio"] >= best["ratio"] * STRONGER_FACTOR
             and MATCH_MINUTES * 60 < ev["peak"] - expected <= LONG_DELAY_MINUTES * 60]
    if later:
      best["trains"] = [n for n in best["trains"] if not n.startswith(train_name(t, label))]
      ev = min(later, key=lambda ev: ev["peak"])
      if ev["peak"] - expected <= DELAY_MINUTES * 60:
        assign(m, ev, "(遅れ)", "遅れ?")
      else:
        assign(m, ev, "(大幅な遅れ)", "大幅な遅れ?")
  return matches

# 時刻表と対応しなかった大きな揺れ(貨物列車の候補)か
# どの段階でも見つからなかった列車について、基準を WEAK_RATIO に下げて揺れを探し直す
#   見つかった揺れは events に加え(ev["weak"] = True)、備考を「弱い揺れ?」とする
def match_weak(matches, events, times, rms, noise, filtered, day):
  ratio = rms / noise
  for m in matches:
    t, label, expected, best, note = m
    if best or note:
      continue
    sel = np.flatnonzero((times >= expected - MATCH_MINUTES * 60) & (times <= expected + DELAY_MINUTES * 60))
    sel = sel[rms[sel] > 0]
    if len(sel) == 0:
      continue
    i = sel[np.nanargmax(ratio[sel])]
    if not ratio[i] >= WEAK_RATIO or any(ev["start"] <= times[i] <= ev["end"] for ev in events):
      continue
    # 最大から前後に、倍率が WEAK_RATIO の半分を下回るところまでを揺れの区間とする
    a, b = i, i
    while a > 0 and ratio[a - 1] > WEAK_RATIO / 2:
      a -= 1
    while b < len(ratio) - 1 and ratio[b + 1] > WEAK_RATIO / 2:
      b += 1
    ev = {"start": times[a] - RMS_WINDOW / 2, "end": times[b] + RMS_WINDOW / 2, "peak": times[i],
          "ratio": ratio[i], "weak": True, "quake": False, "others": {}}
    classify_shape(filtered, day, [ev])
    ev["trains"] = [train_name(t, f"{label}(弱い揺れ)")]
    events.append(ev)
    m[3], m[4] = ev, "弱い揺れ?"
  events.sort(key=lambda ev: ev["peak"])

def is_freight_candidate(ev):
  return (not ev.get("trains", True) and not ev.get("quake") and ev["ratio"] >= FREIGHT_MIN_RATIO
          and ev.get("duration", 0) >= FREIGHT_MIN_DURATION)

# 波形から揺れを拾い出し、地震を見分けて、時刻表と対応させる(単体実行とアプリの画面で共通)
#   traces    ... {拠点名: トレース}(上下動。調べる期間を含む)
#   station   ... 列車を調べる拠点
#   day       ... 秒の基準にする日の始まり(時刻表の時刻もこの日の秒)
#   timetable ... load_timetable の結果(None なら時刻表とは対応させず、貨物列車の候補も決めない。
#                 空のリストなら、どの揺れも時刻表の列車と対応しないものとして貨物列車の候補を決める)
#   戻り値 ... dict(times, rms, noise(列車用の帯域の窓ごとの値), filtered, events, matches)
def analyze(traces, station, day, timetable):
  filtered = band_filtered(traces[station])
  times, rms = windowed_rms(filtered, day)
  noise = noise_level(rms)
  events = detect_events(times, rms, noise)
  classify_shape(filtered, day, events)
  mark_earthquakes(traces, station, day, events, times, rms / noise)
  matches = match_timetable(timetable, events) if timetable is not None else []
  if timetable:
    match_weak(matches, events, times, rms, noise, filtered, day)
  return dict(times=times, rms=rms, noise=noise, filtered=filtered, events=events, matches=matches)

# 「2026/8/23」を日付にする。日付でなければ None
def parse_date(text):
  try:
    return datetime.strptime(text, "%Y/%m/%d")
  except ValueError:
    return None

# 揺れに付ける列車名(洞爺駅の発車時刻つき)。例: 08:43発 特急北斗 函館方面
def train_name(t, label):
  return f"{hhmmss(t)[:5]}発 {label}"

# 日の始まりからの秒を時刻にする(前後の日の分は、その日の時刻にする)
def hhmmss(sec):
  sec = int(round(sec)) % 86400
  return f"{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"

def is_freight(label):
  return label.startswith("貨")

# 6時間ずつ4段のグラフにする
#   pdf ... PdfPages を渡すと、そのページとしても保存する
def save_image(path, title, times, rms, noise, events, timetable, pdf=None):
  fig, axes = plt.subplots(4, 1, figsize=(16, 13))
  positive = rms[rms > 0]
  ymin = np.percentile(positive, 1) * 0.5 if len(positive) else 1e-9
  ymax = rms.max() * 2 if len(positive) else 1e-6
  for row, ax in enumerate(axes):
    lo, hi = row * 6 * 3600, (row + 1) * 6 * 3600
    sel = (times >= lo) & (times < hi)
    hours = times[sel] / 3600
    ax.semilogy(hours, rms[sel], linewidth=0.6, color="#1f77b4", label="RMS")
    ax.semilogy(hours, noise[sel] * EVENT_RATIO, linewidth=0.8, color="gray",
                linestyle="--", label=f"雑音×{EVENT_RATIO:g}")
    for ev in events:
      if lo <= ev["peak"] < hi:
        # 貨物列車の候補は赤っぽく、地震は紫に、それ以外の揺れはオレンジで塗る
        color = "#ff9896" if is_freight_candidate(ev) else "#c5b0d5" if ev.get("quake") else "#ffbb78"
        ax.axvspan(ev["start"] / 3600, ev["end"] / 3600, color=color, alpha=0.6, lw=0)
    # 時刻表の線は、拠点の横を通ると見込んだ時刻に引く
    for t_dep, label in timetable:
      t = expected_time(t_dep, label)
      if lo <= t < hi:
        color = "#d62728" if is_freight(label) else "#2ca02c"
        ax.axvline(t / 3600, color=color, linewidth=0.8)
        ax.text(t / 3600, ymax, train_name(t_dep, label), rotation=90, fontsize=7, color=color,
                va="top", ha="right")
    ax.set_xlim(lo / 3600, hi / 3600)
    ax.set_ylim(ymin, ymax)
    ax.set_xticks(np.arange(lo / 3600, hi / 3600 + 0.01, 0.5))
    ax.set_xticklabels([f"{int(h)}:{int(round(h % 1 * 60)):02d}"
                        for h in np.arange(lo / 3600, hi / 3600 + 0.01, 0.5)], fontsize=8)
    ax.set_ylabel("RMS [cm/s]")
    ax.grid(True, which="major", alpha=0.3)
  axes[0].legend(loc="upper right", fontsize=8)
  fig.suptitle(title)
  fig.tight_layout()
  fig.savefig(path, dpi=120)   # SVG・PDF のときは dpi は使われない(ベクターのまま)
  if pdf:
    pdf.savefig(fig)
  plt.close(fig)

# 揺れの帯の色: 時刻表の列車と対応 / 地震 / 貨物列車の候補 / その他
def event_color(ev):
  if ev.get("trains"):
    return "#2ca02c"
  if ev.get("quake"):
    return "#9467bd"
  if is_freight_candidate(ev):
    return "#d62728"
  return "#ff7f0e"

# 帯の説明(1時間スタック波形の下に書く)
STACK_LEGEND = ("▼ 時刻表の列車が通ると見込んだ時刻 / 帯: 緑=時刻表の列車と対応した揺れ、"
                "赤=貨物列車の候補、紫=地震、橙=その他の揺れ(×は雑音の何倍か)")

# 1時間スタック波形の軸に、揺れと時刻表の列車を重ねる(画像の保存と、アプリの画面で共通)
#   揺れ   ... その区間を段ごとに色の帯で塗り、始まりに種類と倍率を書く
#   時刻表 ... 見込んだ通過時刻に ▼ を描く
#   base   ... 表示する1時間の開始の、日の始まりからの秒(揺れ・時刻表の秒と同じ基準)
#   quakes ... 地震とみなした揺れも描くか(アプリの画面で地震の重ね表示もオンのときは、重ならないよう描かない)
#   戻り値 ... 追加した図形のリスト
def draw_train_overlay(ax, events, timetable, base, quakes=True):
  artists = []
  for ev in events:
    if ev.get("quake") and not quakes:
      continue
    if ev.get("trains"):
      text = " / ".join(ev["trains"])
    elif ev.get("quake"):
      text = "地震(ほかの拠点でも大きい)"
    elif is_freight_candidate(ev):
      text = f"貨物列車の候補 {ev['duration']:.0f}秒"
    else:
      text = "揺れ"
    artists += add_span(ax, ev["start"] - base, ev["end"] - base, event_color(ev), f"{text} ×{ev['ratio']:.0f}")
  for t, label in timetable:
    t = expected_time(t, label) - base
    if 0 <= t < STACK_ROWS * 60:
      row, x = int(t // 60), t % 60
      color = "#d62728" if is_freight(label) else "#2ca02c"
      artists += ax.plot(x, row - 0.3, marker="v", color=color, markersize=5)
  return artists

# 1時間スタック波形(アプリの画像と同じ図)に、揺れと時刻表の列車を重ねて保存する
#   pdf ... PdfPages を渡すと、そのページとしても保存する(波形は画像にして、ファイルを小さくする)
def save_stack_image(path, tr, hour_start, day, station, comp, events, timetable, pdf=None):
  stack = StackFigure(for_screen=False)
  stack.render(tr, hour_start, station, comp)
  draw_train_overlay(stack.ax, events, timetable, (hour_start - day).total_seconds())

  # 下に説明の1行を入れるため、軸の下の余白を少し広げる
  stack.fig.subplots_adjust(bottom=0.075)
  stack.fig.text(0.07, 0.008, STACK_LEGEND, fontsize=7, color="#333333", va="bottom")
  stack.fig.savefig(path, dpi=100)
  if pdf:
    for line in stack.lines:
      line.set_rasterized(True)
    pdf.savefig(stack.fig, dpi=150)
  plt.close(stack.fig)

def main():
  parser = argparse.ArgumentParser(description="1日分の波形と時刻表を比べるグラフを作る")
  parser.add_argument("date", help="日付(例: 2026/8/23)")
  parser.add_argument("timetable", nargs="?", help="時刻表のCSV(時刻,ラベル)")
  parser.add_argument("--station", default="Bamba", help="拠点名(既定: Bamba)")
  parser.add_argument("--comp", default="U", help="成分 U/N/E(既定: U)")
  parser.add_argument("--stack", action="store_true",
                      help="1時間スタック波形に揺れと時刻表を重ねた画像も24枚作る")
  parser.add_argument("--format", choices=IMAGE_FORMATS, default="png",
                      help="画像の形式(既定: png。svg・pdf は拡大しても粗くならない)")
  parser.add_argument("--hours", help="--stack で画像を作る時間(例: 18,22。既定は0〜23時のすべて)")
  args = parser.parse_args()

  # 日付と時刻表のCSVの順番が逆でも受け付ける
  day = parse_date(args.date)
  if day is None and args.timetable and parse_date(args.timetable):
    args.date, args.timetable = args.timetable, args.date
    day = parse_date(args.date)
  if day is None:
    parser.error(f"日付を 2026/8/23 のように指定してください(指定された値: {args.date})\n"
                 f"  例: python3 scripts/train_day_plot.py 2026/8/23 etc/train_timetable.csv")
  try:
    hours = sorted({int(h) for h in args.hours.split(",")}) if args.hours else list(range(24))
  except ValueError:
    hours = []
  if not hours or any(not 0 <= h <= 23 for h in hours):
    parser.error(f"--hours は 0〜23 の時をカンマで区切って指定してください(指定された値: {args.hours})")
  ext = args.format

  set_console(True)
  setup_japanese_font()
  label = f"{day:%Y-%m-%d} {args.station}.{args.comp}"
  log_message(f"{label}: 読み込み中")
  traces = load_day(day, args.comp)
  tr = traces.get(args.station)
  if tr is None:
    log_message("  波形がありません")
    return

  timetable = load_timetable(args.timetable, day) if args.timetable else []
  res = analyze(traces, args.station, day, timetable if args.timetable else None)
  times, rms, noise, events, matches = res["times"], res["rms"], res["noise"], res["events"], res["matches"]
  quakes = [ev for ev in events if ev["quake"]]
  log_message(f"  拾い出した揺れ: {len(events)}件(うち地震とみなしたもの: {len(quakes)}件 "
              + " ".join(hhmmss(ev["peak"]) for ev in quakes) + ")")
  candidates = [ev for ev in events if is_freight_candidate(ev)]

  OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  stem = f"{day:%Y%m%d}_{args.station}.{args.comp}"
  title = (f"{label}  {BAND[0]:g}-{BAND[1]:g}Hz {RMS_WINDOW:g}秒RMS  "
           f"揺れ(雑音の{EVENT_RATIO:g}倍超) {len(events)}件")
  if timetable:
    title += f"  時刻表にない大きな揺れ(貨物列車の候補・赤) {len(candidates)}件"
  if quakes:
    title += f"  地震(紫) {len(quakes)}件"
  # --stack のときは、1日のグラフと24時間分のスタック波形を1つのPDFにもまとめる
  #   (--hours で時間を絞ったときは、24時間分のPDFを上書きしないよう作らない)
  pdf = PdfPages(OUTPUT_DIR / f"{stem}_trains.pdf") if args.stack and not args.hours else None
  save_image(OUTPUT_DIR / f"{stem}.{ext}", title, times, rms, noise, events, timetable, pdf)

  # 拾い出した揺れの一覧(時刻表を指定したときは、対応した列車と貨物列車の候補も)
  with open(OUTPUT_DIR / f"{stem}_events.csv", "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["開始", "最大", "終了", "長さ[秒]", "最大の倍率", "前後の比(後/前)",
                     "揺れの形から見た方面", "時刻表の列車", "貨物列車の候補", "地震",
                     f"ほかの拠点の最大の倍率({QUAKE_BAND[0]:g}〜{QUAKE_BAND[1]:g} Hz)", f"揺れの長さ(最大から{DURATION_DB:g}dB)[秒]"])
    for ev in events:
      writer.writerow([hhmmss(ev["start"]), hhmmss(ev["peak"]), hhmmss(ev["end"]),
                       f"{ev['end'] - ev['start']:.0f}", f"{ev['ratio']:.1f}",
                       f"{ev['asym']:.2f}" if ev.get("asym") else "",
                       (ev.get("dir") or "") if ev.get("trains") else "",
                       " / ".join(ev.get("trains", [])), "★" if is_freight_candidate(ev) else "",
                       "★" if ev["quake"] else "",
                       " / ".join(f"{k} {v:.0f}" for k, v in ev["others"].items()),
                       f"{ev['duration']:.0f}"])

  # 見込んだ時刻の前後にデータがあるか(欠測は0で埋まっているので、RMS が0より大きい窓があるか)
  def has_data(expected):
    sel = np.abs(times - expected) <= MATCH_MINUTES * 60
    return bool(np.any(rms[sel] > 0))

  # 時刻表との対応(入力の順(方面ごと)ではなく、洞爺駅の時刻の順に並べる)
  if timetable:
    with open(OUTPUT_DIR / f"{stem}_match.csv", "w", encoding="utf-8-sig", newline="") as f:
      writer = csv.writer(f)
      writer.writerow(["時刻表", "ラベル", "見込んだ時刻", "揺れの最大", "見込みとのずれ[分]",
                       f"揺れの長さ(最大から{DURATION_DB:g}dB)[秒]", "最大の倍率", "備考"])
      for t, lbl, expected, ev, note in sorted(matches, key=lambda m: m[0]):
        row = [hhmmss(t)[:5], lbl, hhmmss(expected)]
        if ev:
          row += [hhmmss(ev["peak"]), f"{(ev['peak'] - expected) / 60:+.1f}",
                  f"{ev['duration']:.0f}", f"{ev['ratio']:.1f}", note]
        else:
          row += ["", "", "", "", note or ("見つからない" if has_data(expected) else "欠測(データなし)")]
        writer.writerow(row)
    counts = {}
    for m in matches:
      key = (m[4] or "時刻どおり") if m[3] else (m[4] or ("見つからない" if has_data(m[2]) else "欠測"))
      counts[key] = counts.get(key, 0) + 1
    log_message(f"  時刻表 {len(matches)}本: " + "、".join(f"{k} {v}本" for k, v in counts.items()))
    log_message(f"  時刻表にない大きく長い揺れ(雑音の{FREIGHT_MIN_RATIO:g}倍以上・{FREIGHT_MIN_DURATION:g}秒以上、貨物列車の候補): "
                f"{len(candidates)}件")

  # 1時間スタック波形に重ねた画像(stack_日付_時_拠点_成分_trains.png など)
  if args.stack:
    for hour in hours:
      hour_start = day + timedelta(hours=hour)
      save_stack_image(OUTPUT_DIR / f"stack_{day:%Y%m%d}_{hour:02d}_{args.station}_{args.comp}_trains.{ext}",
                       tr, hour_start, day, args.station, args.comp, events, timetable, pdf)
    if pdf:
      pdf.close()
    log_message(f"  1時間スタック波形の画像: {len(hours)}枚" + (f"(まとめたPDF: {stem}_trains.pdf)" if pdf else ""))
  log_message(f"出力: {OUTPUT_DIR}")

if __name__ == "__main__":
  main()
