# stack_overlays.py
# 1時間スタック波形の画面に重ねて表示するもの(画面上部のトグルボタンでオン・オフする)
#   地震 ... 気象庁の震源リストの地震の到達見込みと、地震の候補(震源リストと照合したもの・照合できないもの)
#   列車 ... JR の時刻表と対応した列車の揺れ・貨物列車の候補(線路に近い拠点だけ)
#   どちらも、ほかの処理(jma_match・quake_detect・train_day_plot)の結果を重ねるだけで、基準はそちらと同じ
#   画面(stack_plot.StackWindow)とバッチ処理の画像保存で共通。1つの重ね表示は次を持つ
#     name ... ボタンの文字 / legend ... 色の説明
#     available(1時間の開始, 拠点) -> (使えるか, 使えない理由)
#     draw(軸, 1時間の開始, 拠点, オンになっている重ね表示の名前の集合) -> 追加した図形のリスト
from datetime import timedelta
from pathlib import Path

import numpy as np

import jma_hypocenter
import jma_match
import quake_detect
import train_day_plot
from stack_plot import STACK_ROWS, add_span, add_label
from log_common import log_message

# 帯・線の色
COLOR_MATCHED = "#9467bd"   # 震源リストと照合した地震の候補
COLOR_UNKNOWN = "#e377c2"   # 震源リストに無い地震の候補(未知の地震かもしれない)
COLOR_P, COLOR_S = "#d62728", "#2ca02c"   # 震源リストの地震の P波・S波の到達見込み
COLOR_JMA_TEXT = "#1f4e9c"   # 震源リストの地震の文字(貨物列車の候補の赤と区別する)
# 検出結果が無い日の注意書きの高さ(軸の下端からの割合。横軸の目盛りの下、横軸の名前と同じ高さ)
NO_RMS_NOTE_Y = -0.034
# 到達見込みを描く震源リストの地震の、見込みの振幅(log A)の下限
#   照合に使う下限(jma_match.MIN_LOG_AMP)の地震まで描くと、揺れの見えない小さな地震で図が埋まるため、
#   揺れの始まりで照合を確かめる基準(jma_match.ONSET_CHECK_LOG_AMP)以上にする
MIN_DRAW_LOG_AMP = jma_match.ONSET_CHECK_LOG_AMP


def _day_start(dt):
  return dt.replace(hour=0, minute=0, second=0, microsecond=0)

