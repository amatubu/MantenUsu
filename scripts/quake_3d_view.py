# quake_3d_view.py
# 震源・山体・観測点・地下構造・波の通り道を、ブラウザで回して見る3Dの図(HTML)に書き出す(模式的な表示の試作)
#   地形: 地理院の標高タイル(gsi_tiles.elevations)。湖は水面の高さで、湖底の形は入っていない
#   地下構造: 震源を求めるのに使う、深さで速度が変わる構造(velocity_model)を、ブロックの側面の色で示す
#   波の通り道: 同じ構造での最短経路(velocity_model.ray_path)。P波を読んだ拠点ごとに、震源から拠点まで
#   図の表示は plotly.js(インターネットから読む。登録や鍵は要らない)。データは HTML に書き込むので、ファイル1つで開ける
#   座標は拠点の中心を原点にした km(x: 東、y: 北、z: 上。深さは海面から下を正として表示する)
#
#   単体で実行すると、有珠山付近の地震の一覧(地震リストの usu)の震源を描く。あわせて、満点の拠点だけの波形の画像を
#   batch_plots/quakes/locate/waveforms_manten に作る(地震を選んだときに出す画像。アプリの「ブラウザで3D」もこれがあれば使う)
#     python3 scripts/quake_3d_view.py [-o 出力のHTML] [--no-images]
#   震源を求めた結果は、地震の候補の震源の画面と同じ保存先(quake_locate.LOCATE_CACHE_FILE)を使い、無ければ計算して保存する
import argparse
import html
import json
import os
import re
import zlib
from datetime import timedelta
from pathlib import Path

import numpy as np

import gsi_tiles
import station_names
import velocity_model
from hypocenter import LOCAL_KINDS, KIND_LOCAL, LocalProjection, depth_for_plot, depth_text
from quake_features import USU_SUMMIT
from quake_list import TYPE_BLAST, TYPE_QUAKE, TYPE_UNCERTAIN
from quake_locate import GOOD_RMS, EVENT_STYLES, event_type
from quake_web_map import depth_color

USU_LIST_TITLE = "有珠山付近の地震"   # 有珠山付近の地震の一覧の題名(ファイル名を決まった名前にする)
USU_LIST_FILE = "usu_quakes"
WAVE_PAGE_FILE = f"{USU_LIST_FILE}_waves.html"   # 拡大できる波形のページ(quake_wave_view.py。有珠山付近の地震の一覧だけ)
WAVE_PAGE_WINDOW = "usu_waves"   # 波形のページを開くタブの名前(同じタブを使い回し、2回目からは読み直さずに地震だけ変える)
PLOTLY_VERSION = "2.35.2"
PLOTLY_URL = f"https://cdn.jsdelivr.net/npm/plotly.js-dist-min@{PLOTLY_VERSION}/plotly.min.js"
MP4_MUXER_VERSION = "5.1.3"   # 動画(MP4)の書き出しに使う
MP4_MUXER_URL = f"https://cdn.jsdelivr.net/npm/mp4-muxer@{MP4_MUXER_VERSION}/build/mp4-muxer.min.js"

MARGIN_KM = 1.5            # 拠点・震源の外側に足す広さ [km]
MIN_HALF_WIDTH_KM = 3.0    # 山頂からの範囲の最小 [km](震源が少なくても山体が見えるように)
GRID_STEP_KM = 0.05        # 地形の格子の間隔 [km]
BOTTOM_MIN_KM = 3.0        # ブロックの底の深さの最小 [km](震源がもっと深ければ、その下まで)
VP_RANGE = (2.0, 6.0)      # 側面の色の P波の速度の範囲 [km/s]
EXAGGERATIONS = (1, 1.2, 1.5, 2, 3)  # 縦の倍率の選択肢(全体)
ABOVE_EXAGGERATIONS = (1.5, 2)       # 海面より上だけ強調する倍率の選択肢(3Dプリントの模型と同じ。深さはそのまま)
DEFAULT_OPACITY = 0.6      # 地表のはじめの不透明度(地下の震源が透けて見えるように)
SIDE_HALF_OPACITY = 0.35   # 側面を半透明にしたときの不透明度(横から見て内部の震源が見えるように)
OTHERS_OPACITY = 0.3       # 地震を1つ選んだときの、ほかの震源の不透明度(選んだ地震を目立たせる)

# 地形の色(低い所は緑、高い所は茶色。模式的に落ち着いた色)
TERRAIN_COLORS = [[0.0, "#5b8c5a"], [0.25, "#8fae6b"], [0.5, "#c8b77a"], [0.75, "#a98763"], [1.0, "#8a7462"]]
SEA_LEVEL_COLOR = "#6fa8dc"
VP_COLORS = "YlGnBu"
P_RAY_COLOR = "#1f6fb2"
P_WAVE_COLOR = "#1565c0"   # 波の動きの P波の点
S_WAVE_COLOR = "#e65100"   # 波の動きの S波の点
WAVE_SLOWDOWNS = (1, 5, 10, 20)   # 波の動きを実際の何倍遅くするかの選択肢(1 は実際の速さ)
VIDEO_FPS = 30             # 保存する動画のコマ数 [コマ/秒]
VIDEO_MAX_WIDTH = 1920     # 保存する動画の幅の上限 [画素](図がこれより大きいときは縮める)
VIDEO_BITRATE = 12_000_000 # 保存する動画のビットレート [bit/秒]


# --- 範囲と地形 ---

# 描く範囲(拠点の中心からの km): 拠点・震源を含み、山頂の周りは少なくとも MIN_HALF_WIDTH_KM
#   points ... [(x, y), ...]
def bounds(points, proj):
  sx, sy = proj.to_xy(*USU_SUMMIT)
  xs = [p[0] for p in points] + [sx - MIN_HALF_WIDTH_KM, sx + MIN_HALF_WIDTH_KM]
  ys = [p[1] for p in points] + [sy - MIN_HALF_WIDTH_KM, sy + MIN_HALF_WIDTH_KM]
  return (min(xs) - MARGIN_KM, max(xs) + MARGIN_KM, min(ys) - MARGIN_KM, max(ys) + MARGIN_KM)

# 範囲の地形: (x の並び, y の並び, 標高 [km] の配列 (ny, nx), 水面かどうかの配列)。海などのデータなしは 0(海面)
def terrain(box, proj, step=GRID_STEP_KM):
  x = np.arange(box[0], box[1] + step / 2, step)
  y = np.arange(box[2], box[3] + step / 2, step)
  gx, gy = np.meshgrid(x, y)
  lat, lon = proj.to_latlon(gx, gy)
  h = gsi_tiles.elevations(lat, lon) / 1000.0
  water = ~np.isfinite(h)
  h = np.where(water, 0.0, h)
  return x, y, h, water | lake_mask(h, water)

# 湖(洞爺湖)の水面: 標高タイルでは湖が同じ高さの平らな面なので、いちばん多い同じ値が広くつながった所を湖とみなす
LAKE_MIN_FRACTION = 0.02   # この割合より広い同じ高さの面だけを湖とする(平らな畑などを湖にしないため)
#   sea ... 海など(データなし)の所。湖を探すときは除く(海の 0 m がいちばん多くならないように)
def lake_mask(h, sea=None):
  land = h[~sea] if sea is not None else h.ravel()
  if land.size == 0:
    return np.zeros(h.shape, bool)
  values, counts = np.unique(np.round(land, 4), return_counts=True)
  i = int(np.argmax(counts))
  if counts[i] < LAKE_MIN_FRACTION * h.size:
    return np.zeros(h.shape, bool)
  mask = np.abs(h - values[i]) < 1e-4
  if sea is not None:
    mask &= ~sea
  # たまたま同じ高さの陸の点(ばらばらの小さな所)は除き、いちばん広くつながった所だけを湖にする
  from scipy import ndimage
  labels, n = ndimage.label(mask)
  if n > 1:
    sizes = ndimage.sum(mask, labels, range(1, n + 1))
    mask = labels == (int(np.argmax(sizes)) + 1)
  return mask


# --- 図の部品(plotly.js の trace) ---

def r3(values):
  return [round(float(v), 3) for v in values]

