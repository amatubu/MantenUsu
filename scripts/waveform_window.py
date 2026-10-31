# waveform_window.py
# 指定期間の波形表示(全拠点・全成分を縦に並べる)と、震度推定・P波・S波検出への入口
import tkinter as tk
from tkinter import messagebox, ttk

from obspy import Stream
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg

from jma_intensity import analyze_jma_scale
from plot_common import NM_TO_CM, get_component_color
from ps_picker import plot_ps_arrival_time

# 成分の並び順: U(上下)、N(南北)、E(東西)
COMP_ORDER = ["U", "N", "E"]

# 拠点名、成分(U, N, E)の順に並べ替えたStreamを返す
def sort_stream(st):
  # 並び替え後のリストを得る
  ordered_st = Stream(
      sorted(
          st,  # 並び替えたいStreamオブジェクト
          key=lambda tr: (  # st からひとつひとつ取り出して tr に格納し、繰り返す
              tr.stats.station,  # 拠点名
              COMP_ORDER.index(tr.stats.channel[-1])  # チャンネルの並び順を末尾の文字から設定
              if tr.stats.channel[-1] in COMP_ORDER
              else 99,  # U、N、E 以外だったら 99番にして最後に並べる
          ),
      )
  )
  return ordered_st

# Stream全体の期間の文字列 (開始, 終了) を返す(先頭のトレースの開始時刻、最後のトレースの終了時刻)
def period_strings(ordered_st):
  return (ordered_st[0].stats.starttime.strftime("%Y/%m/%d %H:%M"),
          ordered_st[-1].stats.endtime.strftime("%Y/%m/%d %H:%M"))

# 拠点を2x2に並べる表示ができる拠点数
GRID_STATIONS = 4

# 拠点名の一覧(並べ替え後の順、重複なし)
def station_names(ordered_st):
  return list(dict.fromkeys(tr.stats.station for tr in ordered_st))

# 指定期間の波形を描く図(空)を作る。中身は draw_vertical / draw_station_grid で描く
#   ordered_st ... sort_stream で並べ替えたStream
def create_waveform_figure(ordered_st):
  # グラフの横幅(インチ単位)
  fig_width = 11.5
  # グラフの縦の高さ(インチ単位)
  # 最低6インチ、波形データの数に応じて1インチずつ拡張
  fig_height = max(6, len(ordered_st) * 1.0)
  return plt.figure(figsize=(fig_width, fig_height))

# 1つの軸に1本の波形を描く(縦軸のラベル・目盛・グリッドも設定)
#   ax    ... 描画先の軸
#   tr    ... 波形データ(nm/s)
#   label ... 縦軸のラベル
def plot_trace(ax, tr, label):
  # 波形の実データを nm/s -> cm/s に変換し、チャンネル名を元にした色(U、N、E以外はデフォルト色)で描く
  ax.plot(
    tr.times(),                              # 横軸(時間軸データ)
    tr.data * NM_TO_CM,                      # 波形の実データ
    color = get_component_color(tr.stats.channel),  # 色
    linewidth = 0.4                          # 線の太さ(単位はpt)
  )

  # Y(振幅)軸のラベル(90度回転)。位置は Matplotlib に任せ、目盛りの数字の左側に置かせる
  ax.set_ylabel(label, fontsize=8)
  # Y軸の目盛(数値)サイズ
  ax.tick_params(axis="y", labelsize=7)
  # グリッド線の表示(破線、不透明度 30%)
  ax.grid(True, linestyle="--", alpha=0.3)
  # 波形の下のラベル(X軸)を省略(重ならないように)。一番下の波形だけ後で表示する
  ax.tick_params(labelbottom=False)

# 横軸(時間)の範囲を、0秒から最後のデータまでぴったりにする(左右の余白をなくす)
#   ax ... 横軸を共有している軸のどれか1つ
def fit_time_axis(ax, ordered_st):
  # 終わりは記録の長さ(データ数 × サンプリング間隔)。最後のデータの時刻より1サンプル分長く、
  # 3分なら 179.996秒ではなく180秒になって、右端に目盛りの数字が出る
  ax.set_xlim(0, max(tr.stats.npts * tr.stats.delta for tr in ordered_st))

# 一番下の波形にだけ下のラベル(X軸)を表示する
def show_time_axis(ax, with_label=True):
  ax.tick_params(labelbottom=True)
  if with_label:
    ax.set_xlabel("Time [s]", fontsize=11)

