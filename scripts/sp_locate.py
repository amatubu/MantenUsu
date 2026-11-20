# sp_locate.py
# 「S-P 時間で震源をさがす」教材の HTML(ファイル1つ)を書き出す(TODO 42)
#   満点の4拠点の波形で P波・S波の到着を読むと、S-P 時間から求めた距離の円が地図に描かれ、円が交わる所が震源になる。
#   読み取りをずらすと、円と震源がその場で動く。最後に深さを変えて「深さはどうやってわかるの?」につなげる
#   入れる地震と説明は etc/sp_locate.csv(1行が1問)
#     問題, 地震(有珠山リストの候補の開始時刻 YYYYmmdd_HHMMSS), 説明, 正解の場所(「緯度 経度」。発破など。空でもよい), 正解の名前
#   画面の進め方(ステップ式): 1 P・S を読む(円が出る) → 2 震源(✕)と大森係数 k → 3 答え合わせ → 4 深さ
#     縦長の画面は上に地図・下に1拠点の3成分、横長の画面は左に地図・右に4拠点の3成分を並べる
#     地図・波形は「大きく」で画面いっぱいに広げられる(もう一度押すか Esc で元に戻す)
#   S波を読む助け(S波は P波のあとの揺れにまぎれやすく、計算で付けた目印は当たらないことが多いので、読む人を助ける形にする)
#     振幅をそろえる(拠点の3成分を同じ物差しで描く)、ヒント(アプリの S の前後 0.3 秒を塗る。使った拠点は表に印)、
#     S の見込み(✕ と k から逆に計算した S の時刻 = P + ✕ までの距離 ÷ k の点線)、P を置いたら P のまわりに拡大
#   距離 = k × S-P 時間。k の初期値 3.7 km/s は、有珠山の地震 26 件のアプリの震源までの距離と S-P 時間から求めた値(2026-11-20)
#   震源(✕)は、各拠点の円(震源からの距離)とのずれの二乗和がいちばん小さい点(ブラウザで格子を探す)
#   正解: アプリの震源(quake_locate.locate_all_cached)、気象庁の震源(ある地震だけ)、正解の場所(CSV)、アプリの P・S の読み取り
#   地図は地理院タイル(淡色地図)をつないだ1枚の画像を入れる(インターネットが無くても開ける。出典を表示する)
#   波形の符号化・フィルタは quake_wave_view.py と同じ。入れるのは満点の拠点の波形だけ(再配布できない外部の観測点の波形は入れない)
#
#     python3 scripts/sp_locate.py [-o 出力の HTML]
#   省略すると batch_plots/quiz/sp_locate.html に書き出す
import argparse
import base64
import csv
import html
import io
import tempfile
from datetime import timedelta
from pathlib import Path

import numpy as np

import app_paths
import gsi_tiles
import station_names
from hypocenter import LOCAL_KINDS, PICK_BAND, LocalProjection, depth_for_plot, depth_short, load_stations
from quake_wave_view import (COMPONENTS, SAMPLE_RATE, count_steps, filter_sections, load_stream, origin_time,
                             pick_lines, script_json, trace_samples)

PROBLEM_FILE = "sp_locate.csv"
OUTPUT_DIR = app_paths.BATCH_PLOTS_DIR / "quiz"
OUTPUT_FILE = "sp_locate.html"
STATIONS = ["Bamba", "Hokudai", "Mimatsu", "Nizaemn"]
TITLE = "S-P 時間で震源をさがす"

BEFORE_SECONDS = 2.0   # 発生時刻の何秒前から入れるか
AFTER_SECONDS = 8.0    # 発生時刻の何秒後まで入れるか(S-P は長くても 2.5 秒なので短くてよい)

# 大森係数 k [km/s]: 初期値と、スライダーの範囲
OMORI_K = 3.7
OMORI_K_RANGE = (3.0, 6.0)
# 説明に出す、速度から求めた k の例(アプリの既定の Vp と vp/vs)
EXAMPLE_VP, EXAMPLE_VP_VS = 3.0, 1.73
DEPTH_MAX = 6.0        # 深さのスライダーの最大 [km]

# 地図: 拠点を囲む範囲に足す余白 [km] と、タイル(淡色地図)のズームレベル
MAP_MARGIN_KM = (3.0, 2.0)   # (東西, 南北)
MAP_ZOOM = 14
MAP_LAYER = "pale"
MAP_URL = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"
MAP_ATTRIBUTION = "出典: 地理院タイル(淡色地図)"
MAP_JPEG_QUALITY = 82
SUMMIT = (42.5436, 140.8394)   # 有珠山(大有珠)。地図の目印


# --- 問題 ---

# etc/sp_locate.csv を読む。戻り値 ... [{"no", "key", "note", "site": (緯度, 経度) または None, "site_name"}, ...]
def read_problems(path=None):
  path = Path(path) if path else app_paths.ETC_DIR / PROBLEM_FILE
  problems = []
  with open(path, encoding="utf-8-sig", newline="") as f:
    for row in csv.DictReader(f):
      key = (row.get("地震") or "").strip()
      if not key or key.startswith("#"):
        continue
      site = None
      text = (row.get("正解の場所") or "").strip()
      if text:
        lat, lon = (float(v) for v in text.replace(",", " ").split())
        site = (lat, lon)
      problems.append({"no": int(row["問題"]), "key": key, "note": (row.get("説明") or "").strip(),
                       "site": site, "site_name": (row.get("正解の名前") or "").strip() or "正解の場所"})
  return problems


# --- 地図 ---

# 地図に入れる範囲(緯度・経度): 拠点と目印を囲み、余白を足す
def map_bounds(stations, proj, extra=()):
  pts = [proj.to_xy(*stations[nm][:2]) for nm in STATIONS if nm in stations]
  pts += [proj.to_xy(*p) for p in extra]
  xs, ys = [p[0] for p in pts], [p[1] for p in pts]
  south, west = proj.to_latlon(min(xs) - MAP_MARGIN_KM[0], min(ys) - MAP_MARGIN_KM[1])
  north, east = proj.to_latlon(max(xs) + MAP_MARGIN_KM[0], max(ys) + MAP_MARGIN_KM[1])
  return south, north, west, east

# タイル1枚の画像(RGB の uint8 配列)。取れなければ None
def _tile(z, x, y):
  from PIL import Image
  data = gsi_tiles._tile_bytes(MAP_LAYER, MAP_URL, z, x, y)
  if not data:
    return None
  return np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))

# 範囲のタイルをつないで切り抜いた画像 (RGB の配列, 実際の範囲 (南, 北, 西, 東))。1枚も取れなければ None
def stitch_tiles(south, north, west, east, z=MAP_ZOOM, tile=_tile):
  fx0, fy0 = gsi_tiles.lonlat_to_tile(west, north, z)
  fx1, fy1 = gsi_tiles.lonlat_to_tile(east, south, z)
  tx0, ty0, tx1, ty1 = int(fx0), int(fy0), int(fx1), int(fy1)
  size = 256
  img = np.full(((ty1 - ty0 + 1) * size, (tx1 - tx0 + 1) * size, 3), 255, dtype=np.uint8)
  got = 0
  for tx in range(tx0, tx1 + 1):
    for ty in range(ty0, ty1 + 1):
      t = tile(z, tx, ty)
      if t is None:
        continue
      img[(ty - ty0) * size:(ty - ty0 + 1) * size, (tx - tx0) * size:(tx - tx0 + 1) * size] = t[:size, :size, :3]
      got += 1
  if not got:
    return None
  px0, py0 = int(round((fx0 - tx0) * size)), int(round((fy0 - ty0) * size))
  px1, py1 = int(round((fx1 - tx0) * size)), int(round((fy1 - ty0) * size))
  west2, north2 = gsi_tiles.tile_to_lonlat(tx0 + px0 / size, ty0 + py0 / size, z)
  east2, south2 = gsi_tiles.tile_to_lonlat(tx0 + px1 / size, ty0 + py1 / size, z)
  return img[py0:py1, px0:px1], (south2, north2, west2, east2)

# HTML に入れる地図: {"src": data URI, "x0", "x1", "y0", "y1"(画像の端の km), "credit"}。地図が無ければ src は ""
#   範囲が十数 km なので、Webメルカトルの画像を km の座標にそのまま当てはめる(ずれは数 m)
def map_data(bounds, proj, stitch=stitch_tiles):
  from PIL import Image
  result = stitch(*bounds)
  if result is None:
    img, (south, north, west, east) = None, bounds
  else:
    img, (south, north, west, east) = result
  x0, y0 = proj.to_xy(south, west)
  x1, y1 = proj.to_xy(north, east)
  src = ""
  if img is not None:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=MAP_JPEG_QUALITY, optimize=True)
    src = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
  return {"src": src, "x0": round(x0, 4), "x1": round(x1, 4), "y0": round(y0, 4), "y1": round(y1, 4),
          "credit": MAP_ATTRIBUTION}


# --- 地震ごとのデータ ---

# 位置を地図の km にする
def xy_of(proj, lat, lon):
  x, y = proj.to_xy(lat, lon)
  return {"x": round(float(x), 3), "y": round(float(y), 3)}

# 波形の最初の点の時刻(発生時刻の BEFORE_SECONDS 前を、サンプルの刻みに切り下げたもの)
def window_start(origin, rate=SAMPLE_RATE):
  t = origin - timedelta(seconds=BEFORE_SECONDS)
  tick = 1_000_000 // rate
  return t - timedelta(microseconds=t.microsecond % tick)

# 気象庁の震源 {"x", "y", "z"} または None(候補の "jma" に緯度・経度が無ければ None)
def jma_hypocenter(cand, proj):
  q = cand.get("jma") or {}
  if q.get("緯度") is None or q.get("経度") is None:
    return None
  item = xy_of(proj, q["緯度"], q["経度"])
  item["z"] = q.get("深さ[km]")
  return item

