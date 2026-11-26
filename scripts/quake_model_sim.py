# quake_model_sim.py
# 地下構造の想定を変えると、有珠山付近の地震の震源がどう動くかを見るページ(TODO 59)を書き出す
#   想定(構造の形・速さ・vp/vs・拠点ごとの補正)の組み合わせごとに、震源推定と同じ計算(hypocenter.locate_event)で、
#   保存した到着時刻から震源を求め直し、結果を HTML(ファイル1つ)に入れる。ページの中では計算しない
#   既定の想定(勾配の構造・海面下 1 km で 3.5 km/s・vp/vs は深さで変わる・補正あり)では、アプリの震源と同じになる
#   地図は地理院タイル(淡色地図)をつないだ1枚の画像(sp_locate.py と同じ)。断面の地形は地理院の標高タイル
#   求め直した結果は output/quake_model_sim/results.json に保存し、到着時刻・補正が変わらなければ次から使う
import argparse
import hashlib
import html
import json
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import app_paths
import station_correction
import station_names
import velocity_model
from hypocenter import LOCAL_KINDS, LocalProjection, locate_event

OUTPUT_FILE = "usu_quakes_velocity.html"
CACHE_DIR_NAME = "quake_model_sim"
CACHE_VERSION = 3
TITLE = "地下構造の想定と震源の位置"

# 変える想定
#   構造の形: "gradient" は既定の勾配の構造の、海面下 1 km の点と地表付近(海面下 −0.5 km の点と、その上の −1.0 km の点)の
#   速さを組み合わせて変える。"uniform" はどこも同じ速さ
SHAPES = ("gradient", "uniform")
SHAPE_NAMES = {"gradient": "深さで速くなる", "uniform": "どこも同じ速さ"}
SPEED_DEPTH = 1.0              # 勾配の構造で速さ(SPEEDS)を変える点の深さ [km]
TOP_POINTS = (-1.0, -0.5)      # 勾配の構造で地表付近の速さ(TOP_SPEEDS)を変える点の深さ [km]
SPEED_LABELS = {"gradient": "海面下 1 km の P波の速さ [km/s]", "uniform": "P波の速さ(どの深さも同じ)[km/s]"}
TOP_LABEL = "地表付近(標高 0.5 km より上)の P波の速さ [km/s]"
SPEEDS = {"gradient": (3.0, 3.25, 3.5, 3.75, 4.0), "uniform": (3.5, 3.75, 4.0, 4.25, 4.5)}
TOP_SPEEDS = (1.6, 1.9, 2.2, 2.5, 2.8)
# vp/vs: 一定の値か、深さで変わるもの(velocity_model.middle_vp_vs の重み。0 で Vp = 1.45 Vs + 0.9 の式、1 で 1.74 一定)
#   "formula" は先行研究の式(Onizawa et al. 2007)、"half" は式と 1.74 の 1:1(Aoyama et al. 2009)、"mid" は 3:1(今の既定)
RATIOS = ("formula", "half", "mid", "1.74", "1.70", "1.80", "1.90")
RATIO_WEIGHTS = {"formula": 0.0, "half": 0.5, "mid": velocity_model.VP_VS_WEIGHT}
RATIO_NAMES = {"formula": "先行研究の式", "half": "式と1.74の平均(1:1)", "mid": "今(3:1)", "1.74": "1.74",
               "1.70": "1.70", "1.80": "1.80", "1.90": "1.90"}
DEFAULT = {"shape": "gradient", "speed": 2, "top": 1, "ratio": "mid", "corr": True}   # speed・top は SPEEDS・TOP_SPEEDS の何番目か

# ページの図
CURVE_DEPTHS = np.round(np.arange(-0.8, 6.01, 0.1), 2)   # 速さの曲線を描く深さ [km]
MAP_MARGIN_KM = 1.5       # 地図の範囲: 震源(既定の想定)と満点の拠点の外の余白
SECTION_MAX_KM = 6.0      # 断面のいちばん深い所の上限 [km]
PROFILE_STEP_KM = 0.05    # 断面の地形の刻み [km]


# --- 想定ごとの構造 ---

# 想定の見分け(ページの中でも同じ形で使う)
#   top_index ... 地表付近の速さの番号(一様の構造は 0 だけ)
def model_key(shape, speed_index, top_index, ratio):
  return f"{shape}-{speed_index}-{top_index}-{ratio}"

def constant(value):
  return lambda z: np.full(np.shape(z), float(value))

# 想定の構造(velocity_model.Model)。勾配の構造は、既定の構造の海面下 1 km と地表付近の点の速さだけを変える
def build_model(shape, speed_index, top_index, ratio):
  speed = SPEEDS[shape][speed_index]
  if shape == "gradient":
    top = TOP_SPEEDS[top_index]
    vp = velocity_model.piecewise([(z, speed if z == SPEED_DEPTH else top if z in TOP_POINTS else v)
                                   for z, v in velocity_model.GRADIENT_POINTS])
  else:
    vp = constant(speed)
  vp_vs = velocity_model.middle_vp_vs(vp, weight=RATIO_WEIGHTS[ratio]) if ratio in RATIO_WEIGHTS else constant(ratio)
  return velocity_model.Model("sim-" + model_key(shape, speed_index, top_index, ratio), vp, vp_vs)

def top_indices(shape):
  return range(len(TOP_SPEEDS)) if shape == "gradient" else range(1)

def all_models():
  return [(shape, i, j, ratio) for shape in SHAPES for i in range(len(SPEEDS[shape])) for j in top_indices(shape) for ratio in RATIOS]


# --- 震源を求め直す入力 ---

# 一覧の地震ごとの入力(到着時刻と、観測点の位置)。番号は 3Dの図と同じ(位置の決まった近い地震を一覧の順に数える)
#   items ... quake_locate.locate_all_cached の結果。stations ... 満点の拠点 {名前: (緯度, 経度, 高度[m])}
def event_inputs(items, stations, proj):
  from quake_3d_view import event_key
  from quake_locate import event_type
  local = [(cand, r) for cand, r in items if r["kind"] in LOCAL_KINDS and r.get("lat") is not None]
  out = []
  for no, (cand, r) in enumerate(local, 1):
    coords = {**stations, **(r.get("external") or {})}
    excluded = set(r.get("excluded") or [])
    picks = {nm: round(float(t), 4) for nm, t in sorted((r.get("picks") or {}).items()) if nm not in excluded and nm in coords}
    s_all = r.get("s_picks") or {}
    s_picks = {nm: round(float(s_all[nm]), 4) for nm in sorted(r.get("s_used") or []) if nm in picks and nm in s_all}
    xyz = {nm: [round(float(v), 5) for v in (*proj.to_xy(*coords[nm][:2]), coords[nm][2] / 1000.0)] for nm in picks}
    app_x, app_y = proj.to_xy(r["lat"], r["lon"])
    out.append({"no": no, "key": event_key(cand), "time": f"{cand['start']:%Y-%m-%d %H:%M}", "type": event_type(cand),
                "picks": picks, "s_picks": s_picks, "xyz": xyz, "confirmed": bool(r.get("manual")),
                "app": {"x": round(float(app_x), 3), "y": round(float(app_y), 3), "depth": round(float(r["loc"]["depth"]), 3),
                        "layered": bool(r.get("layered", True)), "corrected": bool(r.get("corrected", True))}})
  return out

