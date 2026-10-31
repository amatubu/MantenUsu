# stack_plot.py
# 1時間スタック波形(1分ごとに60段に並べた図)の計算と描画
import sys
import tkinter as tk
import numpy as np
import matplotlib.pyplot as plt
from obspy import UTCDateTime
from datetime import timedelta
from matplotlib.widgets import Button
from matplotlib.transforms import Bbox, TransformedBbox
from plot_common import NM_TO_CM, get_component_color
from log_common import log_message

STACK_ROWS = 60  # 1時間 = 60分 = 60段

COMP_ORDER = ["U", "N", "E"]   # 成分の並び順

COMPARE_MINUTES = [1, 2, 3, 4, 5, 10, 15, 20, 30]   # 右クリックメニューで選べる比較表示の長さ(分)

# ボタンの状態ごとの色: (通常の色, マウスが乗ったときの色)
BTN_COLORS = {
  "selected": ("#9ec5f0", "#9ec5f0"),
  "normal":   ("#f0f0f0", "#e0e0e0"),
  "disabled": ("#d8d8d8", "#d8d8d8"),
}

# 拠点名と成分(U/N/E)に一致する最初のトレースを返す。無ければ None
def find_trace(stream, station, comp):
  for tr in stream:
    if tr.stats.station.lower() == station.lower() and tr.stats.channel[-1].upper() == comp.upper():
      return tr
  return None

# 1時間分のトレースを1分ごと(60本)に切り出す
#   trace    ... 1時間分のトレース
#   start_dt ... 1時間の開始日時(datetime)
#   戻り値    ... 60本のトレースのリスト(データが無い区間は0で埋める)
def slice_into_minutes(trace, start_dt):
  t0 = UTCDateTime(start_dt)
  return [
    trace.copy().trim(starttime=t0 + i * 60, endtime=t0 + (i + 1) * 60,
                      pad=True, fill_value=0)
    for i in range(STACK_ROWS)
  ]

# 60本全体での最大振幅(絶対値)を返す。全部0または空のときは 1.0
def calc_global_max(minute_traces):
  arrays = [tr.data for tr in minute_traces if len(tr.data) > 0]
  if not arrays:
    return 1.0
  global_max = np.max(np.abs(np.concatenate(arrays)))
  return global_max if global_max > 0 else 1.0