# 地震1件: (画面に出す情報, {内部の拠点名: {成分: 波形}})
def event_data(prob, cand, r, st, stations, steps, proj):
  import pick_store
  from quake_locate import candidate_window, event_type
  origin = origin_time(r)
  start = window_start(origin)
  n = int(round((BEFORE_SECONDS + AFTER_SECONDS) * SAMPLE_RATE))
  waves = {}
  for name in STATIONS:
    if name not in stations:
      continue
    comps = {}
    for comp in COMPONENTS:
      item = trace_samples(st, name, comp, start, n, steps.get((name, comp), 1.0))
      if item:
        comps[comp] = item
    if comps:
      waves[name] = comps
  p_manual, s_manual = set(), set()
  _path, record = pick_store.find_record(*candidate_window(cand))
  if record is not None:
    _p, _s, p_manual, s_manual = pick_store.record_seconds(record, r["ref"])
  t0 = (start - origin).total_seconds()
  # アプリの読み取り(波形の最初からの秒)。満点の拠点で、波形があるものだけ
  picks = {}
  for name, item in pick_lines(r, set(waves), origin, p_manual, s_manual).items():
    picks[name] = {k: (round(v - t0, 3) if k in ("p", "s") else v) for k, v in item.items()}
  app = xy_of(proj, r["lat"], r["lon"])
  app.update({"z": round(float(depth_for_plot(r["loc"])), 2), "zText": depth_short(r["loc"])})
  q = cand.get("jma") or {}
  mag = f"{q['M']:.1f}" if q.get("M") is not None else ""
  site = None
  if prob["site"]:
    site = xy_of(proj, *prob["site"])
    site["name"] = prob["site_name"]
  info = {"no": prob["no"], "key": prob["key"], "note": prob["note"],
          "time": f"{origin:%Y-%m-%d %H:%M}", "type": event_type(cand), "m": mag,
          "n": n, "originAt": round(-t0, 3), "picks": picks, "app": app,
          "jma": jma_hypocenter(cand, proj), "site": site}
  return info, waves


# --- HTML ---

# 拠点の情報: [{"id", "label", "x", "y", "h"(標高 km)}, ...]
def station_items(stations, proj):
  items = []
  for nm in STATIONS:
    if nm in stations:
      lat, lon, elev = stations[nm][:3]
      item = xy_of(proj, lat, lon)
      item.update({"id": nm, "label": station_names.short(nm), "h": round(elev / 1000, 4)})
      items.append(item)
  return items

# 速度から求めた大森係数 k = Vp・Vs / (Vp − Vs)
def omori_k(vp, vp_vs):
  vs = vp / vp_vs
  return vp * vs / (vp - vs)

def write_page(path, events, stations_info, map_info, summit):
  path = Path(path)
  meta = {"title": TITLE, "rate": SAMPLE_RATE, "comps": list(COMPONENTS),
          "filter": {"low": PICK_BAND[0], "high": PICK_BAND[1], "sections": filter_sections()},
          "k": OMORI_K, "kRange": list(OMORI_K_RANGE), "depthMax": DEPTH_MAX,
          "example": {"vp": EXAMPLE_VP, "vpvs": EXAMPLE_VP_VS, "k": round(omori_k(EXAMPLE_VP, EXAMPLE_VP_VS), 1)},
          "stations": stations_info, "map": map_info, "summit": summit,
          "events": [info for info, _waves in events]}
  blocks = "\n".join(f'<script type="application/json" id="w{info["no"]}">{script_json(waves)}</script>'
                     for info, waves in events)
  page = (PAGE.replace("__TITLE__", html.escape(TITLE))
          .replace("__META__", script_json(meta))
          .replace("__WAVES__", blocks))
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(page, encoding="utf-8")
  return path.stat().st_size


PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #ffffff; --panel: #f4f5f7; --ink: #1c2126; --muted: #68727d; --line: #dfe3e8; --grid: #eceff2;
  --accent: #1565c0; --p: #1565c0; --s: #d84e00; --app: #68727d; --wave: #1c2126; --gap: #e4e7eb;
  --Bamba: #7b55c7; --Hokudai: #1a6fc9; --Mimatsu: #11875a; --Nizaemn: #c9481c; --best: #111111;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1f2428; --ink: #e8ecef; --muted: #9aa5af; --line: #333a41; --grid: #24292e;
    --accent: #6cb0f5; --p: #6cb0f5; --s: #ff9a57; --app: #9aa5af; --wave: #e8ecef; --gap: #2b3137;
    --Bamba: #b39af0; --Hokudai: #6cb0f5; --Mimatsu: #4fd19a; --Nizaemn: #ff9a6e; --best: #ffffff;
  }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; -webkit-text-size-adjust: 100%; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
[hidden] { display: none !important; }
#list { max-width: 680px; margin: 0 auto; padding: calc(12px + env(safe-area-inset-top)) 16px calc(24px + env(safe-area-inset-bottom)); }
#list h1 { font-size: 21px; margin: 4px 0; }
.note { color: var(--muted); font-size: 13px; }
#events { list-style: none; margin: 12px 0; padding: 0; border-top: 1px solid var(--line); }
#events button { display: block; width: 100%; padding: 10px 0; text-align: left; border-bottom: 1px solid var(--line); }
.evHead { display: flex; gap: 8px; align-items: baseline; font-variant-numeric: tabular-nums; }
.evNo { color: var(--muted); font-size: 13px; width: 3.2em; flex: none; }
.evNote { display: block; color: var(--muted); font-size: 13px; margin-left: 3.2em; }
.badge { display: inline-block; padding: 0 6px; border-radius: 4px; background: var(--panel); color: var(--muted); font-size: 12px; }
.warn { margin: 12px 0; padding: 10px 12px; border-radius: 8px; background: var(--panel); font-size: 14px; }

#view { display: grid; grid-template-columns: minmax(0, 1fr); grid-template-areas: "head" "map" "wave" "guide";
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
header { grid-area: head; display: flex; align-items: center; gap: 2px; min-height: 46px; padding: 0 4px; border-bottom: 1px solid var(--line); }
header button { min-width: 42px; height: 42px; color: var(--accent); font-size: 17px; }
header button:disabled { color: var(--line); cursor: default; }
#heading { flex: 1; min-width: 0; text-align: center; line-height: 1.25; }
#headTime { font-size: 14px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
#headInfo { color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#mapPane { grid-area: map; position: relative; height: 37vh; min-height: 220px; border-bottom: 1px solid var(--line); overflow: hidden; }
#map { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; user-select: none; -webkit-user-select: none; }
.floatTools { position: absolute; right: 6px; top: 6px; display: flex; flex-direction: column; gap: 4px; }
.floatTools button, .tool { min-width: 36px; height: 34px; padding: 0 8px; border-radius: 8px; background: var(--panel);
  border: 1px solid var(--line); font-size: 14px; white-space: nowrap; }
#credit { position: absolute; right: 4px; bottom: 2px; font-size: 11px; color: var(--muted); background: var(--bg); padding: 0 4px; border-radius: 3px; opacity: 0.85; }
#legend { position: absolute; left: 4px; top: 4px; max-width: calc(100% - 60px); font-size: 11px; color: var(--muted); background: var(--bg); padding: 0 4px; border-radius: 3px; opacity: 0.85; }
#wavePane { grid-area: wave; display: flex; flex-direction: column; height: 43vh; min-height: 280px; border-bottom: 1px solid var(--line); }
#tabs { display: flex; gap: 4px; padding: 6px 6px 0; }
#tabs button { flex: 1; height: 34px; border-radius: 8px 8px 0 0; background: var(--panel); font-size: 13px; color: var(--muted); border-bottom: 3px solid transparent; }
#tabs button[aria-pressed="true"] { color: var(--ink); font-weight: 600; }
#waveTools { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px; align-items: center; }
.seg { display: flex; padding: 2px; border-radius: 8px; background: var(--panel); }
.seg button { height: 32px; padding: 0 10px; border-radius: 6px; font-size: 13px; color: var(--muted); white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--bg); color: var(--ink); font-weight: 600; box-shadow: 0 0 0 1px var(--line); }
#modeP[aria-pressed="true"] { color: var(--p); } #modeS[aria-pressed="true"] { color: var(--s); }
.spacer { flex: 1; }
.short { display: none; }
#helpTools { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 6px 6px; align-items: center; }
#helpTools .tool { height: 30px; font-size: 13px; }
@media (max-width: 480px) {
  #waveIn, #waveOut { display: none; }   /* スマートフォンはピンチで拡大・縮小する */
  #waveMax .long { display: none; } #waveMax .short { display: inline; }   /* ボタンが1列に収まるように記号にする */
  #helpTools { gap: 4px; padding: 0 4px 6px; } #helpTools .tool { font-size: 12px; padding: 0 6px; min-width: 0; }
  #waveTools .spacer { display: none; } .seg button, #waveTools .tool { font-size: 12px; }
  .seg button { padding: 0 6px; } #waveTools { gap: 4px; padding: 6px 4px; } #waveTools .tool { padding: 0 6px; min-width: 0; }
}
#stage { position: relative; flex: 1; min-height: 0; }
#wave { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none; user-select: none; -webkit-user-select: none; }
#guide { grid-area: guide; padding: 10px 14px calc(18px + env(safe-area-inset-bottom)); max-width: 760px; width: 100%; }
#steps { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px; }
#steps button { padding: 3px 10px; border-radius: 14px; border: 1px solid var(--line); font-size: 13px; color: var(--muted); }
#steps button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--bg); font-weight: 600; }
#steps .all { margin-left: auto; }
#steps .all[aria-pressed="true"] { background: var(--panel); color: var(--ink); border-color: var(--muted); }
#guide h2 { font-size: 17px; margin: 4px 0 4px; }
#guide p { margin: 6px 0; font-size: 15px; }
.formula { padding: 6px 10px; border-radius: 8px; background: var(--panel); font-size: 15px; font-variant-numeric: tabular-nums; }
.row { display: flex; align-items: center; gap: 10px; margin: 8px 0; flex-wrap: wrap; }
.row input[type=range] { flex: 1; min-width: 160px; }
.val { font-weight: 600; font-variant-numeric: tabular-nums; min-width: 6em; }
.tableWrap { overflow-x: auto; }
table { border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; width: 100%; margin: 6px 0; }
th, td { padding: 3px 6px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 500; font-size: 12px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: 0; }
.bad { color: var(--s); }
#nav { display: flex; gap: 8px; margin-top: 12px; }
#nav button { flex: 1; height: 42px; border-radius: 10px; border: 1px solid var(--line); background: var(--panel); font-size: 15px; }
#nav #nextStep { background: var(--accent); color: var(--bg); border-color: var(--accent); font-weight: 600; }
#depthChart { width: 100%; height: 130px; display: block; }
.small { font-size: 13px; color: var(--muted); }

