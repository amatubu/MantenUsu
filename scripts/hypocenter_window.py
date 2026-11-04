# hypocenter_window.py
# 震源推定の画面: 拠点ごとに P波(上下動)・S波(水平動)の到着時刻を検出し、震源(または到来方向)を推定して表示する
#   P波は STA/LTA で検出したあと AIC 法で決め直し、S波は P波のあとの水平動に AIC 法を使う(hypocenter.py)
#   震源は P波・S波の到着時刻から求める(均質な速度構造。拠点の高度を考え、深さは海面からの深さ)
#   到来方向は P波の到着時刻だけから求める(遠い地震は位置が決まらないため)
#   到着時刻(手で直したものを含む)とパラメータは「到着時刻を保存」で output/picks に保存し(pick_store.py)、
#   同じ範囲を開いたときに読み込んで再現する
from datetime import timedelta

import numpy as np
import obspy
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, Button
from matplotlib.text import Annotation

from hypocenter import (load_stations, LocalProjection, pick_p_arrivals, locate_event,
                        pick_filtered, refine_p, pick_s, HORIZONTAL_RANGE,
                        LOCAL_VP, LOCAL_VP_VS, LOCAL_S_SEARCH, OMORI_K, S_EXCLUDED_DEFAULT,
                        MIN_OBSERVATIONS, MAX_LOCAL_RMS, depth_text)
from plot_common import NM_TO_CM, get_component_color
import gsi_tiles
from ps_picker import load_params, save_params
from log_common import log_message
import app_paths
import jma_match
import pick_store
import external_stations
import magnitude
from obspy.geodetics import gps2dist_azimuth

# 設定ファイル(P波・S波検出とは別に保存する)
SETTINGS_FILE = app_paths.ETC_DIR / "hypocenter_params.json"

# スライダー定義: (キー, 表示名, 最小, 最大, 刻み)
SLIDER_SPECS = [
  ("p_sta",  "P波 STA [秒]",       0.1, 10.0, 0.1),
  ("p_lta",  "P波 LTA [秒]",       1.0, 60.0, 0.5),
  ("p_thr",  "P波 閾値",           1.0, 8.0,  0.1),
  ("window", "同時検出の幅 [秒]",  0.5, 10.0, 0.5),
  # 遠い地震の S波も探せるよう、上限は60秒にしている(2026/8/23 22:45 の浦河沖(188 km)の S-P時間の見込みは約21秒)
  ("s_search", "S波を探す範囲 [秒]", 0.5, 60.0, 0.5),
  ("vp",     "P波の速度 [km/s]",   2.0, 8.0,  0.1),
  ("vp_vs",  "vp/vs",              1.5, 2.2,  0.01),
  ("omori_k", "大森係数 k [km/s]",  5.0, 10.0, 0.1),
]

# パラメータのデフォルト値(速度などは、地震の候補をまとめて計算する処理と同じ値。hypocenter.py を参照)
DEFAULT_PARAMS = dict(p_sta=2.5, p_lta=10.0, p_thr=2.6, window=3.0, s_search=LOCAL_S_SEARCH, vp=LOCAL_VP,
                      vp_vs=LOCAL_VP_VS, omori_k=OMORI_K)

# P波の検出に関わるパラメータ(これが変わったときだけ検出をやり直す)
PICK_KEYS = ("p_sta", "p_lta", "p_thr", "window")

# 波形の表示に使う帯域通過フィルター [Hz](小さな地震を見やすくする。検出には使わない)
DISPLAY_BAND = (2.0, 20.0)

# 結果の欄の文字の大きさ・行の間隔・行数の上限・色(見出し、本文、注意、気象庁の震源リスト)
#   見出し(◆〜「: 」まで)だけ色付きの太字にし、値は黒にする。気象庁の震源リストの見出しは別の色にする
RESULT_FONTSIZE = 9
RESULT_LINESPACING = 1.45
RESULT_MAX_LINES = 22
RESULT_COLORS = {"head": "#d35400", "body": "black", "warn": "#c62828", "jma": "#00695c"}
# 結果の文字列で、値を注意の色(赤)で表示する印(画面とテキスト出力では表示しない)
WARN_MARK = "\x01"

# 配置図の横:縦の比(拠点が東西に並んでいるので横長にする)
MAP_ASPECT = 1.5

# 配置図の背景の切り替え順(None は背景なし)
MAP_LAYERS = ["std", "relief", None]

# 16方位の名前(北から時計回り)
DIRECTIONS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東",
              "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]