# 60段のスタック図(画面表示・画像保存で共通)
# 速く描けるよう、1つの軸に60本の線を並べる。i 分目の段は y = i を中心に描く(上が00分)
class StackFigure:
  # for_screen ... True: 画面表示用(上部に前後ボタン用の余白) / False: 画像保存用
  def __init__(self, for_screen=True):
    self.fig = plt.figure(figsize=(8.5, 10.0))   # 60本並べるため、ある程度の縦の高さを確保

    if for_screen:
      # 画面用: 上部に「前後1時間」ボタンを置くので余白を広く取り、その左に最大振幅を表示
      self.fig.subplots_adjust(left=0.07, right=0.97, bottom=0.05, top=0.90)
      self.max_amp_text = self.fig.text(0.75, 0.925, "", fontsize=9, va="center", ha="right", color="#333333")
    else:
      # 画像保存用: ボタンが無いので上の余白は小さく、最大振幅は右上に表示
      self.fig.subplots_adjust(left=0.07, right=0.97, bottom=0.05, top=0.94)
      self.max_amp_text = self.fig.text(0.95, 0.95, "", fontsize=9, va="center", ha="right", color="#333333")

    ax = self.fig.add_subplot(111)
    self.ax = ax
    # 縦軸: 段の番号(0〜59)。上が00分になるよう反転する
    ax.set_xlim(0, 60)
    ax.set_ylim(STACK_ROWS - 0.5, -0.5)
    # 縦軸の目盛りとして「何分目か(例: 00)」を表示
    ax.set_yticks(range(STACK_ROWS))
    ax.set_yticklabels([f"{i:02d}" for i in range(STACK_ROWS)], fontsize=6)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Time [s (0-60s per trace)]", fontsize=10)
    # 10秒単位の縦線をグレーの実線で表示
    ax.grid(True, axis="x", which="major", linestyle="-", color="#cccccc", alpha=0.5)
    # 外枠
    for spine in ax.spines.values():
      spine.set_linewidth(0.8)
      spine.set_color("#333333")

    # 60段分の線。左右は枠で切り、上下は枠の外へのはみ出しも描く(大きな揺れが見えるように)
    clip = TransformedBbox(Bbox([[0, -1], [1, 2]]), ax.transAxes)
    self.lines = []
    for _ in range(STACK_ROWS):
      line, = ax.plot([], [], linewidth=0.3)
      line.set_clip_box(clip)
      self.lines.append(line)

  # 縦軸の値(マウス位置など)から、何段目(何分目)かを返す。範囲外は None
  def row_at(self, ydata):
    if ydata is None:
      return None
    row = int(round(ydata))
    return row if 0 <= row < STACK_ROWS else None

  # 1時間分の波形を描画する
  #   trace         ... 1時間分のトレース
  #   start_dt      ... 1時間の開始日時(datetime)
  #   station, comp ... 拠点名・成分(タイトル表示用)
  def render(self, trace, start_dt, station, comp):
    # 1. 1時間データを1分ごとにスライス
    minute_traces = slice_into_minutes(trace, start_dt)

    # 2. 共通の最大振幅とスケール計算
    #    最大振幅の 1/5 が段の半分の高さ(0.5)になる。大きな揺れは上下の段にはみ出る
    global_max = calc_global_max(minute_traces)
    half_height = global_max / 5.0
    self.max_amp_text.set_text(f"最大振幅: {global_max * NM_TO_CM:.3e} cm/s")

    # 3. 60本の波形のデータを差し替える(0分〜59分)。色は成分(U/N/E)ごと
    #    縦軸は下向きが正なので、符号を反転して上向きを正にする
    col = get_component_color(comp.upper())
    for i, (tr, line) in enumerate(zip(minute_traces, self.lines)):
      line.set_data(tr.times(), i - tr.data / half_height * 0.5)
      line.set_color(col)

    # ズームしていたら全体表示に戻す
    self.ax.set_xlim(0, 60)

    # 全体タイトル(この図のタイトルとして設定する)
    date_title = start_dt.strftime("%Y/%m/%d %H:00")
    self.fig.suptitle(f"有珠満点地震計 1時間スタック波形 ({date_title}) - チャンネル: {station}.{comp}", fontsize=12)

