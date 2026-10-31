# stack_plot.py
# 1時間スタック波形(1分ごとに60段に並べた図)の計算と描画
import numpy as np
import matplotlib.pyplot as plt
from obspy import UTCDateTime
from datetime import timedelta
from matplotlib.widgets import Button

STACK_ROWS = 60  # 1時間 = 60分 = 60段

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

# 60段のスタック図を作る(画面表示・画像保存で共通)
#   for_screen   ... True: 画面表示用(上部に前後ボタン用の余白) / False: 画像保存用
#   戻り値        ... (図, 60段の軸, 最大振幅を表示するテキスト)
def create_stack_figure(for_screen=True):
  fig, axes = plt.subplots(
      STACK_ROWS, 1,
      sharex=True,
      figsize=(8.5, 10.0),   # 60本並べるため、ある程度の縦の高さを確保
      gridspec_kw={"hspace": 0}
  )

  if for_screen:
    # 画面用: 上部に「前後1時間」ボタンを置くので余白を広く取り、その左に最大振幅を表示
    fig.subplots_adjust(left=0.07, right=0.97, bottom=0.05, top=0.90)
    max_amp_text = fig.text(0.75, 0.925, "", fontsize=9, va="center", ha="right", color="#333333")
  else:
    # 画像保存用: ボタンが無いので上の余白は小さく、最大振幅は右上に表示
    fig.subplots_adjust(left=0.07, right=0.97, bottom=0.05, top=0.94)
    max_amp_text = fig.text(0.95, 0.95, "", fontsize=9, va="center", ha="right", color="#333333")

  # 全サブプロットを包み込む外枠(実線)。図に1回だけ作る
  ax_box = fig.add_subplot(111, frameon=True)
  ax_box.set_facecolor("none")
  ax_box.tick_params(labelcolor="none", top=False, bottom=False, left=False, right=False)
  ax_box.tick_params(which="both", length=0)
  for spine in ax_box.spines.values():
    spine.set_visible(True)
    spine.set_linewidth(0.8)
    spine.set_color("#333333")
  # 外枠を一番奥に下げて、マウスイベントを波形側に通す
  ax_box.set_zorder(-10)

  return fig, axes, max_amp_text

# 1時間分の波形を、60段の軸に描画する
#   fig, axes     ... create_stack_figure で作った図と軸
#   max_amp_text  ... 最大振幅を表示するテキスト(create_stack_figure の戻り値)
#   trace         ... 1時間分のトレース
#   start_dt      ... 1時間の開始日時(datetime)
#   station, comp ... 拠点名・成分(タイトル表示用)
def render_stack(fig, axes, max_amp_text, trace, start_dt, station, comp):
  # 1. 1時間データを1分ごとにスライス
  minute_traces = slice_into_minutes(trace, start_dt)

  # 2. 共通の最大振幅とスケール計算
  global_max = calc_global_max(minute_traces)
  ylim_range = global_max / 5.0
  max_amp_text.set_text(f"最大振幅: {global_max:,.2f}")

  # 3. 60本の波形プロット描画(0分〜59分)
  for i, (tr, ax) in enumerate(zip(minute_traces, axes)):
    ax.clear()
    # clip_on=False: 枠外(上下)へのはみ出しも描画する
    ax.plot(tr.times(), tr.data, color="#1f77b4", linewidth=0.3, clip_on=False)

    # 全段でY軸の範囲を共通の最大値に固定(大きな揺れが上下にはみ出る)
    ax.set_ylim(-ylim_range, ylim_range)
    # X軸の範囲を0〜60秒に固定し、左右の余白を詰める
    ax.set_xlim(0, 60)
    ax.margins(x=0)

    # Y軸のラベルとして「何分目か(例: 00)」を表示
    ax.set_ylabel(f"{i:02d}", fontsize=6, rotation=0, labelpad=12, va="center", ha="right")

    # 10秒単位の縦線をグレーの実線で表示
    ax.grid(False)
    ax.grid(True, axis="x", which="major", linestyle="-", color="#cccccc", alpha=0.5)

    # 目盛りの数字・線を消してスッキリさせる
    ax.tick_params(axis="y", left=False, labelleft=False)
    ax.tick_params(labelbottom=False)
    ax.tick_params(axis="x", which="both", bottom=False, top=False)

    # グラフ間の枠線を消してスダレ状にする
    for spine in ax.spines.values():
      spine.set_visible(False)
    # 背景を透明にして、はみ出た波形が下の段に重なって見えるようにする
    ax.set_facecolor("none")

  # 一番下の軸だけにX軸(秒)の目盛りとラベルを表示
  axes[-1].tick_params(labelbottom=True)
  axes[-1].tick_params(axis="x", which="both", bottom=True)
  axes[-1].set_xlabel("Time [s (0-60s per trace)]", fontsize=10)

  # 全体タイトル(この図のタイトルとして設定する)
  date_title = start_dt.strftime("%Y/%m/%d %H:00")
  fig.suptitle(f"有珠満点地震計 1時間スタック波形 ({date_title}) - チャンネル: {station}.{comp}", fontsize=12)