# 観測の並び(P波を読んだ拠点の名前順、そのあと S波)。locate_event の並びと同じ
def observations(ev):
  names = sorted(ev["picks"])
  return [(nm, "P") for nm in names] + [(nm, "S") for nm in names if nm in ev["s_picks"]]


# --- 求め直す ---

# 1つの地震を1つの想定で求め直す → [x, y, 深さ, 残差, 深さの下限, 深さの上限, [観測ごとの残差 [秒]]](求まらなければ None)
def relocate(ev, model, proj, corrections):
  xyz = {nm: np.array(v, float) for nm, v in ev["xyz"].items()}
  res = locate_event(xyz, ev["picks"], ev["s_picks"], proj, confirmed=ev["confirmed"], model=model, corrections=corrections)
  loc = res.get("loc")
  if res["kind"] not in LOCAL_KINDS or loc is None:
    return None
  obs = observations(ev)
  corr = corrections or {}
  t = np.array([(ev["picks"] if ph == "P" else ev["s_picks"])[nm] - corr.get((nm, ph), 0.0) for nm, ph in obs])
  pos = np.array([ev["xyz"][nm] for nm, _ph in obs])
  travel = velocity_model.travel_function(model, pos, [ph for _nm, ph in obs])
  tt = travel(np.array([loc["x"]]), np.array([loc["y"]]), np.array([loc["depth"]]))[0]
  resid = t - tt - loc["origin"]
  r3 = lambda v: round(float(v), 3)
  return [r3(loc["x"]), r3(loc["y"]), r3(loc["depth"]), round(float(loc["rms"]), 4),
          r3(loc["depth_min"]), r3(loc["depth_max"]), [r3(v) for v in resid]]

# 1つの構造で、すべての地震を補正あり・なしで求め直す(別のプロセスで動かす)
#   戻り値 ... (想定の見分け, {"c": [地震ごと], "n": [地震ごと]})
def relocate_model(args):
  m, events, proj, corrections = args
  model = build_model(*m)
  out = {"c": [relocate(ev, model, proj, corrections) for ev in events],
         "n": [relocate(ev, model, proj, None) for ev in events]}
  velocity_model._tables.clear()   # 走時表は大きいので、構造ごとに捨てる
  return model_key(*m), out

# 入力が同じかを見分ける文字列(到着時刻・位置・拠点ごとの補正・作り方の版)
def input_signature(events):
  data = json.dumps([CACHE_VERSION, [{k: ev[k] for k in ("key", "picks", "s_picks", "xyz", "confirmed")} for ev in events],
                     station_correction.signature()], sort_keys=True, ensure_ascii=False)
  return hashlib.sha1(data.encode("utf-8")).hexdigest()

# 想定の中身を表す文字列(想定を足したり変えたりしても、同じ中身の結果は使い回す)
def model_def(shape, speed_index, top_index, ratio):
  gradient = shape == "gradient"
  return json.dumps([shape, SPEEDS[shape][speed_index], TOP_SPEEDS[top_index] if gradient else None, ratio, RATIO_WEIGHTS.get(ratio),
                     [velocity_model.GRADIENT_POINTS, SPEED_DEPTH, TOP_POINTS] if gradient else None])

def cache_path():
  return app_paths.OUTPUT_DIR / CACHE_DIR_NAME / "results.json"