# 1時間スタック波形の表示ウィンドウ(前後の時間への移動・拠点と成分の切り替えつき)
#   start_dt      ... 最初に表示する1時間の開始日時(datetime)
#   station, comp ... 最初に表示する拠点名・成分(U/N/E)
#   load_stream   ... 日時を受け取り、その1時間分の全チャンネルのStreamを返す関数。
#                     失敗したら None を返す(エラー表示は関数側で行う)
#   channels      ... "拠点名.成分" のリスト(例: ["Hokudai.U", ...])。ボタンの一覧に使う
#   log           ... ログを受け取る関数(メッセージ文字列を1つ受け取る)
#   show_error    ... エラーメッセージを受け取って表示する関数
#   on_compare    ... 右クリックメニューで期間を選んだときに呼ぶ関数。
#                     (開始日時, 分数, 拠点名, 成分, このウィンドウ(Tk)) を受け取る。
#                     None なら右クリックメニューを出さない
class StackWindow:
  def __init__(self, start_dt, station, comp, load_stream, channels,
               log=log_message, show_error=log_message, on_compare=None):
    # 表示中の状態(時刻・拠点・成分)
    self.dt = start_dt
    self.station = station
    self.comp = comp
    self.load_stream = load_stream
    self.log = log
    self.show_error = show_error
    self.on_compare = on_compare
    # 右クリックメニューで選択中の範囲を示すハイライト(軸に追加した図形のリスト)
    self._highlights = []

    # 直近に表示した1時間分のStream(同じ時間内の切り替えで読み直さないため)
    self._stream_dt = None
    self._stream = None

    # 拠点・成分の一覧(定義ファイル順。成分は U, N, E の順に並べる)
    self.stations = list(dict.fromkeys(ch.split(".", 1)[0] for ch in channels))
    comps = {ch.split(".", 1)[1] for ch in channels}
    self.comps = sorted(comps, key=lambda c: COMP_ORDER.index(c) if c in COMP_ORDER else 99)

    # 表示中の1時間にデータがある (拠点名(小文字), 成分(大文字)) の集合
    self.available = set()

    # 60段の図を作る(画面表示用)
    self.stack = StackFigure()
    self.fig = self.stack.fig

    # グラフ上部右側に「前の1時間」「次の1時間」ボタンを配置
    ax_prev = self.fig.add_axes([0.77, 0.91, 0.09, 0.03])  # [left, bottom, width, height]
    ax_next = self.fig.add_axes([0.88, 0.91, 0.09, 0.03])
    self.btn_prev = Button(ax_prev, "◀ -1h", color="#f0f0f0", hovercolor="#e0e0e0")
    self.btn_next = Button(ax_next, "+1h ▶", color="#f0f0f0", hovercolor="#e0e0e0")
    self.btn_prev.label.set_fontsize(9)
    self.btn_next.label.set_fontsize(9)
    self.btn_prev.on_clicked(lambda _event: self.move_hour(-1))
    self.btn_next.on_clicked(lambda _event: self.move_hour(+1))

    # グラフ上部左側に、拠点ボタン(上段)と成分ボタン(下段)を配置
    self.station_buttons = self._make_button_row(
        self.stations, y=0.930, width=0.085, on_click=self.set_station)
    self.comp_buttons = self._make_button_row(
        self.comps, y=0.905, width=0.06, on_click=self.set_comp)

    # 横軸のズーム・ドラッグ・リセットを設定
    attach_zoom_pan(self.fig, self.stack.ax)

    # 右クリックで、クリックした分からの比較表示メニューを出す
    if on_compare is not None:
      self.fig.canvas.mpl_connect("button_press_event", self._on_right_click)

    # ボタンなどが回収されないよう、図からこのウィンドウを参照しておく
    self.fig._stack_window = self

  # 横一列のボタンを作って、{名前: Button} を返す
  def _make_button_row(self, names, y, width, on_click, height=0.02):
    buttons = {}
    for i, name in enumerate(names):
      ax = self.fig.add_axes([0.07 + i * (width + 0.005), y, width, height])
      btn = Button(ax, name)
      btn.label.set_fontsize(8)
      btn.on_clicked(lambda _event, n=name: on_click(n))
      buttons[name] = btn
    return buttons

  # ボタンの見た目と、押せるかどうかを状態("selected"/"normal"/"disabled")に合わせる
  def _style_button(self, btn, state):
    color, hover = BTN_COLORS[state]
    btn.color = color
    btn.hovercolor = hover
    btn.ax.set_facecolor(color)
    btn.label.set_color("#999999" if state == "disabled" else "black")
    btn.set_active(state != "disabled")

  # その拠点・成分のデータが、表示中の1時間にあるか
  def _has(self, station, comp):
    return (station.lower(), comp.upper()) in self.available

  # 拠点・成分ボタンの状態を、選択中のチャンネルとデータの有無に合わせる
  def _refresh_buttons(self):
    # 拠点ボタン: その拠点のデータが1つでもあれば選べる
    for st, btn in self.station_buttons.items():
      if st == self.station:
        state = "selected"
      elif any(self._has(st, c) for c in self.comps):
        state = "normal"
      else:
        state = "disabled"
      self._style_button(btn, state)
    # 成分ボタン: 選択中の拠点に、その成分のデータがあれば選べる
    for c, btn in self.comp_buttons.items():
      if c == self.comp:
        state = "selected"
      elif self._has(self.station, c):
        state = "normal"
      else:
        state = "disabled"
      self._style_button(btn, state)
    self.fig.canvas.draw_idle()

  # 選択中のチャンネルがこの時間に無いとき、代わりに表示する (拠点, 成分) を選ぶ。無ければ None
  def _pick_fallback(self, stream):
    avail = {(tr.stats.station.lower(), tr.stats.channel[-1].upper()) for tr in stream}
    # 今の拠点を優先し、定義ファイル順に探す
    for st in [self.station] + [s for s in self.stations if s != self.station]:
      for c in self.comps:
        if (st.lower(), c.upper()) in avail:
          return st, c
    return None

  # 現在の状態(時刻・拠点・成分)で描画し直す。成功したら True
  def update(self):
    # 1時間分のStreamを取得(同じ時間ならキャッシュを使う)
    if self._stream_dt == self.dt:
      stream = self._stream
    else:
      stream = self.load_stream(self.dt)
      if stream is None:
        return False

    trace = find_trace(stream, self.station, self.comp)
    if trace is None:
      # 選択中のチャンネルがこの時間に無いので、データのある別のチャンネルに切り替える
      fallback = self._pick_fallback(stream)
      if fallback is None:
        self.show_error(f"この時間には表示できるデータがありません: {self.dt.strftime('%Y/%m/%d %H:00')}")
        return False
      old_ch = f"{self.station}.{self.comp}"
      self.station, self.comp = fallback
      trace = find_trace(stream, self.station, self.comp)
      self.log(f"{old_ch} のデータが無いため、{self.station}.{self.comp} に切り替えました")

    # 成功したので、キャッシュと「データがあるチャンネル」の情報を更新
    self._stream_dt, self._stream = self.dt, stream
    self.available = {(tr.stats.station.lower(), tr.stats.channel[-1].upper()) for tr in stream}

    self.stack.render(trace, self.dt, self.station, self.comp)
    # ウィンドウのタイトル(既定の「Figure 1」の代わり。時間・拠点・成分の切り替えに追従)
    if self.fig.canvas.manager is not None:
      self.fig.canvas.manager.set_window_title(
          f"1時間スタック波形 - {self.station}.{self.comp} ({self.dt.strftime('%Y/%m/%d %H:00')})")
    self._refresh_buttons()
    self.log(f"スタック波形プロット完了: {self.station}.{self.comp} "
             f"({self.dt.strftime('%Y/%m/%d %H:00')})")
    return True

  # 状態(時刻・拠点・成分)を変更して描画し直す。失敗したら元に戻す
  def _change(self, dt=None, station=None, comp=None):
    old = (self.dt, self.station, self.comp)
    if dt is not None:
      self.dt = dt
    if station is not None:
      self.station = station
    if comp is not None:
      self.comp = comp
    if not self.update():
      self.dt, self.station, self.comp = old   # 表示は元のままなので、状態も戻す

  # 指定した時間数だけ前後に移動する
  def move_hour(self, hours):
    self._change(dt=self.dt + timedelta(hours=hours))

  # 成分を切り替える(拠点は今のまま)
  def set_comp(self, comp):
    if comp != self.comp and self._has(self.station, comp):
      self._change(comp=comp)

  # 拠点を切り替える。成分は今のものを優先し、その拠点に無ければ最初に見つかる成分にする
  def set_station(self, station):
    if station == self.station:
      return
    comps_here = [c for c in self.comps if self._has(station, c)]
    if not comps_here:
      return
    self._change(station=station, comp=self.comp if self.comp in comps_here else comps_here[0])

  # 指定した段(分)の範囲をハイライトする。minutes が None ならハイライトを消す
  #   minute  ... 開始の段(0〜59)
  #   minutes ... 長さ(分)。この1時間の外にはみ出す分は表示しない
  def _set_highlight(self, minute, minutes=None):
    for patch in self._highlights:
      patch.remove()
    self._highlights = []
    if minutes is not None:
      last = min(minute + minutes, STACK_ROWS)   # この1時間の外は表示しない
      self._highlights.append(
          self.stack.ax.axhspan(minute - 0.5, last - 0.5, color="#ffd54f", alpha=0.35, zorder=0, lw=0))
    # メニュー表示中でもすぐ反映されるよう、その場で描画する
    self.fig.canvas.draw()
    self.fig.canvas.get_tk_widget().update_idletasks()

  # 右クリック: クリックした段(分)を開始時刻として、比較表示する長さを選ぶメニューを出す
  def _on_right_click(self, event):
    # Mac では Tk と Matplotlib の両方が左右のボタン番号を入れ替えるため、右ボタンが
    # 中ボタン(2)として届くことがある。中ボタンは他で使っていないので、両方で開く
    if event.button not in (2, 3) or event.inaxes is not self.stack.ax:
      return
    minute = self.stack.row_at(event.ydata)
    if minute is None:
      return
    # Tk のウィンドウでないとメニューを出せない
    gui = event.guiEvent
    if not hasattr(self.fig.canvas, "get_tk_widget") or gui is None:
      return

    start_dt = self.dt + timedelta(minutes=minute)
    station, comp = self.station, self.comp
    tk_widget = self.fig.canvas.get_tk_widget()
    window = tk_widget.winfo_toplevel()

    # メニューを選んだとき: ハイライトを消してから比較表示を呼ぶ
    def choose(minutes):
      self._set_highlight(minute)
      self.on_compare(start_dt, minutes, station, comp, window)

    menu = tk.Menu(tk_widget, tearoff=0)
    menu.add_command(label=f"{start_dt.strftime('%H:%M')} から全拠点を比較表示",
                     state=tk.DISABLED)
    menu.add_separator()
    first_item = menu.index(tk.END) + 1   # 期間の項目の先頭の位置
    for minutes in COMPARE_MINUTES:
      menu.add_command(label=f"{minutes}分間", command=lambda m=minutes: choose(m))

    # カーソルが乗っている項目に合わせて、その範囲をハイライトする
    def on_select(_event):
      active = menu.index("active")
      if active is None or active < first_item:
        self._set_highlight(minute)
      else:
        self._set_highlight(minute, COMPARE_MINUTES[active - first_item])
    menu.bind("<<MenuSelect>>", on_select)
    # メニューが閉じたらハイライトを消す(Windows / Linux)
    menu.bind("<Unmap>", lambda _event: self._set_highlight(minute))

    try:
      menu.tk_popup(gui.x_root, gui.y_root)
    finally:
      menu.grab_release()
    # Mac ではメニューが閉じるまで tk_popup から戻らないので、ここでハイライトを消す
    if sys.platform == "darwin":
      self._set_highlight(minute)

  # 初回描画をして画面に表示する(最初の描画に失敗したら閉じる)
  def show(self):
    if not self.update():
      plt.close(self.fig)
      return
    plt.show()