# 図全体のタイトル(期間・タイトル・波形数)と、縦軸の単位(全波形で共通なので左上に1回だけ表示)
def set_figure_title(fig, ordered_st, title_str):
  fig.text(0.01, 0.94, "縦軸: 速度 [cm/s]", fontsize=9, color="#333333")
  start_time, end_time = period_strings(ordered_st)
  title_display = f"{start_time} - {end_time}"
  if title_str:
    title_display += f" : {title_str}"
  fig.suptitle(
      f"有珠・満点地震計波形 ({title_display}) - 波形数: {len(ordered_st)}",
      fontsize=13,
  )

# すべての波形を縦に1列に並べて描く(横軸の時間は共通)
#   fig        ... create_waveform_figure で作った図(中身は消してから描く)
#   title_str  ... 波形上部に表示するタイトル(日本語表示可)
def draw_vertical(fig, ordered_st, title_str):
  fig.clear()
  # 縦に波形の数だけ分割し、グリッド間の縦の隙間を0にする(ぴったりくっつける)
  axes = fig.subplots(len(ordered_st), 1, sharex=True, gridspec_kw={"hspace": 0}, squeeze=False)[:, 0]

  # 描画領域の周りの余白を調整
  fig.subplots_adjust(
    left   = 0.09,  # 左: 9%(Y軸のラベルの幅を確保)
    right  = 0.97,  # 右: 3%(狭め)
    bottom = 0.08,  # 下: 8%(X軸の時間軸ラベルの高さを確保)
    top    = 0.92   # 上: 8%(タイトルの高さを確保)
  )

  for tr, ax in zip(ordered_st, axes):
    plot_trace(ax, tr, f"{tr.stats.station}\n({tr.stats.channel})")
  # 縦軸のラベルを、目盛りの数字の左側にそろえて置く(数字の桁が多くても重ならないように)
  fig.align_ylabels(axes)

  show_time_axis(axes[-1])
  fit_time_axis(axes[0], ordered_st)
  set_figure_title(fig, ordered_st, title_str)

