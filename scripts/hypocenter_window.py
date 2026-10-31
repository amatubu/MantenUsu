# hypocenter_window.py
# 震源推定の画面: 4拠点の上下動で P波の到着時刻を検出し、震源(または到来方向)を推定して表示する
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, Button

from hypocenter import (load_stations, LocalProjection, pick_p_arrivals,
                        fit_plane_wave, locate_grid, HORIZONTAL_RANGE)
from plot_common import NM_TO_CM, get_component_color
from ps_picker import load_params, save_params
from log_common import log_message
import app_paths

# 設定ファイル(P波・S波検出とは別に保存する)
SETTINGS_FILE = app_paths.ETC_DIR / "hypocenter_params.json"

# スライダー定義: (キー, 表示名, 最小, 最大, 刻み)
SLIDER_SPECS = [
  ("p_sta",  "P波 STA [秒]",       0.1, 10.0, 0.1),
  ("p_lta",  "P波 LTA [秒]",       1.0, 60.0, 0.5),
  ("p_thr",  "P波 閾値",           1.0, 8.0,  0.1),
  ("window", "同時検出の幅 [秒]",  0.5, 10.0, 0.5),
  ("vp",     "P波の速度 [km/s]",   2.0, 8.0,  0.1),
]

# パラメータのデフォルト値
DEFAULT_PARAMS = dict(p_sta=2.5, p_lta=10.0, p_thr=2.6, window=3.0, vp=5.5)

# P波の検出に関わるパラメータ(これが変わったときだけ検出をやり直す)
PICK_KEYS = ("p_sta", "p_lta", "p_thr", "window")

# 波形の表示に使う帯域通過フィルター [Hz](小さな地震を見やすくする。検出には使わない)
DISPLAY_BAND = (2.0, 20.0)

# 16方位の名前(北から時計回り)
DIRECTIONS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
              "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]