# 色は標高(水面は WATER_VALUE にして、色の並びのいちばん下の青にする)
WATER_VALUE = -0.05
def terrain_trace(x, y, h, water):
  top = max(float(np.max(h)), 0.1)
  color = np.where(water, WATER_VALUE, h)
  scale = [[0.0, SEA_LEVEL_COLOR], [0.999 * -WATER_VALUE / (top - WATER_VALUE), SEA_LEVEL_COLOR]]
  scale += [[(-WATER_VALUE + f * top) / (top - WATER_VALUE), c] for f, c in TERRAIN_COLORS]
  scale[2][0] = max(scale[2][0], scale[1][0] + 1e-6)
  return {"type": "surface", "name": "地形", "x": r3(x), "y": r3(y), "z": [r3(row) for row in h],
          "surfacecolor": [r3(row) for row in color], "cmin": WATER_VALUE, "cmax": top,
          "colorscale": scale, "showscale": False, "opacity": DEFAULT_OPACITY,
          "lighting": {"ambient": 0.55, "diffuse": 0.8, "roughness": 0.9, "specular": 0.05},
          "lightposition": {"x": -1e5, "y": 1e5, "z": 2e5},
          "contours": {"z": {"show": True, "start": 0, "end": 1, "size": 0.1, "color": "rgba(60,50,40,0.25)",
                             "width": 1}},
          "hoverinfo": "skip"}   # 地表の後ろの震源を押せるように、地表は押せないようにする

# ブロックの4つの側面(地表の縁から底まで)。色は P波の速度(深さで決まる)
def side_traces(x, y, h, bottom, model):
  zs = np.linspace(bottom * -1, max(0.0, float(np.max(h))), 60)   # 上が正
  vp = model.vp(-zs)
  edges = [(x, np.full_like(x, y[0]), h[0, :]), (x, np.full_like(x, y[-1]), h[-1, :]),
           (np.full_like(y, x[0]), y, h[:, 0]), (np.full_like(y, x[-1]), y, h[:, -1])]
  traces = []
  for i, (ex, ey, top) in enumerate(edges):
    # 縦に zs の段を並べ、地表より上は地表に押しつぶす(地表の縁の形にする)
    zz = np.minimum(zs[:, None], top[None, :])
    traces.append({
      "type": "surface", "name": "地下構造", "legendgroup": "structure", "showlegend": i == 0,
      "x": [r3(ex)] * len(zs), "y": [r3(ey)] * len(zs), "z": [r3(row) for row in zz],
      "surfacecolor": [r3(np.full(len(ex), v)) for v in vp], "cmin": VP_RANGE[0], "cmax": VP_RANGE[1],
      "colorscale": VP_COLORS, "reversescale": True, "opacity": SIDE_HALF_OPACITY, "showscale": i == 0,
      "colorbar": {"title": {"text": "P波の速度<br>[km/s]"}, "len": 0.5, "x": 1.0, "y": 0.3},
      "lighting": {"ambient": 0.9, "diffuse": 0.2},
      "hoverinfo": "skip"})   # 側面の向こうの震源を押せるように
  return traces

# 海面(深さ 0)の枠。地下の深さの目安
def sea_level_trace(box):
  x0, x1, y0, y1 = box
  return {"type": "scatter3d", "mode": "lines", "name": "海面(深さ 0 km)",
          "x": [x0, x1, x1, x0, x0], "y": [y0, y0, y1, y1, y0], "z": [0] * 5,
          "line": {"color": SEA_LEVEL_COLOR, "width": 3, "dash": "dash"}, "hoverinfo": "skip"}

# 拠点(満点の拠点と、外部の観測点)
def station_traces(manten, external, proj, box):
  traces = []
  for name, coords, symbol, color in (("満点の拠点", manten, "diamond", "#d62728"),
                                       ("外部の観測点", external, "square", "#7f7f7f")):
    pts = [(nm, *proj.to_xy(la, lo), el / 1000.0) for nm, (la, lo, el) in sorted(coords.items())]
    pts = [p for p in pts if box[0] <= p[1] <= box[1] and box[2] <= p[2] <= box[3]]
    if not pts:
      continue
    traces.append({"type": "scatter3d", "mode": "markers+text", "name": name,
                   "x": r3(p[1] for p in pts), "y": r3(p[2] for p in pts), "z": r3(p[3] + 0.03 for p in pts),
                   "text": [station_names.full(p[0]) for p in pts], "textposition": "top center",
                   "textfont": {"size": 11, "color": color},
                   "marker": {"symbol": symbol, "size": 5, "color": color, "line": {"color": "white", "width": 1}},
                   "hovertemplate": "%{text}<br>標高 %{customdata:.0f} m<extra></extra>",
                   "customdata": [round(p[3] * 1000) for p in pts]})
  return traces

def summit_trace(proj):
  sx, sy = proj.to_xy(*USU_SUMMIT)
  sz = float(gsi_tiles.elevations(np.array([USU_SUMMIT[0]]), np.array([USU_SUMMIT[1]]))[0]) / 1000.0
  sz = sz if np.isfinite(sz) else 0.7
  return {"type": "scatter3d", "mode": "markers+text", "name": "有珠山の山頂",
          "x": [round(sx, 3)], "y": [round(sy, 3)], "z": [round(sz + 0.05, 3)], "text": ["有珠山"],
          "textposition": "top center", "marker": {"symbol": "x", "size": 4, "color": "black"},
          "hovertemplate": "有珠山の山頂<extra></extra>"}

# 種別ごとの印(plotly の3Dの図には星が無いので、発破はひし形にする)
EVENT_SYMBOLS = {TYPE_QUAKE: "circle", TYPE_BLAST: "diamond", TYPE_UNCERTAIN: "square"}

# 震源。色は深さ(ブラウザの地図と同じ赤の濃淡。地震以外は種別の色)、縦の線は深さの範囲。残差が大きいものは白抜き
def quake_trace(quakes):
  return {"type": "scatter3d", "mode": "markers", "name": "震源",
          "x": [q["x"] for q in quakes], "y": [q["y"] for q in quakes], "z": [-q["depth"] for q in quakes],
          "marker": {"size": 5, "color": [q["color"] if q["good"] else "white" for q in quakes],
                     "line": {"color": [q["color"] for q in quakes], "width": 2},
                     "symbol": [EVENT_SYMBOLS[q["type"]] for q in quakes]},
          "error_z": {"type": "data", "symmetric": False, "color": "rgba(180,40,40,0.5)", "thickness": 1.5,
                      "width": 0,
                      "array": [q["err_up"] for q in quakes], "arrayminus": [q["err_down"] for q in quakes]},
          "text": [q["label"] for q in quakes], "hovertemplate": "%{text}<extra></extra>"}

# 震源の番号(地図で見たい地震の番号が分かるように。上の欄の「番号」で隠せる)
def number_trace(quakes):
  return {"type": "scatter3d", "mode": "text", "name": "震源の番号", "showlegend": False,
          "x": [q["x"] for q in quakes], "y": [q["y"] for q in quakes], "z": [-q["depth"] for q in quakes],
          "text": [str(q["no"]) for q in quakes], "textposition": "top right",
          "textfont": {"size": 10, "color": "#8b1a1a"}, "hoverinfo": "skip"}

# 波の通り道(地震ごとに1つの trace。拠点ごとの線は None で区切る)。はじめは隠しておく
def ray_trace(q, rays):
  xs, ys, zs = [], [], []
  for path in rays:
    xs += [p[0] for p in path] + [None]
    ys += [p[1] for p in path] + [None]
    zs += [p[2] for p in path] + [None]
  return {"type": "scatter3d", "mode": "lines", "name": f"P波の通り道(No.{q['no']})", "visible": False,
          "showlegend": False, "x": xs, "y": ys, "z": zs,
          "line": {"color": P_RAY_COLOR, "width": 3}, "opacity": 0.8, "hoverinfo": "skip"}


