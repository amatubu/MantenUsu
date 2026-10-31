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

# 指定期間の波形の図を作る(波形ごとに1段、横軸の時間は共通)
#   ordered_st ... sort_stream で並べ替えたStream
#   title_str  ... 波形上部に表示するタイトル(日本語表示可)
def create_waveform_figure(ordered_st, title_str):
  # 波形データの数
  num_traces = len(ordered_st)
  # グラフの横幅(インチ単位)
  fig_width = 11.5
  # グラフの縦の高さ(インチ単位)
  # 最低6インチ、波形データの数に応じて1インチずつ拡張
  fig_height = max(6, num_traces * 1.0)

  # 波形をプロットする領域を準備する
  #   fig  ... 波形を描くための全体領域
  #   axes ... 個々の波形を描くグラフ部分
  fig, axes = plt.subplots(
      num_traces,                         # 縦に波形の数だけグリッドに分割
      1,                                  # 横は分割しない
      sharex = True,                      # 横(時間)軸を同期させる
      figsize = (fig_width, fig_height),  # 描画領域のサイズ
      gridspec_kw = {"hspace": 0},        # グリッド間の縦の隙間を0にする(ぴったりくっつける)
  )
  # 波形がひとつだけの場合でも軸をリストにしておく
  if num_traces == 1:
    axes = [axes]

  # 描画領域の周りの余白を調整
  fig.subplots_adjust(
    left   = 0.09,  # 左: 9%(Y軸のラベルの幅を確保)
    right  = 0.97,  # 右: 3%(狭め)
    bottom = 0.08,  # 下: 8%(X軸の時間軸ラベルの高さを確保)
    top    = 0.92   # 上: 8%(タイトルの高さを確保)
  )

  # 波形データ(ordered_st)と描画領域(axes)をzipでペアにしてループさせる
  for tr, ax in zip(ordered_st, axes):
    # 時間軸データ(横軸)の取得
    times = tr.times()
    # 波形の実データ(nm/s -> cm/s に変換)
    data = tr.data * NM_TO_CM
    # チャンネル名
    ch = tr.stats.channel
    # チャンネル名を元に色を決定(U、N、E以外はデフォルト色)
    col = get_component_color(ch)

    # 波形を描画する
    ax.plot(
      times,           # 横軸(時間軸データ)
      data,            # 波形の実データ
      color = col,     # 色
      linewidth = 0.4  # 線の太さ(単位はpt)
    )
    # 拠点名
    station_name = tr.stats.station

    # Y(振幅)軸のラベルを定義
    ax.set_ylabel(
        f"{station_name}\n({ch})\n[cm/s]",  # ラベルの内容
        fontsize = 8,               # 文字サイズ
        rotation = 90,              # 90度回転(反時計周り)
        ha = "center",              # 水平方向中揃え(horizontal alignment)
        va = "center",              # 垂直方向中揃え(vertical alignment)
    )
    # Y軸のラベル位置を調整(グラフ外(左)の中央へ)
    ax.yaxis.set_label_coords(-0.06, 0.5)

    # Y軸の目盛(数値)サイズ
    ax.tick_params(
      axis = "y",    # Y軸を指定
      labelsize = 7  # 目盛の文字サイズ
    )

    # グリッド線の表示
    ax.grid(
      True,              # グリッドを表示
      linestyle = "--",  # 破線
      alpha = 0.3        # 不透明度 30%
    )

    # 波形の下のラベル(X軸)を省略(重ならないように)
    ax.tick_params(labelbottom=False)

  # 一番下の波形にだけ下のラベル(X軸)を表示
  axes[-1].tick_params(labelbottom=True)
  # ラベルの表示内容と文字サイズ
  axes[-1].set_xlabel("Time [s]", fontsize=11)

  # 波形の上に表示するタイトル
  start_time, end_time = period_strings(ordered_st)
  title_display = f"{start_time} - {end_time}"
  if title_str:
    title_display += f" : {title_str}"
  fig.suptitle(
      f"有珠・満点地震計波形 ({title_display}) - 波形数: {num_traces}",
      fontsize=13,
  )

  return fig

# 指定期間の波形表示ウィンドウを開く(震度推定・P波・S波検出のボタンつき)
#   parent     ... 親ウィンドウ
#   st         ... SACファイルから読み込んだStream
#   title_str  ... 波形上部に表示するタイトル(日本語表示可)
#   return_to  ... このウィンドウを閉じたときに戻るウィンドウ(Tk)。None ならOS任せ
def open_waveform_window(parent, st, title_str, return_to=None):
  ordered_st = sort_stream(st)
  fig = create_waveform_figure(ordered_st, title_str)

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