# 1時間スタック波形の表示ウィンドウ(前後の時間への移動つき)
#   start_dt      ... 最初に表示する1時間の開始日時(datetime)
#   station, comp ... 最初に表示する拠点名・成分(U/N/E)
#   load_trace    ... (日時, 拠点名, 成分) を受け取り、その1時間分のトレースを返す関数。
#                     失敗したら None を返す(エラー表示は関数側で行う)
#   log           ... ログを受け取る関数(メッセージ文字列を1つ受け取る)
class StackWindow:
  def __init__(self, start_dt, station, comp, load_trace, log=print):
    # 表示中の状態(時刻・拠点・成分)
    self.dt = start_dt
    self.station = station
    self.comp = comp
    self.load_trace = load_trace
    self.log = log

    # 60段の図を作る(画面表示用)
    self.fig, self.axes, self.max_amp_text = create_stack_figure()

    # グラフ上部に「前の1時間」「次の1時間」ボタンを配置
    ax_prev = self.fig.add_axes([0.77, 0.91, 0.09, 0.03])  # [left, bottom, width, height]
    ax_next = self.fig.add_axes([0.88, 0.91, 0.09, 0.03])
    self.btn_prev = Button(ax_prev, "◀ -1h", color="#f0f0f0", hovercolor="#e0e0e0")
    self.btn_next = Button(ax_next, "+1h ▶", color="#f0f0f0", hovercolor="#e0e0e0")
    self.btn_prev.label.set_fontsize(9)
    self.btn_next.label.set_fontsize(9)
    self.btn_prev.on_clicked(lambda _event: self.move_hour(-1))
    self.btn_next.on_clicked(lambda _event: self.move_hour(+1))

    # 横軸のズーム・ドラッグ・リセットを設定
    attach_zoom_pan(self.fig, self.axes)

    # ボタンなどが回収されないよう、図からこのウィンドウを参照しておく
    self.fig._stack_window = self

  # 現在の状態(時刻・拠点・成分)で描画し直す。成功したら True
  def update(self):
    trace = self.load_trace(self.dt, self.station, self.comp)
    if trace is None:
      return False
    render_stack(self.fig, self.axes, self.max_amp_text, trace,
                 self.dt, self.station, self.comp)
    self.fig.canvas.draw_idle()
    self.log(f"スタック波形プロット完了: {self.station}.{self.comp} "
             f"({self.dt.strftime('%Y/%m/%d %H:00')})")
    return True

  # 指定した時間数だけ前後に移動する。失敗したら元の時刻に戻す
  def move_hour(self, hours):
    old_dt = self.dt
    self.dt = old_dt + timedelta(hours=hours)
    if not self.update():
      self.dt = old_dt   # 表示は元のままなので、状態も元に戻す

  # 初回描画をして画面に表示する(最初の描画に失敗したら閉じる)
  def show(self):
    if not self.update():
      plt.close(self.fig)
      return
    plt.show()

# 横軸(時間)のズーム・ドラッグ移動・リセットを、60段の軸にまとめて設定する
#   fig  ... 図
#   axes ... 60段の軸(X軸は共有されている)
#   xlim ... 全体の表示範囲(この外へは動かさない)
#   操作 ... ホイール: 拡大縮小 / 左ドラッグ: 横移動 / ダブルクリック: 全体表示に戻す
def attach_zoom_pan(fig, axes, xlim=(0, 60)):
  axes_list = list(axes)
  full_min, full_max = xlim
  full_width = full_max - full_min
  drag = {"dragging": False, "press_x": None, "start_xmin": None, "start_xmax": None}

  # 全段のX軸の範囲をまとめて設定して再描画
  def set_xlim_all(xmin, xmax):
    for ax in axes_list:
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
    if event.inaxes not in axes_list or event.xdata is None:
      return
    if event.button == "down":
      scale = 1.2        # 縮小(表示範囲を広げる)
    elif event.button == "up":
      scale = 1 / 1.2    # 拡大(表示範囲を狭める)
    else:
      return

    cur_min, cur_max = axes_list[0].get_xlim()
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
    set_xlim_all(new_min, new_max)

  # ボタン押下: ダブルクリックで全体表示、左ボタンでドラッグ開始
  def on_press(event):
    if event.inaxes not in axes_list:
      return
    if event.dblclick:
      set_xlim_all(full_min, full_max)
      return
    if event.button == 1:
      cur_min, cur_max = axes_list[0].get_xlim()
      drag.update(dragging=True, press_x=event.x,
                  start_xmin=cur_min, start_xmax=cur_max)

  # ドラッグ中: 開始時点の表示範囲を基準に、ピクセルの移動量を時間に換算して横移動
  def on_motion(event):
    if not drag["dragging"]:
      return
    width = drag["start_xmax"] - drag["start_xmin"]
    px_width = axes_list[0].bbox.width              # 軸の幅(ピクセル)
    dx = (drag["press_x"] - event.x) * width / px_width
    new_min, new_max = clamp(drag["start_xmin"] + dx, width)
    set_xlim_all(new_min, new_max)

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
  fig, axes, max_amp_text = create_stack_figure(for_screen=False)
  render_stack(fig, axes, max_amp_text, tr, start, "TEST", "U")
  fig.savefig("stack_test.png", dpi=100)
  print("OK: stack_test.png")