# 地震を1つ選んだときの時刻の表示(震源の発生時刻と、拠点の P波の走時。到着時刻は波形の画像の下の一覧に出す)。はじめは隠しておく
def time_label_trace(q):
  pts = q["times"]
  # 震源には輪の印を付ける(選んだ地震がどれか分かるように)。拠点の時刻は拠点の名前と重ならないよう下に出す
  return {"type": "scatter3d", "mode": "markers+text", "name": f"時刻(No.{q['no']})", "visible": False,
          "showlegend": False, "x": [p[0] for p in pts], "y": [p[1] for p in pts], "z": [p[2] for p in pts],
          "text": [p[3] for p in pts], "textposition": [p[4] for p in pts],
          "textfont": {"size": [13] + [11] * (len(pts) - 1), "color": [p[5] for p in pts]},
          "marker": {"size": [14] + [0] * (len(pts) - 1), "symbol": "circle-open", "color": TIME_TEXT_COLOR,
                     "line": {"width": 3}},
          "hoverinfo": "skip"}

TIME_TEXT_COLOR = "#7a1f1f"   # 発生時刻の文字の色
ARRIVAL_TEXT_COLOR = P_RAY_COLOR

def hhmmss_cc(t):
  return f"{t:%H:%M:%S.%f}"[:-4]


# 地震の波形の画像(満点の拠点の波形と、P波・S波の到着時刻の印)。有珠山付近の地震の一覧の画像、
# 震源の確認用の画像、地震の候補の画像の順に探す。戻り値は画像のパス(無ければ None)
#   満点の拠点だけの波形の画像(save_manten_images で作ったもの)を最初に使う
def waveform_image(cand, r, usu_images=None):
  import quake_detect
  import quake_locate
  start = f"{cand['start']:%Y-%m-%d %H:%M:%S}"
  manten = manten_image_path(cand)
  if manten.exists():
    return manten
  if usu_images and usu_images.get(start):
    return usu_images[start]
  if r.get("image") and (quake_locate.OUTPUT_DIR / r["image"]).exists():
    return quake_locate.OUTPUT_DIR / r["image"]
  path = quake_detect.OUTPUT_DIR / f"{cand['start']:%Y%m%d_%H%M%S}.png"
  return path if path.exists() else None

# --- 満点の拠点だけの波形の画像 ---
#   震源の確認用の画像(quake_locate.save_check_image)と同じ形で、満点の拠点の波形と P波・S波の到着時刻だけを描く
#   (外部の観測点の波形は、3Dの図の時刻の一覧と合わせて見るには多すぎるため)。今の震源の結果から作るので、見出しの値も今のもの
def manten_image_dir():
  import quake_locate
  return quake_locate.OUTPUT_DIR / "waveforms_manten"

def manten_image_path(cand):
  return manten_image_dir() / f"{cand['start']:%Y%m%d_%H%M%S}.png"

def save_manten_images(items, stations, load_hour, log=print):
  """近い地震ごとに、満点の拠点だけの波形の画像を作る(前に作ったものは置き換える)。戻り値は作った数"""
  from obspy import Stream
  import quake_locate
  manten_image_dir().mkdir(parents=True, exist_ok=True)
  cache, n = {}, 0
  local = [(c, r) for c, r in items if r["kind"] in LOCAL_KINDS and r.get("lat") is not None]
  for k, (cand, r) in enumerate(local, 1):
    picks = {nm: t for nm, t in (r.get("picks") or {}).items() if nm in stations}
    if not picks:
      continue
    st = quake_locate.window_stream(*quake_locate.candidate_window(cand), load_hour, cache)
    st = Stream([tr for tr in st if tr.stats.station in stations])
    if len(st) == 0:
      continue
    r2 = dict(r, traces=st, picks=picks,
              s_picks={nm: t for nm, t in (r.get("s_picks") or {}).items() if nm in stations})
    title = quake_locate.check_title(cand, r2).replace("\n", "(満点の拠点の波形だけ)\n", 1)
    if quake_locate.save_check_image(manten_image_path(cand), cand, r2, title):
      n += 1
    log(f"  波形の画像 {k}/{len(local)}: {cand['start']:%Y-%m-%d %H:%M:%S}")
  return n

# 有珠山付近の地震の一覧の画像 {開始: 画像のパス}
def usu_list_images():
  import quake_list
  try:
    return {e["start"]: quake_list.image_path(e) for e in quake_list.load()[quake_list.USU]}
  except Exception:
    return {}

# HTML から画像への相対パス(ブラウザで開くための URL の形。同じドライブに無ければ file の URL)
def image_url(image, html_dir):
  if image is None:
    return ""
  try:
    return Path(os.path.relpath(image, html_dir)).as_posix()
  except ValueError:   # Windows でドライブが違うとき
    return Path(image).resolve().as_uri()


# --- 震源と通り道 ---

# 波の動き(アニメーション)に使う、通り道の点と震源からの時刻: [[x, y, z, 秒], ...](震源が最初)
#   path ... velocity_model.ray_path の (拠点からの水平距離, 深さ) の並び。velocity ... 深さ → 速さの関数
#   時刻は、通り道に沿って遅さ(1/速さ)を足したもの(走時表と 1% ほどで合う)。点は間引く
WAVE_POINT_STEP = 2
def timed_path(path, velocity, sx, sy, ux, uy):
  p = np.array(path, float)
  ds = np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1]))
  t = np.concatenate([[0.0], np.cumsum(ds * 0.5 * (1 / velocity(p[:-1, 1]) + 1 / velocity(p[1:, 1])))])
  keep = list(range(0, len(p), WAVE_POINT_STEP))
  if keep[-1] != len(p) - 1:
    keep.append(len(p) - 1)
  return [[round(sx + ux * p[k, 0], 3), round(sy + uy * p[k, 0], 3), round(-p[k, 1], 3), round(float(t[k]), 4)]
          for k in keep]

# 地震を見分ける文字列(地震リストの開始の時刻)。拡大できる波形のページを、この地震の所で開くのに使う
def event_key(cand):
  return f"{cand['start']:%Y%m%d_%H%M%S}"

# 描く震源: [(表示用の辞書, 通り道のリスト), ...]
#   items ... [(候補, 結果), ...](quake_locate.locate_all_cached の結果)
#   stations ... 満点の拠点 {名前: (緯度, 経度, 高度[m])}
#   html_dir ... HTML を書き出すフォルダ(波形の画像への相対パスを作るため。None なら画像を探さない)
def quake_items(items, stations, proj, model, html_dir=None):
  out = []
  usu_images = usu_list_images() if html_dir is not None else None
  local = [(cand, r) for cand, r in items if r["kind"] in LOCAL_KINDS and r.get("lat") is not None]
  for i, (cand, r) in enumerate(local, 1):
    loc, q = r["loc"], cand["jma"]
    x, y = proj.to_xy(r["lat"], r["lon"])
    depth = depth_for_plot(loc)
    lo, hi = loc.get("depth_min"), loc.get("depth_max")
    origin = r["ref"] + timedelta(seconds=loc["origin"]) if r.get("ref") is not None else cand["start"]
    good = r["kind"] == KIND_LOCAL and loc["rms"] < GOOD_RMS
    etype = event_type(cand)
    label = (f"No.{i} {origin:%Y-%m-%d %H:%M:%S}" + ("" if etype == TYPE_QUAKE else f"({etype})")
             + f"<br>深さ {depth_text(loc)}<br>残差 {loc['rms']:.3f} 秒"
             + (f"<br>気象庁 M{q['M']:g}" if q and q.get("M") is not None else ""))
    color = depth_color(depth) if etype == TYPE_QUAKE else EVENT_STYLES[etype]["color"]
    info = {"no": i, "key": event_key(cand), "x": round(x, 3), "y": round(y, 3), "depth": round(depth, 3), "color": color, "type": etype,
            "good": good, "label": label, "short": f"No.{i} {origin:%Y-%m-%d %H:%M}",
            "err_up": round(max(0.0, depth - lo), 3) if lo is not None else 0,
            "err_down": round(max(0.0, hi - depth), 3) if hi is not None else 0}
    # P波を読んだ拠点ごとの通り道(震源から拠点まで)と、時刻の表示(震源の発生時刻、拠点の到着時刻と走時)
    positions = dict(stations)
    positions.update(r.get("external") or {})
    rays, waves = [], {"P": [], "S": []}
    times = [(info["x"], info["y"], -info["depth"], f"発生 {hhmmss_cc(origin)}", "middle left", TIME_TEXT_COLOR)]
    lines = [f"発生 {hhmmss_cc(origin)}(深さ {depth_text(loc)})"]
    for name in sorted(r.get("picks") or {}):
      if name not in positions:
        continue
      la, lo_, el = positions[name]
      sx, sy = proj.to_xy(la, lo_)
      dist = float(np.hypot(x - sx, y - sy))
      ux, uy = ((x - sx) / dist, (y - sy) / dist) if dist > 0 else (0.0, 0.0)
      path = velocity_model.ray_path(model, "P", el / 1000.0, dist, depth)
      rays.append([(round(sx + ux * pr, 3), round(sy + uy * pr, 3), round(-pz, 3)) for pr, pz in path])
      waves["P"].append(timed_path(path, model.vp, sx, sy, ux, uy))
      if name in (r.get("s_picks") or {}):
        waves["S"].append(timed_path(velocity_model.ray_path(model, "S", el / 1000.0, dist, depth), model.vs, sx, sy, ux, uy))
      if r.get("ref") is not None:
        arrival = r["ref"] + timedelta(seconds=r["picks"][name])
        travel = (arrival - origin).total_seconds()
        times.append((round(sx, 3), round(sy, 3), round(el / 1000.0 + 0.03, 3),
                      f"P +{travel:.2f}秒", "bottom center", ARRIVAL_TEXT_COLOR))
        lines.append(f"{station_names.full(name)}: P {hhmmss_cc(arrival)}(走時 {travel:.2f} 秒)")
    info["times"] = times
    info["waves"] = waves
    info["lines"] = lines
    info["image"] = image_url(waveform_image(cand, r, usu_images), html_dir) if html_dir is not None else ""
    out.append((info, rays))
  return out