# 方位(度)を16方位の名前にする
def direction_name(azimuth):
  return DIRECTIONS[int((azimuth + 11.25) % 360 // 22.5)]

# 時刻を 0.01秒まで表示する
def format_time(t):
  return t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-4]

# 震源推定の画面を開く
#   st ... 4拠点分の波形(上下動 U を使う)
def plot_hypocenter(st):
  # --- データ取得: 拠点ごとの上下動 ---
  traces = {tr.stats.station: tr for tr in st.select(channel="*U*")}
  names = sorted(traces)
  try:
    station_coords = load_stations(app_paths.ETC_DIR)
  except OSError as e:
    log_message(f"拠点の位置を読み込めませんでした: {e}")
    return
  missing = [n for n in names if n not in station_coords]
  if missing:
    log_message(f"チャンネル表に位置が無い拠点があります: {', '.join(missing)}")
    return

  proj = LocalProjection([station_coords[n] for n in names])
  xy = {n: np.array(proj.to_xy(*station_coords[n][:2])) for n in names}
  # 時刻の基準(全拠点で最も早い記録開始時刻)。到着時刻はここからの秒で扱う
  ref_time = min(tr.stats.starttime for tr in traces.values())
  start_str = ref_time.strftime("%Y-%m-%d %H:%M:%S")

  # 検出に関わる線の色(P波・S波検出と同じく、自動検出=赤。手で直した時刻=オレンジ)
  p_color, manual_color, cft_color = "red", "darkorange", "gray"

  # ==========================================
  # 図のレイアウト: 左に拠点ごとの上下動、右上に拠点の配置図、右下にパラメータと結果
  # ==========================================
  fig = plt.figure(figsize=(15, 9))
  if fig.canvas.manager is not None:
    fig.canvas.manager.set_window_title(f"震源推定 - {', '.join(names)} ({start_str})")
  # グラフ領域の左余白と幅、グラフの間隔と下余白(グラフの縦幅は拠点数から決める)
  left, width = 0.06, 0.50
  gap, bottom = 0.04, 0.08
  n = len(names)
  h = (0.84 - gap * (n - 1)) / n

  # 拠点ごとの時刻と、表示する波形(フィルターなし・あり) [cm/s]
  times, raw_data, filtered_data = {}, {}, {}
  for name in names:
    tr = traces[name]
    times[name] = tr.times() + (tr.stats.starttime - ref_time)
    raw_data[name] = tr.data * NM_TO_CM
    tr_f = tr.copy()
    tr_f.detrend("demean")
    tr_f.filter("bandpass", freqmin=DISPLAY_BAND[0], freqmax=DISPLAY_BAND[1], zerophase=True)
    filtered_data[name] = tr_f.data * NM_TO_CM

  wave_axes, cft_axes, pick_lines, wave_lines = {}, {}, {}, {}
  first_ax = None
  for i, name in enumerate(names):
    y0 = bottom + (n - 1 - i) * (h + gap)
    ax = fig.add_axes([left, y0, width, h], sharex=first_ax)
    first_ax = first_ax or ax
    wave_lines[name], = ax.plot(times[name], filtered_data[name], lw=0.5,
                                color=get_component_color("U"))
    ax.set_ylabel("速度 (cm/s)")
    # 拠点名はグラフの左上に表示する
    ax.text(0.01, 0.95, name, transform=ax.transAxes, va="top", fontsize=10, weight="bold",
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.8))
    ax.grid(True)
    if i < n - 1:
      plt.setp(ax.get_xticklabels(), visible=False)
    # CFT は右の軸に薄く重ねる(閾値の調整の目安)
    cax = ax.twinx()
    cax.set_ylabel("CFT", color=cft_color)
    cax.tick_params(axis="y", colors=cft_color)
    wave_axes[name], cft_axes[name] = ax, cax
    pick_lines[name] = ax.axvline(0, color=p_color, ls="--", lw=1.2, visible=False)
  first_ax.set_title("左クリック: P波到着時刻を手で指定 / Shift+左クリック: その拠点だけ自動検出に戻す / "
                     "右クリック: 拠点を計算から外す・戻す",
                     fontsize=10)
  wave_axes[names[-1]].set_xlabel("時刻 (s)")

  # CFT の線と閾値の線(内容は update() で設定)
  cft_lines, thr_lines = {}, {}
  for name in names:
    cft_lines[name], = cft_axes[name].plot(times[name], np.zeros_like(times[name]), lw=0.5,
                                           color=cft_color, alpha=0.6)
    thr_lines[name] = cft_axes[name].axhline(0, color=cft_color, ls=":", lw=0.8)

  # --- 拠点の配置図 ---
  map_ax = fig.add_axes([0.66, 0.64, 0.28, 0.29])

  # --- 結果のテキスト ---
  result_text = fig.text(0.62, 0.235, "", fontsize=9, va="top")

  # ==========================================
  # スライダー(前回の設定で初期化)
  # ==========================================
  initial = load_params(SETTINGS_FILE, SLIDER_SPECS, DEFAULT_PARAMS)
  sliders = {}
  top, step_y = 0.485, 0.04
  for i, (key, label, vmin, vmax, vstep) in enumerate(SLIDER_SPECS):
    sax = fig.add_axes([0.73, top - i * step_y, 0.20, 0.025])
    sliders[key] = Slider(sax, label, vmin, vmax, valinit=initial[key], valstep=vstep)

  # --- 状態: 自動検出の結果、手で指定した時刻、計算から外した拠点 ---
  state = {"auto": {}, "pick_params": None, "result": None, "filtered": True,
           "p_thr": None}
  manual = {}
  excluded = set()

  # 計算に使う到着時刻(手で指定したものを優先)
  def current_picks():
    return {nm: manual.get(nm, state["auto"].get(nm)) for nm in names}

  # 縦軸を、表示している時間の範囲の波形・CFT に合わせる
  # (範囲の外の大きな揺れに引きずられて、小さな地震が見えなくならないように)
  def fit_ylim(_ax=None):
    x0, x1 = first_ax.get_xlim()
    for nm in names:
      visible = (times[nm] >= x0) & (times[nm] <= x1)
      if not visible.any():
        continue
      data = wave_lines[nm].get_ydata()[visible]
      lo, hi = float(data.min()), float(data.max())
      margin = (hi - lo) * 0.05 or 1e-9
      wave_axes[nm].set_ylim(lo - margin, hi + margin)
      cft_top = max(float(cft_lines[nm].get_ydata()[visible].max()), state["p_thr"] or 0.0)
      cft_axes[nm].set_ylim(0, cft_top * 1.1 or 1.0)
  # ズーム・横移動・「ホーム」などで横軸が変わったとき
  for ax in wave_axes.values():
    ax.callbacks.connect("xlim_changed", fit_ylim)

  # 到着時刻のまわりに横軸を合わせる
  def fit_xlim(picks):
    times = [p for p in picks.values() if p is not None]
    if times:
      first_ax.set_xlim(min(times) - 5, max(times) + 10)

  # 配置図を描き直す
  def draw_map(res):
    map_ax.clear()
    map_ax.set_title("拠点の配置 (中心からの距離)", fontsize=10)
    map_ax.set_xlabel("東 [km]")
    map_ax.set_ylabel("北 [km]")
    map_ax.grid(True)
    map_ax.set_aspect("equal", adjustable="box")
    for nm in names:
      x, y = xy[nm]
      color = "lightgray" if nm in excluded or current_picks()[nm] is None else "black"
      map_ax.plot(x, y, "^", color=color, ms=9)
      map_ax.annotate(nm, (x, y), textcoords="offset points", xytext=(5, 5),
                      fontsize=8, color=color)
    radius = max(np.hypot(*xy[nm]) for nm in names)
    extent = radius * 1.6
    loc = res.get("grid")
    if loc is not None and not loc["at_edge"]:
      # 近い地震: 震央を星で示す
      # 拠点の間にあると拠点名と重なるため、説明の文字は凡例に出す
      map_ax.plot(loc["x"], loc["y"], "*", color=p_color, ms=16,
                  label=f"震央 (深さ {loc['depth']:.1f} km)")
      extent = max(extent, abs(loc["x"]) * 1.2, abs(loc["y"]) * 1.2)
    elif res.get("azimuth") is not None:
      # 遠い地震: 到来方向(震源のある方向)を矢印で示す
      az = np.radians(res["azimuth"])
      length = radius * 1.4
      map_ax.annotate("", xy=(length * np.sin(az), length * np.cos(az)), xytext=(0, 0),
                      arrowprops=dict(arrowstyle="->", color=p_color, lw=2))
      # 凡例用の見えない線(矢印そのものは凡例に出せないため)
      map_ax.plot([], [], "-", color=p_color, lw=2,
                  label=f"到来方向 {res['azimuth']:.0f}° ({direction_name(res['azimuth'])})")
    map_ax.set_xlim(-extent, extent)
    map_ax.set_ylim(-extent, extent)
    if map_ax.get_legend_handles_labels()[0]:
      map_ax.legend(loc="lower right", fontsize=8)

  # 結果の文字列を作る
  def result_message(picks, res):
    lines = [f"開始時刻: {start_str}", "P波到着(最初の拠点からの差):"]
    used = [nm for nm in names if picks[nm] is not None and nm not in excluded]
    t_first = min((picks[nm] for nm in used), default=None)
    for nm in names:
      if picks[nm] is None:
        lines.append(f"  {nm}: 検出なし")
        continue
      mark = " (手動)" if nm in manual else ""
      mark += " (除外)" if nm in excluded else ""
      diff = picks[nm] - t_first if t_first is not None else 0.0
      lines.append(f"  {nm}: {format_time(ref_time + picks[nm])[11:]}  +{diff:.3f} 秒{mark}")
    if res.get("azimuth") is not None:
      lines.append(f"到来方向: {res['azimuth']:.0f}° ({direction_name(res['azimuth'])})"
                   f"  見かけの速度: {res['app_v']:.1f} km/s  残差: {res['plane_rms']:.3f} 秒")
    else:
      lines.append("到来方向: 3拠点以上の到着時刻が必要です")
    loc = res.get("grid")
    if loc is None:
      lines.append("震源: 4拠点の到着時刻が必要です")
    elif loc["at_edge"]:
      lines.append(f"震源: 探索範囲(±{HORIZONTAL_RANGE:.0f} km)の外です。"
                   "遠い地震のため、P波だけでは位置が決まりません")
    else:
      lat, lon = proj.to_latlon(loc["x"], loc["y"])
      lines.append(f"震源: 北緯 {lat:.4f}° 東経 {lon:.4f}° 深さ {loc['depth']:.1f} km")
      lines.append(f"発生時刻: {format_time(ref_time + loc['origin'])}  残差: {loc['rms']:.3f} 秒")
    return "\n".join(lines)

  # ==========================================
  # 更新処理
  # ==========================================
  def update(_=None):
    params = {k: s.val for k, s in sliders.items()}
    pick_params = tuple(params[k] for k in PICK_KEYS)
    refit = False
    if pick_params != state["pick_params"]:
      # 検出のパラメータが変わったときだけ、P波の検出をやり直す
      if params["p_lta"] <= params["p_sta"]:
        result_text.set_text("LTA は STA より長くしてください。\nスライダーを調整してください。")
        fig.canvas.draw_idle()
        return
      auto, cfts = pick_p_arrivals(traces, ref_time, params["p_sta"], params["p_lta"],
                                   params["p_thr"], params["window"])
      state["auto"], state["pick_params"] = auto, pick_params
      state["p_thr"] = params["p_thr"]
      for nm in names:
        cft_lines[nm].set_ydata(cfts[nm])
        thr_lines[nm].set_ydata([params["p_thr"]] * 2)
      refit = True

    picks = current_picks()
    for nm in names:
      ln = pick_lines[nm]
      if picks[nm] is not None:
        ln.set_xdata([picks[nm]] * 2)
        ln.set_color(manual_color if nm in manual else p_color)
        ln.set_linestyle("-" if nm in manual else "--")
        ln.set_visible(True)
      else:
        ln.set_visible(False)
      wave_axes[nm].set_facecolor("#eeeeee" if nm in excluded else "white")
    if refit:
      fit_xlim(picks)
      fit_ylim()

    # --- 震源の計算 ---
    used = [nm for nm in names if picks[nm] is not None and nm not in excluded]
    pts = np.array([xy[nm] for nm in used])
    times = np.array([picks[nm] for nm in used])
    res = {}
    if len(used) >= 3:
      res["azimuth"], res["app_v"], res["plane_rms"] = fit_plane_wave(pts, times)
    if len(used) >= 4:
      res["grid"] = locate_grid(pts, times, params["vp"])
    state["result"] = res

    draw_map(res)
    result_text.set_text(result_message(picks, res))
    fig.canvas.draw_idle()

  for s in sliders.values():
    s.on_changed(update)

  # --- 波形のクリック: 左=到着時刻を手で指定、Shift+左=その拠点だけ自動検出に戻す、
  #     右=拠点を計算から外す・戻す ---
  def on_click(event):
    # ズーム・移動のツールを使っているときは何もしない
    toolbar = fig.canvas.toolbar
    if toolbar is not None and toolbar.mode:
      return
    name = next((nm for nm in names
                 if event.inaxes in (wave_axes[nm], cft_axes[nm])), None)
    if name is None or event.xdata is None:
      return
    if event.button == 1 and event.key == "shift":
      manual.pop(name, None)
    elif event.button == 1:
      manual[name] = float(event.xdata)
    elif event.button in (2, 3):   # Mac では右クリックが 2 になることがある
      excluded.symmetric_difference_update({name})
    else:
      return
    update()
  fig.canvas.mpl_connect("button_press_event", on_click)

  # --- ボタン: 自動検出に戻す・リセット・テキスト出力 ---
  aax = fig.add_axes([0.62, 0.255, 0.11, 0.04])
  btn_auto = Button(aax, "自動検出に戻す")
  def back_to_auto(_):
    manual.clear()
    excluded.clear()
    update()
    fit_xlim(current_picks())
    fig.canvas.draw_idle()
  btn_auto.on_clicked(back_to_auto)

  rax = fig.add_axes([0.745, 0.255, 0.08, 0.04])
  btn_reset = Button(rax, "リセット")
  def reset(_):
    for key, val in DEFAULT_PARAMS.items():
      s = sliders[key]
      s.eventson = False      # 個別の更新イベントを止める
      s.set_val(val)
      s.eventson = True
    update()
  btn_reset.on_clicked(reset)

  pax = fig.add_axes([0.84, 0.255, 0.10, 0.04])
  btn_print = Button(pax, "テキスト出力")
  def print_result(_):
    log_message("--- 震源推定 ---")
    for k, s in sliders.items():
      log_message(f"{k}: {s.val}")
    for line in result_message(current_picks(), state["result"] or {}).splitlines():
      log_message(line)
  btn_print.on_clicked(print_result)

  fig.text(0.62, 0.53, "パラメータ", fontsize=12, weight="bold")

  # --- 波形のフィルター切り替え(表示だけ。P波の検出は元の波形で行う) ---
  fax = fig.add_axes([0.80, 0.52, 0.14, 0.035])
  def filter_label():
    return (f"表示: {DISPLAY_BAND[0]:g}-{DISPLAY_BAND[1]:g}Hz" if state["filtered"]
            else "表示: フィルターなし")
  btn_filter = Button(fax, filter_label())
  btn_filter.label.set_fontsize(9)
  def toggle_filter(_):
    state["filtered"] = not state["filtered"]
    for nm in names:
      wave_lines[nm].set_ydata(filtered_data[nm] if state["filtered"] else raw_data[nm])
    btn_filter.label.set_text(filter_label())
    fit_ylim()
    fig.canvas.draw_idle()
  btn_filter.on_clicked(toggle_filter)

  # --- ウィンドウを閉じるとき、最後のパラメータを保存 ---
  def on_close(_event):
    save_params({k: s.val for k, s in sliders.items()}, SETTINGS_FILE)
  fig.canvas.mpl_connect("close_event", on_close)

  update()
  fig._widgets = (sliders, btn_auto, btn_reset, btn_print, btn_filter)
  plt.show()

if __name__ == "__main__":
  import glob
  import obspy
  from plot_common import setup_japanese_font
  setup_japanese_font()
  st = obspy.Stream()
  for f in glob.glob("./output/202608240135_202608240139/*.U.*_sac"):
    st += obspy.read(f)
  plot_hypocenter(st)