# 拠点ごとに2x2に並べて描く(各拠点の中は成分を縦に並べる。横軸の時間は全拠点で共通)
#   fig        ... create_waveform_figure で作った図(中身は消してから描く)
#   title_str  ... 波形上部に表示するタイトル(日本語表示可)
def draw_station_grid(fig, ordered_st, title_str):
  fig.clear()
  outer = fig.add_gridspec(2, 2, left=0.07, right=0.98, bottom=0.07, top=0.90,
                           hspace=0.22, wspace=0.18)
  first_ax = None
  for idx, station in enumerate(station_names(ordered_st)[:GRID_STATIONS]):
    traces = ordered_st.select(station=station)
    # この拠点の枠を成分の数だけ縦に分割する(隙間なし)
    inner = outer[idx // 2, idx % 2].subgridspec(len(traces), 1, hspace=0)
    axes = []
    for k, tr in enumerate(traces):
      ax = fig.add_subplot(inner[k], sharex=first_ax)
      first_ax = first_ax or ax
      plot_trace(ax, tr, f"({tr.stats.channel})")
      axes.append(ax)
    # 拠点名を枠の上に表示し、縦軸のラベルの位置を揃える
    axes[0].set_title(station, fontsize=11)
    fig.align_ylabels(axes)
    # 各拠点の一番下に時間の目盛を表示(ラベルは下の段だけ)
    show_time_axis(axes[-1], with_label=idx // 2 == 1)

  fit_time_axis(first_ax, ordered_st)
  set_figure_title(fig, ordered_st, title_str)

# 指定期間の波形表示ウィンドウを開く(震度推定・P波・S波検出のボタンつき)
#   parent     ... 親ウィンドウ
#   st         ... SACファイルから読み込んだStream
#   title_str  ... 波形上部に表示するタイトル(日本語表示可)
#   return_to  ... このウィンドウを閉じたときに戻るウィンドウ(Tk)。None ならOS任せ
def open_waveform_window(parent, st, title_str, return_to=None):
  ordered_st = sort_stream(st)
  fig = create_waveform_figure(ordered_st)
  # 4拠点のときは拠点ごとに2x2、それ以外は縦に1列に並べる
  can_grid = len(station_names(ordered_st)) == GRID_STATIONS
  layout = {"grid": can_grid}
  (draw_station_grid if can_grid else draw_vertical)(fig, ordered_st, title_str)

  # --- 描画用ウィンドウの作成 ---
  wave_win = tk.Toplevel(parent)
  # ウィンドウのタイトル(拠点名と期間)
  start_time, end_time = period_strings(ordered_st)
  title_stations = ", ".join(dict.fromkeys(tr.stats.station for tr in ordered_st))
  wave_win.title(f"波形表示 - {title_stations} ({start_time} - {end_time})")

  # Pyplotのグローバル管理からこのfigを外す（他のplt.show()に巻き込まれなくする）
  plt.close(fig)

  # 【重要】ウィンドウが閉じられたときにMatplotlibのリソースを解放して確実に閉じる
  def on_close():
    plt.close(fig)      # Matplotlibのグラフをメモリから破棄する
    wave_win.destroy()  # ウィンドウを破棄する

    # 戻り先があれば前面に出す。戻り先(Matplotlibのウィンドウ)はメインとは別の Tk なので、
    # 閉じた直後に Mac がメインウィンドウを前面にする。その後にもう一度前面に出し直す
    if return_to is not None:
      def bring_back():
        try:
          if return_to.winfo_exists():
            return_to.lift()
            return_to.focus_force()
        except tk.TclError:
          pass  # 戻り先のウィンドウが既に閉じられている
      bring_back()
      parent.after(100, bring_back)

  wave_win.protocol("WM_DELETE_WINDOW", on_close)

  # 1. ウィンドウ内のキャンバスを取得
  canvas = FigureCanvasTkAgg(fig, master=wave_win)
  canvas.draw()

  # 2. 下部にコントロール用フレームを配置する
  control_frame = ttk.Frame(wave_win, padding=10)
  control_frame.pack(side=tk.BOTTOM, fill=tk.X)

  # 【追加】もともとあった虫めがねや保存などのツールバーをここに追加！
  toolbar_frame = ttk.Frame(wave_win)
  toolbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
  toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
  toolbar.update()

  # 3. 残り部分にグラフを埋め込む
  canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

  # 読み込まれたデータから重複のない拠点名リストを作成
  stations = sorted(list(set(tr.stats.station for tr in ordered_st)))

  # 拠点名のドロップダウン（コンボボックス）
  ttk.Label(control_frame, text="拠点名:").pack(side=tk.LEFT, padx=(0, 5))
  station_combobox = ttk.Combobox(control_frame, values=stations, state="readonly", width=15)
  if stations:
    station_combobox.set(stations[0])  # 初期選択
  station_combobox.pack(side=tk.LEFT, padx=(0, 10))

  # 選択中の拠点の波形データで analyze を呼ぶ(震度推定・P波・S波検出で共通)
  def run_for_station(analyze):
    selected_station = station_combobox.get()
    if not selected_station:
      messagebox.showwarning("警告", "拠点名が選択されていません。")
      return

    station_st = ordered_st.select(station=selected_station)

    # 該当拠点のデータが空でないかチェック
    if len(station_st) == 0:
      messagebox.showerror("エラー", f"拠点 [{selected_station}] の波形データが見つかりません。")
      return

    analyze(station_st)

  # 「震度推定」ボタン・「P波・S波検出」ボタン
  ttk.Button(control_frame, text="震度推定",
             command=lambda: run_for_station(analyze_jma_scale)).pack(side=tk.LEFT, padx=(0, 10))
  ttk.Button(control_frame, text="P波・S波検出",
             command=lambda: run_for_station(plot_ps_arrival_time)).pack(side=tk.LEFT, padx=(0, 10))

  # 「2x2 / 縦に並べる」切り替えボタン(4拠点のときだけ)。押すと同じ図に描き直す
  if can_grid:
    def layout_label():
      return "縦に並べる" if layout["grid"] else "拠点ごとに2x2"
    def toggle_layout():
      layout["grid"] = not layout["grid"]
      (draw_station_grid if layout["grid"] else draw_vertical)(fig, ordered_st, title_str)
      canvas.draw()
      toolbar.update()   # ツールバーの「戻る・ホーム」の履歴を新しい表示に合わせる
      btn_layout.config(text=layout_label())
    btn_layout = ttk.Button(control_frame, text=layout_label(), command=toggle_layout)
    btn_layout.pack(side=tk.RIGHT)