# --- HTML ---

#   html_dir ... HTML を書き出すフォルダ(波形の画像への相対パスのため)
#   戻り値 ... (plotly.js の図 {data, layout}, 画面の切り替えに使う情報, 描いた震源の数)
def figure(items, stations, title, model=None, html_dir=None):
  model = model or velocity_model.MODELS[velocity_model.DEFAULT]
  proj = LocalProjection(list(stations.values()))
  external = {}
  for _cand, r in items:
    external.update(r.get("external") or {})
  quakes = quake_items(items, stations, proj, model, html_dir)
  pts = [proj.to_xy(la, lo) for la, lo, _el in list(stations.values()) + list(external.values())]
  pts += [(q["x"], q["y"]) for q, _rays in quakes]
  box = bounds(pts, proj)
  x, y, h, water = terrain(box, proj)
  deepest = max([q["depth"] + q["err_down"] for q, _rays in quakes] + [0.0])
  bottom = max(BOTTOM_MIN_KM, float(np.ceil(deepest + 0.5)))

  sides = side_traces(x, y, h, bottom, model)
  traces = [terrain_trace(x, y, h, water)] + sides + [sea_level_trace(box)]
  traces += station_traces(stations, external, proj, box) + [summit_trace(proj)]
  quake_index = None
  if quakes:
    quake_index = len(traces)
    traces.append(quake_trace([q for q, _rays in quakes]))
  number_index = None
  if quakes:
    number_index = len(traces)
    traces.append(number_trace([q for q, _rays in quakes]))
  ray_start = len(traces)
  traces += [ray_trace(q, rays) for q, rays in quakes]
  label_start = len(traces)
  traces += [time_label_trace(q) for q, _rays in quakes]
  # 波の動き: P波の点、S波の点(経過時間などの文字は、視点によらないよう図の外に出す)
  wave_index = list(range(len(traces), len(traces) + 2))
  traces += [{"type": "scatter3d", "mode": "markers", "name": "P波(動き)", "showlegend": False, "visible": False,
              "x": [], "y": [], "z": [], "hoverinfo": "skip",
              "marker": {"size": 6, "color": P_WAVE_COLOR, "line": {"color": "white", "width": 1}}},
             {"type": "scatter3d", "mode": "markers", "name": "S波(動き)", "showlegend": False, "visible": False,
              "x": [], "y": [], "z": [], "hoverinfo": "skip",
              "marker": {"size": 6, "color": S_WAVE_COLOR, "line": {"color": "white", "width": 1}}}]

  span = max(box[1] - box[0], box[3] - box[2])
  aspect = {"x": (box[1] - box[0]) / span, "y": (box[3] - box[2]) / span, "z": (float(np.max(h)) + bottom) / span}
  layout = {
    "title": {"text": title, "x": 0.01, "font": {"size": 15}},
    "margin": {"l": 0, "r": 0, "t": 40, "b": 0}, "paper_bgcolor": "white",
    "legend": {"x": 0.01, "y": 0.95, "bgcolor": "rgba(255,255,255,0.7)"},
    "scene": {"xaxis": {"title": {"text": "東 [km]"}}, "yaxis": {"title": {"text": "北 [km]"}},
              "zaxis": {"title": {"text": "高さ [km]"}}, "aspectmode": "manual", "aspectratio": aspect,
              "camera": {"eye": {"x": -0.9, "y": -1.5, "z": 0.8}}},
    "annotations": [{"text": f"{gsi_tiles.DEM_ATTRIBUTION}。地下構造と波の通り道は、震源を求めるのに使う深さで速度が変わる構造(模式的なもの)。"
                             "<br>震源の縦の線は深さの範囲、白抜きは残差が大きいもの。湖・海は青(湖底の形は無い)",
                     "xref": "paper", "yref": "paper", "x": 0.01, "y": 0.0, "xanchor": "left", "yanchor": "bottom", "align": "left",
                     "showarrow": False, "font": {"size": 11, "color": "#555"}}],
  }
  # 画面の切り替え(地表・側面・縦の倍率・地震を選ぶ)に使う trace の番号など
  meta = {"terrain": 0, "sides": list(range(1, 1 + len(sides))), "opacity": DEFAULT_OPACITY,
          "side_half": SIDE_HALF_OPACITY, "aspect": aspect, "span": span, "top": float(np.max(h)), "bottom": bottom,
          "slowdowns": list(WAVE_SLOWDOWNS),
          "exaggerations": [{"label": f"{ex:g} 倍", "ex": ex, "above": 1} for ex in EXAGGERATIONS]
                           + [{"label": f"海面より上だけ {a:g} 倍", "ex": 1, "above": a} for a in ABOVE_EXAGGERATIONS],
          "quake_trace": quake_index, "number_trace": number_index, "others_opacity": OTHERS_OPACITY,
          "rays": list(range(ray_start, label_start)), "labels": list(range(label_start, len(traces))),
          "quakes": [{"short": q["short"], "key": q["key"], "image": q["image"], "lines": q["lines"], "waves": q["waves"],
                      "label": q["short"]} for q, _rays in quakes],
          "waves": wave_index, "attribution": gsi_tiles.DEM_ATTRIBUTION, "p_color": P_WAVE_COLOR, "s_color": S_WAVE_COLOR,
          "video": {"fps": VIDEO_FPS, "max_width": VIDEO_MAX_WIDTH, "bitrate": VIDEO_BITRATE}}
  return {"data": traces, "layout": layout}, meta, len(quakes)

# 3Dの図の HTML のファイル名(サーバーに置けるよう、英数字と _ - だけにする)
#   有珠山付近の地震の一覧は usu_quakes_3d.html。日本語などを含む題名は、使える文字に、題名から作った番号を足す(別の一覧と重ならないように)
def html_file_name(title):
  if USU_LIST_TITLE in title:
    return f"{USU_LIST_FILE}_3d.html"
  slug = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_-")
  if slug == title:
    return f"{slug}_3d.html"
  code = f"{zlib.crc32(title.encode('utf-8')):08x}"
  return f"quakes_{slug}_{code}_3d.html" if slug else f"quakes_{code}_3d.html"