# 地震: 震源リストの地震の到達見込みと、地震の候補
#   stations ... {拠点名: (緯度, 経度, 高度)}(到達見込みの計算と、震源リストとの照合に使う)
class QuakeOverlay:
  name = "地震"
  legend = ("地震: 帯 紫=震源リストと照合した地震の候補、桃=震源リストに無い地震の候補(×は雑音の倍率)\n"
            "　　 縦線=震源リストの地震の到達見込み(赤 P波・緑 S波、青い文字は地名・M・距離)")

  def __init__(self, stations, log=log_message):
    self.stations = stations
    self.log = log
    self._days = {}   # 日 → その日の候補(震源リストと照合済み)

  def available(self, dt, station):
    day = _day_start(dt)
    if quake_detect.load_rms_cache(day) is None and not jma_hypocenter.csv_path(day).exists():
      return False, "この日の RMS(--detect-quakes で保存)も震源リスト(jma_hypocenter.py で取得)もありません"
    return True, ""

  # その日の地震の候補。RMS は前後の日もつなぎ、日の境目の雑音の基準をそろえる
  def _candidates(self, day):
    if day in self._days:
      return self._days[day]
    days = [d for d in (day - timedelta(days=1), day, day + timedelta(days=1))]
    caches = [quake_detect.load_rms_cache(d) for d in days]
    cands = []
    if caches[1] is not None:
      # 当日を含む、続いている日だけを使う
      first = 0 if caches[0] is not None else 1
      last = 2 if caches[2] is not None else 1
      use = caches[first:last + 1]
      names = sorted(set().union(*[c.keys() for c in use]))
      rms = {nm: np.concatenate([c.get(nm, np.full(quake_detect.WINDOWS_PER_DAY, np.nan)) for c in use])
             for nm in names}
      found, _info = quake_detect.find_candidates(days[first], rms)
      cands = [c for c in found if day <= c["start"] < day + timedelta(days=1)]
      if cands:
        catalog, _missing = jma_match.load_catalog(day, day + timedelta(days=1), jma_match.array_center(self.stations))
        jma_match.match_candidates(cands, catalog)
    self._days[day] = cands
    return cands

  def draw(self, ax, t0, station, active):
    t1 = t0 + timedelta(minutes=STACK_ROWS)
    sec = lambda t: (t - t0).total_seconds()
    artists = []

    # 地震の候補(帯)。帯の文字に地名を書いた地震は、到達見込みの文字を省く(重ならないように)
    labeled = set()
    if quake_detect.load_rms_cache(_day_start(t0)) is None:
      # 検出結果が無い日は、候補が無いのか検出していないのか見分けられないので、図の左下に書いておく
      #   (図の上は画面ではボタンと重なるため、横軸の名前の左に置く。作り方はメンテナンス画面の「地震の候補の検出」)
      artists.append(ax.text(0.0, NO_RMS_NOTE_Y, "※ 検出結果(RMS)が無い日のため、候補の帯なし",
                             transform=ax.transAxes, fontsize=8, color="#b03a2e", va="top", ha="left"))
    for cand in self._candidates(_day_start(t0)):
      if cand["end"] <= t0 or cand["start"] >= t1:
        continue
      q = cand.get("match")
      if q:
        text = f"地震(震源リスト: {q['震央地名']} M{q['M']:g}) ×{cand['max_ratio']:.0f}"
        color = COLOR_MATCHED
        labeled.add(q["日時"])
      else:
        text = f"地震の候補(震源リストに無い) {cand['over']}拠点 ×{cand['max_ratio']:.0f}"
        color = COLOR_UNKNOWN
      artists += add_span(ax, sec(cand["start"]), sec(cand["end"]), color, text, alpha=0.15)

    # 震源リストの地震の到達見込み(表示中の拠点での P波・S波)
    site = self.stations.get(station) or next(iter(self.stations.values()))
    hits, _available = jma_match.quakes_in_window(t0, t1, site[:2])
    for q in hits:
      if q["log_amp"] < MIN_DRAW_LOG_AMP:
        continue
      for t, color in ((q["p_time"], COLOR_P), (q["s_time"], COLOR_S)):
        s = sec(t)
        if 0 <= s < STACK_ROWS * 60:
          row, x = int(s // 60), s % 60
          artists += ax.plot([x, x], [row - 0.45, row + 0.45], color=color, lw=1.2, zorder=3)
      s = sec(q["p_time"])
      if 0 <= s < STACK_ROWS * 60 and q["日時"] not in labeled:
        # 帯の文字(段の上端の左)と重ならないよう、P波の線の右に書く
        artists.append(add_label(ax, s + 0.3, f"{q['震央地名']} M{q['M']:g} {q['dist_km']:.0f} km", COLOR_JMA_TEXT))
    return artists

# 列車: 時刻表と対応した揺れ・貨物列車の候補(train_day_plot と同じ解析)
#   timetable_path ... 時刻表の CSV(etc/train_timetable.csv)
#   load_hour      ... 日時を受け取り、その1時間の全拠点の上下動の Stream(または None)を返す関数
#   雑音の基準(前後30分)と大幅な遅れ(30分)を見込むため、表示する1時間の前後1時間も読み込む
class TrainOverlay:
  name = "列車"
  legend = "列車: " + train_day_plot.STACK_LEGEND.replace(" / 帯: ", "\n　　 帯: ")
  # 列車を調べる拠点(揺れの形の見分け方や通過時刻のずれは、この拠点と洞爺駅の位置関係で決めている)
  STATIONS = ("Bamba",)

  def __init__(self, timetable_path, load_hour, log=log_message):
    self.timetable_path = Path(timetable_path)
    self.load_hour = load_hour
    self.log = log
    self._timetable = None
    self._hours = {}   # (表示する1時間の開始, 拠点) → (揺れ, その時間の前後の時刻表)

  def available(self, dt, station):
    if station not in self.STATIONS:
      return False, f"線路に近い拠点({'・'.join(self.STATIONS)})だけで使えます"
    if not self.timetable_path.exists():
      return False, f"時刻表がありません({self.timetable_path.name})"
    return True, ""

  # 表示する1時間(t0 から)の揺れと、その前後の時刻表(地震の候補を外部の観測点で確かめる処理(quake_loose.py)でも使う)
  def analyze_hour(self, t0, station):
    key = (t0, station)
    if key in self._hours:
      return self._hours[key]
    if self._timetable is None:
      self._timetable = train_day_plot.load_timetable(self.timetable_path)
    self.log(f"列車の揺れを調べています: {t0:%Y/%m/%d %H}時台(前後1時間を含む)")
    st = None
    for h in (-1, 0, 1):
      hour_st = self.load_hour(t0 + timedelta(hours=h))
      if hour_st:
        hour_st = hour_st.select(channel="*U")
        st = hour_st if st is None else st + hour_st
    if st is None:
      raise RuntimeError("波形を読み込めませんでした")
    st.merge(method=1, fill_value=0)
    traces = {tr.stats.station: tr for tr in st}
    if station not in traces:
      raise RuntimeError(f"{station} の上下動がありません")
    # 秒の基準は表示する時間の日の始まり。前後の日の時刻表も、その日の秒に直して加える
    day = _day_start(t0)
    lo = (t0 - day).total_seconds() - 3600
    hi = lo + 3 * 3600
    timetable = [(t + k * 86400, label) for k in (-1, 0, 1) for t, label in self._timetable
                 if lo <= train_day_plot.expected_time(t + k * 86400, label) < hi]
    res = train_day_plot.analyze(traces, station, day, timetable)
    self._hours[key] = (res["events"], timetable)
    return self._hours[key]

  def draw(self, ax, t0, station, active):
    events, timetable = self.analyze_hour(t0, station)
    base = (t0 - _day_start(t0)).total_seconds()
    # 地震の重ね表示もオンなら、地震はそちらに任せる(同じ揺れに2つの帯と文字が重なるため)
    quakes = QuakeOverlay.name not in active
    return train_day_plot.draw_train_overlay(ax, events, timetable, base, quakes)


# 保存する画像(StackFigure(for_screen=False))に重ね表示を描き、下に色の説明を書く
#   overlays ... 重ね表示のリスト(使えないものは飛ばし、理由を説明の欄に書く)
def draw_on_image(stack, overlays, t0, station, log=log_message):
  active = {ov.name for ov in overlays}
  legends = []
  for ov in overlays:
    ok, reason = ov.available(t0, station)
    if not ok:
      legends.append(f"{ov.name}: {reason}")
      continue
    try:
      ov.draw(stack.ax, t0, station, active)
      legends.append(ov.legend)
    except Exception as e:
      log(f"     {ov.name}の重ね表示に失敗しました: {e}")
      legends.append(f"{ov.name}: 表示できませんでした({e})")
  text = "\n".join(legends)
  lines = text.count("\n") + 1
  # 説明の行数だけ、軸の下の余白を広げる(横軸の文字と重ならないように)
  stack.fig.subplots_adjust(bottom=0.05 + 0.014 * lines)
  stack.fig.text(0.07, 0.005, text, fontsize=7, color="#333333", va="bottom", linespacing=1.3)