/* 地図だけ・波形だけを大きくする(「大きく」ボタン) */
#view.max-map #wavePane, #view.max-wave #mapPane { display: none; }
#view.max-map #mapPane { height: calc(100vh - 60px); height: calc(100dvh - 60px); }
#view.max-wave #wavePane { height: calc(100vh - 60px); height: calc(100dvh - 60px); }
.tool[aria-pressed="true"], .floatTools button[aria-pressed="true"] { background: var(--accent); color: var(--bg); border-color: var(--accent); }
@media (min-width: 900px) and (min-aspect-ratio: 5/4) {
  #view { height: 100vh; height: 100dvh; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1.25fr) minmax(0, 1fr); grid-template-areas: "head head" "map wave" "guide wave"; }
  #mapPane { height: auto; border-right: 1px solid var(--line); }
  #wavePane { height: auto; border-bottom: 0; }
  #guide { overflow: auto; border-right: 1px solid var(--line); max-width: none; }
  #tabs { display: none; }
  #view.max-map { grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr);
    grid-template-areas: "head head" "map guide"; }
  #view.max-map #mapPane, #view.max-wave #wavePane { height: auto; }
  #view.max-map #guide { border-right: 0; border-left: 1px solid var(--line); }
  #view.max-wave { grid-template-columns: minmax(0, 1fr); grid-template-rows: auto minmax(0, 1fr); grid-template-areas: "head" "wave"; }
  #view.max-wave #guide { display: none; }
}
</style>
</head>
<body>
<main id="list">
  <h1>__TITLE__</h1>
  <p class="note">有珠山の満点地震計の 4 つの拠点の波形で、P波と S波の到着を読みます。読んだ S-P 時間から拠点ごとの距離の円が地図に描かれ、円が交わる所が震源です。</p>
  <noscript><p class="warn">プレビューの画面では動きません。Safari などのブラウザで開いてください。</p></noscript>
  <p class="warn" id="unsupported" hidden>このブラウザは圧縮の展開(DecompressionStream)に対応していません。iOS 16.4 以降の Safari などで開いてください。</p>
  <ul id="events"></ul>
  <p class="note">時刻は日本時間。M は気象庁の値です(無い地震もあります)。波形は満点地震計の記録です。</p>
</main>
<section id="view" hidden>
  <header>
    <button id="back" type="button" aria-label="一覧に戻る">‹ 一覧</button>
    <div id="heading"><div id="headTime"></div><div id="headInfo"></div></div>
    <button id="prev" type="button" aria-label="前の問題">◀</button>
    <button id="next" type="button" aria-label="次の問題">▶</button>
  </header>
  <div id="mapPane">
    <canvas id="map" aria-label="地図。拠点と、S-P 時間から求めた距離の円"></canvas>
    <div class="floatTools">
      <button id="mapIn" type="button" aria-label="地図を拡大">＋</button>
      <button id="mapOut" type="button" aria-label="地図を縮小">−</button>
      <button id="mapFit" type="button" aria-label="地図を全体に戻す">全体</button>
      <button id="mapMax" type="button" aria-pressed="false" aria-label="地図を画面いっぱいに広げる"></button>
    </div>
    <div id="legend"></div>
    <div id="credit"></div>
  </div>
  <div id="wavePane">
    <div id="tabs"></div>
    <div id="waveTools">
      <div class="seg" role="group" aria-label="置く線">
        <button id="modeP" type="button">P を置く</button><button id="modeS" type="button">S を置く</button>
      </div>
      <button id="clearPick" class="tool" type="button">消す</button>
      <div class="seg" role="group" aria-label="フィルタ">
        <button id="filterOn" type="button">フィルタ</button><button id="filterOff" type="button">元波形</button>
      </div>
      <span class="spacer"></span>
      <button id="waveIn" class="tool" type="button" aria-label="波形を拡大">＋</button>
      <button id="waveOut" class="tool" type="button" aria-label="波形を縮小">−</button>
      <button id="waveFit" class="tool" type="button">全体</button>
      <button id="waveMax" class="tool" type="button" aria-pressed="false" aria-label="波形を画面いっぱいに広げる"></button>
    </div>
    <div id="helpTools">
      <span class="small">S を読む助け</span>
      <button id="commonAmp" class="tool" type="button" aria-pressed="false">振幅をそろえる</button>
      <button id="hintBtn" class="tool" type="button" aria-pressed="false">ヒント</button>
      <button id="predBtn" class="tool" type="button" aria-pressed="true">S の見込み</button>
    </div>
    <div id="stage"><canvas id="wave" aria-label="波形。タップで線を置き、線はドラッグで動かす。線の無い所のドラッグで移動"></canvas></div>
  </div>
  <div id="guide">
    <div id="steps">
      <button type="button" data-step="1">1 読む</button>
      <button type="button" data-step="2">2 震源</button>
      <button type="button" data-step="3">3 答え合わせ</button>
      <button type="button" data-step="4">4 深さ</button>
      <button type="button" class="all" id="showAll">全部を見せる</button>
    </div>
    <div id="stepText"></div>
    <div id="kBox">
      <div class="row"><label for="kSlider">大森係数 k</label><input id="kSlider" type="range"><span class="val" id="kVal"></span>
        <button id="kReset" class="tool" type="button">元に戻す</button></div>
      <p class="small" id="kNote"></p>
    </div>
    <div id="depthBox">
      <div class="row"><label for="depthSlider">震源の深さ</label><input id="depthSlider" type="range"><span class="val" id="depthVal"></span></div>
      <canvas id="depthChart" aria-label="深さごとの、円からのずれ"></canvas>
      <div class="row"><button id="bestDepth" class="tool" type="button">ずれがいちばん小さい深さにする</button>
        <button id="zeroDepth" class="tool" type="button">深さ 0 に戻す</button></div>
      <p class="small" id="depthNote"></p>
    </div>
    <div id="result"></div>
    <div class="tableWrap"><table id="table"></table></div>
    <div class="row" id="answerTools">
      <button id="useApp" class="tool" type="button">アプリの読み取りにする</button>
      <button id="useMine" class="tool" type="button">自分の読み取りに戻す</button>
    </div>
    <div id="nav"><button id="prevStep" type="button">戻る</button><button id="nextStep" type="button">次へ</button></div>
  </div>
</section>
<script type="application/json" id="meta">__META__</script>
__WAVES__
<script>
"use strict";
const META = JSON.parse(document.getElementById("meta").textContent);
const COMP_NAMES = { U: "上下", N: "南北", E: "東西" };
const STEP_COUNT = 4;
const MIN_SPAN = 0.4;          // 波形の拡大の限度(画面の幅に入る秒)
const GRID_STEP = 0.1, FINE_STEP = 0.01;   // 震源を探す格子 [km]
const HIT_PX = 14;
const HINT_HALF = 0.3;          // ヒントで塗る、アプリの S の前後 [秒]
const ZOOM_BEFORE_P = 0.5, ZOOM_AFTER_P = 2.5;   // P を置いたときに拡大する範囲 [秒]             // 線をつかめる距離 [px]
const $ = id => document.getElementById(id);
const waveCanvas = $("wave"), wctx = waveCanvas.getContext("2d");
const mapCanvas = $("map"), mctx = mapCanvas.getContext("2d");
const STA = Object.fromEntries(META.stations.map(s => [s.id, s]));
const S = { common: false, pred: true, hints: {}, max: null, idx: -1, data: null, step: 1, all: false, mode: "p", cur: null, filtered: true, k: META.k, depth: 0,
  v0: 0, span: 1, picks: {}, mine: {}, map: null, dpr: 1, token: 0 };
let colors = {}, drawQueued = false, mapImage = null, curveCache = { key: "", curve: null };
const wide = matchMedia("(min-width: 900px) and (min-aspect-ratio: 5/4)");

// --- 波形を戻す(quake_wave_view と同じ) ---
async function gunzipBase64(text) {
  const bin = atob(text), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
function decodeSamples(b) {
  const n = b.length / 4, out = new Int32Array(n);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum = (sum + (b[i] | b[n + i] << 8 | b[2 * n + i] << 16 | b[3 * n + i] << 24)) | 0;
    out[i] = sum;
  }
  return out;
}
// 到着時刻の検出と同じフィルタ(往復でかけるので時間のずれがない)
function applySections(x) {
  const n = x.length;
  for (const [b0, b1, b2, a1, a2] of META.filter.sections) {
    let z1 = 0, z2 = 0;
    for (let i = 0; i < n; i++) {
      const input = x[i], output = b0 * input + z1;
      z1 = b1 * input - a1 * output + z2;
      z2 = b2 * input - a2 * output;
      x[i] = output;
    }
  }
}
function filterSamples(v) {
  const x = new Float64Array(v);
  applySections(x); x.reverse(); applySections(x); x.reverse();
  return x;
}
async function loadWaves(ev) {
  const raw = JSON.parse($("w" + ev.no).textContent), data = {}, jobs = [];
  for (const [name, comps] of Object.entries(raw)) {
    data[name] = {};
    for (const [comp, item] of Object.entries(comps)) {
      jobs.push(gunzipBase64(item.d).then(bytes => {
        const v = decodeSamples(bytes), gap = new Uint8Array(v.length);
        for (const [a, b] of item.g) gap.fill(1, a, b);
        data[name][comp] = { raw: v, gap, filtered: null };
      }));
    }
  }
  await Promise.all(jobs);
  return data;
}
function samplesOf(st, comp) {
  const tr = S.data && S.data[st] && S.data[st][comp];
  if (!tr) return null;
  if (!S.filtered) return { v: tr.raw, gap: tr.gap };
  if (!tr.filtered) tr.filtered = filterSamples(tr.raw);
  return { v: tr.filtered, gap: tr.gap };
}