# HTML を書き出す
#   有珠山付近の地震の一覧で、同じフォルダに拡大できる波形のページ(WAVE_PAGE_FILE)があれば、そこへのリンクを出す
#   戻り値 ... 描いた震源の数
def write_3d_view(path, items, stations, title, model=None):
  path = Path(path)
  fig, meta, count = figure(items, stations, title, model, html_dir=path.parent.resolve())
  has_page = USU_LIST_TITLE in title and (path.parent / WAVE_PAGE_FILE).exists()
  meta.update(wave_page=WAVE_PAGE_FILE if has_page else "", wave_window=WAVE_PAGE_WINDOW)
  path.parent.mkdir(parents=True, exist_ok=True)
  page = PAGE.format(title=html.escape(title), plotly_url=PLOTLY_URL, muxer_url=MP4_MUXER_URL,
                     fig=json.dumps(fig, ensure_ascii=False, separators=(",", ":")),
                     meta=json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
  path.write_text(page, encoding="utf-8")
  return count

# ページ: 上に切り替えの欄、その下に3Dの図。地震を1つ選ぶと、時刻を図に出し、波形の画像を右下に重ねる
PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} (3D)</title>
<script src="{plotly_url}"></script>
<script src="{muxer_url}"></script>
<style>
html, body {{ margin: 0; height: 100%; background: white; color: #222; font-family: sans-serif; font-size: 13px;
              overflow: hidden; }}
body {{ display: flex; flex-direction: column; }}
#bar {{ display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; padding: 6px 10px;
        border-bottom: 1px solid #ddd; background: #fafafa; }}
#bar label {{ white-space: nowrap; }}
#bar .group {{ display: inline-flex; flex-wrap: wrap; gap: 6px; align-items: center; padding-left: 10px;
              border-left: 1px solid #ddd; }}
#bar button {{ font-size: 13px; white-space: nowrap; }}
#view {{ flex: 1; min-height: 0; position: relative; touch-action: none; }}
/* 波形の枠: はじめは小さく(右下)、画像を押すと大きく(もう一度押すと小さく)。枠は図の高さを超えないようにし、
   見出し(閉じるボタン)は枠の上に固定して、波形が長くても見切れないようにする */
#wave {{ position: absolute; right: 8px; bottom: 28px; width: 28%; min-width: 220px; max-height: calc(100% - 40px);
         background: white; border: 1px solid #bbb; box-shadow: 0 2px 8px rgba(0,0,0,0.2); display: none;
         overflow: auto; z-index: 10; touch-action: pan-y; }}
#wave.large {{ width: 70%; max-width: 1100px; }}
#wave header {{ position: sticky; top: 0; display: flex; justify-content: space-between; align-items: center;
                padding: 3px 6px; background: #f0f0f0; font-size: 12px; z-index: 1; }}
#wave img {{ width: 100%; display: block; cursor: zoom-in; }}
#wave.large img {{ cursor: zoom-out; }}
#wave button {{ border: none; background: none; font-size: 15px; cursor: pointer; }}
#waveinfo {{ position: absolute; top: 6px; left: 50%; transform: translateX(-50%); display: none; z-index: 5;
             pointer-events: none; padding: 4px 14px; text-align: center; white-space: nowrap;
             background: rgba(255,255,255,0.85); border-radius: 6px; }}
#waveinfo .t {{ font-size: 22px; font-weight: 600; }}
#waveinfo .s {{ font-size: 12px; color: #444; }}
#wave header a {{ margin-left: auto; margin-right: 8px; color: #1565c0; white-space: nowrap; }}
#wave .none {{ padding: 10px; color: #666; }}
#wave .times {{ padding: 4px 8px; font-size: 12px; line-height: 1.5; border-top: 1px solid #eee; }}
</style>
</head>
<body>
<div id="bar">
  <label>地表 <select id="surface"><option value="half">半透明</option><option value="opaque">不透明</option>
    <option value="hide">隠す</option></select></label>
  <label>側面 <select id="sides"><option value="half">半透明</option><option value="opaque">不透明</option>
    <option value="hide">隠す</option></select></label>
  <label>縦の倍率 <select id="exag"></select></label>
  <label><input type="checkbox" id="numbers" checked> 番号</label>
  <label><input type="checkbox" id="legend"> 凡例</label>
  <label>地震(通り道) <select id="quake"><option value="none">表示しない</option>
    <option value="all">すべての地震</option></select></label>
  <label id="wavekindbox" style="display:none">波形 <select id="wavekind"><option value="image">画像</option>
    <option value="page">拡大できる波形(別のタブ)</option></select></label>
  <button id="wavebtn">波形を表示</button>
  <span class="group"><button id="play">▶ 波を動かす</button>
    <label><select id="slow"></select></label>
    <label><input type="checkbox" id="orbit"> 回しながら</label>
    <button id="record">● 動画に保存</button></span>
  <span style="color:#777">(震源を押しても選べます)</span>
</div>
<div id="view"><div id="waveinfo"><div class="s" id="infotitle"></div><div class="t" id="infotime"></div>
  <div class="s"><span id="infop">●</span> P波 <span id="infos">●</span> S波</div></div><div id="wave"><header><span id="wavetitle"></span><a id="wavelink" style="display:none">拡大できる波形 ↗</a><button id="waveclose" title="閉じる">×</button></header>
  <div id="wavebody"></div></div></div>