# 方位(度)を16方位の名前にする
def direction_name(azimuth):
  return DIRECTIONS[int((azimuth + 11.25) % 360 // 22.5)]

# 時刻を 0.01秒まで表示する
def format_time(t):
  return t.strftime("%Y-%m-%d %H:%M:%S.%f")[:-4]

# 波形の期間に、ほかの機関の観測点の波形があれば加える(すでにある拠点は加えない)
def add_external(st):
  if not st:
    return st
  t0 = min(tr.stats.starttime for tr in st).datetime
  t1 = max(tr.stats.endtime for tr in st).datetime
  try:
    ext = external_stations.load(t0, t1)
  except Exception as e:
    log_message(f"ほかの機関の観測点の波形を読み込めませんでした: {e}")
    return st
  have = {tr.stats.station for tr in st}
  ext = [tr for tr in ext if tr.stats.station not in have]
  if not ext:
    return st
  log_message(f"ほかの機関の観測点を加えました: {', '.join(sorted({tr.stats.station for tr in ext}))}")
  return st.copy() + obspy.Stream(ext)

# 震源推定の画面を開く
#   st ... 3拠点以上の波形(上下動で P波、水平動(N・E)で S波を検出する)
#   overrides ... 前回の設定より優先するパラメータ(例: 遠い地震の候補を開くときの {"s_search": 30})
#                 保存した到着時刻があれば、そのときのパラメータをさらに優先する
#   on_apply  ... 地震の候補の震源の画面から開いたとき、保存したあとに呼ぶ関数(保存したファイルのパスを渡す)
#                 ボタンは「保存して一覧に反映」になる
#   external  ... ほかの機関の観測点(external_stations.py)の波形が v-net フォルダにあれば、拠点に加える
#                 (加えた観測点も、右クリックで計算から外せる。外したときと比べて、震源がどう変わるかを確かめられる)
def plot_hypocenter(st, overrides=None, on_apply=None, external=True):
  if external:
    st = add_external(st)
  # --- データ取得: 拠点ごとの上下動と水平動 ---
  traces = {tr.stats.station: tr for tr in st.select(channel="*U*")}
  # 満点の拠点を上に、ほかの機関の観測点を下に並べる(それぞれ名前の順)
  try:
    external_names = set(external_stations.coords())
  except OSError:
    external_names = set()
  names = sorted(traces, key=lambda nm: (nm in external_names, nm))
  horizontals = {nm: {c: st.select(station=nm, channel=f"*{c}*")[0] for c in "NE"
                      if st.select(station=nm, channel=f"*{c}*")}
                 for nm in names}
  try:
    station_coords = load_stations(app_paths.ETC_DIR)
    station_coords.update(external_stations.coords())
  except OSError as e:
    log_message(f"拠点の位置を読み込めませんでした: {e}")
    return
  missing = [n for n in names if n not in station_coords]
  if missing:
    log_message(f"チャンネル表に位置が無い拠点があります: {', '.join(missing)}")
    return

  proj = LocalProjection([station_coords[n] for n in names])
  xy = {n: np.array(proj.to_xy(*station_coords[n][:2])) for n in names}
  # 震源の計算に使う拠点の座標 [km](東, 北, 海面からの高度)
  xyz = {n: np.array([*xy[n], station_coords[n][2] / 1000]) for n in names}
  # 時刻の基準(全拠点で最も早い記録開始時刻)。到着時刻はここからの秒で扱う
  ref_time = min(tr.stats.starttime for tr in traces.values())
  start_str = ref_time.strftime("%Y-%m-%d %H:%M:%S")
  # 推定マグニチュードに使う、満点の拠点の上下動(帯域を通して cm/s にしたもの)
  mag_traces = magnitude.filtered_traces(traces)

  # 検出に関わる線の色(P波・S波検出と同じく、P波=赤・S波=マゼンタ。手で直した時刻はオレンジ・紫の実線)
  # 気象庁の震源リストの地震(震央・到達見込み)は青緑
  p_color, manual_color, cft_color, jma_color = "red", "darkorange", "gray", "teal"
  s_color, s_manual_color = "magenta", "purple"

  # 気象庁の震源リストで、この期間に揺れが届きそうな地震(取得済みのときだけ。到達見込みは拠点の中心で計算)
  t_ref = ref_time.datetime
  end_time = max(tr.stats.endtime for tr in traces.values()).datetime
  jma_hits, jma_available = [], False
  try:
    jma_hits, jma_available = jma_match.quakes_in_window(t_ref, end_time, (proj.lat0, proj.lon0))
  except Exception as e:
    log_message(f"気象庁の震源リストを読み込めませんでした: {e}")

  # ==========================================
  # 図のレイアウト: 左に拠点ごとの波形(上下動・水平動)、右上に拠点の配置図、右下にパラメータと結果
  # ==========================================
  #   結果の欄の行が多いので、縦を少し長くしている
  fig = plt.figure(figsize=(15, 10))
  if fig.canvas.manager is not None:
    fig.canvas.manager.set_window_title(f"震源推定 - {', '.join(names)} ({start_str})")
  # グラフ領域の左余白と幅、グラフの間隔と下余白(グラフの縦幅は拠点数から決める)
  left, width = 0.06, 0.50
  # (下に線の意味を2行で書くので、下余白を少し広くしている)
  gap, bottom = 0.04, 0.10
  n = len(names)
  h = (0.82 - gap * (n - 1)) / n

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

  # 水平動の表示用の波形(フィルターなし・あり) [cm/s]: {拠点名: {成分: (時刻, フィルターなし, あり)}}
  hor_display = {}
  for name in names:
    hor_display[name] = {}
    for c, tr in horizontals[name].items():
      tr_f = tr.copy()
      tr_f.detrend("demean")
      tr_f.filter("bandpass", freqmin=DISPLAY_BAND[0], freqmax=DISPLAY_BAND[1], zerophase=True)
      hor_display[name][c] = (tr.times() + (tr.stats.starttime - ref_time), tr.data * NM_TO_CM,
                              tr_f.data * NM_TO_CM)

  # 検出に使うデータ: 上下動(AIC 法で P波を決め直す)と、水平動のエネルギー(S波)
  #   pick_data ... {拠点名: (上下動, 水平動のエネルギー または None, サンプリング周波数, 記録開始の基準からの秒)}
  pick_data = {}
  for name in names:
    tr = traces[name]
    hor = horizontals[name]
    energy = None
    if len(hor) == 2:
      n_data, e_data = pick_filtered(hor["N"]), pick_filtered(hor["E"])
      m = min(len(n_data), len(e_data))
      energy = n_data[:m] ** 2 + e_data[:m] ** 2
    pick_data[name] = (pick_filtered(tr), energy, tr.stats.sampling_rate, tr.stats.starttime - ref_time)

  wave_axes, cft_axes, pick_lines, wave_lines, jma_lines = {}, {}, {}, {}, {}
  s_lines, hor_lines = {}, {}
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
    s_lines[name] = ax.axvline(0, color=s_color, ls="--", lw=1.2, visible=False)
    jma_lines[name] = ax.axvline(0, color=jma_color, ls=":", lw=1.5, visible=False)
    # 水平動(はじめは表示しない。「波形」のボタンで上下動と切り替える)
    hor_lines[name] = {c: ax.plot(t, f, lw=0.5, color=get_component_color(c), visible=False)[0]
                       for c, (t, _raw, f) in hor_display[name].items()}
  wave_axes[names[-1]].set_xlabel("時刻 (s)")
  # 線の意味(グラフの下に書く)
  fig.text(left, 0.008, "線: 赤の破線 = P波(自動)、橙の実線 = P波(手で指定)、マゼンタの破線 = S波(自動)、\n"
           "紫の実線 = S波(手で指定)(薄い線は計算に使わない S波)、青緑の点線 = 気象庁の震源からの P波の到達見込み、"
           "灰色 = CFT(右の軸)と閾値", fontsize=8, color="#333333", va="bottom", linespacing=1.3)

  # CFT の線と閾値の線(内容は update() で設定)
  cft_lines, thr_lines = {}, {}
  for name in names:
    cft_lines[name], = cft_axes[name].plot(times[name], np.zeros_like(times[name]), lw=0.5,
                                           color=cft_color, alpha=0.6)
    thr_lines[name] = cft_axes[name].axhline(0, color=cft_color, ls=":", lw=0.8)

  # --- 拠点の配置図 ---
  map_ax = fig.add_axes([0.60, 0.655, 0.38, 0.30])

  # --- 結果のテキスト ---
  #   見やすくするため、1行ずつ別の文字にして、見出し(◆)・注意・気象庁の震源リストを色分けする
  #   (行の高さは、文字の大きさ × RESULT_LINESPACING を図の高さの割合にしたもの)
  result_top = 0.30
  line_height = RESULT_FONTSIZE * RESULT_LINESPACING / 72 / fig.get_figheight()
  #   1行は「見出し」と「値」の2つの文字からなり、値は見出しのすぐ右に置く(見出しの右下に合わせる)
  result_rows = []
  for i in range(RESULT_MAX_LINES):
    label = fig.text(0.61, result_top - (i + 1) * line_height, "", fontsize=RESULT_FONTSIZE, va="bottom")
    value = Annotation("", xy=(1, 0), xycoords=label, fontsize=RESULT_FONTSIZE, va="bottom")
    fig.add_artist(value)
    result_rows.append((label, value))

  # ==========================================
  # スライダー(前回の設定で初期化)
  # ==========================================
  initial = load_params(SETTINGS_FILE, SLIDER_SPECS, DEFAULT_PARAMS)
  initial.update(overrides or {})
  # この範囲で保存した到着時刻(最初の P波が範囲内にあるもの)
  record_path, record = pick_store.find_record(t_ref, end_time)
  if record is not None:
    limits = {key: (vmin, vmax) for key, _label, vmin, vmax, _step in SLIDER_SPECS}
    for key, val in record.get("params", {}).items():
      if key in limits:
        initial[key] = min(max(float(val), limits[key][0]), limits[key][1])
  sliders = {}
  top, step_y = 0.54, 0.026
  for i, (key, label, vmin, vmax, vstep) in enumerate(SLIDER_SPECS):
    sax = fig.add_axes([0.73, top - i * step_y, 0.20, 0.019])
    sliders[key] = Slider(sax, label, vmin, vmax, valinit=initial[key], valstep=vstep)

  # --- 状態: 自動検出の結果、手で指定した時刻、計算から外した拠点 ---
  #   horizontal ... 水平動を表示している(クリックで S波を指定する)
  #   fixed・fixed_s ... 保存した到着時刻のうち、自動で検出したもの(検出をやり直しても同じになるとは限らないので、
  #                      保存した時刻を使う。P波の検出のパラメータを変えたら P波、P波か S波を探す範囲が変わったら S波を捨てる)
  state = {"auto": {}, "pick_params": None, "result": None, "filtered": True, "layer": MAP_LAYERS[0],
           "p_thr": None, "jma": None, "horizontal": False, "auto_s": {}, "fixed": {}, "fixed_s": {},
           "record_path": record_path}
  manual = {}
  manual_s = {}
  excluded = set()
  s_excluded = S_EXCLUDED_DEFAULT & set(names)
  if record is not None:
    p_saved, s_saved, p_manual, s_manual = pick_store.record_seconds(record, t_ref)
    for nm in names:
      if nm in p_saved:
        (manual if nm in p_manual else state["fixed"])[nm] = p_saved[nm]
      if nm in s_saved:
        (manual_s if nm in s_manual else state["fixed_s"])[nm] = s_saved[nm]
    # 保存した S波は、そのときの P波と S波を探す範囲のときだけ使う
    state["fixed_s_basis"] = ({nm: p_saved.get(nm) for nm in names}, initial["s_search"])
    excluded = set(record.get("excluded", [])) & set(names)
    s_excluded = set(record.get("s_excluded", [])) & set(names)
    log_message(f"保存した到着時刻を読み込みました: {record_path}")

  # 計算に使う P波の到着時刻(手で指定したもの、保存したもの、自動で検出したものの順)
  def current_picks():
    return {nm: manual.get(nm, state["fixed"].get(nm, state["auto"].get(nm))) for nm in names}

  # 計算に使う S波の到着時刻(手で指定したもの、保存したもの、自動で検出したものの順)
  def current_s_picks():
    return {nm: manual_s.get(nm, state["fixed_s"].get(nm, state["auto_s"].get(nm))) for nm in names}

  # 画面の上の説明(上下動・水平動の表示で、クリックの意味が変わる)
  def set_help_title():
    if state["horizontal"]:
      text = ("水平動: 左クリック: S波到着時刻を手で指定 / Shift+左クリック: 自動検出に戻す / "
              "右クリック: その拠点の S波を計算から外す・戻す")
    else:
      text = ("上下動: 左クリック: P波到着時刻を手で指定 / Shift+左クリック: 自動検出に戻す / "
              "右クリック: 拠点を計算から外す・戻す")
    first_ax.set_title(text, fontsize=10)
  set_help_title()

  # 縦軸を、表示している時間の範囲の波形・CFT に合わせる
  # (範囲の外の大きな揺れに引きずられて、小さな地震が見えなくならないように)
  def fit_ylim(_ax=None):
    x0, x1 = first_ax.get_xlim()
    for nm in names:
      visible = (times[nm] >= x0) & (times[nm] <= x1)
      if not visible.any():
        continue
      if state["horizontal"] and hor_lines[nm]:
        data = np.concatenate([ln.get_ydata()[(ln.get_xdata() >= x0) & (ln.get_xdata() <= x1)]
                               for ln in hor_lines[nm].values()])
      else:
        data = wave_lines[nm].get_ydata()[visible]
      if len(data) == 0:
        continue
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

  # 配置図の背景に地理院タイルを敷く(中心から東西 ±half_x km、南北 ±half_y km の範囲)
  # タイルはWebメルカトルだが、狭い範囲なので四隅の位置だけ合わせて平面に貼る
  def draw_background(half_x, half_y):
    layer = state["layer"]
    if layer is None:
      return
    lat_s, lon_w = proj.to_latlon(-half_x, -half_y)
    lat_n, lon_e = proj.to_latlon(half_x, half_y)
    for img, (west, east, south, north) in gsi_tiles.tiles_for_bounds(
        layer, lat_s, lat_n, lon_w, lon_e):
      x0, y0 = proj.to_xy(south, west)
      x1, y1 = proj.to_xy(north, east)
      map_ax.imshow(img, extent=(x0, x1, y0, y1), zorder=0, interpolation="bilinear")
    map_ax.grid(False)
    map_ax.text(0.01, 0.01, gsi_tiles.ATTRIBUTION, transform=map_ax.transAxes, fontsize=6,
                va="bottom", zorder=5, bbox=dict(facecolor="white", edgecolor="none", alpha=0.7))

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
      color = "gray" if nm in excluded or current_picks()[nm] is None else "black"
      map_ax.plot(x, y, "^", color=color, ms=9, markeredgecolor="white", zorder=3)
      map_ax.annotate(nm, (x, y), textcoords="offset points", xytext=(5, 5),
                      fontsize=8, color=color, zorder=3,
                      bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=1))
    radius = max(np.hypot(*xy[nm]) for nm in names)
    # 南北の半分の幅 [km](東西はその MAP_ASPECT 倍)。拠点が東西に収まるように決める
    half_y = radius * 1.6 / MAP_ASPECT
    loc = res.get("loc")
    if loc is not None and not loc["at_edge"] and res.get("far") is None:
      # 近い地震: 震央を星で示す(遠い地震として推定したときは、到来方向の矢印にする)
      # 拠点の間にあると拠点名と重なるため、説明の文字は凡例に出す
      depth = f"深さ {depth_text(loc)}"
      # 残差が大きいときは、当てにならないので白抜きにする
      doubtful = loc["rms"] > MAX_LOCAL_RMS
      map_ax.plot(loc["x"], loc["y"], "*", color="none" if doubtful else p_color, ms=16,
                  markeredgecolor=p_color if doubtful else "white", zorder=4,
                  label=f"震央 ({depth}" + (", 残差大)" if doubtful else ")"))
      half_y = max(half_y, abs(loc["x"]) * 1.2 / MAP_ASPECT, abs(loc["y"]) * 1.2)
    elif res.get("azimuth") is not None:
      # 遠い地震: 到来方向(震源のある方向)を矢印で示す
      az = np.radians(res["azimuth"])
      length = half_y * 0.9
      map_ax.annotate("", xy=(length * np.sin(az), length * np.cos(az)), xytext=(0, 0),
                      arrowprops=dict(arrowstyle="->", color=p_color, lw=2), zorder=4)
      # 凡例用の見えない線(矢印そのものは凡例に出せないため)
      far = res.get("far")
      map_ax.plot([], [], "-", color=p_color, lw=2,
                  label=f"到来方向 {res['azimuth']:.0f}° ({direction_name(res['azimuth'])})"
                  + (f"  距離 {far['distance']:.0f} km" if far else ""))
    half_x = half_y * MAP_ASPECT
    # 気象庁の震源リストの地震: 地図の範囲内なら震央、範囲外なら方位を示す
    q = state["jma"]
    if q is not None:
      qx, qy = proj.to_xy(q["緯度"], q["経度"])
      label = f"気象庁の震央 ({q['震央地名']} M{q['M']:.1f})"
      if abs(qx) <= half_x and abs(qy) <= half_y:
        map_ax.plot(qx, qy, "*", color=jma_color, ms=14, markeredgecolor="white", zorder=4, label=label)
      else:
        az = np.radians(q["azimuth"])
        length = half_y * 0.8
        map_ax.annotate("", xy=(length * np.sin(az), length * np.cos(az)), xytext=(0, 0),
                        arrowprops=dict(arrowstyle="->", color=jma_color, lw=2, ls="--"), zorder=4)
        map_ax.plot([], [], "--", color=jma_color, lw=2,
                    label=f"{label} の方位 {q['azimuth']:.0f}° ({direction_name(q['azimuth'])})")
    draw_background(half_x, half_y)
    map_ax.set_xlim(-half_x, half_x)
    map_ax.set_ylim(-half_y, half_y)
    if map_ax.get_legend_handles_labels()[0]:
      map_ax.legend(loc="lower right", fontsize=8)

  # 結果の文字列を作る
  def result_message(picks, res):
    s_picks = current_s_picks()
    # 開始時刻はウィンドウのタイトルにあるので書かない(行数を減らすため)
    #   見出しの行は「◆」で始め、注意の行は「※」で始める。見出しの値を赤にするときは WARN_MARK を付ける
    #   (show_result で色分けする)
    lines = ["◆P波到着(最初の拠点からの差)・S-P時間:"]
    used = [nm for nm in names if picks[nm] is not None and nm not in excluded]
    t_first = min((picks[nm] for nm in used), default=None)
    for nm in names:
      if picks[nm] is None:
        lines.append(f"  {nm}: 検出なし")
        continue
      mark = " (手動)" if nm in manual else ""
      mark += " (除外)" if nm in excluded else ""
      diff = picks[nm] - t_first if t_first is not None else 0.0
      if s_picks[nm] is not None:
        sp = f"  S-P {s_picks[nm] - picks[nm]:.2f} 秒" + (" (手動)" if nm in manual_s else "")
        sp += " (計算に使わない)" if nm in s_excluded and nm not in excluded else ""
      else:
        sp = "  S波 検出なし"
      lines.append(f"  {nm}: {format_time(ref_time + picks[nm])[11:]}  +{diff:.3f} 秒{mark}{sp}")
    if res.get("azimuth") is not None:
      lines.append(f"◆到来方向: {res['azimuth']:.0f}° ({direction_name(res['azimuth'])})"
                   f"  見かけの速度: {res['app_v']:.1f} km/s  残差: {res['plane_rms']:.3f} 秒")
    else:
      lines.append(f"◆到来方向: {WARN_MARK}3拠点以上の到着時刻が必要です")
    loc = res.get("loc")
    if loc is None:
      lines.append(f"◆震源: {WARN_MARK}P波 3拠点以上と、P波・S波を合わせて {MIN_OBSERVATIONS}つ以上の到着時刻が必要です")
    elif loc["at_edge"]:
      # 遠い地震として推定できたときは、その結果だけを書く(行数を減らすため)
      if res.get("far") is None:
        lines.append(f"◆震源: {WARN_MARK}探索範囲(±{HORIZONTAL_RANGE:.0f} km)の外です。"
                     "遠い地震のため、位置が決まりません")
    else:
      lat, lon = proj.to_latlon(loc["x"], loc["y"])
      depth = f"深さ {depth_text(loc)}(海面下)"
      lines.append(f"◆震源(P波 {res['n_p']}・S波 {res['n_s']}): 北緯 {lat:.4f}° 東経 {lon:.4f}° {depth}")
      lines.append(f"  発生時刻: {format_time(ref_time + loc['origin'])}  残差: {loc['rms']:.3f} 秒")
      if loc["rms"] > MAX_LOCAL_RMS:
        lines.append("  ※残差が大きく、遠い地震か、到着時刻の検出がずれています")
        if res.get("far") is None:
          lines.append("  ※推定 M: 震源が当てにならないため求めません")
      else:
        lines.append("  " + magnitude_text(loc))

    # 気象庁の震源リストの地震(最初の P波に合うもの)。遠い地震の推定マグニチュードでも使う
    p_abs = t_ref + timedelta(seconds=t_first) if t_first is not None else None
    jma_text, q = jma_match.window_summary(jma_hits, jma_available, t_ref, p_abs, wide=True)
    state["jma"] = q

    far = res.get("far")
    if far is not None:
      lines.append(f"◆遠い地震として(到来方向と S-P時間から): 距離 {far['distance']:.0f} km"
                   f"(S-P {far['sp']:.1f} 秒 × k {sliders['omori_k'].val:.1f})")
      lines.append(f"  震央: 北緯 {far['lat']:.2f}° 東経 {far['lon']:.2f}°")
      lines.append("  " + magnitude_far_text(far, t_first, q))
    elif res.get("azimuth") is not None and (loc is None or loc["at_edge"]):
      # 残差が大きいだけで S-P時間が短いときは、近い地震の検出のずれなので、この案内は出さない
      lines.append(f"◆遠い地震として: {WARN_MARK}S波の到着時刻が必要です")
      lines.append("  ※「S波を探す範囲」を S-P時間より長くしてください")

    # 気象庁の震源リストの地震(最初の P波に合うものを選ぶ)と、推定した結果との比較
    lines += ["◆" + jma_text[0]] + jma_text[1:]
    if q is not None:
      if loc is not None and not loc["at_edge"] and loc["rms"] <= MAX_LOCAL_RMS:
        qx, qy = proj.to_xy(q["緯度"], q["経度"])
        lines.append(f" 推定した震央との距離: {np.hypot(qx - loc['x'], qy - loc['y']):.1f} km")
      elif res.get("azimuth") is not None:
        d = (res["azimuth"] - q["azimuth"] + 180) % 360 - 180
        text = f" 到来方向との差: {d:+.0f}°"
        if far is not None:
          dist_m, _, _ = gps2dist_azimuth(far["lat"], far["lon"], q["緯度"], q["経度"])
          text += (f"  距離の差: {far['distance'] - q['dist_km']:+.0f} km"
                   f"  推定した震央との距離: {dist_m / 1000:.0f} km")
        lines.append(text)
    return "\n".join(lines)

  # 推定マグニチュードの行(満点の拠点の最大振幅と震源距離から。magnitude.py)
  #   深さが上限だけ決まる浅い震源は、深さの範囲の中ほどで震源距離を計算する
  def magnitude_text(loc):
    depth = loc["depth"]
    if loc.get("at_top") and loc.get("depth_max") is not None:
      depth = (loc["depth_min"] + loc["depth_max"]) / 2
    distances = {nm: float(np.sqrt((loc["x"] - xyz[nm][0]) ** 2 + (loc["y"] - xyz[nm][1]) ** 2 + (depth + xyz[nm][2]) ** 2))
                 for nm in mag_traces}
    origin = ref_time + loc["origin"]
    m, values = magnitude.estimate(mag_traces, origin - magnitude.SIGNAL_BEFORE, origin + magnitude.SIGNAL_AFTER, distances)
    return magnitude_line(m, values)

  # 遠い地震の推定マグニチュードの行(大森公式の距離を、どの拠点にも同じ震源距離として使う)
  #   最大振幅は、最初の P波の少し前から表示している範囲の終わりまでで測る(S波が範囲の外なら小さく出る)
  #   S波の検出がずれると距離も外れるので、合う震源リストの地震が MAX_DISTANCE より遠いときも求めない
  def magnitude_far_text(far, t_first, q):
    if far["distance"] > magnitude.MAX_DISTANCE:
      return f"※推定 M: 震源距離が {magnitude.MAX_DISTANCE:.0f} km を超えるため求めません"
    if q is not None and np.hypot(q["dist_km"], q["深さ[km]"]) > magnitude.MAX_DISTANCE:
      return f"※推定 M: 震源リストの地震が {magnitude.MAX_DISTANCE:.0f} km より遠いため求めません"
    m, values = magnitude.estimate(mag_traces, ref_time + t_first - magnitude.SIGNAL_BEFORE, None,
                                   {nm: far["distance"] for nm in mag_traces})
    return magnitude_line(m, values) + ("(表示範囲の最大振幅)" if m is not None else "")

  def magnitude_line(m, values):
    if m is None:
      return f"※推定 M: 求められません(揺れが雑音の{magnitude.MIN_SNR:g}倍を超える拠点がありません)"
    return f"推定 M: {m:.1f}({len(values)}拠点の中央値。±{magnitude.UNCERTAINTY:g} 程度の推計)"

  # 結果の欄に表示する(1行ずつ)
  #   「◆」で始まる行は、「: 」までを見出し(色付きの太字)、残りを値(黒)にする
  #   値が WARN_MARK で始まるときと、「※」で始まる行は、注意の色(赤)にする
  #   「◆気象庁」の見出しは気象庁の色にする
  def show_result(message):
    lines = message.splitlines()[:RESULT_MAX_LINES]
    for i, (label, value) in enumerate(result_rows):
      line = lines[i] if i < len(lines) else ""
      head, body = "", line
      if line.startswith("◆"):
        k = line.find(": ")
        head, body = (line, "") if k < 0 else (line[:k + 2], line[k + 2:])
      warn = body.startswith(WARN_MARK) or body.lstrip().startswith("※")
      label.set_text(head)
      label.set_color(RESULT_COLORS["jma" if head.startswith("◆気象庁") else "head"])
      label.set_fontweight("bold")
      value.set_text(body.replace(WARN_MARK, ""))
      value.set_color(RESULT_COLORS["warn" if warn else "body"])

  # テキスト出力用(注意の印を除く)
  def plain(message):
    return message.replace(WARN_MARK, "")

  # ==========================================
  # 更新処理
  # ==========================================
  def update(_=None):
    params = {k: s.val for k, s in sliders.items()}
    pick_params = tuple(params[k] for k in PICK_KEYS)
    refit = False
    if pick_params != state["pick_params"]:
      # 検出のパラメータが変わったときだけ、P波の検出をやり直す(保存した P波は、開いたときだけ使う)
      if state["pick_params"] is not None:
        state["fixed"].clear()
      if params["p_lta"] <= params["p_sta"]:
        show_result("※LTA は STA より長くしてください。\n※スライダーを調整してください。")
        fig.canvas.draw_idle()
        return
      auto, cfts = pick_p_arrivals(traces, ref_time, params["p_sta"], params["p_lta"],
                                   params["p_thr"], params["window"])
      # STA/LTA で検出した時刻の前後を、AIC 法で決め直す
      for nm, t in auto.items():
        if t is not None:
          data_u, _energy, df, offset = pick_data[nm]
          auto[nm] = refine_p(data_u, df, offset, t)
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

    # S波: P波のあとの水平動から検出する(P波を手で直したときや、探す範囲を変えたときも検出し直す)
    state["auto_s"] = {}
    for nm in names:
      _data_u, energy, df, offset = pick_data[nm]
      if picks[nm] is not None and energy is not None:
        state["auto_s"][nm] = pick_s(energy, df, offset, picks[nm], params["s_search"])
    # 保存した S波は、P波か S波を探す範囲が保存したときから変わったら捨てる
    if state["fixed_s"]:
      p_basis, s_search_basis = state["fixed_s_basis"]
      for nm in list(state["fixed_s"]):
        if picks[nm] != p_basis.get(nm) or params["s_search"] != s_search_basis:
          del state["fixed_s"][nm]
    s_picks = current_s_picks()
    for nm in names:
      ln = s_lines[nm]
      if s_picks[nm] is not None:
        ln.set_xdata([s_picks[nm]] * 2)
        ln.set_color(s_manual_color if nm in manual_s else s_color)
        ln.set_linestyle("-" if nm in manual_s else "--")
        # 計算に使わない S波は薄く表示する
        ln.set_alpha(0.35 if nm in s_excluded or nm in excluded else 1.0)
        ln.set_visible(True)
      else:
        ln.set_visible(False)
    if refit:
      fit_xlim(picks)
      fit_ylim()

    # --- 震源の計算(地震の候補をまとめて計算する処理と共通。hypocenter.locate_event) ---
    #   近い地震として位置が決まらないとき(範囲外・残差が大きい)は、遠い地震として到来方向と S-P時間から震央を推定する
    used = {nm: picks[nm] for nm in names if picks[nm] is not None and nm not in excluded}
    used_s = {nm: s_picks[nm] for nm in used if s_picks[nm] is not None and nm not in s_excluded}
    res = locate_event(xyz, used, used_s, proj, params["vp"], params["vp_vs"], params["omori_k"])
    state["result"] = res

    message = result_message(picks, res)   # 気象庁の震源リストの地震(state["jma"])もここで選ぶ
    q = state["jma"]
    for nm in names:
      if q is not None:
        x = (q["p_time"] - t_ref).total_seconds()
        jma_lines[nm].set_xdata([x, x])
      jma_lines[nm].set_visible(q is not None)
    draw_map(res)
    show_result(message)
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
    # 水平動を表示しているときは S波、上下動のときは P波を扱う
    picked, removed = (manual_s, s_excluded) if state["horizontal"] else (manual, excluded)
    if event.button == 1 and event.key == "shift":
      picked.pop(name, None)
    elif event.button == 1:
      picked[name] = float(event.xdata)
    elif event.button in (2, 3):   # Mac では右クリックが 2 になることがある
      removed.symmetric_difference_update({name})
    else:
      return
    update()
  fig.canvas.mpl_connect("button_press_event", on_click)

  # --- ボタン: 自動検出に戻す・リセット・テキスト出力・到着時刻を保存 ---
  aax = fig.add_axes([0.61, 0.315, 0.095, 0.032])
  btn_auto = Button(aax, "自動検出に戻す")
  btn_auto.label.set_fontsize(9)
  def back_to_auto(_):
    manual.clear()
    manual_s.clear()
    state["fixed"].clear()
    state["fixed_s"].clear()
    excluded.clear()
    s_excluded.clear()
    s_excluded.update(S_EXCLUDED_DEFAULT & set(names))
    update()
    fit_xlim(current_picks())
    fig.canvas.draw_idle()
  btn_auto.on_clicked(back_to_auto)

  rax = fig.add_axes([0.715, 0.315, 0.06, 0.032])
  btn_reset = Button(rax, "リセット")
  btn_reset.label.set_fontsize(9)
  def reset(_):
    for key, val in DEFAULT_PARAMS.items():
      s = sliders[key]
      s.eventson = False      # 個別の更新イベントを止める
      s.set_val(val)
      s.eventson = True
    update()
  btn_reset.on_clicked(reset)

  pax = fig.add_axes([0.785, 0.315, 0.08, 0.032])
  btn_print = Button(pax, "テキスト出力")
  btn_print.label.set_fontsize(9)
  def print_result(_):
    log_message("--- 震源推定 ---")
    for k, s in sliders.items():
      log_message(f"{k}: {s.val}")
    for line in plain(result_message(current_picks(), state["result"] or {})).splitlines():
      log_message(line)
  btn_print.on_clicked(print_result)

  # 到着時刻(手で直したものを含む)・計算から外した拠点・パラメータを output/picks に保存する
  #   地震の候補の震源の画面から開いたときは、保存したあと一覧に反映する(on_apply)
  sax_btn = fig.add_axes([0.875, 0.315, 0.105, 0.032])
  btn_save = Button(sax_btn, "保存して一覧に反映" if on_apply else "到着時刻を保存")
  btn_save.label.set_fontsize(9)
  def save_picks(_):
    def absolute(values):
      return {nm: t_ref + timedelta(seconds=t) for nm, t in values.items() if t is not None}
    path = pick_store.save_record(absolute(current_picks()), absolute(current_s_picks()), set(manual), set(manual_s),
                                  excluded, s_excluded, {k: s.val for k, s in sliders.items()},
                                  replace=state["record_path"])
    if path is None:
      status_text.set_text("P波の到着時刻がないため、保存できません")
      fig.canvas.draw_idle()
      return
    state["record_path"] = path
    text = f"保存しました: {path.name}"
    if on_apply:
      try:
        on_apply(path)
        text += "(一覧に反映しました)"
      except Exception as e:
        log_message(f"一覧に反映できませんでした: {e}")
        text += "(一覧に反映できませんでした)"
    status_text.set_text(text)
    fig.canvas.draw_idle()
  btn_save.on_clicked(save_picks)

  # 保存した到着時刻の状態(右上に表示する)
  status_text = fig.text(0.98, 0.995, "", ha="right", va="top", fontsize=9, color=RESULT_COLORS["jma"])
  if record is not None:
    status_text.set_text(f"保存した到着時刻を読み込みました: {record_path.name}(保存 {record.get('saved', '')})")

  fig.text(0.605, 0.578, "パラメータ", fontsize=12, weight="bold")

  # --- 配置図の背景の切り替え(標準地図 → 色別標高図 → なし) ---
  lax = fig.add_axes([0.68, 0.57, 0.095, 0.032])
  def layer_label():
    layer = state["layer"]
    return f"地図: {gsi_tiles.LAYERS[layer][0] if layer else 'なし'}"
  btn_layer = Button(lax, layer_label())
  btn_layer.label.set_fontsize(9)
  def toggle_layer(_):
    state["layer"] = MAP_LAYERS[(MAP_LAYERS.index(state["layer"]) + 1) % len(MAP_LAYERS)]
    btn_layer.label.set_text(layer_label())
    draw_map(state["result"] or {})
    fig.canvas.draw_idle()
  btn_layer.on_clicked(toggle_layer)

  # --- 波形のフィルター切り替え(表示だけ。P波の検出は元の波形で行う) ---
  fax = fig.add_axes([0.78, 0.57, 0.10, 0.032])
  def filter_label():
    return (f"表示: {DISPLAY_BAND[0]:g}-{DISPLAY_BAND[1]:g}Hz" if state["filtered"]
            else "表示: フィルターなし")
  # 水平動もフィルターを切り替える
  btn_filter = Button(fax, filter_label())
  btn_filter.label.set_fontsize(9)
  def toggle_filter(_):
    state["filtered"] = not state["filtered"]
    for nm in names:
      wave_lines[nm].set_ydata(filtered_data[nm] if state["filtered"] else raw_data[nm])
      for c, (_t, raw, filtered) in hor_display[nm].items():
        hor_lines[nm][c].set_ydata(filtered if state["filtered"] else raw)
    btn_filter.label.set_text(filter_label())
    fit_ylim()
    fig.canvas.draw_idle()
  btn_filter.on_clicked(toggle_filter)

  # --- 波形の切り替え(上下動 ⇔ 水平動)。クリックで指定するのも P波 ⇔ S波 に変わる ---
  cax_btn = fig.add_axes([0.885, 0.57, 0.095, 0.032])
  def comp_label():
    return "波形: 水平動(S波)" if state["horizontal"] else "波形: 上下動(P波)"
  btn_comp = Button(cax_btn, comp_label())
  btn_comp.label.set_fontsize(9)
  def toggle_comp(_):
    state["horizontal"] = not state["horizontal"]
    for nm in names:
      wave_lines[nm].set_visible(not state["horizontal"])
      for ln in hor_lines[nm].values():
        ln.set_visible(state["horizontal"])
    btn_comp.label.set_text(comp_label())
    set_help_title()
    fit_ylim()
    fig.canvas.draw_idle()
  btn_comp.on_clicked(toggle_comp)

  # --- ウィンドウを閉じるとき、最後のパラメータを保存 ---
  def on_close(_event):
    save_params({k: s.val for k, s in sliders.items()}, SETTINGS_FILE)
  fig.canvas.mpl_connect("close_event", on_close)

  update()
  fig._widgets = (sliders, btn_auto, btn_reset, btn_print, btn_save, btn_filter, btn_layer, btn_comp)
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