// --- 読み取りと距離 ---
function ev() { return META.events[S.idx]; }
function picksOf() { return S.picks[ev().no] || (S.picks[ev().no] = {}); }
function shown(step) { return S.all || S.step >= step; }
// S-P 時間と距離: [{st, sp, R}](P と S がそろい、S が P より後の拠点だけ)
function readings(picks = picksOf()) {
  const out = [];
  for (const s of META.stations) {
    const p = picks[s.id];
    if (p && p.p != null && p.s != null && p.s > p.p) out.push({ st: s, sp: p.s - p.p, R: S.k * (p.s - p.p) });
  }
  return out;
}
// 深さ z の震源から拠点までの、地表での距離(届かないときは 0)
function surfaceRadius(r, z) {
  const v = z + r.st.h;
  return r.R > v ? Math.sqrt(r.R * r.R - v * v) : 0;
}
// 円(震源からの距離)とのずれの二乗和がいちばん小さい点 {x, y, rms}。3拠点より少なければ null
function solve(list, z) {
  if (list.length < 3) return null;
  const m = META.map, cost = (x, y) => {
    let c = 0;
    for (const r of list) {
      const dx = x - r.st.x, dy = y - r.st.y, v = z + r.st.h;
      const d = Math.sqrt(dx * dx + dy * dy + v * v) - r.R;
      c += d * d;
    }
    return c;
  };
  let best = { x: 0, y: 0, c: Infinity };
  for (let x = m.x0; x <= m.x1; x += GRID_STEP)
    for (let y = m.y0; y <= m.y1; y += GRID_STEP) {
      const c = cost(x, y);
      if (c < best.c) best = { x, y, c };
    }
  const cx = best.x, cy = best.y;
  for (let x = cx - GRID_STEP; x <= cx + GRID_STEP; x += FINE_STEP)
    for (let y = cy - GRID_STEP; y <= cy + GRID_STEP; y += FINE_STEP) {
      const c = cost(x, y);
      if (c < best.c) best = { x, y, c };
    }
  return { x: best.x, y: best.y, rms: Math.sqrt(best.c / list.length) };
}
// 深さごとのずれ [{z, rms}](同じ読み取り・k なら覚えておく)
function depthCurve(list) {
  const key = S.k + "|" + list.map(r => r.st.id + r.R.toFixed(4)).join(",");
  if (curveCache.key === key) return curveCache.curve;
  const curve = [];
  if (list.length >= 3)
    for (let i = 0; i <= Math.round(META.depthMax * 10); i++) {
      const z = i / 10, b = solve(list, z);
      curve.push({ z, rms: b.rms });
    }
  curveCache = { key, curve };
  return curve;
}
// 2つの円の交点
function intersections(a, ra, b, rb) {
  const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy);
  if (!ra || !rb || d === 0 || d > ra + rb || d < Math.abs(ra - rb)) return [];
  const l = (ra * ra - rb * rb + d * d) / (2 * d), h = Math.sqrt(Math.max(0, ra * ra - l * l));
  const mx = a.x + dx * l / d, my = a.y + dy * l / d;
  return [{ x: mx + dy * h / d, y: my - dx * h / d }, { x: mx - dy * h / d, y: my + dx * h / d }];
}
function hypoDistance(st, q) {
  if (!q) return null;
  const v = (q.z || 0) + st.h;
  return Math.sqrt((q.x - st.x) ** 2 + (q.y - st.y) ** 2 + v * v);
}
const fmt = (v, d = 2) => v == null || !isFinite(v) ? "—" : v.toFixed(d);

// --- 描く(共通) ---
function readColors() {
  const css = getComputedStyle(document.documentElement);
  for (const k of ["bg", "ink", "muted", "line", "grid", "accent", "p", "s", "app", "wave", "gap", "best", "panel", ...META.stations.map(s => s.id)])
    colors[k] = css.getPropertyValue("--" + k).trim();
}
function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; drawAll(); });
}
function sizeCanvas(c) {
  const r = c.getBoundingClientRect();
  S.dpr = window.devicePixelRatio || 1;
  const w = Math.round(r.width * S.dpr), h = Math.round(r.height * S.dpr);
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  return { w: r.width, h: r.height };
}
function drawAll() {
  if (S.idx < 0 || !S.data) return;
  drawWave(); drawMap(); updateGuide();
}