<script>
const fig = {fig};
const meta = {meta};
const gd = document.getElementById("view");
const NARROW_WIDTH = 700;
// 幅の狭い画面(スマートフォン)では、はじめは凡例と速度の色の帯を隠して、図を広く使う(上の「凡例」で出せる)
const showLegendAtStart = window.innerWidth >= NARROW_WIDTH;
fig.layout.showlegend = showLegendAtStart;
fig.data[meta.sides[0]].showscale = showLegendAtStart;
if (!showLegendAtStart) fig.layout.annotations[0].text = meta.attribution;   // 長い説明は入らないので、出典だけにする
Plotly.newPlot(gd, fig.data, fig.layout, {{responsive: true, displaylogo: false}});
const $ = (id) => document.getElementById(id);
$("legend").checked = showLegendAtStart;
$("legend").onchange = (e) => {{
  Plotly.update(gd, {{showscale: e.target.checked}}, {{showlegend: e.target.checked, "scene.camera": currentCamera()}},
                [meta.sides[0]]);
}};
meta.exaggerations.forEach((e, i) => $("exag").add(new Option(e.label, i)));
meta.quakes.forEach((q, i) => $("quake").add(new Option(q.short, i)));
$("surface").onchange = (e) => {{
  const v = e.target.value;
  Plotly.restyle(gd, v === "hide" ? {{visible: false}} : {{visible: true, opacity: v === "half" ? meta.opacity : 1}},
                 [meta.terrain]);
}};
$("sides").onchange = (e) => {{
  const v = e.target.value;
  Plotly.restyle(gd, v === "hide" ? {{visible: false}} : {{visible: true, opacity: v === "half" ? meta.side_half : 1}},
                 meta.sides);
}};
// 縦の倍率: 全体の倍率は縦横比で、海面より上だけの倍率は高さのデータ(z が正のところ)を伸ばして表す
//   目盛りは本当の高さで出す(伸ばしたときは、目盛りの位置を伸ばした高さにする)
const origZ = gd.data.map((t) => (t.z ? JSON.parse(JSON.stringify(t.z)) : null));
const zIdx = origZ.map((z, i) => (z ? i : -1)).filter((i) => i >= 0);
const stretch = (z, a) => (Array.isArray(z) ? z.map((v) => stretch(v, a)) : (z !== null && z > 0 ? z * a : z));
let curAbove = 1;
$("exag").onchange = (e) => {{
  const o = meta.exaggerations[parseInt(e.target.value)];
  if (o.above !== curAbove) {{
    Plotly.restyle(gd, {{z: zIdx.map((i) => stretch(origZ[i], o.above))}}, zIdx);
    curAbove = o.above;
  }}
  const zr = ((meta.top * o.above + meta.bottom) * o.ex) / meta.span;
  const ticks = [];
  for (let v = -Math.floor(meta.bottom); v <= meta.top + 1e-9; v += 0.5) ticks.push(v);
  const axis = o.above === 1 ? {{tickmode: "auto"}}
    : {{tickmode: "array", tickvals: ticks.map((v) => (v > 0 ? v * o.above : v)), ticktext: ticks.map((v) => v.toFixed(1))}};
  Plotly.relayout(gd, {{"scene.aspectratio": {{x: meta.aspect.x, y: meta.aspect.y, z: zr}},
                       "scene.zaxis.tickmode": axis.tickmode, "scene.zaxis.tickvals": axis.tickvals || null,
                       "scene.zaxis.ticktext": axis.ticktext || null,
                       "scene.zaxis.title.text": o.above === 1 ? "高さ [km]" : "高さ [km](海面より上は " + o.above + " 倍)"}});
}};
$("numbers").onchange = (e) => {{
  if (meta.number_trace !== null) Plotly.restyle(gd, {{visible: e.target.checked}}, [meta.number_trace]);
}};
// 震源を押すと、その地震を選ぶ
//   3Dの図では、ボタンを押している間は描き直すたびに plotly_click が出る。ここで図を描き直すと、また plotly_click が出て
//   止まらなくなる(ブラウザが固まる)ので、選んでいる地震と同じなら何もせず、切り替えはイベントの外(setTimeout)で行う
let clickPending = false;
gd.on("plotly_click", (ev) => {{
  const pt = ev.points && ev.points[0];
  if (!pt || (pt.curveNumber !== meta.quake_trace && pt.curveNumber !== meta.number_trace)) return;
  const v = String(pt.pointNumber);
  if (clickPending || $("quake").value === v) return;
  clickPending = true;
  setTimeout(() => {{
    $("quake").value = v;
    $("quake").dispatchEvent(new Event("change"));
    setTimeout(() => {{ clickPending = false; }}, 300);
  }}, 0);
}});
// 波形の枠を開いているか(「波形を表示」で開き、× で閉じる。開いている間は、選んだ地震に合わせて中身を変える)
let waveOpen = false;
// 拡大できる波形のページ(あるときだけ「波形」で選べる。いつも同じタブで開く)
const wavePageUrl = (i) => meta.wave_page + "#" + meta.quakes[i].key;
if (meta.wave_page) {{
  $("wavekindbox").style.display = "";
  $("wavelink").style.display = "";
  $("wavelink").target = meta.wave_window;
}}
function showWave(i) {{
  const box = $("wave");
  if (i === null || !waveOpen) {{ box.style.display = "none"; return; }}
  const q = meta.quakes[i];
  $("wavetitle").textContent = q.short + " の波形(満点の拠点)と時刻";
  if (meta.wave_page) $("wavelink").href = wavePageUrl(i);
  $("wavebody").innerHTML = "";
  if (q.image) {{
    const img = new Image();
    img.src = q.image;
    img.alt = q.short + " の波形";
    img.title = "押すと大きく(もう一度押すと小さく)";
    img.onclick = () => box.classList.toggle("large");
    img.onerror = () => {{ $("wavebody").innerHTML = '<div class="none">画像を開けませんでした(' + q.image + ')</div>'; }};
    $("wavebody").appendChild(img);
  }} else {{
    $("wavebody").innerHTML = '<div class="none">この地震の波形の画像はありません</div>';
  }}
  const times = document.createElement("div");
  times.className = "times";
  times.innerHTML = q.lines.map((t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;")).join("<br>");
  $("wavebody").appendChild(times);
  box.style.display = "block";
}}
$("quake").onchange = (e) => {{
  const v = e.target.value;
  const one = v !== "none" && v !== "all" ? parseInt(v) : null;
  if (meta.rays.length) {{
    Plotly.restyle(gd, {{visible: meta.rays.map((_, i) => v === "all" || i === one)}}, meta.rays);
    Plotly.restyle(gd, {{visible: meta.labels.map((_, i) => i === one)}}, meta.labels);
    Plotly.restyle(gd, {{opacity: one === null ? 1 : meta.others_opacity}}, [meta.quake_trace]);
  }}
  showWave(one);
}};
$("wavebtn").onclick = () => {{
  const v = $("quake").value;
  const i = v !== "none" && v !== "all" ? parseInt(v) : null;
  if (i === null) {{ alert("「地震(通り道)」で地震を1つ選んでください(震源を押しても選べます)"); return; }}
  if (meta.wave_page && $("wavekind").value === "page") {{ window.open(wavePageUrl(i), meta.wave_window); return; }}
  waveOpen = !waveOpen;
  showWave(i);
  $("wavebtn").textContent = waveOpen ? "波形を隠す" : "波形を表示";
}};
$("waveclose").onclick = () => {{ waveOpen = false; $("wave").style.display = "none"; $("wavebtn").textContent = "波形を表示"; }};

// 地表と側面は押せないので、当たり判定の絵を描かない(視点を動かすたびの重い描き直しをなくす)
function surfacesNoPick() {{
  const sc = gd._fullLayout.scene && gd._fullLayout.scene._scene;
  if (!sc) return;
  [meta.terrain].concat(meta.sides).forEach((i) => {{
    const fd = gd._fullData[i], t = fd && sc.traces[fd.uid];
    if (t && t.surface) {{ t.surface.drawPick = null; t.surface.pick = () => null; }}
  }});
}}
gd.on("plotly_afterplot", surfacesNoPick);
surfacesNoPick();

// --- 波の動き: 選んだ地震の震源から、P波(青の点)・S波(橙の点)が各拠点へ通り道に沿って進む ---
//   時刻は深さで変わる速さから求めたもの(拠点に着く時刻は計算の値で、読み取った到着時刻とは少し違う)
//   図全体の描き直しは遅い(1コマ 150 ms ほど)ので、再生中は波の点と視点だけを直接更新する
const speedName = (v) => parseFloat(v) === 1 ? "実際の速さ" : "実際の " + v + " 倍遅く";
meta.slowdowns.forEach((v) => $("slow").add(new Option(speedName(v), v)));
$("slow").value = "10";
$("infop").style.color = meta.p_color;
$("infos").style.color = meta.s_color;
let playing = null, recording = null;
const glScene = () => gd._fullLayout.scene._scene;
// 今の視点(ドラッグで回したあとのもの)。gd.layout にはすぐ入らないことがあるので、図から直接読む
const currentCamera = () => glScene().getCamera();
function selectedQuake() {{
  const v = $("quake").value;
  return v !== "none" && v !== "all" ? parseInt(v) : null;
}}
function posAt(path, t) {{
  if (t <= 0) return path[0];
  for (let k = 1; k < path.length; k++) {{
    if (t <= path[k][3]) {{
      const a = path[k - 1], b = path[k], f = (t - a[3]) / (b[3] - a[3] || 1);
      return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
    }}
  }}
  return path[path.length - 1];
}}
function frameData(q, t) {{
  const pts = (list) => {{
    const p = list.map((path) => posAt(path, t));
    return [p.map((v) => v[0]), p.map((v) => v[1]), p.map((v) => stretch(v[2], curAbove))];
  }};
  const P = pts(q.waves.P), S = pts(q.waves.S);
  return {{x: [P[0], S[0]], y: [P[1], S[1]], z: [P[2], S[2]]}};
}}
const waveEnd = (q) => Math.max(0, ...q.waves.P.concat(q.waves.S).map((p) => p[p.length - 1][3])) + 0.3;
const infoTitle = (q) => q.label + "(" + speedName($("slow").value) + ")";
const infoTime = (t) => "発生から " + (Math.floor(t * 10 + 1e-6) / 10).toFixed(1) + " 秒";
// 1コマ分を描く(波の点、上の文字、「回しながら」の視点)。orbitSec は回し始めからの秒数
function drawWaves(run, t, orbitSec) {{
  const sc = glScene(), d = frameData(run.q, t);
  run.last = d;
  [0, 1].forEach((k) => {{
    const fd = gd._fullData[meta.waves[k]], obj = sc.traces[fd.uid];
    if (!obj) return;
    fd.x = d.x[k]; fd.y = d.y[k]; fd.z = d.z[k];
    obj.update(fd);
  }});
  $("infotitle").textContent = infoTitle(run.q);
  $("infotime").textContent = infoTime(t);
  if ($("orbit").checked) {{
    const a = run.a0 + orbitSec * 0.25, c = run.cam.center, u = run.cam.up;   // 1秒に約 14°
    sc.glplot.camera.lookAt([run.r0 * Math.cos(a), run.r0 * Math.sin(a), run.cam.eye.z], [c.x, c.y, c.z], [u.x, u.y, u.z]);
  }}
  sc.glplot.redraw();
}}
// 再生の準備(点を出し、今の視点を覚える)
async function prepareWaves(q) {{
  await Plotly.update(gd, Object.assign({{visible: true}}, frameData(q, 0)), {{"scene.camera": currentCamera()}}, meta.waves);
  const cam = currentCamera();
  $("waveinfo").style.display = "block";
  return {{q, cam, r0: Math.hypot(cam.eye.x, cam.eye.y), a0: Math.atan2(cam.eye.y, cam.eye.x), tEnd: waveEnd(q),
          slow: parseFloat($("slow").value)}};
}}
// 最後の様子を図のデータにも入れ、今の視点で1回描き直す(この後の操作で、点や視点が戻らないように)
function syncWaves(run) {{
  return Plotly.update(gd, run.last || {{}}, {{"scene.camera": currentCamera()}}, meta.waves);
}}
function stopWaves() {{
  const was = playing;
  if (was) cancelAnimationFrame(was.raf);
  playing = null;
  $("play").textContent = "▶ 波を動かす";
  if (was && was.q) syncWaves(was);
}}
function needQuake() {{
  const i = selectedQuake();
  if (i === null) alert("「地震(通り道)」で地震を1つ選んでください(震源を押しても選べます)");
  return i;
}}
async function playWaves() {{
  const i = needQuake();
  if (i === null) return;
  stopWaves();
  const me = {{}};
  playing = me;
  $("play").textContent = "■ 止める";
  Object.assign(me, await prepareWaves(meta.quakes[i]));
  if (playing !== me) return;
  let start = null;
  const step = (now) => {{
    if (playing !== me) return;
    if (start === null) start = now;
    const sec = (now - start) / 1000, t = Math.min(sec / me.slow, me.tEnd);
    drawWaves(me, t, sec);
    if (t < me.tEnd) me.raf = requestAnimationFrame(step); else stopWaves();
  }};
  me.raf = requestAnimationFrame(step);
}}
$("play").onclick = () => {{ if (recording) return; if (playing) stopWaves(); else playWaves(); }};
// 地震を選び直したら、動きの点と文字は消す
$("quake").addEventListener("change", () => {{
  stopWaves();
  $("waveinfo").style.display = "none";
  Plotly.restyle(gd, {{visible: false}}, meta.waves);
}});

// --- スマートフォンの操作: 2本指で拡大・縮小(ピンチ)と移動。1本指の回転は plotly のまま ---
//   plotly の3Dの図は最初の指しか見ないので、2本目の指が触れたら指の動きを plotly に渡さず、ここで視点を動かす
//   指を離したら視点を図の設定に保存する(plotly はタッチでは保存せず、上のボタンを押すと最初の視点に戻っていたため)
const MIN_DISTANCE = 0.15, MAX_DISTANCE = 8;
const vec = {{
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  add: (a, b, k) => [a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  len: (a) => Math.hypot(a[0], a[1], a[2]),
  unit: (a) => {{ const n = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / n, a[1] / n, a[2] / n]; }},
}};
const xyz = (p) => [p.x, p.y, p.z];
const fingers = (e) => {{
  const a = e.touches[0], b = e.touches[1];
  return {{dist: Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY),
          x: (a.clientX + b.clientX) / 2, y: (a.clientY + b.clientY) / 2}};
}};
let pinch = null, pinched = false;
gd.addEventListener("touchstart", (e) => {{
  if (e.target.tagName !== "CANVAS" || e.touches.length < 2) return;
  e.stopPropagation();
  e.preventDefault();
  const c = currentCamera();
  pinch = {{f: fingers(e), eye: xyz(c.eye), center: xyz(c.center), up: xyz(c.up)}};
  pinched = true;
}}, {{capture: true, passive: false}});
gd.addEventListener("touchmove", (e) => {{
  if (!pinched) return;     // 1本指の回転は plotly に任せる
  e.stopPropagation();      // 2本指のあとは、指がすべて離れるまで回転させない(視点が飛ばないように)
  e.preventDefault();
  if (!pinch || e.touches.length < 2) return;
  const f = fingers(e), sc = glScene(), rel = vec.sub(pinch.eye, pinch.center), d0 = vec.len(rel);
  const d = Math.min(MAX_DISTANCE, Math.max(MIN_DISTANCE, d0 * pinch.f.dist / Math.max(f.dist, 1)));
  // 2本指の移動ぶん、見ている場所をずらす(画面上で指についてくる量)
  const forward = vec.unit(vec.sub(pinch.center, pinch.eye)), right = vec.unit(vec.cross(forward, pinch.up));
  const up = vec.cross(right, forward);
  const perPixel = (2 * d * Math.tan((sc.glplot.fovy || Math.PI / 4) / 2)) / gd.clientHeight;
  let center = vec.add(pinch.center, right, -(f.x - pinch.f.x) * perPixel);
  center = vec.add(center, up, (f.y - pinch.f.y) * perPixel);
  sc.glplot.camera.lookAt(vec.add(center, rel, d / d0), center, pinch.up);
  sc.glplot.redraw();
}}, {{capture: true, passive: false}});
gd.addEventListener("touchend", (e) => {{
  if (e.touches.length >= 2 && pinched) {{   // 3本から2本になったときなどは、今の位置から続ける
    const c = currentCamera();
    pinch = {{f: fingers(e), eye: xyz(c.eye), center: xyz(c.center), up: xyz(c.up)}};
    return;
  }}
  pinch = null;
  if (e.touches.length) {{ if (pinched) e.stopPropagation(); return; }}
  pinched = false;          // 全部の指が離れた(plotly にも伝えて、回転を終わらせる)
  if (e.target.tagName === "CANVAS") Plotly.relayout(gd, {{"scene.camera": currentCamera()}});
}}, {{capture: true}});
gd.addEventListener("gesturestart", (e) => e.preventDefault());   // iPhone の画面全体の拡大を止める

// --- 動画に保存 ---
//   1コマずつ描いて、コマの番号どおりの時刻で動画に書き込む(処理が間に合わなくてもコマは落ちない。実時間より遅くなるだけ)
//   図の絵に、経過時間などの文字を重ねた絵を動画にする。録画中は、カーソルに付く線や枠を出さない
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 録画の間は、図の当たり判定を止める(カーソルの下の点の線や枠が出なくなり、視点を動かすときの描き直しも軽くなる)
//   戻り値は元に戻す関数
function freezePicking() {{
  const sc = glScene(), saved = sc.glplot.objects.map((o) => [o, o.drawPick, o.pick]);
  saved.forEach(([o]) => {{ o.drawPick = null; o.pick = () => null; }});
  const sel = sc.glplot.selection;
  if (sel.object && sel.object.highlight) sel.object.highlight(null);
  sel.object = null;
  return () => saved.forEach(([o, d, k]) => {{ o.drawPick = d; o.pick = k; }});
}}
const nextPaint = () => new Promise((r) => {{ requestAnimationFrame(r); setTimeout(r, 100); }});
function drawOverlay(g, w, h, run, t) {{
  const u = h / 1000, cx = w / 2, title = infoTitle(run.q), time = infoTime(t);
  const f1 = Math.round(20 * u), f2 = Math.round(44 * u), f3 = Math.round(20 * u);
  const family = '"Hiragino Sans", "Yu Gothic", "Noto Sans CJK JP", sans-serif';
  g.font = f2 + "px " + family;
  g.textAlign = "center"; g.textBaseline = "alphabetic";
  const tw = g.measureText("発生から 00.0 秒").width;
  g.font = f1 + "px " + family;
  const bw = Math.max(tw, g.measureText(title).width) + 40 * u, top = 12 * u, bh = f1 + f2 + f3 + 34 * u;
  g.fillStyle = "rgba(255,255,255,0.85)";
  g.fillRect(cx - bw / 2, top, bw, bh);
  g.fillStyle = "#444";
  g.fillText(title, cx, top + f1 + 4 * u);
  g.fillStyle = "#222";
  g.font = "600 " + f2 + "px " + family;
  g.fillText(time, cx, top + f1 + f2 + 8 * u);
  g.font = f3 + "px " + family;
  const y = top + f1 + f2 + f3 + 16 * u;
  g.fillStyle = meta.p_color; g.fillText("●", cx - 62 * u, y);
  g.fillStyle = "#444"; g.fillText("P波", cx - 26 * u, y);
  g.fillStyle = meta.s_color; g.fillText("●", cx + 22 * u, y);
  g.fillStyle = "#444"; g.fillText("S波", cx + 58 * u, y);
}}
// MP4 に書き込む入れ物(WebCodecs)。使えないブラウザでは MediaRecorder を使う
async function openVideo(canvas, w, h) {{
  const fps = meta.video.fps, bitrate = meta.video.bitrate;
  if (window.VideoEncoder && window.Mp4Muxer) {{
    for (const codec of ["avc1.640033", "avc1.4d0033", "avc1.42e033"]) {{
      const config = {{codec, width: w, height: h, bitrate, framerate: fps, latencyMode: "quality"}};
      const ok = await VideoEncoder.isConfigSupported(config).catch(() => null);
      if (!ok || !ok.supported) continue;
      const target = new Mp4Muxer.ArrayBufferTarget();
      const muxer = new Mp4Muxer.Muxer({{target, video: {{codec: "avc", width: w, height: h, frameRate: fps}}, fastStart: "in-memory"}});
      let failure = null;
      const enc = new VideoEncoder({{output: (c, m) => muxer.addVideoChunk(c, m), error: (e) => {{ failure = e; }}}});
      enc.configure(config);
      return {{
        ext: "mp4",
        add: async (n) => {{
          if (failure) throw failure;
          const frame = new VideoFrame(canvas, {{timestamp: Math.round(n * 1e6 / fps), duration: Math.round(1e6 / fps)}});
          enc.encode(frame, {{keyFrame: n % (fps * 2) === 0}});
          frame.close();
          while (enc.encodeQueueSize > 4) await sleep(4);
        }},
        finish: async () => {{
          await enc.flush(); enc.close(); muxer.finalize();
          if (failure) throw failure;
          return new Blob([target.buffer], {{type: "video/mp4"}});
        }},
        cancel: () => {{ try {{ enc.close(); }} catch (e) {{}} }},
      }};
    }}
  }}
  if (!window.MediaRecorder) return null;
  const type = ["video/mp4", "video/webm;codecs=vp9", "video/webm"].find((t) => MediaRecorder.isTypeSupported(t));
  const stream = canvas.captureStream(0), track = stream.getVideoTracks()[0];
  const rec = new MediaRecorder(stream, {{mimeType: type, videoBitsPerSecond: bitrate}}), chunks = [];
  rec.ondataavailable = (e) => {{ if (e.data.size) chunks.push(e.data); }};
  rec.start();
  return {{
    ext: type.includes("mp4") ? "mp4" : "webm",
    add: async () => {{ track.requestFrame(); }},
    finish: () => new Promise((res) => {{
      rec.onstop = () => {{ stream.getTracks().forEach((t) => t.stop()); res(new Blob(chunks, {{type}})); }};
      rec.stop();
    }}),
    cancel: () => {{ rec.onstop = () => stream.getTracks().forEach((t) => t.stop()); rec.stop(); }},
  }};
}}
// 録画する。戻り値は動画の Blob(中止・失敗のときは null)
async function recordWaves() {{
  const i = needQuake();
  if (i === null) return null;
  stopWaves();
  const gl = [...gd.querySelectorAll("canvas")].sort((a, b) => b.width * b.height - a.width * a.height)[0];
  const ratio = Math.min(1, meta.video.max_width / gl.width);
  const w = Math.floor(gl.width * ratio / 2) * 2, h = Math.floor(gl.height * ratio / 2) * 2;
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const g = canvas.getContext("2d");
  const me = {{cancel: false}};
  recording = me;
  let run = null, video = null, blob = null, unfreeze = null;
  try {{
    run = await prepareWaves(meta.quakes[i]);
    unfreeze = freezePicking();
    video = await openVideo(canvas, w, h);
    if (!video) {{ alert("このブラウザでは動画に保存できません"); return null; }}
    const fps = meta.video.fps, total = Math.ceil(run.tEnd * run.slow * fps) + fps;   // 最後に1秒止める
    const start = performance.now();
    for (let n = 0; n < total && !me.cancel; n++) {{
      // 実時間より速くは進めない(録画中も図を回せるように)。遅れたときは待たずに次のコマを描く
      do {{ await nextPaint(); }} while (performance.now() - start < (n * 1000) / fps - 8);
      const sec = n / fps, t = Math.min(sec / run.slow, run.tEnd);
      drawWaves(run, t, sec);       // 図を描いた直後に絵を写す(時間をおくと図の絵は消えることがある)
      g.fillStyle = "white";
      g.fillRect(0, 0, w, h);
      g.drawImage(gl, 0, 0, w, h);
      drawOverlay(g, w, h, run, t);
      await video.add(n);
      $("record").textContent = "■ 録画を中止(" + Math.round((n + 1) * 100 / total) + "%)";
    }}
    if (me.cancel) video.cancel(); else {{ $("record").textContent = "動画を仕上げています…"; blob = await video.finish(); }}
  }} catch (e) {{
    console.error(e);
    alert("動画に保存できませんでした: " + e.message);
    if (video) video.cancel();
    blob = null;
  }} finally {{
    recording = null;
    $("record").textContent = "● 動画に保存";
    if (unfreeze) unfreeze();
    if (run) await syncWaves(run);
  }}
  if (blob) blob.fileName = meta.quakes[i].short.replace(/[ :.]/g, "_") + "_波の動き." + video.ext;
  return blob;
}}
$("record").onclick = async () => {{
  if (recording) {{ recording.cancel = true; return; }}
  const blob = await recordWaves();
  if (!blob) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = blob.fileName;
  a.click();
}};
// 上の欄に選択肢を入れると欄の高さが変わるので、図の大きさを合わせ直す(図の下の端が画面の外に出ないように)
Plotly.Plots.resize(gd);
</script>
</body>
</html>
"""


# --- 単体で実行するとき: 有珠山付近の地震の一覧 ---

def load_hour_from_zips(dt):
  import tempfile
  from obspy import Stream, read
  import app_paths
  import win_pipeline
  end = dt + timedelta(hours=1)
  with tempfile.TemporaryDirectory(prefix="UsuMantenViewer_3d_") as work:
    work = Path(work)
    zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(dt, end))
    if win_pipeline.zips_to_sacs(zips, dt, end, work, app_paths.TOOLS_DIR, app_paths.ETC_DIR) == 0:
      return None
    st = Stream()
    for p in win_pipeline.find_sacs(work):
      st += read(str(p))
    return st

def main():
  import tempfile
  import app_paths
  import quake_list
  import quake_locate
  from hypocenter import load_stations
  parser = argparse.ArgumentParser(description="有珠山付近の地震の震源を、ブラウザで見る3Dの図に書き出す")
  parser.add_argument("-o", "--output", help="出力する HTML(省略すると batch_plots/quakes/locate/usu_quakes_3d.html)")
  parser.add_argument("--no-images", action="store_true",
                      help="満点の拠点だけの波形の画像を作り直さない(前に作ったものがあればそれを使う)")
  args = parser.parse_args()
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  with tempfile.TemporaryDirectory() as d:
    csv_path = Path(d) / "usu.csv"
    quake_list.write_usu_candidates(csv_path)
    candidates = quake_locate.read_candidates(csv_path)
  def progress(i, n, cand, r, err):
    if err:
      print(f"  {cand['start']:%Y-%m-%d %H:%M:%S}: 震源を求められませんでした({err})")
  items, computed = quake_locate.locate_all_cached(candidates, load_hour_from_zips, stations, proj, progress)
  print(f"震源: {len(items)}件(新しく計算したもの {computed}件)")
  if not args.no_images:
    import matplotlib
    matplotlib.use("Agg")
    from plot_common import setup_japanese_font
    setup_japanese_font()
    n_img = save_manten_images(items, stations, load_hour_from_zips, log=lambda m: None)
    print(f"満点の拠点だけの波形の画像: {n_img}件({manten_image_dir()})")
  out = Path(args.output) if args.output else quake_locate.OUTPUT_DIR / html_file_name(USU_LIST_TITLE)
  n = write_3d_view(out, items, stations, USU_LIST_TITLE)
  print(f"3Dの図を書き出しました(震源 {n}件): {out}")

if __name__ == "__main__":
  main()