# すべての想定で求め直す(保存した結果があれば使う)。log ... 進み具合を出す関数
def relocate_all(events, proj, workers=None, cache=None, log=print):
  cache = Path(cache) if cache else cache_path()
  sig = input_signature(events)
  saved, defs = {}, {}
  if cache.exists():
    try:
      data = json.loads(cache.read_text(encoding="utf-8"))
      if data.get("signature") == sig:
        saved, defs = data.get("models", {}), data.get("defs", {})
    except (OSError, ValueError):
      saved, defs = {}, {}
  # 同じ中身の想定の結果だけを、いまの見分けにつけ直して使う
  by_def = {defs[k]: v for k, v in saved.items() if k in defs}
  saved = {model_key(*m): by_def[model_def(*m)] for m in all_models() if model_def(*m) in by_def}
  todo = [m for m in all_models() if model_key(*m) not in saved]
  if todo:
    corrections = station_correction.load()
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    log(f"想定 {len(todo)} 通りで震源を求め直します({workers} 個ずつ並べて)")
    with ProcessPoolExecutor(max_workers=workers) as pool:
      for n, (key, out) in enumerate(pool.map(relocate_model, [(m, events, proj, corrections) for m in todo]), 1):
        saved[key] = out
        log(f"  {n}/{len(todo)} {key}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    defs = {model_key(*m): model_def(*m) for m in all_models()}
    cache.write_text(json.dumps({"signature": sig, "defs": defs, "models": saved}, ensure_ascii=False), encoding="utf-8")
  return saved


# --- ページ ---

def default_model():
  return DEFAULT["shape"], DEFAULT["speed"], DEFAULT["top"], DEFAULT["ratio"]

# 速さの曲線(ページの「想定」の図)。P波は構造の形と速さ、S波はさらに vp/vs で決まる
def curves():
  vp, vs = {}, {}
  for shape, i, j, ratio in all_models():
    m = build_model(shape, i, j, ratio)
    vp[f"{shape}-{i}-{j}"] = [round(float(v), 3) for v in m.vp(CURVE_DEPTHS)]
    vs[model_key(shape, i, j, ratio)] = [round(float(v), 3) for v in m.vs(CURVE_DEPTHS)]
  return {"depths": [float(z) for z in CURVE_DEPTHS], "vp": vp, "vs": vs}

# 地図の範囲(既定の想定の震源と満点の拠点を囲む): (南, 北, 西, 東)
def map_bounds(events, results, stations, proj):
  base = results[model_key(*default_model())]["c"]
  pts = [(r[0], r[1]) for r in base if r] + [proj.to_xy(*c[:2]) for c in stations.values()]
  xs, ys = [p[0] for p in pts], [p[1] for p in pts]
  south, west = proj.to_latlon(min(xs) - MAP_MARGIN_KM, min(ys) - MAP_MARGIN_KM)
  north, east = proj.to_latlon(max(xs) + MAP_MARGIN_KM, max(ys) + MAP_MARGIN_KM)
  return south, north, west, east

# 断面の地形: 山頂を通る東西・南北の線の標高 [km](データが無い所は 0)
def profiles(map_info, proj, summit, elevations=None):
  if elevations is None:
    import gsi_tiles
    elevations = gsi_tiles.elevations
  sx, sy = proj.to_xy(*summit)
  xs = np.arange(map_info["x0"], map_info["x1"] + PROFILE_STEP_KM / 2, PROFILE_STEP_KM)
  ys = np.arange(map_info["y0"], map_info["y1"] + PROFILE_STEP_KM / 2, PROFILE_STEP_KM)
  def heights(px, py):
    lat, lon = proj.to_latlon(px, py)
    h = np.asarray(elevations(np.asarray(lat, float), np.asarray(lon, float)), float) / 1000.0
    return [round(float(v), 3) if np.isfinite(v) else 0.0 for v in h]
  return {"ew": {"at": round(float(sy), 3), "pos": [round(float(v), 3) for v in xs], "h": heights(xs, np.full_like(xs, sy))},
          "ns": {"at": round(float(sx), 3), "pos": [round(float(v), 3) for v in ys], "h": heights(np.full_like(ys, sx), ys)}}

# ページに入れる拠点(満点の拠点と、到着時刻を読んだほかの機関の観測点)
def station_items(events, stations, proj):
  out = [{"name": station_names.short(nm), "x": round(float(x), 3), "y": round(float(y), 3), "h": round(el / 1000.0, 3), "manten": True}
         for nm, (la, lo, el) in sorted(stations.items()) for x, y in [proj.to_xy(la, lo)]]
  seen = set(stations)
  for ev in events:
    for nm, (x, y, h) in ev["xyz"].items():
      if nm not in seen:
        seen.add(nm)
        out.append({"name": station_names.short(nm), "x": x, "y": y, "h": h, "manten": False})
  return out

# 断面のいちばん深い所 [km]: すべての想定の震源の深さの 98% 点に余白を足す(上限 SECTION_MAX_KM)
def section_bottom(results):
  depths = [r[2] for m in results.values() for side in m.values() for r in side if r]
  if not depths:
    return 3.0
  return float(min(SECTION_MAX_KM, np.ceil((np.percentile(depths, 98) + 0.3) * 2) / 2))

# vp/vs の想定を、海面下 1 km での比の小さい順に並べる(通る跡と「順に変える」の並び。式は浅いほど比が大きい)
def ratio_order():
  z = np.array([SPEED_DEPTH])
  m = lambda r: build_model(DEFAULT["shape"], DEFAULT["speed"], DEFAULT["top"], r)
  return sorted(RATIOS, key=lambda r: float((m(r).vp(z) / m(r).vs(z))[0]))

def write_page(path, events, results, stations, proj, map_info, profile, summit):
  from quake_3d_view import ACKNOWLEDGEMENT
  meta = {
    "title": TITLE, "shapes": list(SHAPES), "shape_names": SHAPE_NAMES, "speeds": SPEEDS, "speed_labels": SPEED_LABELS, "tops": TOP_SPEEDS, "top_label": TOP_LABEL,
    "ratios": list(RATIOS), "ratio_order": ratio_order(), "ratio_names": RATIO_NAMES, "default": DEFAULT, "curves": curves(),
    "events": [{"no": ev["no"], "time": ev["time"], "type": ev["type"],
                "obs": [[station_names.short(nm), ph] for nm, ph in observations(ev)],
                "app_model": ev["app"]["layered"] and ev["app"]["corrected"]} for ev in events],
    "results": results, "stations": station_items(events, stations, proj), "map": map_info, "profile": profile,
    "summit": dict(zip(("x", "y"), (round(float(v), 3) for v in proj.to_xy(*summit)))),
    "bottom": section_bottom(results), "acknowledgement": ACKNOWLEDGEMENT,
  }
  text = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
  page = PAGE.replace("__TITLE__", html.escape(TITLE)).replace("__META__", text)
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(page, encoding="utf-8")
  return path.stat().st_size


PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root { color-scheme: light; --bg: #ffffff; --panel: #f4f5f7; --ink: #1c2126; --muted: #68727d; --line: #dfe3e8;
  --accent: #1565c0; --p: #1565c0; --s: #e65100; }
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; background: var(--bg); color: var(--ink);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; -webkit-text-size-adjust: 100%; }
header { padding: 12px 16px 4px; }
h1 { font-size: 19px; margin: 0 0 4px; }
.lead, .note { color: var(--muted); font-size: 12.5px; margin: 4px 0; }
#loading { padding: 16px; color: var(--muted); }
#app { display: block; padding: 0 16px 16px; }
/* 広い画面は左に想定とまとめ、右に図。狭い画面は 想定 → 図 → まとめ の順(切り替えてすぐ図を見られるように) */
@media (min-width: 900px) { #app { display: grid; grid-template-columns: 340px minmax(0, 1fr); grid-template-areas: "ctl figs" "side figs";
  grid-template-rows: auto 1fr; gap: 0 20px; align-items: start; } #ctlbox { grid-area: ctl; } #side { grid-area: side; } #figs { grid-area: figs; } }
.ctl { display: grid; gap: 8px; padding: 10px; border-radius: 10px; background: var(--panel); }
.ctl .name { font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.seg { display: flex; flex-wrap: wrap; gap: 4px; }
.seg button { flex: 1 1 auto; min-height: 34px; padding: 0 8px; border: 1px solid var(--line); border-radius: 7px; background: var(--bg);
  font: inherit; font-size: 13px; color: var(--ink); cursor: pointer; white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
.seg button.def::after { content: " ●"; font-size: 8px; vertical-align: middle; opacity: 0.6; }
.actions { display: flex; gap: 6px; }
.actions button { flex: 1; min-height: 36px; border-radius: 8px; border: 1px solid var(--accent); background: var(--bg); color: var(--accent);
  font: inherit; font-weight: 600; cursor: pointer; }
h2 { font-size: 14px; margin: 14px 0 4px; }
canvas { display: block; width: 100%; touch-action: manipulation; }
#summary { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.stat { padding: 6px 8px; border-radius: 8px; background: var(--panel); }
.stat .v { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
.stat .k, .stat .d { font-size: 11.5px; color: var(--muted); }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 6px; }
.fighead { display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: baseline; margin: 10px 0 4px; font-weight: 600; }
.fighead label { font-weight: normal; font-size: 13px; color: var(--muted); }
.fighead small { font-weight: normal; color: var(--muted); }
#detail { margin-top: 8px; padding: 10px; border-radius: 10px; border: 1px solid var(--line); }
#detail .t { font-weight: 600; }
.res { display: grid; grid-template-columns: 6.5em 1fr 4.2em; gap: 2px 6px; align-items: center; font-size: 12px; margin-top: 6px; }
.res .bar { position: relative; height: 12px; background: linear-gradient(to right, transparent calc(50% - 0.5px), #999 calc(50% - 0.5px), #999 calc(50% + 0.5px), transparent calc(50% + 0.5px)); }
.res .bar div { position: absolute; top: 2px; height: 8px; border-radius: 2px; }
.res .num { text-align: right; font-variant-numeric: tabular-nums; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--muted); margin: 4px 0; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; vertical-align: -1px; margin-right: 3px; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <p class="lead">震源は、観測点に P波・S波が届いた時刻に、地下を伝わる速さの想定から計算した時刻がいちばん合う場所です。想定を変えると、同じ到着時刻から求めた震源がどう動くかを見られます。うすい灰色の丸がいまアプリで使っている想定の震源、色の丸が選んだ想定の震源です。</p>
</header>
<div id="loading">読み込み中…</div>
<div id="app" hidden>
  <section id="ctlbox">
    <div class="ctl">
      <div><div class="name">構造の形</div><div class="seg" id="shape"></div></div>
      <div id="topBox"><div class="name" id="topName"></div><div class="seg" id="top"></div></div>
      <div><div class="name" id="speedName"></div><div class="seg" id="speed"></div></div>
      <div><div class="name">vp/vs(P波と S波の速さの比)</div><div class="seg" id="ratio"></div></div>
      <div><div class="name">拠点ごとの補正(観測点の真下の地盤による遅れ・早さ)</div><div class="seg" id="corr"></div></div>
      <div><div class="name">通る跡・「順に変える」で動かす項目(押した項目に切り替わる)</div><div class="seg" id="axis"></div></div>
      <div class="actions"><button id="sweep" type="button">▶ 順に変える</button><button id="reset" type="button">いまの想定に戻す</button></div>
    </div>
  </section>
  <section id="figs">
    <div class="fighead">上から見た図
      <label><input type="checkbox" id="nums"> 番号</label>
      <label><input type="checkbox" id="trails"> 通る跡</label></div>
    <div class="legend"><span><i style="background:#fde725"></i>浅い</span><span><i style="background:#c9314c"></i>深い</span>
      <span>◆ 発破</span><span>▲ 満点の拠点</span><span>■ ほかの機関の観測点</span><span>○ いまの想定</span></div>
    <canvas id="map"></canvas>
    <div class="fighead">東西の断面 <small>(震源は南北の位置によらず、この面に写す。地形は山頂を通る線)</small></div>
    <canvas id="ew"></canvas>
    <div class="fighead">南北の断面 <small>(左が南、右が北。東西の断面と同じ縮尺)</small></div>
    <canvas id="ns"></canvas>
    <p class="note" id="credit"></p>
  </section>
  <section id="side">
    <h2>速さの想定 <small style="font-weight:normal;color:var(--muted)">(点線はいまの想定)</small></h2>
    <canvas id="curve"></canvas>
    <h2>まとめ</h2>
    <div id="summary"></div>
    <div id="gridBox"><h2>地表付近と 1 km の速さの組み合わせ</h2>
      <canvas id="grid"></canvas>
      <p class="note">ます目は、vp/vs・補正をいまの選択にしたときの残差の中央値(色。青いほど小さい)と、地震の深さの中央値(下の数字)です。押すとその組み合わせにします。太い枠が選んでいる組み合わせ、点線の枠がいまの想定。</p></div>
    <div class="charts"><canvas id="rmsChart"></canvas><canvas id="depthChart"></canvas></div>
    <p class="note">グラフは、構造の形・地表付近の速さ・補正をいまの選択にして、速さ(横)と vp/vs(線)を変えたときの中央値です。太い線が選んでいる vp/vs。</p>
    <div id="detail"><span class="note">図の震源を押すと、その地震の観測点ごとのずれを出します。</span></div>
  </section>
</div>
<div style="padding:0 16px 24px">
  <p class="note">震源の求め方は、アプリの震源推定と同じです(深さ・東西・南北を細かくしながら探し、観測と計算の時刻のずれ(残差)の二乗平均がいちばん小さい所)。到着時刻は、アプリで読んだもの(手で直したものを含む)をそのまま使います。</p>
  <p class="note">残差がいちばん小さい想定が、本当の地下の構造とは限りません。浅い所の速さの想定と震源の深さは入れ替わることがあり(ここでは、海面下 1 km を速く想定するほど震源は浅くなり、地表付近を速く想定するほど深くなります)、違う組み合わせが同じくらい観測に合うことがあります。発破(場所が分かっている揺れ)の位置が合うかも手がかりになります。</p>
  <p class="note" id="ack"></p>
</div>
<script type="application/json" id="meta">__META__</script>
<script>
"use strict";
const M = JSON.parse(document.getElementById("meta").textContent);
const $ = (id) => document.getElementById(id);
const S = { shape: M.default.shape, speed: M.default.speed, top: M.default.top, ratio: M.default.ratio, corr: M.default.corr, sel: null, sweep: null, axis: "speed" };
const topOf = (st) => (st.shape === "gradient" ? st.top : 0);
const keyOf = (st) => `${st.shape}-${st.speed}-${topOf(st)}-${st.ratio}`;
const vpKey = (st) => `${st.shape}-${st.speed}-${topOf(st)}`;
const resultsOf = (st) => M.results[keyOf(st)][st.corr ? "c" : "n"];
// 通る跡・順に変える項目: 地表付近の速さ(top)・1 km の速さ(speed)・vp/vs(ratio)。その項目だけを、ほかはそのままで並びの順に変える
const AXIS_NAMES = { top: "地表付近の速さ", speed: "1 km の速さ", ratio: "vp/vs" };
const axisOf = (st) => (st.axis === "top" && st.shape !== "gradient" ? "speed" : st.axis);
const axisValues = (st) => { const a = axisOf(st);
  return a === "ratio" ? M.ratio_order : (a === "top" ? M.tops : M.speeds[st.shape]).map((_v, i) => i); };
const BASE = resultsOf(M.default);
const N = M.events.length;
const median = (a) => { const v = a.filter((x) => x !== null && isFinite(x)).sort((p, q) => p - q); if (!v.length) return null;
  const m = v.length >> 1; return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2; };
const fmt = (v, d) => (v === null ? "—" : v.toFixed(d));
const signed = (v, d) => (v === null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d));

// --- 切り替え ---
function seg(id, items, get, set) {
  const box = $(id); box.innerHTML = "";
  items.forEach(([value, label, isDefault]) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = label;
    if (isDefault) b.classList.add("def");
    b.setAttribute("aria-pressed", String(get() === value));
    b.addEventListener("click", () => { stopSweep(); set(value); update(); });
    box.append(b);
  });
}
function buildControls() {
  const d = M.default;
  seg("shape", M.shapes.map((s) => [s, M.shape_names[s], s === d.shape]), () => S.shape, (v) => { S.shape = v; });
  $("speedName").textContent = M.speed_labels[S.shape];
  $("topBox").hidden = S.shape !== "gradient"; $("gridBox").hidden = S.shape !== "gradient";
  $("topName").textContent = M.top_label;
  seg("top", M.tops.map((v, i) => [i, v.toFixed(2), i === d.top]), () => S.top, (v) => { S.top = v; S.axis = "top"; });
  seg("speed", M.speeds[S.shape].map((v, i) => [i, v.toFixed(2), S.shape === d.shape && i === d.speed]), () => S.speed, (v) => { S.speed = v; S.axis = "speed"; });
  seg("ratio", M.ratios.map((r) => [r, M.ratio_names[r], r === d.ratio]), () => S.ratio, (v) => { S.ratio = v; S.axis = "ratio"; });
  const axes = S.shape === "gradient" ? ["top", "speed", "ratio"] : ["speed", "ratio"];
  seg("axis", axes.map((a) => [a, a === "speed" && S.shape !== "gradient" ? "速さ" : AXIS_NAMES[a], a === "speed"]), () => axisOf(S), (v) => { S.axis = v; });
  seg("corr", [[true, "あり", d.corr], [false, "なし", !d.corr]], () => S.corr, (v) => { S.corr = v; });
}
$("reset").addEventListener("click", () => { stopSweep(); Object.assign(S, { shape: M.default.shape, speed: M.default.speed, top: M.default.top, ratio: M.default.ratio, corr: M.default.corr }); update(); });
$("nums").addEventListener("change", () => drawFigures());
$("trails").addEventListener("change", () => drawFigures());

// --- 震源の動き(切り替えたとき、前の位置から新しい位置へすべらせる) ---
let shown = BASE.map((r) => (r ? [r[0], r[1], r[2]] : null));
let anim = null;
function moveTo(target, ms) {
  const from = shown.map((p) => (p ? p.slice() : null));
  anim = { from, to: target.map((r) => (r ? [r[0], r[1], r[2]] : null)), t0: performance.now(), ms };
  requestAnimationFrame(step);
}
function step(now) {
  if (!anim) return;
  const f = Math.min(1, (now - anim.t0) / anim.ms), e = f < 0.5 ? 2 * f * f : 1 - 2 * (1 - f) * (1 - f);
  shown = anim.to.map((b, i) => { const a = anim.from[i]; return a && b ? [0, 1, 2].map((k) => a[k] + (b[k] - a[k]) * e) : b; });
  drawFigures();
  if (f < 1) requestAnimationFrame(step); else { const done = anim.done; anim = null; if (done) done(); }
}
function update() {
  buildControls();
  moveTo(resultsOf(S), 600);
  drawCurve(); drawSummary(); drawCharts(); drawDetail();
}
// ▶ 順に変える: 選んだ項目を、並びの最初から最後まで1つずつ変えてすべらせる(通る跡を残す)
function stopSweep() { if (S.sweep) { S.sweep = null; $("sweep").textContent = "▶ 順に変える"; } }
$("sweep").addEventListener("click", () => {
  if (S.sweep) { stopSweep(); return; }
  const me = {}; S.sweep = me; $("sweep").textContent = "■ 止める";
  let i = 0;
  const next = () => {
    if (S.sweep !== me) return;
    const vals = axisValues(S);
    if (i >= vals.length) { stopSweep(); drawFigures(); return; }
    S[axisOf(S)] = vals[i]; me.upto = i++; buildControls(); drawCurve(); drawSummary(); drawCharts(); drawDetail();
    moveTo(resultsOf(S), 900);
    anim.done = () => setTimeout(next, 350);
  };
  next();
});

// --- 図の部品 ---
function canvasCtx(c, cssH) {
  const dpr = window.devicePixelRatio || 1, w = c.clientWidth;
  c.style.height = cssH + "px"; c.width = Math.round(w * dpr); c.height = Math.round(cssH * dpr);
  const g = c.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, cssH);
  return { g, w, h: cssH };
}
const STOPS = [[-0.5, [253, 231, 37]], [0.5, [242, 142, 43]], [1.5, [201, 49, 76]], [3.0, [91, 42, 134]]];
function depthColor(z) {
  if (z <= STOPS[0][0]) return `rgb(${STOPS[0][1]})`;
  for (let k = 1; k < STOPS.length; k++) {
    if (z <= STOPS[k][0]) { const [z0, c0] = STOPS[k - 1], [z1, c1] = STOPS[k], f = (z - z0) / (z1 - z0);
      return `rgb(${c0.map((v, j) => Math.round(v + (c1[j] - v) * f))})`; }
  }
  return `rgb(${STOPS[STOPS.length - 1][1]})`;
}
function marker(g, x, y, r, blast) {
  g.beginPath();
  if (blast) { g.moveTo(x, y - r * 1.3); g.lineTo(x + r * 1.3, y); g.lineTo(x, y + r * 1.3); g.lineTo(x - r * 1.3, y); g.closePath(); }
  else g.arc(x, y, r, 0, 2 * Math.PI);
}
// 3つの図の座標(地図: 東・北 → 画素。断面: 位置・深さ → 画素)。地図は横幅いっぱい、縦は画面の 65% まで
const view = {};
function layout() {
  const m = M.map, w = $("map").clientWidth, spanX = m.x1 - m.x0, spanY = m.y1 - m.y0;
  const maxH = Math.max(260, window.innerHeight * 0.65);
  const k = Math.min(w / spanX, maxH / spanY);
  view.map = { k, ox: (w - spanX * k) / 2, h: spanY * k };
  const top = Math.max(0.3, ...M.profile.ew.h, ...M.profile.ns.h, ...M.stations.map((s) => s.h)) + 0.15;
  view.top = top;
  const ks = w / Math.max(spanX, spanY);
  view.ew = { k: ks, h: (top + M.bottom) * ks };
  view.ns = { k: ks, h: (top + M.bottom) * ks };
  // 短いほうの断面は横幅を縮め、もう一方と中央をそろえる
  [["ew", spanX], ["ns", spanY]].forEach(([id, span]) => { const c = $(id); c.style.width = `${span * ks}px`; c.style.margin = "0 auto"; });
}
const mapXY = (x, y) => [view.map.ox + (x - M.map.x0) * view.map.k, (M.map.y1 - y) * view.map.k];
const secXY = (v, pos, z) => [(pos - (v === view.ew ? M.map.x0 : M.map.y0)) * v.k, (z + view.top) * v.k];

let mapImg = null;
if (M.map.src) { mapImg = new Image(); mapImg.onload = () => drawFigures(); mapImg.src = M.map.src; }

function trailPoints(i) {
  const out = [], vals = axisValues(S), a = axisOf(S);
  const last = S.sweep ? S.sweep.upto : vals.length - 1;
  for (let k = 0; k <= last; k++) { const r = resultsOf({ ...S, [a]: vals[k] })[i]; if (r) out.push(r); }
  return out;
}
function drawFigures() {
  if (!view.map) return;
  drawMap(); drawSection("ew"); drawSection("ns");
}
function drawMap() {
  const c = $("map"), { g, w, h } = canvasCtx(c, view.map.h);
  const [ix0, iy0] = mapXY(M.map.x0, M.map.y1), [ix1, iy1] = mapXY(M.map.x1, M.map.y0);
  if (mapImg && mapImg.complete) { g.globalAlpha = 0.85; g.drawImage(mapImg, ix0, iy0, ix1 - ix0, iy1 - iy0); g.globalAlpha = 1; }
  else { g.fillStyle = "#f3f1ea"; g.fillRect(ix0, iy0, ix1 - ix0, iy1 - iy0); }
  // 断面の線
  g.setLineDash([5, 4]); g.strokeStyle = "rgba(0,0,0,0.35)"; g.lineWidth = 1;
  let [a, b] = mapXY(M.map.x0, M.profile.ew.at), [c2, d] = mapXY(M.map.x1, M.profile.ew.at);
  g.beginPath(); g.moveTo(a, b); g.lineTo(c2, d); g.stroke();
  [a, b] = mapXY(M.profile.ns.at, M.map.y0); [c2, d] = mapXY(M.profile.ns.at, M.map.y1);
  g.beginPath(); g.moveTo(a, b); g.lineTo(c2, d); g.stroke(); g.setLineDash([]);
  drawStations(g, (s) => mapXY(s.x, s.y));
  const [sx, sy] = mapXY(M.summit.x, M.summit.y);
  g.strokeStyle = "#000"; g.lineWidth = 2; g.beginPath(); g.moveTo(sx - 5, sy - 5); g.lineTo(sx + 5, sy + 5); g.moveTo(sx + 5, sy - 5); g.lineTo(sx - 5, sy + 5); g.stroke();
  g.fillStyle = "#000"; g.font = "11px sans-serif"; g.fillText("有珠山", sx + 7, sy - 4);
  drawEvents(g, (p) => mapXY(p[0], p[1]));
}
function drawStations(g, at) {
  g.font = "11px sans-serif";
  M.stations.forEach((s) => {
    const [x, y] = at(s);
    g.beginPath();
    if (s.manten) { g.moveTo(x, y - 6); g.lineTo(x + 5.5, y + 4); g.lineTo(x - 5.5, y + 4); g.closePath(); g.fillStyle = "#d62728"; }
    else { g.rect(x - 4, y - 4, 8, 8); g.fillStyle = "#7f7f7f"; }
    g.fill(); g.strokeStyle = "#fff"; g.lineWidth = 1; g.stroke();
    g.fillStyle = s.manten ? "#a01818" : "#555"; g.fillText(s.name, x + 6, y + 12);
  });
}
// 震源: いまの想定(灰色の丸)→ 選んだ想定(色の丸)の矢印。通る跡は、選んだ項目だけを順に変えたときの位置をつないだ線
function drawEvents(g, at) {
  const trails = $("trails").checked || S.sweep;
  M.events.forEach((ev, i) => {
    const b = BASE[i]; if (!b) return;
    const [x, y] = at(b); g.beginPath(); g.arc(x, y, 4, 0, 2 * Math.PI); g.strokeStyle = "rgba(90,90,90,0.8)"; g.lineWidth = 1.2; g.stroke();
  });
  M.events.forEach((ev, i) => {
    if (!(trails || S.sel === i)) return;
    const pts = trailPoints(i); if (pts.length < 2) return;
    g.beginPath(); pts.forEach((p, k) => { const [x, y] = at(p); if (k) g.lineTo(x, y); else g.moveTo(x, y); });
    g.strokeStyle = S.sel === i ? "rgba(21,101,192,0.9)" : "rgba(21,101,192,0.45)"; g.lineWidth = S.sel === i ? 2 : 1.2; g.stroke();
    pts.forEach((p) => { const [x, y] = at(p); g.beginPath(); g.arc(x, y, 1.8, 0, 2 * Math.PI); g.fillStyle = g.strokeStyle; g.fill(); });
  });
  M.events.forEach((ev, i) => {
    const b = BASE[i], p = shown[i]; if (!b || !p) return;
    const [x0, y0] = at(b), [x1, y1] = at(p);
    if (Math.hypot(x1 - x0, y1 - y0) > 3) { g.beginPath(); g.moveTo(x0, y0); g.lineTo(x1, y1); g.strokeStyle = "rgba(60,60,60,0.55)"; g.lineWidth = 1; g.stroke(); }
  });
  const order = M.events.map((_e, i) => i).filter((i) => shown[i]).sort((p, q) => (p === S.sel) - (q === S.sel));
  order.forEach((i) => {
    const ev = M.events[i], p = shown[i], [x, y] = at(p), sel = S.sel === i;
    marker(g, x, y, sel ? 7 : 5, ev.type === "発破");
    g.fillStyle = ev.type === "発破" ? "#333" : depthColor(p[2]); g.fill();
    g.strokeStyle = sel ? "#000" : "#fff"; g.lineWidth = sel ? 2.5 : 1.2; g.stroke();
    if ($("nums").checked || sel) { g.font = sel ? "bold 12px sans-serif" : "10.5px sans-serif"; g.fillStyle = "#111"; g.fillText(String(ev.no), x + 7, y - 5); }
  });
}
function drawSection(name) {
  const v = view[name], prof = M.profile[name], c = $(name), { g, w, h } = canvasCtx(c, v.h);
  const along = (s) => (name === "ew" ? s.x : s.y);
  // 地下(海面より下)と地形
  const [, sea] = secXY(v, 0, 0);
  g.fillStyle = "#f6f3ee"; g.fillRect(0, 0, w, h);
  g.beginPath(); g.moveTo(0, h);
  prof.pos.forEach((p, k) => { const [x, y] = secXY(v, p, -prof.h[k]); g.lineTo(x, y); });
  g.lineTo(w, h); g.closePath(); g.fillStyle = "#e3dccd"; g.fill(); g.strokeStyle = "#8a7462"; g.lineWidth = 1.2; g.stroke();
  g.setLineDash([4, 4]); g.strokeStyle = "#6fa8dc"; g.beginPath(); g.moveTo(0, sea); g.lineTo(w, sea); g.stroke(); g.setLineDash([]);
  // 深さの目盛り
  g.fillStyle = "#666"; g.font = "10.5px sans-serif"; g.strokeStyle = "rgba(0,0,0,0.12)";
  for (let z = 0; z <= M.bottom + 1e-6; z += 1) { const [, y] = secXY(v, 0, z); g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); g.fillText(`${z} km`, 3, y - 2); }
  g.fillText(name === "ew" ? "西" : "南", 3, h - 4); g.fillText(name === "ew" ? "東" : "北", w - 14, h - 4);
  drawStations(g, (s) => secXY(v, along(s), -s.h));
  drawEvents(g, (p) => secXY(v, name === "ew" ? p[0] : p[1], p[2]));
}
// 図の震源を押して選ぶ(いちばん近い震源。14 画素より遠ければ選びを外す)
function pick(name, ev) {
  const c = $(name), r = c.getBoundingClientRect(), px = ev.clientX - r.left, py = ev.clientY - r.top;
  const at = name === "map" ? (p) => mapXY(p[0], p[1]) : (p) => secXY(view[name], name === "ew" ? p[0] : p[1], p[2]);
  let best = null, bd = 14;
  shown.forEach((p, i) => { if (!p) return; const [x, y] = at(p), d = Math.hypot(x - px, y - py); if (d < bd) { bd = d; best = i; } });
  S.sel = best; drawFigures(); drawDetail();
}
["map", "ew", "ns"].forEach((n) => $(n).addEventListener("click", (e) => pick(n, e)));

// --- 速さの曲線 ---
function drawCurve() {
  const c = $("curve"), { g, w, h } = canvasCtx(c, 200), cv = M.curves, zs = cv.depths;
  const zMax = Math.min(zs[zs.length - 1], Math.max(3, M.bottom)), vMax = 6.5;
  const L = 34, R = 8, T = 8, B = 22, X = (v) => L + (v / vMax) * (w - L - R), Y = (z) => T + ((z - zs[0]) / (zMax - zs[0])) * (h - T - B);
  g.font = "10.5px sans-serif"; g.fillStyle = "#666"; g.strokeStyle = "rgba(0,0,0,0.1)";
  for (let v = 0; v <= vMax; v += 1) { g.beginPath(); g.moveTo(X(v), T); g.lineTo(X(v), h - B); g.stroke(); g.fillText(String(v), X(v) - 3, h - 8); }
  for (let z = 0; z <= zMax; z += 1) { g.beginPath(); g.moveTo(L, Y(z)); g.lineTo(w - R, Y(z)); g.stroke(); g.fillText(`${z}`, L - 12, Y(z) + 4); }
  g.fillText("km/s", w - 30, h - 8); g.fillText("深さ km", 2, T + 8);
  const line = (vals, color, dash, width) => {
    g.beginPath(); vals.forEach((v, k) => { if (zs[k] > zMax) return; const x = X(v), y = Y(zs[k]); if (k) g.lineTo(x, y); else g.moveTo(x, y); });
    g.setLineDash(dash); g.strokeStyle = color; g.lineWidth = width; g.stroke(); g.setLineDash([]);
  };
  const d = M.default;
  line(cv.vp[vpKey(d)], "#999", [4, 3], 1.5); line(cv.vs[keyOf(d)], "#999", [4, 3], 1.5);
  line(cv.vp[vpKey(S)], getComputedStyle(document.body).getPropertyValue("--p"), [], 2.5);
  line(cv.vs[keyOf(S)], getComputedStyle(document.body).getPropertyValue("--s"), [], 2.5);
  g.fillStyle = "#1565c0"; g.fillText("P波", X(cv.vp[vpKey(S)][zs.length - 1 > 30 ? 30 : 0]) + 4, Y(2.2));
  g.fillStyle = "#e65100"; g.fillText("S波", X(cv.vs[keyOf(S)][30]) - 26, Y(2.2));
}

// --- まとめ ---
function stats(st) {
  const res = resultsOf(st), base = BASE;
  const moved = res.map((r, i) => (r && base[i] ? Math.hypot(r[0] - base[i][0], r[1] - base[i][1], r[2] - base[i][2]) : null));
  return { n: res.filter((r) => r).length, rms: median(res.map((r) => (r ? r[3] : null))), depth: median(res.map((r, i) => (r && M.events[i].type !== "発破" ? r[2] : null))),
           moved: median(moved), blast: res.map((r, i) => (r && M.events[i].type === "発破" && base[i] ? Math.hypot(r[0] - base[i][0], r[1] - base[i][1]) : null)).filter((v) => v !== null) };
}
function drawSummary() {
  const s = stats(S), b = stats(M.default);
  const box = (k, v, d) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="d">${d}</div></div>`;
  $("summary").innerHTML =
    box("残差の中央値", `${fmt(s.rms, 3)} 秒`, `いまの想定 ${fmt(b.rms, 3)} 秒`) +
    box("地震の深さの中央値", `${fmt(s.depth, 2)} km`, `いまの想定 ${fmt(b.depth, 2)} km`) +
    box("動いた量の中央値", `${fmt(s.moved, 2)} km`, "いまの想定から(3次元の距離)") +
    box("求まった数", `${s.n} / ${N} 件`, s.blast.length ? `発破の動き ${s.blast.map((v) => v.toFixed(2)).join("・")} km` : "");
}
function drawChart(id, title, value, unit, digits) {
  const c = $(id), { g, w, h } = canvasCtx(c, 150), speeds = M.speeds[S.shape];
  const series = M.ratios.map((r) => speeds.map((_v, i) => value(stats({ ...S, speed: i, ratio: r }))));
  const all = series.flat().filter((v) => v !== null), lo = Math.min(...all), hi = Math.max(...all), pad = (hi - lo) * 0.1 || 0.01;
  const L = 34, R = 6, T = 20, B = 20, X = (i) => L + (i / (speeds.length - 1)) * (w - L - R), Y = (v) => T + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (h - T - B);
  g.font = "bold 11px sans-serif"; g.fillStyle = "#333"; g.fillText(title, 2, 12);
  g.font = "10px sans-serif"; g.fillStyle = "#666";
  speeds.forEach((v, i) => g.fillText(v.toFixed(2).replace(/0$/, ""), X(i) - 9, h - 6));
  [lo, hi].forEach((v) => g.fillText(v.toFixed(digits), 1, Y(v) + 3));
  series.forEach((vals, k) => {
    const cur = M.ratios[k] === S.ratio;
    g.beginPath(); vals.forEach((v, i) => { if (v === null) return; if (i) g.lineTo(X(i), Y(v)); else g.moveTo(X(i), Y(v)); });
    g.strokeStyle = cur ? "#1565c0" : "rgba(21,101,192,0.25)"; g.lineWidth = cur ? 2.5 : 1; g.stroke();
  });
  const v = series[M.ratios.indexOf(S.ratio)][S.speed];
  if (v !== null) { g.beginPath(); g.arc(X(S.speed), Y(v), 4.5, 0, 2 * Math.PI); g.fillStyle = "#1565c0"; g.fill(); g.strokeStyle = "#fff"; g.lineWidth = 1.5; g.stroke(); }
  g.fillStyle = "#666"; g.fillText(unit, w - 22, 12);
}
function drawCharts() {
  drawChart("rmsChart", "残差", (s) => s.rms, "秒", 3);
  drawChart("depthChart", "地震の深さ", (s) => s.depth, "km", 2);
  drawGrid();
}
// 地表付近(縦)と 1 km(横)の速さの組み合わせごとの残差と深さ
const GRID = { L: 40, T: 16, B: 26 };
function drawGrid() {
  if (S.shape !== "gradient") return;
  const c = $("grid"), tops = M.tops, sp = M.speeds.gradient, nx = sp.length, ny = tops.length;
  const cell = Math.min(56, (c.clientWidth - GRID.L - 4) / nx), { g } = canvasCtx(c, GRID.T + cell * ny + GRID.B);
  const vals = tops.map((_t, j) => sp.map((_v, i) => stats({ ...S, speed: i, top: j })));
  const rs = vals.flat().map((v) => v.rms).filter((v) => v !== null), lo = Math.min(...rs), hi = Math.max(...rs);
  const X = (i) => GRID.L + i * cell, Y = (j) => GRID.T + (ny - 1 - j) * cell;   // 速いほど上
  g.font = "10px sans-serif"; g.textAlign = "center";
  vals.forEach((row, j) => row.forEach((v, i) => {
    const f = v.rms === null ? 1 : (v.rms - lo) / (hi - lo || 1);
    g.fillStyle = `hsl(${210 - f * 190}, 70%, ${45 + f * 30}%)`; g.fillRect(X(i) + 1, Y(j) + 1, cell - 2, cell - 2);
    g.fillStyle = f < 0.45 ? "#fff" : "#222";
    g.font = "bold 11px sans-serif"; g.fillText(fmt(v.rms, 3), X(i) + cell / 2, Y(j) + cell / 2 - 1);
    g.font = "10px sans-serif"; g.fillText(`${fmt(v.depth, 2)}km`, X(i) + cell / 2, Y(j) + cell / 2 + 12);
  }));
  const frame = (i, j, dash, width) => { g.setLineDash(dash); g.strokeStyle = "#111"; g.lineWidth = width; g.strokeRect(X(i) + 1, Y(j) + 1, cell - 2, cell - 2); g.setLineDash([]); };
  frame(M.default.speed, M.default.top, [3, 2], 1.5); frame(S.speed, S.top, [], 3);
  g.fillStyle = "#666";
  sp.forEach((v, i) => g.fillText(v.toFixed(2).replace(/0$/, ""), X(i) + cell / 2, GRID.T + ny * cell + 12));
  g.fillText("1 km の速さ →", GRID.L + (nx * cell) / 2, GRID.T + ny * cell + 24);
  g.textAlign = "right"; tops.forEach((v, j) => g.fillText(v.toFixed(1), GRID.L - 4, Y(j) + cell / 2 + 3));
  g.textAlign = "left"; g.fillText("地表付近 ↑", 0, 10);
  c.dataset.cell = cell;
}
$("grid").addEventListener("click", (e) => {
  const c = $("grid"), r = c.getBoundingClientRect(), cell = +c.dataset.cell, ny = M.tops.length;
  const i = Math.floor((e.clientX - r.left - GRID.L) / cell), j = ny - 1 - Math.floor((e.clientY - r.top - GRID.T) / cell);
  if (i < 0 || i >= M.speeds.gradient.length || j < 0 || j >= ny) return;
  stopSweep(); S.speed = i; S.top = j; update();
});

// --- 選んだ地震 ---
function drawDetail() {
  const box = $("detail");
  if (S.sel === null) { box.innerHTML = '<span class="note">図の震源を押すと、その地震の観測点ごとのずれを出します。</span>'; return; }
  const ev = M.events[S.sel], r = resultsOf(S)[S.sel], b = BASE[S.sel];
  let html = `<div class="t">No.${ev.no} ${ev.time}${ev.type !== "地震" ? `(${ev.type})` : ""}</div>`;
  if (!r) { box.innerHTML = html + '<div class="note">この想定では、近い地震として震源が求まりませんでした。</div>'; return; }
  const dz = b ? r[2] - b[2] : null, dh = b ? Math.hypot(r[0] - b[0], r[1] - b[1]) : null;
  html += `<div>深さ ${r[2].toFixed(2)} km(${r[4].toFixed(1)}〜${r[5].toFixed(1)} km)・残差 ${r[3].toFixed(3)} 秒</div>`;
  if (b) html += `<div class="note">いまの想定から: 深さ ${signed(dz, 2)} km、横に ${dh.toFixed(2)} km</div>`;
  html += '<div class="note" style="margin-top:6px">観測点ごとのずれ(観測 − 計算。+ は計算より遅く届いた)</div><div class="res">';
  const lim = 0.3;
  ev.obs.forEach(([name, ph], k) => {
    const v = r[6][k], f = Math.max(-1, Math.min(1, v / lim)), color = ph === "P" ? "#1565c0" : "#e65100";
    const left = f >= 0 ? 50 : 50 + f * 50, width = Math.abs(f) * 50;
    html += `<div>${name} <b style="color:${color}">${ph}</b></div><div class="bar"><div style="left:${left}%;width:${width}%;background:${color}"></div></div><div class="num">${signed(v, 3)}</div>`;
  });
  html += '</div><div class="note">棒の端は ±0.3 秒</div>';
  box.innerHTML = html;
}

// --- はじめ ---
$("credit").textContent = M.map.credit + "。断面の地形: 地理院タイル(標高タイル)";
$("ack").textContent = M.acknowledgement;
$("loading").remove(); $("app").hidden = false;
buildControls(); layout(); drawFigures(); drawCurve(); drawSummary(); drawCharts(); drawDetail();
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { layout(); drawFigures(); drawCurve(); drawCharts(); }, 100); });
</script>
</body>
</html>
"""


def main():
  import quake_list
  import quake_locate
  import sp_locate
  from hypocenter import load_stations
  from quake_3d_view import load_hour_from_zips
  parser = argparse.ArgumentParser(description="地下構造の想定を変えると震源がどう動くかを見るページ(HTML)を書き出す")
  parser.add_argument("-o", "--output", help=f"出力する HTML(省略すると batch_plots/quakes/locate/{OUTPUT_FILE})")
  parser.add_argument("--workers", type=int, help="並べて計算するプロセスの数(省略すると CPU の数 - 1)")
  args = parser.parse_args()
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  with tempfile.TemporaryDirectory() as d:
    csv_path = Path(d) / "usu.csv"
    quake_list.write_usu_candidates(csv_path)
    candidates = quake_locate.read_candidates(csv_path)
  items, computed = quake_locate.locate_all_cached(candidates, load_hour_from_zips, stations, proj)
  print(f"震源: {len(items)}件(新しく計算したもの {computed}件)")
  events = event_inputs(items, stations, proj)
  results = relocate_all(events, proj, workers=args.workers)
  map_info = sp_locate.map_data(map_bounds(events, results, stations, proj), proj)
  profile = profiles(map_info, proj, sp_locate.SUMMIT)
  out = Path(args.output) if args.output else quake_locate.OUTPUT_DIR / OUTPUT_FILE
  size = write_page(out, events, results, stations, proj, map_info, profile, sp_locate.SUMMIT)
  print(f"書き出しました({len(events)}件、想定 {len(results) * 2} 通り、{size / 1e6:.1f} MB): {out}")

if __name__ == "__main__":
  main()