// --- 波形 ---
function waveRows() {
  const comps = META.comps, rows = [];
  const list = wide.matches ? META.stations.filter(s => S.data[s.id]) : [STA[S.cur]];
  for (const s of list) for (const c of comps) rows.push({ st: s.id, comp: c });
  return rows;
}
function tickStep(span) {
  for (const t of [0.05, 0.1, 0.2, 0.5, 1, 2]) if (span / t <= 10) return t;
  return 5;
}
function drawWave() {
  const { w, h } = sizeCanvas(waveCanvas);
  if (!w || !h) return;
  const ctx = wctx, rate = META.rate, n = ev().n;
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, w, h);
  const axisH = 18, rows = waveRows(), rh = (h - axisH) / rows.length;
  const xOf = t => (t - S.v0) / S.span * w;
  // 時間の目盛り
  const step = tickStep(S.span);
  ctx.font = "11px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let t = Math.ceil(S.v0 / step) * step; t <= S.v0 + S.span; t += step) {
    const x = xOf(t);
    ctx.fillStyle = colors.grid; ctx.fillRect(Math.round(x), 0, 1, h - axisH);
    ctx.fillStyle = colors.muted; ctx.fillText(t.toFixed(step < 0.1 ? 2 : step < 1 ? 1 : 0), x, h - axisH + 3);
  }
  ctx.textAlign = "right"; ctx.fillText("秒", w - 2, h - axisH + 3);
  const picks = picksOf(), app = ev().picks;
  // 振幅の物差し: ふだんは段ごとの最大、「振幅をそろえる」では拠点の3成分の最大(見えている範囲)
  const a0 = Math.max(0, Math.floor(S.v0 * rate)), b0 = Math.min(n, Math.ceil((S.v0 + S.span) * rate) + 1);
  const peaks = rows.map(row => {
    const tr = samplesOf(row.st, row.comp);
    let peak = 0;
    if (tr) for (let j = a0; j < b0; j++) if (!tr.gap[j] && Math.abs(tr.v[j]) > peak) peak = Math.abs(tr.v[j]);
    return peak;
  });
  if (S.common) rows.forEach((row, i) => {
    peaks[i] = Math.max(...rows.map((r, j) => r.st === row.st ? peaks[j] : 0));
  });
  // S の見込み(✕ と k から): {拠点: 時刻}
  const pred = predictedS(), hints = hintsOf();
  rows.forEach((row, i) => {
    const top = i * rh, mid = top + rh / 2, tr = samplesOf(row.st, row.comp);
    // ヒント: アプリの S の前後を塗る
    const hs = hints[row.st] && app[row.st] && app[row.st].s;
    if (hs != null) {
      ctx.globalAlpha = 0.16; ctx.fillStyle = colors.s;
      ctx.fillRect(xOf(hs - HINT_HALF), top, xOf(hs + HINT_HALF) - xOf(hs - HINT_HALF), rh);
      ctx.globalAlpha = 1;
    }
    if (i > 0) {
      ctx.fillStyle = row.comp === META.comps[0] ? colors.muted : colors.line;
      ctx.fillRect(0, Math.round(top), w, 1);
    }
    if (tr) {
      const a = Math.max(0, Math.floor(S.v0 * rate)), b = Math.min(n, Math.ceil((S.v0 + S.span) * rate) + 1);
      const peak = peaks[i];
      const yOf = v => mid - v / (peak || 1) * (rh / 2 - 3);
      ctx.strokeStyle = colors.wave; ctx.lineWidth = 1; ctx.beginPath();
      const spp = S.span * rate / w;
      if (spp > 2) {
        for (let px = 0; px < w; px++) {
          const j0 = Math.max(a, Math.floor(S.v0 * rate + px * spp)), j1 = Math.min(b, Math.floor(S.v0 * rate + (px + 1) * spp));
          let lo = Infinity, hi = -Infinity;
          for (let j = j0; j < j1; j++) if (!tr.gap[j]) { if (tr.v[j] < lo) lo = tr.v[j]; if (tr.v[j] > hi) hi = tr.v[j]; }
          if (lo <= hi) { ctx.moveTo(px + 0.5, yOf(hi)); ctx.lineTo(px + 0.5, yOf(lo) + 0.5); }
        }
      } else {
        let pen = false;
        for (let j = a; j < b; j++) {
          if (tr.gap[j]) { pen = false; continue; }
          const x = xOf(j / rate), y = yOf(tr.v[j]);
          if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
          pen = true;
        }
      }
      ctx.stroke();
    } else {
      ctx.fillStyle = colors.muted; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("記録なし", w / 2, mid);
    }
    // 拠点・成分の名前
    ctx.font = "12px sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "top";
    const label = (wide.matches && row.comp === META.comps[0] ? STA[row.st].label + " " : "") + COMP_NAMES[row.comp];
    ctx.fillStyle = colors.bg; ctx.fillRect(2, top + 2, ctx.measureText(label).width + 6, 15);
    ctx.fillStyle = row.comp === META.comps[0] ? colors[row.st] : colors.muted;
    ctx.fillText(label, 5, top + 3);
    // 線: アプリの読み取り(答え合わせから。破線)と自分の読み取り
    const lines = [];
    const ap = app[row.st];
    if (shown(3) && ap) {
      if (ap.p != null) lines.push({ t: ap.p, color: colors.app, dash: true, text: "アプリ P" });
      if (ap.s != null) lines.push({ t: ap.s, color: colors.app, dash: true, text: "アプリ S" });
    }
    if (pred[row.st] != null) lines.push({ t: pred[row.st], color: colors.s, dot: true, text: "見込み" });
    const pk = picks[row.st] || {};
    if (pk.p != null) lines.push({ t: pk.p, color: colors.p, text: "P" });
    if (pk.s != null) lines.push({ t: pk.s, color: colors.s, text: "S" });
    for (const L of lines) {
      const x = Math.round(xOf(L.t)) + 0.5;
      if (x < -2 || x > w + 2) continue;
      ctx.strokeStyle = L.color; ctx.lineWidth = L.dash ? 1 : L.dot ? 1.5 : 2; ctx.setLineDash(L.dash ? [4, 3] : L.dot ? [2, 3] : []);
      ctx.beginPath(); ctx.moveTo(x, top + 1); ctx.lineTo(x, top + rh - 1); ctx.stroke(); ctx.setLineDash([]);
      // 名前は、自分の線は拠点のいちばん上の段の上に、アプリの線はいちばん下の段の下に書く(重ならないように)
      const first = row.comp === META.comps[0], last = row.comp === META.comps[META.comps.length - 1];
      //   S の見込みの点線は、いちばん上の段の下に書く
      if (L.dot ? first : L.dash ? last : first) {
        ctx.font = (L.dash || L.dot ? "11px" : "bold 13px") + " sans-serif"; ctx.fillStyle = L.color;
        ctx.textBaseline = L.dash || L.dot ? "bottom" : "top";
        ctx.textAlign = L.text.endsWith("S") || L.dot ? "left" : "right";
        ctx.fillText(L.text, x + (ctx.textAlign === "left" ? 3 : -3), L.dash || L.dot ? top + rh - 2 : top + 3);
      }
    }
  });
  // S-P 時間の表示(縦長の画面)
  if (!wide.matches) {
    const pk = picks[S.cur] || {};
    if (pk.p != null && pk.s != null) {
      const sp = pk.s - pk.p, text = sp > 0 ? `S-P ${sp.toFixed(2)} 秒 → ${(S.k * sp).toFixed(1)} km` : "S が P より前です";
      ctx.font = "bold 13px sans-serif"; ctx.textAlign = "right"; ctx.textBaseline = "top";
      const tw = ctx.measureText(text).width;
      ctx.fillStyle = colors.bg; ctx.fillRect(w - tw - 10, 2, tw + 8, 18);
      ctx.fillStyle = sp > 0 ? colors.ink : colors.s; ctx.fillText(text, w - 6, 4);
    }
  }
}
// S の見込み: ✕(深さはそのとき仮定している深さ)から拠点までの距離 ÷ k を P に足した時刻。✕ が無ければ空
function predictedS() {
  const out = {};
  if (!S.pred || !shown(2)) return out;
  const z = shown(4) ? S.depth : 0, best = solve(readings(), z);
  if (!best) return out;
  for (const [id, pk] of Object.entries(picksOf())) {
    if (pk.p == null || !STA[id]) continue;
    const st = STA[id], v = z + st.h;
    out[id] = pk.p + Math.sqrt((best.x - st.x) ** 2 + (best.y - st.y) ** 2 + v * v) / S.k;
  }
  return out;
}
// ヒントを出している拠点 {拠点: true}(地震ごと)。一度出した拠点は、表に「ヒント」と残す
function hintState() { return S.hints[ev().no] || (S.hints[ev().no] = { on: {}, used: {} }); }
function hintsOf() { return hintState().on; }
function hintUsed() { return hintState().used; }
function toggleHint() {
  const on = hintsOf(), ids = wide.matches ? Object.keys(ev().picks) : [S.cur];
  const next = !ids.some(id => on[id]);
  for (const id of ids) if (ev().picks[id] && ev().picks[id].s != null) { on[id] = next; if (next) hintUsed()[id] = true; }
  updateHelp(); requestDraw();
}
function updateHelp() {
  const on = hintsOf(), ids = wide.matches ? Object.keys(ev().picks) : [S.cur];
  $("hintBtn").setAttribute("aria-pressed", String(ids.some(id => on[id])));
  $("commonAmp").setAttribute("aria-pressed", String(S.common));
  $("predBtn").setAttribute("aria-pressed", String(S.pred));
  $("predBtn").hidden = !shown(2);
}
// P を置いたら、S が入る範囲(P の 0.5 秒前〜2.5 秒後)に拡大する(全体を見ているときだけ。横長の画面は全拠点の P を入れる)
function zoomToP(force) {
  const total = ev().n / META.rate;
  if (!force && S.span < total - 1e-6) return;
  const ps = (wide.matches ? Object.values(picksOf()) : [picksOf()[S.cur] || {}]).map(pk => pk.p).filter(p => p != null);
  if (!ps.length) return;
  const lo = Math.min(...ps) - ZOOM_BEFORE_P, hi = Math.max(...ps) + ZOOM_AFTER_P;
  setView(lo, hi - lo);
}
function setView(v0, span) {
  const total = ev().n / META.rate;
  S.span = Math.min(total, Math.max(MIN_SPAN, span));
  S.v0 = Math.min(Math.max(0, v0), total - S.span);
  requestDraw();
}
function zoomWave(factor, at) {
  const c = at == null ? S.v0 + S.span / 2 : at;
  setView(c - (c - S.v0) * factor, S.span * factor);
}
// 波形の操作: タップで線を置く、線のドラッグで動かす、ほかのドラッグで移動、2本指で拡大・縮小、ホイールで拡大・縮小
const wp = new Map();
let wGesture = null;
function waveHit(e) {
  const r = waveCanvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  const rows = waveRows(), rh = (r.height - 18) / rows.length, row = rows[Math.min(rows.length - 1, Math.max(0, Math.floor(y / rh)))];
  return { x, t: S.v0 + x / r.width * S.span, st: row.st, w: r.width };
}
waveCanvas.addEventListener("pointerdown", e => {
  waveCanvas.setPointerCapture(e.pointerId);
  wp.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (wp.size === 2) {
    const [a, b] = [...wp.values()];
    wGesture = { kind: "pinch", dist: Math.max(20, Math.abs(a.x - b.x)), v0: S.v0, span: S.span,
      mid: S.v0 + ((a.x + b.x) / 2 - waveCanvas.getBoundingClientRect().left) / waveCanvas.clientWidth * S.span };
    return;
  }
  const hit = waveHit(e), pk = picksOf()[hit.st] || {};
  let grab = null;
  for (const key of ["s", "p"]) {
    if (pk[key] != null && Math.abs((pk[key] - S.v0) / S.span * hit.w - hit.x) < HIT_PX) { grab = key; break; }
  }
  wGesture = { kind: grab ? "line" : "tap", key: grab, st: hit.st, x0: e.clientX, v0: S.v0, moved: false };
});
waveCanvas.addEventListener("pointermove", e => {
  if (!wp.has(e.pointerId) || !wGesture) return;
  wp.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (wGesture.kind === "pinch" && wp.size === 2) {
    const [a, b] = [...wp.values()], f = wGesture.dist / Math.max(20, Math.abs(a.x - b.x));
    const span = wGesture.span * f;
    setView(wGesture.mid - (wGesture.mid - wGesture.v0) * f, span);
    return;
  }
  const dx = e.clientX - wGesture.x0;
  if (Math.abs(dx) > 6) wGesture.moved = true;
  if (wGesture.kind === "line") {
    setPick(wGesture.st, wGesture.key, waveHit(e).t);
  } else if (wGesture.moved) {
    wGesture.kind = "pan";
    setView(wGesture.v0 - dx / waveCanvas.clientWidth * S.span, S.span);
  }
});
function endWave(e) {
  if (!wp.has(e.pointerId)) return;
  wp.delete(e.pointerId);
  if (wGesture && wGesture.kind === "tap" && !wGesture.moved && wp.size === 0) {
    const hit = waveHit(e);
    if (!wide.matches || hit.st) {
      const placedP = S.mode === "p";
      if (wide.matches) S.cur = hit.st;
      setPick(hit.st, S.mode, hit.t);
      if (placedP) zoomToP(false);
      const pk = picksOf()[hit.st];
      if (S.mode === "p" && pk.s == null) setMode("s");
      else if (S.mode === "s" && pk.p == null) setMode("p");
      updateHelp();
    }
  }
  if (wp.size === 0) wGesture = null;
}
waveCanvas.addEventListener("pointerup", endWave);
waveCanvas.addEventListener("pointercancel", endWave);
waveCanvas.addEventListener("wheel", e => {
  e.preventDefault();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) { setView(S.v0 + e.deltaX / waveCanvas.clientWidth * S.span, S.span); return; }
  zoomWave(Math.exp(e.deltaY * 0.002), waveHit(e).t);
}, { passive: false });
function setPick(st, key, t) {
  const total = ev().n / META.rate, pk = picksOf()[st] || (picksOf()[st] = {});
  pk[key] = Math.round(Math.min(total, Math.max(0, t)) * META.rate) / META.rate;
  requestDraw(); updateTabs();
}
function setMode(m) { S.mode = m; pressed($("modeP"), $("modeS"), m === "p"); }