# 横軸(時間)のズーム・ドラッグ移動・リセットを設定する
#   fig  ... 図
#   ax   ... 波形の軸
#   xlim ... 全体の表示範囲(この外へは動かさない)
#   操作 ... ホイール: 拡大縮小 / 左ドラッグ: 横移動 / ダブルクリック: 全体表示に戻す
def attach_zoom_pan(fig, ax, xlim=(0, 60)):
  full_min, full_max = xlim
  full_width = full_max - full_min
  drag = {"dragging": False, "press_x": None, "start_xmin": None, "start_xmax": None}

  # X軸の範囲を設定して再描画
  def set_xlim(xmin, xmax):
    ax.set_xlim(xmin, xmax)
    fig.canvas.draw()
    fig.canvas.flush_events()

  # 表示幅を保ったまま、全体範囲の内側に収める
  def clamp(xmin, width):
    xmin = max(full_min, min(xmin, full_max - width))
    return xmin, xmin + width

  # ホイール: マウス位置を中心に拡大・縮小
  def on_scroll(event):
    # 波形の軸の上でのイベントだけを扱う(ボタンなど他の軸は無視)
    if event.inaxes is not ax or event.xdata is None:
      return
    if event.button == "down":
      scale = 1.2        # 縮小(表示範囲を広げる)
    elif event.button == "up":
      scale = 1 / 1.2    # 拡大(表示範囲を狭める)
    else:
      return

    cur_min, cur_max = ax.get_xlim()
    cur_width = cur_max - cur_min
    new_width = cur_width * scale

    if new_width >= full_width * 0.99:
      # ほぼ全体が見える幅になったら、全体表示に戻す
      new_min, new_max = full_min, full_max
    else:
      # 拡大しすぎ防止(全体の1%より狭くしない)
      if new_width < full_width * 0.01 and scale < 1:
        return
      # マウス位置が画面上で動かないように伸縮する
      ratio = (event.xdata - cur_min) / cur_width   # 左端からマウス位置までの割合
      new_min, new_max = clamp(event.xdata - new_width * ratio, new_width)
    set_xlim(new_min, new_max)

  # ボタン押下: ダブルクリックで全体表示、左ボタンでドラッグ開始
  def on_press(event):
    if event.inaxes is not ax:
      return
    if event.dblclick:
      set_xlim(full_min, full_max)
      return
    if event.button == 1:
      cur_min, cur_max = ax.get_xlim()
      drag.update(dragging=True, press_x=event.x,
                  start_xmin=cur_min, start_xmax=cur_max)

  # ドラッグ中: 開始時点の表示範囲を基準に、ピクセルの移動量を時間に換算して横移動
  def on_motion(event):
    if not drag["dragging"]:
      return
    width = drag["start_xmax"] - drag["start_xmin"]
    px_width = ax.bbox.width              # 軸の幅(ピクセル)
    dx = (drag["press_x"] - event.x) * width / px_width
    new_min, new_max = clamp(drag["start_xmin"] + dx, width)
    set_xlim(new_min, new_max)

  def on_release(event):
    drag["dragging"] = False
    drag["press_x"] = None

  fig.canvas.mpl_connect("scroll_event", on_scroll)
  fig.canvas.mpl_connect("button_press_event", on_press)
  fig.canvas.mpl_connect("motion_notify_event", on_motion)
  fig.canvas.mpl_connect("button_release_event", on_release)

# 単体テスト(実データ不要、画面も開かない)
if __name__ == "__main__":
  import matplotlib
  matplotlib.use("Agg")   # 画像に保存するだけ
  from datetime import datetime
  from obspy import Trace
  from plot_common import setup_japanese_font

  setup_japanese_font()
  start = datetime(2026, 8, 23, 22, 0, 0)
  tr = Trace(data=np.random.default_rng(0).normal(0, 1, 3600 * 100))
  tr.stats.sampling_rate = 100.0
  tr.stats.starttime = UTCDateTime(start)
  tr.stats.station = "TEST"
  tr.stats.channel = "U"
  tr.data[100 * 1800: 100 * 1810] *= 20    # 30分ごろに大きな揺れ

  assert len(slice_into_minutes(tr, start)) == 60
  stack = StackFigure(for_screen=False)
  stack.render(tr, start, "TEST", "U")
  stack.fig.savefig("stack_test.png", dpi=100)
  print("OK: stack_test.png")