// --- 地図 ---
function fitMap() {
  const r = mapCanvas.getBoundingClientRect(), xs = META.stations.map(s => s.x), ys = META.stations.map(s => s.y);
  const pad = 1.5, x0 = Math.min(...xs) - pad, x1 = Math.max(...xs) + pad, y0 = Math.min(...ys) - pad, y1 = Math.max(...ys) + pad;
  const scale = Math.min(r.width / (x1 - x0), r.height / (y1 - y0)) || 30;
  S.map = { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, scale };
  requestDraw();
}
function zoomMap(f, px, py) {
  const r = mapCanvas.getBoundingClientRect(), m = S.map;
  if (px == null) { px = r.width / 2; py = r.height / 2; }
  const kx = m.cx + (px - r.width / 2) / m.scale, ky = m.cy - (py - r.height / 2) / m.scale;
  m.scale = Math.min(400, Math.max(8, m.scale * f));
  m.cx = kx - (px - r.width / 2) / m.scale; m.cy = ky + (py - r.height / 2) / m.scale;
  requestDraw();
}
function drawMap() {
  const { w, h } = sizeCanvas(mapCanvas);
  if (!w || !h) return;
  if (!S.map) fitMap();
  const ctx = mctx, m = S.map;
  const X = x => w / 2 + (x - m.cx) * m.scale, Y = y => h / 2 - (y - m.cy) * m.scale;
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.fillStyle = colors.panel; ctx.fillRect(0, 0, w, h);
  const M = META.map;
  if (mapImage && mapImage.complete) {
    ctx.globalAlpha = matchMedia("(prefers-color-scheme: dark)").matches ? 0.55 : 1;
    ctx.drawImage(mapImage, X(M.x0), Y(M.y1), (M.x1 - M.x0) * m.scale, (M.y1 - M.y0) * m.scale);
    ctx.globalAlpha = 1;
  }
  const list = readings(), z = shown(4) ? S.depth : 0, radii = list.map(r => surfaceRadius(r, z));
  // 円
  list.forEach((r, i) => {
    const c = colors[r.st.id];
    if (!radii[i]) return;
    ctx.beginPath(); ctx.arc(X(r.st.x), Y(r.st.y), radii[i] * m.scale, 0, Math.PI * 2);
    ctx.globalAlpha = 0.07; ctx.fillStyle = c; ctx.fill(); ctx.globalAlpha = 1;
    ctx.strokeStyle = c; ctx.lineWidth = 2.5; ctx.stroke();
  });
  // 円の交点(2つから)
  if (shown(2) || list.length === 2) {
    ctx.fillStyle = colors.muted;
    for (let i = 0; i < list.length; i++)
      for (let j = i + 1; j < list.length; j++)
        for (const q of intersections(list[i].st, radii[i], list[j].st, radii[j])) {
          ctx.beginPath(); ctx.arc(X(q.x), Y(q.y), 3, 0, Math.PI * 2); ctx.fill();
        }
  }
  // 有珠山の目印
  const sm = META.summit;
  ctx.fillStyle = colors.muted; ctx.font = "12px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top";
  ctx.beginPath(); ctx.moveTo(X(sm.x), Y(sm.y) - 6); ctx.lineTo(X(sm.x) - 6, Y(sm.y) + 5); ctx.lineTo(X(sm.x) + 6, Y(sm.y) + 5); ctx.closePath();
  ctx.strokeStyle = colors.muted; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillText(sm.name, X(sm.x), Y(sm.y) + 7);
  // 拠点
  for (const s of META.stations) {
    const x = X(s.x), y = Y(s.y), has = S.data[s.id];
    ctx.beginPath(); ctx.moveTo(x, y - 8); ctx.lineTo(x - 7, y + 5); ctx.lineTo(x + 7, y + 5); ctx.closePath();
    ctx.fillStyle = has ? colors[s.id] : colors.bg; ctx.fill(); ctx.strokeStyle = has ? colors.bg : colors.muted; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.font = "bold 12px sans-serif"; ctx.textBaseline = "bottom";
    const text = s.label + (has ? "" : "(記録なし)");
    ctx.lineWidth = 3; ctx.strokeStyle = colors.bg; ctx.strokeText(text, x, y - 9);
    ctx.fillStyle = has ? colors[s.id] : colors.muted; ctx.fillText(text, x, y - 9);
  }
  // 答え(★ アプリ、◇ 気象庁、■ 正解の場所)
  const e = ev();
  if (shown(3)) {
    if (e.site) mark(ctx, "square", X(e.site.x), Y(e.site.y), colors.ink);
    if (e.jma) mark(ctx, "diamond", X(e.jma.x), Y(e.jma.y), colors.ink);
    mark(ctx, "star", X(e.app.x), Y(e.app.y), colors.ink);
  }
  // 震源(✕)
  const best = shown(2) ? solve(list, z) : null;
  if (best) mark(ctx, "cross", X(best.x), Y(best.y), colors.best);
  // 縮尺(1 km)
  const len = m.scale, bx = 10, by = h - 24;
  ctx.strokeStyle = colors.ink; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(bx, by - 4); ctx.lineTo(bx, by); ctx.lineTo(bx + len, by); ctx.lineTo(bx + len, by - 4); ctx.stroke();
  ctx.fillStyle = colors.ink; ctx.font = "11px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "bottom";
  ctx.fillText("1 km", bx + len / 2, by - 2);
  const legend = [];
  if (shown(2)) legend.push("✕ 震源(円から求めた点)");
  if (shown(3)) legend.push("★ アプリ", ...(e.jma ? ["◇ 気象庁"] : []), ...(e.site ? ["■ " + e.site.name] : []));
  $("legend").textContent = legend.join("  ");
}
function mark(ctx, kind, x, y, color) {
  ctx.save(); ctx.translate(x, y); ctx.lineWidth = 2;
  ctx.strokeStyle = colors.bg; ctx.fillStyle = color;
  ctx.beginPath();
  if (kind === "cross") {
    ctx.lineCap = "round";
    for (const [w, c] of [[6, colors.bg], [3, color]]) {
      ctx.lineWidth = w; ctx.strokeStyle = c; ctx.beginPath();
      ctx.moveTo(-8, -8); ctx.lineTo(8, 8); ctx.moveTo(8, -8); ctx.lineTo(-8, 8); ctx.stroke();
    }
    ctx.restore(); return;
  }
  if (kind === "star") for (let i = 0; i < 10; i++) {
    const r = i % 2 ? 4.5 : 10, a = -Math.PI / 2 + i * Math.PI / 5;
    ctx.lineTo(r * Math.cos(a), r * Math.sin(a));
  }
  if (kind === "diamond") { ctx.moveTo(0, -8); ctx.lineTo(7, 0); ctx.lineTo(0, 8); ctx.lineTo(-7, 0); }
  if (kind === "square") ctx.rect(-6, -6, 12, 12);
  ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.restore();
}
// 地図の操作: ドラッグで移動、2本指・ホイールで拡大・縮小
const mp = new Map();
let mGesture = null;
mapCanvas.addEventListener("pointerdown", e => {
  mapCanvas.setPointerCapture(e.pointerId);
  mp.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const pts = [...mp.values()];
  mGesture = { cx: S.map.cx, cy: S.map.cy, scale: S.map.scale, pts: pts.map(p => ({ ...p })) };
});
mapCanvas.addEventListener("pointermove", e => {
  if (!mp.has(e.pointerId) || !mGesture) return;
  mp.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const pts = [...mp.values()], g = mGesture, m = S.map;
  if (pts.length >= 2 && g.pts.length >= 2) {
    const d0 = Math.hypot(g.pts[0].x - g.pts[1].x, g.pts[0].y - g.pts[1].y), d1 = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    m.scale = Math.min(400, Math.max(8, g.scale * d1 / Math.max(20, d0)));
    const mx0 = (g.pts[0].x + g.pts[1].x) / 2, my0 = (g.pts[0].y + g.pts[1].y) / 2, mx1 = (pts[0].x + pts[1].x) / 2, my1 = (pts[0].y + pts[1].y) / 2;
    m.cx = g.cx - (mx1 - mx0) / m.scale; m.cy = g.cy + (my1 - my0) / m.scale;
  } else if (pts.length === 1 && g.pts.length === 1) {
    m.cx = g.cx - (pts[0].x - g.pts[0].x) / m.scale; m.cy = g.cy + (pts[0].y - g.pts[0].y) / m.scale;
  }
  requestDraw();
});
function endMap(e) {
  mp.delete(e.pointerId);
  const pts = [...mp.values()];
  mGesture = pts.length ? { cx: S.map.cx, cy: S.map.cy, scale: S.map.scale, pts: pts.map(p => ({ ...p })) } : null;
}
mapCanvas.addEventListener("pointerup", endMap);
mapCanvas.addEventListener("pointercancel", endMap);
mapCanvas.addEventListener("wheel", e => {
  e.preventDefault();
  const r = mapCanvas.getBoundingClientRect();
  zoomMap(Math.exp(-e.deltaY * 0.002), e.clientX - r.left, e.clientY - r.top);
}, { passive: false });

// --- 説明と表 ---
const STEP_TEXT = {
  1: () => `<h2>1. P波と S波の到着を読む</h2>
    <p>拠点ごとに、P波(最初に来る小さな揺れ)の始まりに「P」、S波(あとから来る大きな揺れ。南北・東西の横の揺れで大きい)の始まりに「S」の線を置きます。波形をタップすると線が置け、置いた線はドラッグで動かせます。</p>
    <p>P波と S波は速さがちがうので、震源から遠い拠点ほど、P と S の間(S-P 時間)が長くなります。S-P 時間から震源までの距離がわかり、地図に円が描かれます。</p>
    <p class="formula">距離 = k × S-P 時間  (k = ${S.k.toFixed(1)} km/s)</p>
    <p class="small">S波は見分けにくいことがあります。有珠山の近い地震は S-P 時間が 1 秒くらいと短く、S波が P波のあとの揺れにまぎれやすいためです(専門家でも迷います)。「振幅をそろえる」で上下と横の揺れを同じ物差しで比べ、横の揺れが大きくなる所を探しましょう。迷ったら「ヒント」で、アプリの S の前後 ${HINT_HALF} 秒が塗られます。P を置くと、S が入る範囲に拡大します(「全体」で戻る)。</p>`,
  2: () => `<h2>2. 円が交わる所が震源</h2>
    <p>震源は、どの拠点の円の上にもある点です。でも読み取りには少しずつ誤差があるので、円はぴったり1点では交わりません。✕ は、どの円からのずれもいちばん小さくなる点です。</p>
    <p>P・S の線を少しずらしてみましょう。円と ✕ はどう動くでしょうか。k を変えるとどうなるでしょうか。</p>
    <p>波形の点線(S の見込み)は、✕ の位置と k から逆に計算した「円がそろうなら S はここ」という時刻です(P + ✕ までの距離 ÷ k)。見込みから大きく外れた S があれば、読み直してみましょう。S を読んでいない拠点にも出るので、どこを探せばよいかの手がかりにもなります。</p>`,
  3: () => `<h2>3. 答え合わせ</h2>
    <p>★ はアプリの震源(満点とほかの観測点の P・S の到着時刻から、深さも含めて求めたもの)${ev().jma ? "、◇ は気象庁の震源(0.01° 単位なので 1 km ほどの粗さがある)" : ""}${ev().site ? "、■ は" + ev().site.name : ""}です。波形の破線は、アプリの読み取りです。</p>`,
  4: () => `<h2>4. 深さはどうやってわかるの?</h2>
    <p>ここまでは、震源が地表にあるとして円を描きました。本当の震源は地下にあります。S-P 時間からわかる距離は震源までの斜めの距離なので、震源が深さ z にあると、地表の円の半径は √(距離² − z²) に小さくなります。</p>
    <p>深さを動かして、円が1点に集まる(ずれが小さくなる)深さを探しましょう。下のグラフは、深さごとの円からのずれです。</p>`,
};
function updateGuide() {
  const e = ev(), list = readings(), z = shown(4) ? S.depth : 0, best = solve(list, z);
  for (const b of document.querySelectorAll("#steps [data-step]")) b.setAttribute("aria-pressed", String(!S.all && +b.dataset.step === S.step));
  $("showAll").setAttribute("aria-pressed", String(S.all));
  const html = S.all ? [1, 2, 3, 4].map(i => STEP_TEXT[i]()).join("") : STEP_TEXT[S.step]();
  if ($("stepText").dataset.html !== html) { $("stepText").innerHTML = html; $("stepText").dataset.html = html; }
  $("kBox").hidden = !shown(2);
  $("depthBox").hidden = !shown(4);
  $("answerTools").hidden = !shown(3);
  $("useMine").hidden = !S.mine[e.no];
  $("kVal").textContent = S.k.toFixed(1) + " km/s";
  $("kNote").textContent = `k は P波と S波の速さで決まります(k = Vp × Vs ÷ (Vp − Vs))。たとえば P波 ${META.example.vp.toFixed(1)} km/s・P波と S波の速さの比 ${META.example.vpvs.toFixed(2)} なら k は ${META.example.k.toFixed(1)} km/s。はじめの ${META.k.toFixed(1)} km/s は、有珠山の地震 26 件で、アプリの震源までの距離と S-P 時間から求めた値です。`;
  // 結果
  const msg = [];
  if (list.length < 3 && shown(2))
    msg.push(list.length === 2 ? "円が2つだと、交わる点が2つあって震源が1つに決まりません。3つ以上の拠点で P と S を読みましょう。"
      : "震源を決めるには、3つ以上の拠点で P と S を読みます。");
  if (best && shown(2)) msg.push(`円からのずれ: 平均 ${best.rms.toFixed(2)} km`);
  if (best && shown(3)) {
    const d = Math.hypot(best.x - e.app.x, best.y - e.app.y);
    msg.push(`✕ は ★(アプリの震源)から ${d.toFixed(1)} km`);
    if (e.site) msg.push(`■ から ${Math.hypot(best.x - e.site.x, best.y - e.site.y).toFixed(1)} km`);
  }
  $("result").innerHTML = msg.length ? `<p><b>${msg.join("。")}</b></p>` : "";
  if (shown(4)) updateDepth(list);
  // 表
  const ans = shown(3), rows = [];
  rows.push(`<tr><th>拠点</th><th>P [秒]</th><th>S [秒]</th><th>S-P [秒]</th><th>距離 [km]</th>${ans ? "<th>アプリ S-P</th><th>★ まで [km]</th>" : ""}</tr>`);
  const picks = picksOf();
  for (const s of META.stations) {
    const pk = picks[s.id] || {}, ok = pk.p != null && pk.s != null, sp = ok ? pk.s - pk.p : null;
    const ap = e.picks[s.id] || {}, asp = ap.p != null && ap.s != null ? ap.s - ap.p : null;
    const noData = !S.data[s.id];
    const used = hintUsed()[s.id] ? '<span class="small"> ヒント</span>' : "";
    rows.push(`<tr><td><span class="dot" style="background:${colors[s.id]}"></span>${s.label}${noData ? "(記録なし)" : ""}${used}</td>
      <td>${fmt(pk.p)}</td><td>${fmt(pk.s)}</td><td class="${sp != null && sp <= 0 ? "bad" : ""}">${sp != null && sp <= 0 ? "S が前" : fmt(sp)}</td>
      <td>${sp > 0 ? (S.k * sp).toFixed(1) : "—"}</td>${ans ? `<td>${fmt(asp)}</td><td>${noData ? "—" : fmt(hypoDistance(s, e.app), 1)}</td>` : ""}</tr>`);
  }
  $("table").innerHTML = rows.join("");
  $("prevStep").hidden = S.all || S.step === 1;
  $("nextStep").hidden = S.all || S.step === STEP_COUNT;
}
function updateDepth(list) {
  const e = ev(), curve = depthCurve(list);
  $("depthVal").textContent = S.depth.toFixed(1) + " km";
  const c = $("depthChart"), { w, h } = sizeCanvas(c), ctx = c.getContext("2d");
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, w, h);
  const L = 40, R = 10, T = 8, B = 22, zmax = META.depthMax;
  const X = z => L + z / zmax * (w - L - R);
  ctx.strokeStyle = colors.line; ctx.lineWidth = 1; ctx.strokeRect(L, T, w - L - R, h - T - B);
  ctx.fillStyle = colors.muted; ctx.font = "11px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let z = 0; z <= zmax; z++) ctx.fillText(z, X(z), h - B + 3);
  ctx.textAlign = "right"; ctx.fillText("深さ km →", w - R - 4, T + 4);
  let note = "";
  if (curve.length) {
    const top = Math.max(...curve.map(p => p.rms)) * 1.1 || 1, Y = v => T + (1 - v / top) * (h - T - B);
    ctx.textBaseline = "middle"; ctx.textAlign = "right";
    ctx.fillText(top.toFixed(1), L - 4, T + 4); ctx.fillText("0", L - 4, h - B);
    ctx.save(); ctx.translate(10, (h - B + T) / 2); ctx.rotate(-Math.PI / 2); ctx.textAlign = "center"; ctx.fillText("ずれ km", 0, 0); ctx.restore();
    ctx.strokeStyle = colors.accent || colors.p; ctx.lineWidth = 2; ctx.beginPath();
    curve.forEach((p, i) => i ? ctx.lineTo(X(p.z), Y(p.rms)) : ctx.moveTo(X(p.z), Y(p.rms)));
    ctx.stroke();
    const x = X(S.depth);
    ctx.strokeStyle = colors.s; ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, h - B); ctx.stroke();
    if (e.app.z != null && shown(3)) { mark(ctx, "star", X(Math.max(0, e.app.z)), h - B, colors.ink); }
    const bestZ = curve.reduce((a, b) => b.rms < a.rms ? b : a);
    note = `ずれがいちばん小さいのは深さ ${bestZ.z.toFixed(1)} km(ずれ ${bestZ.rms.toFixed(2)} km)。`;
    const flat = Math.max(...curve.filter(p => p.z <= bestZ.z + 1 && p.z >= bestZ.z - 1).map(p => p.rms)) - bestZ.rms;
    if (list.length === 3) note += "円が3つだけのときは、深さも動かすと、ほぼぴったり合う点が見つかります(わからないものが東西・南北・深さの3つで、円の数と同じため)。読み取りの誤差も合わせこんでしまうので、深さは大きくずれることがあります。";
    else if (flat < 0.05) note += "グラフの底が平らなので、この読み取りでは深さがはっきり決まりません。";
  } else note = "3つ以上の拠点で P と S を読むと、グラフが出ます。";
  if (shown(3)) note += `★ アプリの震源の深さは ${e.app.zText} km(海面から)${e.jma && e.jma.z != null ? "、◇ 気象庁は " + e.jma.z + " km" : ""}。`;
  note += "震源の真上に近い拠点では、深さによって距離が大きく変わり、遠い拠点ではあまり変わりません。真上の近くと遠くに拠点があると、深さがよくわかります。";
  $("depthNote").textContent = note;
}

// --- 画面の切り替え ---
function pressed(on, off, value) { on.setAttribute("aria-pressed", String(value)); off.setAttribute("aria-pressed", String(!value)); }
function updateTabs() {
  const tabs = $("tabs");
  if (!tabs.childElementCount) return;
  const picks = picksOf();
  for (const b of tabs.children) {
    const id = b.dataset.st, pk = picks[id] || {}, done = pk.p != null && pk.s != null;
    b.setAttribute("aria-pressed", String(id === S.cur));
    b.style.borderBottomColor = id === S.cur ? colors[id] : "transparent";
    b.textContent = STA[id].label + (done ? " ✓" : pk.p != null || pk.s != null ? " …" : "");
  }
}
function buildTabs() {
  const tabs = $("tabs");
  tabs.textContent = "";
  for (const s of META.stations) {
    if (!S.data[s.id]) continue;
    const b = document.createElement("button");
    b.type = "button"; b.dataset.st = s.id;
    b.addEventListener("click", () => {
      S.cur = s.id; setMode(((picksOf()[s.id] || {}).p == null) ? "p" : "s"); updateTabs(); updateHelp();
      if ((picksOf()[s.id] || {}).p != null) zoomToP(true); else setView(0, ev().n / META.rate);
    });
    tabs.append(b);
  }
  updateTabs();
}
async function openEvent(idx) {
  const token = ++S.token;
  S.idx = idx;
  const e = ev();
  $("list").hidden = true; $("view").hidden = false;
  $("headTime").textContent = `問題 ${e.no}  ${e.time}`;
  $("headInfo").textContent = [e.type, e.m ? "M" + e.m : "", e.note].filter(Boolean).join(" ・ ");
  $("prev").disabled = idx === 0; $("next").disabled = idx === META.events.length - 1;
  S.data = null;
  const data = await loadWaves(e);
  if (token !== S.token) return;
  S.data = data;
  S.cur = META.stations.find(s => data[s.id]).id;
  S.step = 1; S.depth = 0; $("depthSlider").value = 0; updateHelp();
  setMode("p");
  buildTabs();
  setView(0, e.n / META.rate);
  requestAnimationFrame(() => { fitMap(); drawAll(); });
}
function showList() {
  S.token++; S.idx = -1;
  $("view").hidden = true; $("list").hidden = false;
}
function buildList() {
  const ul = $("events");
  META.events.forEach((e, i) => {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `<span class="evHead"><span class="evNo">問題 ${e.no}</span><span>${e.time}</span>
      <span class="badge">${e.type}</span>${e.m ? `<span class="badge">M${e.m}</span>` : ""}</span><span class="evNote"></span>`;
    b.querySelector(".evNote").textContent = e.note;
    b.addEventListener("click", () => openEvent(i));
    li.append(b); ul.append(li);
  });
}
// 地図だけ・波形だけを画面いっぱいに広げる(もう一度押すか Esc で元に戻す)
function setMax(which) {
  S.max = S.max === which ? null : which;
  $("view").classList.toggle("max-map", S.max === "map");
  $("view").classList.toggle("max-wave", S.max === "wave");
  $("mapMax").setAttribute("aria-pressed", String(S.max === "map"));
  $("waveMax").setAttribute("aria-pressed", String(S.max === "wave"));
  for (const [id, on] of [["mapMax", S.max === "map"], ["waveMax", S.max === "wave"]])
    $(id).innerHTML = on ? '<span class="long">戻す</span><span class="short">⤡</span>' : '<span class="long">大きく</span><span class="short">⤢</span>';
  window.scrollTo(0, 0);
  requestAnimationFrame(() => { if (S.max !== "wave") fitMap(); requestDraw(); });
}
function goStep(n) { S.all = false; S.step = Math.min(STEP_COUNT, Math.max(1, n)); updateHelp(); requestDraw(); }

function init() {
  readColors();
  if (META.map.src) { mapImage = new Image(); mapImage.onload = requestDraw; mapImage.src = META.map.src; }
  $("credit").textContent = META.map.credit;
  if (typeof DecompressionStream === "undefined") $("unsupported").hidden = false;
  buildList();
  $("back").addEventListener("click", showList);
  $("prev").addEventListener("click", () => openEvent(S.idx - 1));
  $("next").addEventListener("click", () => openEvent(S.idx + 1));
  $("modeP").addEventListener("click", () => setMode("p"));
  $("modeS").addEventListener("click", () => setMode("s"));
  $("clearPick").addEventListener("click", () => { delete picksOf()[S.cur]; setMode("p"); updateTabs(); requestDraw(); });
  $("filterOn").addEventListener("click", () => { S.filtered = true; pressed($("filterOn"), $("filterOff"), true); requestDraw(); });
  $("filterOff").addEventListener("click", () => { S.filtered = false; pressed($("filterOn"), $("filterOff"), false); requestDraw(); });
  pressed($("filterOn"), $("filterOff"), true);
  $("waveIn").addEventListener("click", () => zoomWave(0.5));
  $("waveOut").addEventListener("click", () => zoomWave(2));
  $("waveFit").addEventListener("click", () => setView(0, ev().n / META.rate));
  $("mapIn").addEventListener("click", () => zoomMap(1.5));
  $("mapOut").addEventListener("click", () => zoomMap(1 / 1.5));
  $("mapFit").addEventListener("click", fitMap);
  $("mapMax").addEventListener("click", () => setMax("map"));
  $("waveMax").addEventListener("click", () => setMax("wave"));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && S.max) setMax(S.max); });
  for (const id of ["mapMax", "waveMax"]) $(id).innerHTML = '<span class="long">大きく</span><span class="short">⤢</span>';
  for (const b of document.querySelectorAll("#steps [data-step]")) b.addEventListener("click", () => goStep(+b.dataset.step));
  $("showAll").addEventListener("click", () => { S.all = !S.all; updateHelp(); requestDraw(); });
  $("commonAmp").addEventListener("click", () => { S.common = !S.common; updateHelp(); requestDraw(); });
  $("hintBtn").addEventListener("click", toggleHint);
  $("predBtn").addEventListener("click", () => { S.pred = !S.pred; updateHelp(); requestDraw(); });
  $("prevStep").addEventListener("click", () => goStep(S.step - 1));
  $("nextStep").addEventListener("click", () => goStep(S.step + 1));
  const ks = $("kSlider");
  ks.min = META.kRange[0]; ks.max = META.kRange[1]; ks.step = 0.1; ks.value = META.k;
  ks.addEventListener("input", () => { S.k = +ks.value; requestDraw(); });
  $("kReset").addEventListener("click", () => { S.k = META.k; ks.value = META.k; requestDraw(); });
  const ds = $("depthSlider");
  ds.min = 0; ds.max = META.depthMax; ds.step = 0.1; ds.value = 0;
  ds.addEventListener("input", () => { S.depth = +ds.value; requestDraw(); });
  $("bestDepth").addEventListener("click", () => {
    const curve = depthCurve(readings());
    if (!curve.length) return;
    S.depth = curve.reduce((a, b) => b.rms < a.rms ? b : a).z; ds.value = S.depth; requestDraw();
  });
  $("zeroDepth").addEventListener("click", () => { S.depth = 0; ds.value = 0; requestDraw(); });
  $("useApp").addEventListener("click", () => {
    const e = ev();
    if (!S.mine[e.no]) S.mine[e.no] = JSON.parse(JSON.stringify(picksOf()));
    const p = {};
    for (const [id, a] of Object.entries(e.picks)) p[id] = { p: a.p, s: a.s };
    S.picks[e.no] = p; updateTabs(); requestDraw();
  });
  $("useMine").addEventListener("click", () => {
    const e = ev();
    S.picks[e.no] = S.mine[e.no]; delete S.mine[e.no]; updateTabs(); requestDraw();
  });
  window.addEventListener("resize", () => { if (S.idx >= 0) { buildTabs(); requestDraw(); } });
  wide.addEventListener("change", () => { if (S.idx >= 0) { fitMap(); requestDraw(); } });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); updateTabs(); requestDraw(); });
}
init();
</script>
</body>
</html>
"""


def main():
  import quake_list
  import quake_locate
  from quake_3d_view import event_key, load_hour_from_zips
  parser = argparse.ArgumentParser(description="S-P 時間で震源をさがす教材の HTML を書き出す")
  parser.add_argument("-o", "--output", help=f"出力する HTML(省略すると batch_plots/quiz/{OUTPUT_FILE})")
  args = parser.parse_args()
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  steps = count_steps(app_paths.ETC_DIR)
  problems = read_problems()
  with tempfile.TemporaryDirectory() as d:
    csv_path = Path(d) / "usu.csv"
    quake_list.write_usu_candidates(csv_path)
    candidates = quake_locate.read_candidates(csv_path)
  items, computed = quake_locate.locate_all_cached(candidates, load_hour_from_zips, stations, proj)
  print(f"震源: {len(items)}件(新しく計算したもの {computed}件)")
  by_key = {event_key(c): (c, r) for c, r in items}
  events = []
  for prob in problems:
    if prob["key"] not in by_key:
      print(f"  問題 {prob['no']}: 有珠山リストに {prob['key']} がありません。飛ばします")
      continue
    cand, r = by_key[prob["key"]]
    if r["kind"] not in LOCAL_KINDS or r.get("lat") is None:
      print(f"  問題 {prob['no']}: {prob['key']} は近い地震として震源が決まっていません。飛ばします")
      continue
    origin = origin_time(r)
    st = load_stream(window_start(origin), origin + timedelta(seconds=AFTER_SECONDS))
    info, waves = event_data(prob, cand, r, st, stations, steps, proj)
    events.append((info, waves))
    sp = [nm for nm, p in info["picks"].items() if "p" in p and "s" in p]
    print(f"  問題 {prob['no']} {info['time']} {info['type']}  波形: {', '.join(waves) or 'なし'}  "
          f"アプリの S-P: {len(sp)}拠点" + ("  気象庁の震源あり" if info["jma"] else ""))
  extra = [SUMMIT] + [p["site"] for p in problems if p["site"]]
  map_info = map_data(map_bounds(stations, proj, extra), proj)
  if not map_info["src"]:
    print("地図のタイルを取得できませんでした(地図なしで書き出します)")
  out = Path(args.output) if args.output else OUTPUT_DIR / OUTPUT_FILE
  summit = xy_of(proj, *SUMMIT)
  summit["name"] = "有珠山"
  size = write_page(out, events, station_items(stations, proj), map_info, summit)
  print(f"書き出しました({len(events)}問、{size / 1e6:.1f} MB): {out}")

if __name__ == "__main__":
  main()
