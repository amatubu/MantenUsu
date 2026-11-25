# shake_world.py
# 「世界の地震が届く」ページの HTML(ファイル1つ)を書き出す(学習ツールのアイデア #13。試作)
#   上に地球の断面を描き、震源から波面(P波の仲間・S波の仲間)が広がって、有珠山まで波の通り道を進む様子をアニメーションにする
#   下には 4 拠点の実際の上下動を、断面と同じ時刻まで描いていく
#   地震は「この揺れは何?」クイズの海外の地震(etc/shake_quiz.csv の種類が「さらに遠い地震(海外)」の問題)
#   震源は USGS(world_quakes)、波の通り道と時刻は iasp91(jma_match.model の TauPy)
#   波形は 0.1〜0.5 Hz(L)と 0.5〜2 Hz(A)をかけて 10 Hz に間引く。読んだ波形は output/shake_world/ に保存して次から使う
#   入れるのは満点の拠点の波形だけ。時刻は日本時間
#   振動(TODO 58): 「振動」にすると、再生に合わせて、音の拠点の揺れの強さ(音と同じ 0.3〜15 Hz を、画面の速さで 20 ms ごと)を振動にする
#     (テスト用の iPhone のアプリの中で。ほかは画面の揺れ。haptic_page.py)
#
#     python3 scripts/shake_world.py [-o 出力の HTML] [--quiz 問題の番号 ...]
#   省略すると batch_plots/quiz/world.html に書き出す
import argparse
import base64
import csv
import html
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import app_paths
import haptic_page
import station_names
import shake_quiz as sq

OUTPUT = sq.OUTPUT_DIR / "world.html"
CACHE_DIR = app_paths.OUTPUT_DIR / "shake_world"
RATE = 10                       # 波形の間引き [Hz]
BANDS = {"V": (0.04, 0.1), "L": (0.1, 0.5), "A": (0.5, 2.0)}   # V は表面波(周期 10〜25 秒)が見える帯域
# 表面波(地表にそって伝わる波)の速さの目安 [km/s]。4 拠点の 0.04〜0.1 Hz の包絡線で、浅く大きな地震(カムチャツカ・アラスカ・フィリピン)の
# 表面波は 2.7〜3.2 km/s で届いていた(山の時刻)。周期で速さが変わり、ゆっくりした揺れはそのあとも続く(カムチャツカは発生の約 13 分後、2.3 km/s まで)。
# 速いほうと遅いほうの間を幅で描く
SURFACE_KMS = (3.6, 2.3)
ICB_DEPTH = 5153.5              # 内核の表面の深さ [km](iasp91)
GRAZE_KM = 100.0                # 内核を通る P波(PKIKP)が内核にこれより浅くしか入らないときは、内核の表面ではね返る P波(PKiKP)として描く
SHADOW = (103.0, 142.0)         # P波の影 [度](断面に灰色の帯で描く)
SHADOW_END = 145.0              # P波の影の遠い端 [度](核のふちを回り込む P波を描く範囲)
SURFACE_MAX_DEPTH = 100.0       # これより深い地震は表面波が弱いので、波形の表示を表面波までのばさない
SURFACE_MAX_DEG = 60.0          # これより遠い地震も(届くのが遅く、M7 前後では記録に見えない)
# 音: 0.3〜15 Hz を 50 Hz に間引き、最初の波の 1 分前から入れる。画面の速さ(×10〜×80)で鳴らすので 3〜1200 Hz の音になる
SOUND_BAND = (0.3, 15.0)
SOUND_RATE = 50              # 記録(250 Hz)を割り切れる間引き
SOUND_LEAD = 60
BEFORE = 30                     # 発生時刻の前 [秒]
AFTER = 600                     # 最初の波の到着見込みの後 [秒](描く波の到着見込みの 2 分後より短ければ、そこまでのばす)
R_EARTH = 6371.0
# 波面を作る波の通り道(震央距離 [度]、波の名前)
#   核を通る波・核のふちを回り込む波は、通り道が大きく曲がって点がまばらになるので、0.25° ごとにとる
FRONT_SETS = [(np.arange(0.5, 180.0, 1.0), ["p", "P", "s", "S", "PcP", "ScS", "PP", "SS"]),
              (np.arange(60.125, 180.0, 0.25), ["PKP", "PKIKP", "SKS", "SKIKS", "Pdiff", "Sdiff"])]
REFLECTED = ("PcP", "ScS", "PP", "SS", "Pdiff", "Sdiff")   # 画面では薄く描く(はね返る波・回り込む波)
FRONT_DEPTH = 50.0              # 波面は震源の深さをこの刻みに丸めて作り、同じ深さの地震で使い回す
FRONT_STEP = 10.0               # 波面の通り道の時刻の刻み [秒]
RAY_STEP = 2.0                  # 有珠山への通り道の時刻の刻み [秒]
TO_USU = ["p", "P", "Pdiff", "PKIKP", "PKiKP", "PKP", "PP", "s", "S", "SKS", "SKIKS", "SS"]
PHASE_NAMES = {"p": "P波", "P": "P波", "PKP": "外核を通るP波", "PKIKP": "内核を通るP波", "PP": "地表ではね返ったP波",
               "Pdiff": "核のふちを回り込むP波(弱い)", "PKiKP": "内核の表面ではね返ったP波",
               "s": "S波", "S": "S波", "SKS": "外核を通るS波", "SKIKS": "内核を通るS波", "SS": "地表ではね返ったS波"}
SOUND_STATION = "Nizaemn"     # 最初に音を出す拠点(遠い地震がいちばんはっきり聞こえる)
# クイズに無い地震(発生時刻(日本時間、分まで), 画面に出す地名)。USGS の M6 以上の月の一覧からさがす
#   メキシコ(105°)・コロンビア(122°)は P波の影の地震(まっすぐの P波は届かず、核を通る P波や地表ではね返った P波から揺れ始める)
EXTRA = [("2026-06-02 07:12", "イタリア南部"), ("2026-07-17 23:48", "メキシコ"), ("2026-08-10 21:34", "コロンビア")]
# 画面に出す震源の地名(問題の番号ごと)
PLACES = {26: "カムチャツカ半島", 7: "アラスカ", 8: "フィリピン", 29: "ボルネオ島北部", 3: "インドネシア", 40: "南サンドウィッチ諸島付近"}

def is_p(name):
  return name[0] in "Pp"

def world_rows(path=None):
  path = path or app_paths.ETC_DIR / sq.QUIZ_FILE
  with open(path, encoding="utf-8-sig", newline="") as f:
    return [r for r in csv.DictReader(f) if r["種類"] == sq.WORLD_KIND]

# クイズに無い地震(EXTRA)の USGS の地震
def find_extra(when, center):
  import world_quakes
  t = datetime.strptime(when, "%Y-%m-%d %H:%M")
  qs = world_quakes.parse_usgs(world_quakes.fetch_month(t.replace(day=1, hour=0, minute=0), 6.0))
  return world_quakes.arrivals(next(q for q in qs if q["日時"].strftime("%Y-%m-%d %H:%M") == when), center)

# 基準の時刻(P波の到着見込み)に合う USGS の地震
def find_quake(ref, center):
  import world_quakes
  cands = world_quakes.quakes_between(ref - timedelta(minutes=30), ref)
  if not cands:
    month = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cands = [q for q in world_quakes.parse_usgs(world_quakes.fetch_month(month, 6.0))
             if ref - timedelta(minutes=30) <= q["日時"] <= ref]
  best = min(cands, key=lambda q: abs((world_quakes.arrivals(q, center)["waves"][0][1] - ref).total_seconds()))
  return world_quakes.arrivals(best, center)

# 通り道を時刻の刻みでとり直す。戻り値 ... [[x, y], ...](km。震源の真上を y 軸、震央距離の向きを +x)
def resample(path, step):
  t = path["time"]
  times = np.append(np.arange(0.0, t[-1], step), t[-1])
  dist = np.interp(times, t, path["dist"])
  r = R_EARTH - np.interp(times, t, path["depth"])
  return np.stack([r * np.sin(dist), r * np.cos(dist)], axis=1)

def b64_int16(a):
  return base64.b64encode(np.asarray(a, dtype="<i2").tobytes()).decode("ascii")

def b64_int8(a):
  return base64.b64encode(np.asarray(a, dtype=np.int8).tobytes()).decode("ascii")

# 波面: いろいろな震央距離への通り道を、FRONT_STEP 秒ごとの点にしたもの
#   fam ... 0: P波の仲間、1: S波の仲間、2・3: そのうちはね返る波・回り込む波
def front_rays(depth):
  import jma_match
  fam, n, xy = [], [], []
  for degs, phases in FRONT_SETS:
    for d in degs:
      for p in jma_match.model().get_ray_paths(source_depth_in_km=depth, distance_in_degree=float(d), phase_list=phases):
        pts = resample(p.path, FRONT_STEP)
        fam.append((0 if is_p(p.name) else 1) + (2 if p.name in REFLECTED else 0))
        n.append(len(pts))
        xy.append(np.round(pts).astype(int))
  return {"fam": fam, "n": n, "xy": b64_int16(np.concatenate(xy).ravel()), "step": FRONT_STEP}

# 有珠山への通り道(名前ごとに最初のもの)
def usu_rays(depth, deg):
  import jma_match
  rays, seen, graze = [], set(), False
  for p in sorted(jma_match.model().get_ray_paths(source_depth_in_km=depth, distance_in_degree=deg, phase_list=TO_USU),
                  key=lambda p: p.time):
    if p.name in seen:
      continue
    seen.add(p.name)
    if p.name == "PKIKP":
      graze = float(p.path["depth"].max()) - ICB_DEPTH < GRAZE_KM
    rays.append({"name": p.name, "label": PHASE_NAMES[p.name], "p": is_p(p.name), "time": round(float(p.time), 1),
                 "xy": b64_int16(np.round(resample(p.path, RAY_STEP)).astype(int).ravel())})
  # P波の仲間・S波の仲間それぞれの最初の波と、それより後でも外核を通る P波(PKP)・核を通らない S波(S)は残す
  # 核のふちを回り込む P波(Pdiff)は、まっすぐの P波が届かない影(約 100° より遠く 145° まで)でだけ、弱い波(weak)として残す
  # 影の入り口(メキシコ 105° など)では、核を通る P波が無く、地表ではね返った P波(PP)が最初の P波の仲間になる
  # 内核を通る P波が内核の表面をかすめるだけのとき(約 110〜130°。コロンビア 122° では 5 km)は、ほぼ同時に届く内核の表面ではね返る P波として描く。
  # それ以外では、はね返る P波は描かない(ふつうは弱い)
  # 地表ではね返る波(PP・SS)は、最初の波から AFTER 秒以内に届き、はね返らない波と 1 分以上離れているときだけ
  names = {r["name"] for r in rays}
  if "PKIKP" in names and "PKiKP" in names and graze:
    rays = [r for r in rays if r["name"] != "PKIKP"]
  else:
    rays = [r for r in rays if r["name"] != "PKiKP"]
  names = {r["name"] for r in rays}
  for r in rays:
    r["weak"] = r["name"] == "Pdiff"
  rays = [r for r in rays if not r["weak"] or ("P" not in names and deg < SHADOW_END)]
  first_p = next((r for r in rays if r["p"] and not r["weak"] and r["name"] not in ("PP",)), None) or \
            next((r for r in rays if r["p"] and not r["weak"]), None)
  first_s = next((r for r in rays if not r["p"]), None)
  keep = [r for r in rays if r is first_p or r is first_s or r["weak"] or r["name"] in ("PKP", "S")]
  for r in rays:
    if r["name"] in ("PP", "SS") and r["time"] <= first_p["time"] + AFTER and \
       all(abs(r["time"] - k["time"]) >= 60 for k in keep if k["p"] == r["p"]):
      r["refl"] = True
      keep.append(r)
  return sorted(keep, key=lambda r: r["time"])

# 4 拠点の上下動(nm/s)に帯域のフィルタをかけて RATE に間引く。戻り値 ... {帯域: {拠点: 波形}}(記録の無い所は NaN)
def waveforms(t0, seconds, bands=None, rate=RATE, load=None):
  from scipy import signal
  import quake_wave_view as wv
  load = load or wv.load_stream
  bands = bands or BANDS
  steps = wv.count_steps(app_paths.ETC_DIR)
  n = int(seconds * rate)
  out = {b: {nm: np.full(n, np.nan, dtype=np.float32) for nm in sq.STATIONS} for b in bands}
  st = load(t0 - timedelta(seconds=60), t0 + timedelta(seconds=seconds + 60))   # フィルタの端の乱れを避けるため前後に 60 秒
  for tr in st:
    nm = tr.stats.station
    if nm not in sq.STATIONS or not tr.stats.channel.endswith("U"):
      continue
    fs = tr.stats.sampling_rate
    k = int(round(fs / rate))
    if k < 1 or abs(fs / rate - k) > 1e-6:
      continue
    x = np.ma.filled(np.ma.asarray(tr.data, dtype=float), np.nan) * steps.get((nm, "U"), 1.0)
    gap = np.isnan(x)
    if gap.all():
      continue
    x = np.where(gap, 0.0, x - np.nanmean(x))
    off = int(round((tr.stats.starttime.datetime - t0).total_seconds() * fs))
    first, last = max(-(-off // k), 0), min((off + len(x)) // k, n)
    for b, band in bands.items():
      y = signal.sosfiltfilt(signal.butter(4, band, "bandpass", fs=fs, output="sos"), x)
      y[gap] = np.nan
      if last > first:
        out[b][nm][first:last] = y[first * k - off:last * k - off:k]
  return out

def cached_waveforms(no, t0, seconds, bands=None, rate=RATE):
  bands = bands or BANDS
  tag = "_" + "_".join(f"{lo:g}-{hi:g}" for lo, hi in bands.values())
  path = CACHE_DIR / f"q{no}_{t0:%Y%m%d%H%M%S}_{seconds:.0f}s_{rate}Hz{tag}.npz"
  if path.exists():
    with np.load(path) as z:
      return {b: {nm: z[f"{b}_{nm}"] for nm in sq.STATIONS} for b in bands}
  wf = waveforms(t0, seconds, bands, rate)
  path.parent.mkdir(parents=True, exist_ok=True)
  np.savez_compressed(path, **{f"{b}_{nm}": y for b, d in wf.items() for nm, y in d.items()})
  return wf

# 画面に入れる波形: 最初の波の到着後(0〜AFTER 秒)の 99.5 % の大きさを 1 とし、1.5 倍で切って -127〜127 にする(-128 は欠測)
def encode_wave(y, i0):
  a = np.abs(y[i0:][np.isfinite(y[i0:])])
  scale = float(np.percentile(a, 99.5)) if a.size else 0.0
  if scale <= 0:
    return None
  v = np.clip(np.nan_to_num(y / (1.5 * scale)), -1, 1) * 127
  v = np.round(v).astype(int)
  v[~np.isfinite(y)] = -128
  return {"d": b64_int8(v), "scale": round(scale / 1000, 3)}   # scale は µm/s

# 音の波形: 最大(99.9 %)を 1 として μ-law で 8 ビットにする(小さな揺れもつぶれにくい)。欠測は 0
MU = 255.0
def encode_sound(y):
  a = np.abs(y[np.isfinite(y)])
  scale = float(np.percentile(a, 99.9)) if a.size else 0.0
  if scale <= 0:
    return None
  v = np.clip(np.nan_to_num(y / scale), -1, 1)
  v = np.sign(v) * np.log1p(MU * np.abs(v)) / np.log1p(MU)
  return b64_int8(np.round(v * 127).astype(int))

# no ... 問題の番号(クイズに無い地震は 100 から)、a ... world_quakes.arrivals の戻り値
def event_data(no, a, place):
  depth = max(float(a["深さ[km]"]), 0.0)
  rays = usu_rays(depth, a["deg"])
  first = min(r["time"] for r in rays)
  end = max(first + AFTER, max(r["time"] for r in rays) + 120)   # 描く波がすべて届いて 2 分後まで
  km = float(a["dist_km"])
  surface = {"fast": SURFACE_KMS[0], "slow": SURFACE_KMS[1], "t0": round(km / SURFACE_KMS[0], 1), "t1": round(km / SURFACE_KMS[1], 1),
             "shown": depth < SURFACE_MAX_DEPTH and a["deg"] < SURFACE_MAX_DEG}
  if surface["shown"]:
    end = max(end, surface["t1"] + 120)                         # 浅く近い地震は、表面波が届き終わる 2 分後まで
  seconds = BEFORE + end
  t0 = a["日時"] - timedelta(seconds=BEFORE)
  wf = cached_waveforms(no, t0, seconds)
  i0 = int((BEFORE + first) * RATE)
  waves = {b: {nm: encode_wave(d[nm], i0) for nm in sq.STATIONS} for b, d in wf.items()}
  s0 = max(first - SOUND_LEAD, 0.0)
  snd = cached_waveforms(no, a["日時"] + timedelta(seconds=s0), end - s0, {"S": SOUND_BAND}, SOUND_RATE)["S"]
  sound = {"start": round(s0, 1), "rate": SOUND_RATE, "d": {nm: encode_sound(snd[nm]) for nm in sq.STATIONS}}
  print(f"{no}: {a['日時']:%Y-%m-%d %H:%M:%S} {a['震央地名']} M{a['M']:g} {a['deg']:.1f}° "
        + " ".join(f"{r['name']} {r['time']:.0f}s" for r in rays), flush=True)
  return {"no": no, "place": place, "usgs": a["震央地名"], "M": a["M"], "depth": round(depth, 1),
          "origin": f"{a['日時']:%Y/%m/%d %H:%M}", "deg": round(a["deg"], 1), "km": round(a["dist_km"]),
          "first": first, "end": round(end, 1), "before": BEFORE, "rate": RATE,
          "band": "A" if depth > 300 else "L", "rays": rays, "front": front_key(depth), "waves": waves, "surface": surface,
          "sound": sound, "sound_station": SOUND_STATION}

_fronts = {}
def front_key(depth):
  key = f"{max(round(depth / FRONT_DEPTH) * FRONT_DEPTH, FRONT_DEPTH):g}"   # 浅い地震は FRONT_DEPTH にまとめる
  if key not in _fronts:
    _fronts[key] = front_rays(float(key))
  return key

def write_page(path, events):
  stations = [{"id": nm, "label": station_names.full(nm)} for nm in sq.STATIONS]
  meta = {"events": events, "fronts": _fronts, "shadow": list(SHADOW), "stations": stations, "bands": {b: list(v) for b, v in BANDS.items()},
          "R": R_EARTH, "cmb": 3482.0, "icb": 1217.0, "haptic": haptic_page.HAPTIC}
  from quake_wave_view import script_json
  page = (PAGE.replace("__DATA__", script_json(meta)).replace("__TITLE__", html.escape("世界の地震が届く"))
          .replace("__HAPTIC_JS__", haptic_page.HAPTIC_JS))
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
:root { color-scheme: light dark; --bg: #fff; --ink: #1c2126; --muted: #68727d; --panel: #f4f5f7; --line: #d5d9de;
  --p: #c8501e; --s: #1565c0; --sf: #2e7d32; --mantle: #efe7da; --outer: #dfe3e8; --inner: #c6cdd6; --wave: #1c2126; --btn: #e9ecef; --on: #1c2126; --onink: #fff; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg: #14171a; --ink: #e8ecef; --muted: #9aa5af; --panel: #1f2428; --line: #39424a;
  --p: #ff8a50; --s: #64a8f0; --sf: #66bb6a; --mantle: #3a332b; --outer: #2c333b; --inner: #3d4650; --wave: #e8ecef; --btn: #2a3036; --on: #e8ecef; --onink: #14171a; } }
:root[data-theme="dark"] { --bg: #14171a; --ink: #e8ecef; --muted: #9aa5af; --panel: #1f2428; --line: #39424a;
  --p: #ff8a50; --s: #64a8f0; --sf: #66bb6a; --mantle: #3a332b; --outer: #2c333b; --inner: #3d4650; --wave: #e8ecef; --btn: #2a3036; --on: #e8ecef; --onink: #14171a; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Yu Gothic", sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 12px 16px 24px; }
h1 { font-size: 20px; margin: 0 0 8px; }
.bar { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; margin: 6px 0; }
.group { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.group > span { color: var(--muted); font-size: 13px; margin-right: 2px; }
button { font: inherit; font-size: 14px; border: 0; border-radius: 6px; padding: 5px 10px; background: var(--btn); color: var(--ink); cursor: pointer; }
button:disabled { opacity: 0.35; cursor: default; }
button.on { background: var(--on); color: var(--onink); }
#play { min-width: 78px; font-weight: 600; }
label { font-size: 14px; display: inline-flex; gap: 4px; align-items: center; }
#seek { flex: 1 1 260px; min-width: 200px; }
#clock { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 11em; }
.hint { color: var(--muted); font-size: 13px; }
#info { color: var(--muted); font-size: 14px; margin: 4px 0 8px; }
.stage { display: grid; grid-template-columns: minmax(300px, 520px) 1fr; gap: 12px; align-items: start; }
@media (max-width: 860px) { .stage { grid-template-columns: 1fr; } }
canvas { width: 100%; display: block; background: var(--panel); border-radius: 8px; }
#earth { aspect-ratio: 1 / 1; }
#waves { height: 440px; }
/* スマートフォンなど狭い画面: 地震は選択リスト、設定はたたむ、再生と時刻のバーは画面の下に固定、断面と波形を 1 画面に収める */
#evsel { display: none; font: inherit; font-size: 16px; padding: 6px 8px; border-radius: 6px; border: 1px solid var(--line); background: var(--bg); color: var(--ink); }
.opts > summary { display: none; cursor: pointer; color: var(--muted); font-size: 14px; padding: 4px 0; }
@media (max-width: 760px) {
  main { padding-bottom: 120px; }
  h1 { font-size: 18px; margin-bottom: 4px; }
  #events { display: none; }
  #evsel { display: block; width: 100%; }
  #info { font-size: 12px; margin: 2px 0 4px; }
  .opts > summary { display: list-item; }
  /* 下に固定する欄: フィルタ(変えたときの波形の変わり方を見られるように)と再生・時刻のバー */
  .dock { position: fixed; left: 0; right: 0; bottom: 0; z-index: 5; background: var(--bg); border-top: 1px solid var(--line);
    padding: 6px 16px calc(8px + env(safe-area-inset-bottom)); }
  #dockbands #bands { flex-wrap: nowrap; }
  #dockbands #bands > span { display: none; }   /* 見出しは省き、3 つのボタンを横 1 列に */
  #dockbands button { flex: 1 1 0; font-size: 13px; padding: 5px 4px; white-space: nowrap; }
  .playbar { margin: 6px 0 0; flex-wrap: nowrap; gap: 10px; }
  #seek { min-width: 0; flex: 1 1 auto; }
  #clock { min-width: 0; font-size: 13px; white-space: nowrap; }
  #clock .lbl { display: none; }
  .stage { gap: 8px; }
  .stage > div { display: contents; }   /* 凡例を波形の下に回す */
  #earth { order: 1; width: min(100%, 36vh); margin: 0 auto; }
  #waves { order: 2; height: 28vh; min-height: 190px; }
  .legend { order: 3; font-size: 12px; margin-top: 0; }
  .note { font-size: 13px; }
}
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 13px; color: var(--muted); margin-top: 6px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; vertical-align: -1px; }
.note { color: var(--muted); font-size: 14px; margin-top: 10px; max-width: 900px; } .note p { margin: 6px 0; }
</style>
</head>
<body>
<main>
<h1>世界の地震が届く</h1>
<div class="bar"><div class="group" id="events"><span>地震</span></div><select id="evsel" aria-label="地震"></select></div>
<div id="info"></div>
<div class="dock"><div id="dockbands"></div>
<div class="bar playbar">
  <button id="play">▶ 再生</button>
  <input id="seek" type="range" min="0" max="1000" step="0.1" value="0" aria-label="時刻">
  <span id="clock"></span>
</div></div>
<details class="opts" id="opts" open>
<summary>設定(速さ・音など)</summary>
<div class="bar">
  <div class="group" id="speeds"><span>速さ</span></div>
  <label><input type="checkbox" id="fast" checked>届くまでは早送り</label>
  <span id="bandshome"></span><div class="group" id="bands"><span>波形のフィルタ</span></div>
  <label><input type="checkbox" id="codes">波の記号(PKIKP など)</label>
  <label><input type="checkbox" id="carry">波形を運ぶ</label>
</div>
<div class="bar">
  <label><input type="checkbox" id="sound">♪ 音を出す</label>
  <label><input type="checkbox" id="feel">📳 振動</label>
  <div class="group" id="sstations"><span>音の拠点</span></div>
  <span class="hint">音は ×40・×80 がよく聞こえます(×10・×20 ではとても低い音)。イヤホンかヘッドホンで</span>
</div>
</details>
<div class="stage">
  <div>
    <canvas id="earth"></canvas>
    <div class="legend">
      <span><i style="background:var(--p)"></i>P波の仲間(押したり引いたりする揺れ。液体の中も進む)</span>
      <span><i style="background:var(--s)"></i>S波の仲間(横にずれる揺れ。液体の中は進めない)</span>
      <span><i style="background:var(--muted);opacity:.5"></i>うすい点: はね返った波・核のふちを回り込む波</span>
      <span><i style="background:var(--muted);opacity:.35;border-radius:2px"></i>P波の影(まっすぐの P波が届かない所)</span>
      <span><i style="background:var(--sf)"></i>表面波(地表にそって伝わる、ゆっくりで長い揺れ)</span>
    </div>
  </div>
  <canvas id="waves"></canvas>
</div>
<div class="note">
<p><b>点の並び</b> … その時刻に、波の先頭がどこまで来ているかを表します(波面)。地震の波は、地球の中の深い所ほど速く進むので、まっすぐではなく、曲がりながら進みます。</p>
<p><b>核のところ</b> … 地球の中心には鉄でできた核があり、外側(外核)は液体、内側(内核)は固体です。
P波は核に入ると急に遅くなって大きく曲がるので、核の中の波面はマントルの波面から遅れ、ちぎれたり折れ曲がったりして見えます。
そのとき一部の波は核の表面ではね返り、一部は核のふちを回り込みます(うすい点)。
S波は液体の中を進めないので、外核には P波に姿を変えて入り、マントルに出るとまた S波にもどります。核の中の点が P波の色なのはそのためです。
</p>
<p><b>P波の影(灰色の帯)</b> … 震源から 100° くらいまでは、マントルの中を曲がりながら進んだ P波が、そのまま地表に届きます。
それより遠くへ向かう P波は、核にぶつかります。核の中では P波が急に遅くなるので、虫めがねのレンズを通る光のように大きく曲げられ、震源から 140° より遠い所へ集まります。
そのため、震源からおよそ 100〜140° 離れた所には、まっすぐの P波がほとんど届きません。これを「P波の影」といいます。
影の中にも、核のふちを回り込む P波(とても弱い)、内核の表面ではね返った P波、地表で 1 回はね返った P波などが届きますが、どれも弱いか、おくれて届きます。
メキシコ(105°)とコロンビア(122°)の地震は、有珠山が影の中にあります。「核のふちを回り込む P波」の線の時刻には記録にほとんど何も見えず、
3〜4 分おくれて、内核の表面ではね返った P波(コロンビア)や、地表ではね返った P波(メキシコ)で揺れ始めます。
S波は液体の外核を通れないので、およそ 100° より遠くには、まっすぐの S波は届きません(S波の影)。<br>
1914 年にグーテンベルクは、P波の影ができる所から、核の大きさ(深さ約 2900 km)を求めました。1936 年にレーマンは、影の中に届く弱い P波に気づき、核の中にもう 1 つ固い内核があることを見つけました。</p>
<p><b>運ばれる波形(黒い線)</b> … 有珠山の地震計に記録された揺れ(音を出している拠点、届いてから 40 秒間)を、その波の通り道にそって描いています。
波が有珠山に着くと、下の波形にその揺れが現れます。見やすいように、通り道の上では波形を長くのばしています。</p>
<p><b>表面波(緑)</b> … 地球の中を通る P波・S波とは別に、地表にそって伝わる波です。P波・S波より遅く(この地域の記録では 1 秒に約 2.7〜3.2 km)、ゆっくりした大きな揺れが長く続きます。
有珠山の地震計では、0.04〜0.1 Hz(周期 10〜25 秒)のフィルタにすると見えます。浅い大きな地震(カムチャツカ・アラスカ・フィリピン)ではっきりし、深い地震(ボルネオ・イタリア)ではほとんど出ません。
緑の帯は、1 秒に 3.6〜2.3 km で進むとしたときの見込みです。</p>
<p><b>太い線</b> … 有珠山に届く波の通り道です。地面ではね返ってから届く波(細い線)は、遠い地震の波形で、P波のあとに来る大きな揺れになることがあります。</p>
<p><b>波形</b> … 4 か所の地震計の上下の揺れで、それぞれ見やすい大きさにしています。4 か所は地球の大きさから見るとほぼ同じ場所なので、遠い地震の波は 4 か所にほぼ同時に届きます。</p>
<p>波の通り道と届く時刻は、地球の中の速さの標準的なモデル(iasp91)からの見込みです。地球の表面波は、速さを決めて描いた目安です。</p>
</div>
</main>
<script>
const META = __DATA__;
__HAPTIC_JS__
const $ = (id) => document.getElementById(id);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const dec16 = (s) => { const b = Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); return new Int16Array(b.buffer); };
const dec8 = (s) => { const b = Uint8Array.from(atob(s), (c) => c.charCodeAt(0)); return new Int8Array(b.buffer); };
const SPEEDS = [10, 20, 40, 80];
const FAST = 6;            // 早送りの倍率
let ev = null, t = 0, playing = false, speed = 40, band = "L", last = null;

// 地震ごとのデータをほどく
for (const f of Object.values(META.fronts)) {
  const xy = dec16(f.xy);
  f.rays = [];
  let k = 0;
  f.n.forEach((n, i) => { f.rays.push({ fam: f.fam[i], xy: xy.subarray(k, k + 2 * n) }); k += 2 * n; });
}
for (const e of META.events) {
  e.front = META.fronts[e.front];
  for (const r of e.rays) r.pts = dec16(r.xy);
  e.decoded = {};
}
// 通り道 pts(x, y の並び、刻み step 秒)の、時刻 tt の位置
function at(pts, step, tt) {
  const n = pts.length / 2;
  const f = Math.min(tt / step, n - 1);
  const i = Math.min(Math.floor(f), n - 2), w = f - i;
  if (i < 0) return [pts[0], pts[1]];
  return [pts[2 * i] + (pts[2 * i + 2] - pts[2 * i]) * w, pts[2 * i + 1] + (pts[2 * i + 3] - pts[2 * i + 1]) * w];
}
function rayAt(r, tt) {   // 有珠山への通り道は最後の刻みだけ短いので、最後の区間は別に補う
  const n = r.pts.length / 2;
  const full = Math.floor(r.time / 2.0);
  if (tt >= r.time) return [r.pts[2 * n - 2], r.pts[2 * n - 1]];
  if (tt <= full * 2.0) return at(r.pts.subarray(0, 2 * (full + 1)), 2.0, tt);
  const w = (tt - full * 2.0) / (r.time - full * 2.0);
  return [r.pts[2 * full] + (r.pts[2 * n - 2] - r.pts[2 * full]) * w, r.pts[2 * full + 1] + (r.pts[2 * n - 1] - r.pts[2 * full + 1]) * w];
}
function frontAt(ray, step, tt) {
  // 最後の点は通り道の終わり(刻みの途中)だが、ほかと同じ刻みとみなす(点が消える時刻のずれは 1 刻み未満)
  if (tt > (ray.xy.length / 2 - 1) * step) return null;
  return at(ray.xy, step, tt);
}
function waves(e, b) {
  const key = b;
  if (!e.decoded[key]) e.decoded[key] = META.stations.map((s) => { const w = e.waves[b][s.id]; return w ? { d: dec8(w.d), scale: w.scale } : null; });
  return e.decoded[key];
}
const mmss = (s) => { const a = Math.max(0, Math.round(s)); return `${Math.floor(a / 60)}分${String(a % 60).padStart(2, "0")}秒`; };
const label = (r) => $("codes").checked ? `${r.label}(${r.name})` : r.label;

// --- 地球の断面 ---
function fit(cv) {
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const w = Math.round(r.width * dpr), h = Math.round(r.height * dpr);
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [g, r.width, r.height];
}
function drawEarth() {
  const [g, W, H] = fit($("earth"));
  if (W < 100 || H < 100) return;   // まだ画面に並んでいないとき
  g.clearRect(0, 0, W, H);
  const cx = W / 2, cy = H / 2 + 6, sc = (Math.min(W, H) / 2 - 30) / META.R;
  // 通り道の座標(震源の真上が +y、有珠山の向きが +x)を回し、有珠山を真上に、震源を左側に置く
  const d = ev.deg * Math.PI / 180, cs = Math.cos(d), sn = Math.sin(d);
  const Q = (x, y) => [cx + (x * cs - y * sn) * sc, cy - (x * sn + y * cs) * sc];
  const disk = (r, c) => { g.beginPath(); g.arc(cx, cy, r * sc, 0, 2 * Math.PI); g.fillStyle = c; g.fill(); };
  disk(META.R, css("--mantle")); disk(META.cmb, css("--outer")); disk(META.icb, css("--inner"));
  g.strokeStyle = css("--line"); g.lineWidth = 1;
  g.beginPath(); g.arc(cx, cy, META.R * sc, 0, 2 * Math.PI); g.stroke();
  g.fillStyle = css("--muted"); g.font = "12px sans-serif"; g.textAlign = "center";
  g.fillText("マントル", cx + (META.R + META.cmb) / 2 * sc, cy - 2); g.fillText("(岩石)", cx + (META.R + META.cmb) / 2 * sc, cy + 13);
  g.fillText("外核", cx + (META.cmb + META.icb) / 2 * sc, cy - 2); g.fillText("(液体の鉄)", cx + (META.cmb + META.icb) / 2 * sc, cy + 13);
  g.fillText("内核", cx, cy - 2); g.fillText("(固体の鉄)", cx, cy + 13);
  const P = css("--p"), S = css("--s");
  // 波面(震源と地球の中心を通る線の両側に描く)
  const fr = ev.front, cmb2 = META.cmb * META.cmb;
  for (const ray of fr.rays) {
    const q = frontAt(ray, fr.step, t);
    if (!q) continue;
    const inCore = q[0] * q[0] + q[1] * q[1] < cmb2, faint = ray.fam >= 2;
    g.fillStyle = ray.fam % 2 === 0 || inCore ? P : S;
    g.globalAlpha = faint ? 0.4 : 1;
    for (const sx of [1, -1]) { const [px, py] = Q(sx * q[0], q[1]); g.beginPath(); g.arc(px, py, faint ? 1.6 : 2.2, 0, 2 * Math.PI); g.fill(); }
  }
  g.globalAlpha = 1;
  // 有珠山への通り道
  for (const r of ev.rays) {
    const n = r.pts.length / 2, c = r.p ? P : S;
    g.strokeStyle = c; g.globalAlpha = (r.weak ? 0.45 : 1) * 0.25; g.lineWidth = 2; g.setLineDash([4, 4]);   // 回り込む弱い波は全体をうすく
    g.beginPath(); for (let i = 0; i < n; i++) g.lineTo(...Q(r.pts[2 * i], r.pts[2 * i + 1])); g.stroke();
    g.setLineDash([]); g.globalAlpha = r.weak ? 0.45 : 1;
    if (t <= 0) { g.globalAlpha = 1; continue; }
    // 進んだところまでを太く。S波の仲間も、核の中は P波として進むので P波の色にする
    const end = Math.min(t, r.time), m = Math.floor(Math.min(end / 2.0, n - 1));
    const q = rayAt(r, end);
    const seg = []; for (let i = 0; i <= m; i++) seg.push([r.pts[2 * i], r.pts[2 * i + 1]]); seg.push(q);
    g.lineWidth = r.refl || r.weak ? 2 : 3;
    for (let i = 1; i < seg.length; i++) {
      const [x0, y0] = seg[i - 1], [x1, y1] = seg[i];
      const mid = ((x0 + x1) / 2) ** 2 + ((y0 + y1) / 2) ** 2;
      g.strokeStyle = r.p || mid < cmb2 ? P : S;
      g.beginPath(); g.moveTo(...Q(x0, y0)); g.lineTo(...Q(x1, y1)); g.stroke();
    }
    if (t < r.time) {
      g.fillStyle = c; g.beginPath(); g.arc(...Q(q[0], q[1]), r.weak ? 3.5 : 5, 0, 2 * Math.PI); g.fill();
    }
    g.globalAlpha = 1;
  }
  drawShadow(g, Q);
  drawSurface(g, Q);
  if ($("carry").checked) for (const r of ev.rays) if (!r.refl && !r.weak) carryWave(g, Q, r);
  // 震源と有珠山
  const [ex, ey] = Q(0, META.R - ev.depth), [lx, ly] = Q(0, META.R * 1.06);
  star(g, ex, ey, 9, "#d32f2f");
  g.fillStyle = css("--ink"); g.font = "bold 13px sans-serif";
  const name = `震源(${ev.place})`, tw = g.measureText(name).width;
  let tx = lx < cx - 20 ? lx - tw : lx > cx + 20 ? lx : lx - tw / 2;
  tx = Math.max(4, Math.min(W - 4 - tw, tx));   // 枠からはみ出さない
  g.textAlign = "left";
  g.fillText(name, tx, ly + (ly > cy ? 14 : -4));
  const ux = cx, uy = cy - META.R * sc;
  const arrived = ev.rays.filter((r) => t >= r.time && !r.weak);   // 弱い波では輪を出さない
  if (arrived.length) {
    const age = t - Math.max(...arrived.map((r) => r.time));
    if (age < 30) { g.strokeStyle = css("--ink"); g.globalAlpha = 1 - age / 30; g.lineWidth = 2;
      g.beginPath(); g.arc(ux, uy, 8 + age * 0.8, 0, 2 * Math.PI); g.stroke(); g.globalAlpha = 1; }
  }
  g.fillStyle = css("--ink"); g.beginPath(); g.arc(ux, uy, 6, 0, 2 * Math.PI); g.fill();
  g.textAlign = "left"; g.font = "bold 13px sans-serif";
  g.fillText("有珠山", ux + 10, uy - 8);
  // 時刻
  g.textAlign = "left"; g.font = "bold 14px sans-serif"; g.fillStyle = css("--ink");
  // 時刻は、震源の名前と重ならないよう、震源が下半分なら左上、上半分なら左下に
  if (W >= 400) g.fillText(`発生から ${mmss(t)}`, 10, ly > cy ? 20 : H - 10);   // 狭い画面では下の欄の時刻を見る
}
// 運ばれる波形: 有珠山で記録された、その波が届いてから PACK_D 秒の揺れを、通り道にそって描く
//   τ 秒あとに有珠山に届く揺れは、いま「波の先頭が τ 秒前にいた所」にあるので、通り道の t − τ の位置に置く
//   そのままでは短すぎて見えないので、通り道の上では PACK_L / PACK_D 倍にのばす
const PACK_D = 40, PACK_L = 300, PACK_N = 240, PACK_AMP = 13;
function carryWave(g, Q, r) {
  if (t <= 0) return;
  const ws = waves(ev, band), k = META.stations.findIndex((s) => s.id === sndStation);
  const w = (ws[k] || ws.find((x) => x));
  if (!w) return;
  const pts = [];
  for (let j = 0; j <= PACK_N; j++) {
    const tau = PACK_D * j / PACK_N, tp = t - tau * PACK_L / PACK_D;
    if (tp < 0) break;                 // まだ震源から出ていない部分
    if (tp > r.time) continue;         // もう有珠山に届いた部分
    const i = Math.round((r.time + tau + ev.before) * ev.rate);
    const v = i < w.d.length && w.d[i] !== -128 ? w.d[i] / 127 : 0;
    const taper = Math.min(1, (PACK_N - j) / (PACK_N * 0.15), j / (PACK_N * 0.03));
    const q = rayAt(r, tp);
    pts.push({ q, v: v * taper });
  }
  if (pts.length < 3) return;
  // 小さな波(S波など)も形が見えるよう、波形ごとに最大 3 倍まで大きくする(40 秒間の全体で決めるので、運ぶ途中では変わらない)
  let peak = 0;
  for (let j = 0; j <= PACK_N; j++) {
    const i = Math.round((r.time + PACK_D * j / PACK_N + ev.before) * ev.rate);
    if (i < w.d.length && w.d[i] !== -128) peak = Math.max(peak, Math.abs(w.d[i]) / 127);
  }
  const gain = peak > 0 ? Math.min(3, 0.8 / peak, 1 / peak) : 1;
  for (const p of pts) p.v = Math.max(-1, Math.min(1, p.v * Math.max(gain, 1)));
  // 通り道に直角の向きに揺れの大きさだけずらす。波面と見分けやすいよう、濃い線に縁を付ける
  const sc = pts.map((p) => Q(p.q[0], p.q[1]));
  const line = sc.map((c, j) => {
    const a = sc[Math.max(0, j - 2)], b = sc[Math.min(sc.length - 1, j + 2)];
    const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
    return [c[0] - dy / len * pts[j].v * PACK_AMP, c[1] + dx / len * pts[j].v * PACK_AMP];
  });
  g.lineJoin = "round";
  for (const [color, width] of [[css("--panel"), 4], [css("--wave"), 1.5]]) {
    g.strokeStyle = color; g.lineWidth = width;
    g.beginPath(); for (const c of line) g.lineTo(c[0], c[1]); g.stroke();
  }
}
// P波の影: 震源から 103〜142° の地表の外側に灰色の帯(両側)
function drawShadow(g, Q) {
  const R = META.R * 1.035, [a0, a1] = META.shadow.map((d) => d * Math.PI / 180);
  g.strokeStyle = css("--muted"); g.globalAlpha = 0.35; g.lineWidth = 7; g.lineCap = "butt";
  for (const side of [1, -1]) {
    g.beginPath();
    for (let i = 0; i <= 40; i++) { const a = a0 + (a1 - a0) * i / 40; g.lineTo(...Q(side * R * Math.sin(a), R * Math.cos(a))); }
    g.stroke();
  }
  g.globalAlpha = 1;
  // 名前は、有珠山と反対側(左)の帯の外側に
  const am = (a0 + a1) / 2, [x, y] = Q(-R * 1.07 * Math.sin(am), R * 1.07 * Math.cos(am));
  g.fillStyle = css("--muted"); g.font = "bold 12px sans-serif"; g.textAlign = "left";
  const tw = g.measureText("P波の影").width, W = g.canvas.getBoundingClientRect().width;
  g.fillText("P波の影", Math.max(4, Math.min(W - 4 - tw, x - tw / 2)), y + 4);   // 枠からはみ出さない
}
// 表面波: 震源から地表にそって両側へ進む。速いほう(fast)と遅いほう(slow)の間を、波打つ帯で描く
//   地球の反対側(180°)を過ぎた分は描かない(反対側で両側からの波が出会う)
function drawSurface(g, Q) {
  const sf = ev.surface, R = META.R;
  const a1 = Math.min(Math.PI, sf.fast * t / R), a0 = Math.min(Math.PI, sf.slow * t / R);
  if (t <= 0 || a1 <= a0) return;
  const n = Math.max(8, Math.round((a1 - a0) * 400));
  g.strokeStyle = css("--sf"); g.lineWidth = 2.2; g.lineJoin = "round";
  for (const side of [1, -1]) {
    g.beginPath();
    for (let i = 0; i <= n; i++) {
      const f = i / n, a = a0 + (a1 - a0) * f;
      const env = Math.sin(Math.PI * f);                       // 帯の両端は小さく
      const rr = R + Math.sin(a * R / 260 * 2 * Math.PI) * 140 * env + 90;   // 地表の少し外側で波打たせる(波長は約 260 km)
      g.lineTo(...Q(side * rr * Math.sin(a), rr * Math.cos(a)));
    }
    g.stroke();
  }
}
function star(g, x, y, r, c) {
  g.beginPath();
  for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * 0.45 : r; g.lineTo(x + rr * Math.cos(a), y + rr * Math.sin(a)); }
  g.closePath(); g.fillStyle = c; g.fill();
}

// --- 波形 ---
function drawWaves() {
  const [g, W, H] = fit($("waves"));
  if (W < 200 || H < 100) return;
  g.clearRect(0, 0, W, H);
  const narrow = W < 500;   // スマートフォン: 拠点名の欄を狭く
  const row = narrow ? 12 : 14;   // 波の名前の段の高さ
  const left = narrow ? 80 : 96, right = 12, top = 8 + row * (ev.rays.length + 1), bottom = narrow ? 40 : 46;
  const t0 = Math.max(0, ev.first - 120), t1 = ev.end;
  const X = (s) => left + (s - t0) / (t1 - t0) * (W - left - right);
  const rows = META.stations.length, rh = (H - top - bottom) / rows;
  const ws = waves(ev, band);
  // 時間の目盛り(発生からの分)
  g.strokeStyle = css("--line"); g.fillStyle = css("--muted"); g.font = "12px sans-serif"; g.textAlign = "center"; g.lineWidth = 1;
  const tick = (t1 - t0) > 1200 ? 300 : (t1 - t0) > 600 ? 120 : 60;
  for (let s = Math.ceil(t0 / tick) * tick; s <= t1; s += tick) {
    g.beginPath(); g.moveTo(X(s), top); g.lineTo(X(s), H - bottom); g.stroke();
    g.fillText(`${Math.floor(s / 60)}分${s % 60 ? String(s % 60).padStart(2, "0") + "秒" : ""}`, X(s), H - bottom + 16);
  }
  if (!narrow) { g.textAlign = "right"; g.fillText("発生からの時間", W - right, H - 6); }
  // 表面波の見込みの帯(届き始めてから)
  const sf = ev.surface;
  if (t >= sf.t0 && sf.t0 < t1) {
    const xa = X(Math.max(sf.t0, t0)), xb = X(Math.min(sf.t1, t1, t));
    g.fillStyle = css("--sf"); g.globalAlpha = 0.12; g.fillRect(xa, top, xb - xa, H - bottom - top); g.globalAlpha = 1;
    g.fillStyle = css("--sf"); g.font = "bold 12px sans-serif"; g.textAlign = "left";
    g.fillText($("codes").checked ? "表面波の見込み(レイリー波など)" : "表面波の見込み", Math.min(xa + 3, W - right - 110), H - bottom - 6);
  }
  // 届いた波の線
  ev.rays.forEach((r, j) => {
    if (t < r.time || r.time < t0) return;
    g.strokeStyle = r.p ? css("--p") : css("--s"); g.lineWidth = 1.5; g.setLineDash(r.weak ? [2, 4] : [5, 4]);
    g.globalAlpha = r.weak ? 0.6 : 1;
    g.beginPath(); g.moveTo(X(r.time), 6 + row * (j + 1) - row + 2);   /* 名前の段の高さから引き、どの名前の線かわかるようにする */ g.lineTo(X(r.time), H - bottom); g.stroke(); g.setLineDash([]);
    g.fillStyle = g.strokeStyle; g.textAlign = "left"; g.font = narrow ? "bold 11px sans-serif" : "bold 12px sans-serif";
    // 名前は波形の上の段に置く(近い波の名前が重ならないよう 1 本ずつ段をずらし、最後の段も波形の上に収める)
    g.fillText(label(r), Math.min(X(r.time) + 3, W - right - g.measureText(label(r)).width), 6 + row * (j + 1));
    g.globalAlpha = 1;
  });
  // 波形(今の時刻まで)
  const rate = ev.rate, i0 = Math.round((t0 + ev.before) * rate);
  const now = Math.min(t, t1);
  ws.forEach((w, k) => {
    const yc = top + rh * (k + 0.5);
    g.fillStyle = css("--ink"); g.textAlign = "left"; g.font = narrow ? "bold 11px sans-serif" : "bold 13px sans-serif";
    g.fillText(META.stations[k].label + ($("sound").checked && META.stations[k].id === sndStation ? " ♪" : ""), 6, yc + 4);
    if (!w) { g.fillStyle = css("--muted"); g.font = "12px sans-serif"; g.fillText("記録なし", left + 6, yc + 4); return; }
    g.strokeStyle = css("--wave"); g.lineWidth = 1;
    const amp = rh * 0.48 / 127;
    const iEnd = Math.min(w.d.length, Math.round((now + ev.before) * rate));
    let pen = false;
    g.beginPath();
    const px0 = Math.floor(X(t0)), px1 = Math.ceil(X(now));
    const per = (t1 - t0) * rate / (W - left - right);   // 1 px あたりの点の数
    for (let px = px0; px < px1; px++) {
      const a = i0 + Math.floor((px - left) * per), b = Math.min(iEnd, i0 + Math.floor((px + 1 - left) * per));
      let lo = 127, hi = -127, ok = false;
      for (let i = a; i < Math.max(b, a + 1) && i < iEnd; i++) { const v = w.d[i]; if (v === -128) continue; ok = true; if (v < lo) lo = v; if (v > hi) hi = v; }
      if (!ok) { pen = false; continue; }
      if (!pen) { g.moveTo(px + 0.5, yc - hi * amp); pen = true; } else g.lineTo(px + 0.5, yc - hi * amp);
      g.lineTo(px + 0.5, yc - lo * amp);
    }
    g.stroke();
  });
  // 今の時刻の線
  if (t >= t0 && t <= t1) { g.strokeStyle = css("--ink"); g.lineWidth = 1; g.beginPath(); g.moveTo(X(t), top); g.lineTo(X(t), H - bottom); g.stroke(); }
  if (t < t0) {
    g.fillStyle = css("--muted"); g.textAlign = "center"; g.font = "bold 16px sans-serif";
    g.fillText(`最初の波が有珠山に届くまで あと ${mmss(ev.first - t)}`, (left + W) / 2, H / 2);
  }
  g.fillStyle = css("--muted"); g.textAlign = "left"; g.font = "12px sans-serif";
  g.fillText(narrow ? `上下動 ${META.bands[band][0]}〜${META.bands[band][1]} Hz・横は発生からの時間`
                     : `上下動 ${META.bands[band][0]}〜${META.bands[band][1]} Hz(拠点ごとに大きさをそろえる)`, narrow ? 6 : left, H - 6);
}

// --- 音 ---
//   音の波形(SOUND_RATE Hz)を BUF_RATE Hz の音のデータとして入れ、再生の速さ playbackRate で画面の速さに合わせる
//   鳴らしている間は、画面の時刻を音の時計から決める(ずれないように)
const BUF_RATE = 8000, MU = 255;
let actx = null, src = null, anchor = null, sndStation = null;
const buffers = {};
function soundBuffer(e, nm) {
  const key = e.no + "_" + nm;
  if (!buffers[key]) {
    const q = dec8(e.sound.d[nm]), buf = actx.createBuffer(1, q.length, BUF_RATE), ch = buf.getChannelData(0);
    for (let i = 0; i < q.length; i++) { const v = q[i] / 127; ch[i] = 0.9 * Math.sign(v) * (Math.pow(1 + MU, Math.abs(v)) - 1) / MU; }
    buffers[key] = buf;
  }
  return buffers[key];
}
function stopSound() {
  if (src) { try { src.stop(); } catch (_) {} src.disconnect(); src = null; }
  anchor = null;
}
function inFast() { return $("fast").checked && t < ev.first - 60; }
function startSound() {
  stopSound();
  if (!playing || !$("sound").checked || inFast() || !ev.sound.d[sndStation]) return;
  if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)();
  if (actx.state === "suspended") actx.resume();
  const buf = soundBuffer(ev, sndStation), rate = ev.sound.rate;
  const pos = (t - ev.sound.start) * rate;               // 音のデータの何点目から
  if (pos >= buf.length) return;
  src = actx.createBufferSource();
  src.buffer = buf;
  src.playbackRate.value = speed * rate / BUF_RATE;
  const gain = actx.createGain(); gain.gain.value = 0.8;
  src.connect(gain).connect(actx.destination);
  const now = actx.currentTime + 0.05;
  if (pos >= 0) src.start(now, pos / BUF_RATE); else src.start(now - pos / rate / speed, 0);
  anchor = { ctx: now, t: t };
}
function restartSound() { if (playing) startSound(); else stopSound(); restartFeel(); }

// --- 振動(TODO 58) ---
//   音の拠点の揺れ(音のデータ: 0.3〜15 Hz、50 Hz)を、画面の速さで 20 ms ごとのコマに分けて揺れの強さにし、再生に合わせて振動させる
//   (×40 なら 1 コマが記録の 0.8 秒)。雑音は最初の波の 5 秒前まで、いちばん大きい揺れを最大にする。早送りの間は振動しない
const feelCache = {};
function feelEnvelope(e, nm, F) {
  const key = `${e.no}_${nm}_${F}`;
  if (!feelCache[key]) {
    const q = dec8(e.sound.d[nm]), v = new Float64Array(q.length);
    for (let i = 0; i < q.length; i++) { const a = q[i] / 127; v[i] = Math.sign(a) * (Math.pow(1 + MU, Math.abs(a)) - 1) / MU; }
    const P = Object.assign({}, META.haptic, { frame: F, noiseBefore: e.first - 5 });
    const env = hapticEnvelope([{ v, step: 1 }], v.length, e.sound.rate, P, false);
    let peak = 0;
    for (const x of env) peak = Math.max(peak, x);
    feelCache[key] = hapticLevels(env, hapticNoise(env, e.sound.start, e.sound.rate, P), peak, P);
  }
  return feelCache[key];
}
function startFeel() {
  hapticStop();
  if (!playing || !$("feel").checked || inFast() || !ev.sound.d[sndStation]) return;
  const rate = ev.sound.rate, F = Math.max(1, Math.round(0.02 * speed * rate)), levels = feelEnvelope(ev, sndStation, F);
  const pos = (t - ev.sound.start) * rate / F, k0 = Math.max(0, Math.floor(pos)), lead = Math.max(0, Math.round(-pos));
  if (k0 >= levels.length) return;
  const part = new Float64Array(lead + levels.length - k0);
  part.set(levels.subarray(k0), lead);      // 音のデータが始まる前は 0
  hapticPlay(part, F / rate / speed, { sharpness: META.haptic.sharpness[1], shake: document.querySelector(".stage") });
}
function restartFeel() { if (playing) startFeel(); else hapticStop(); }

function draw() {
  drawEarth(); drawWaves();
  $("seek").value = t;
  $("clock").innerHTML = `<span class="lbl">発生から </span>${mmss(t)}`;
}
function tick(now) {
  if (!playing) { last = null; return; }
  if (anchor && actx) {
    t = Math.min(ev.end, anchor.t + Math.max(0, actx.currentTime - anchor.ctx) * speed);
  } else if (last != null) {
    const dt = (now - last) / 1000;
    t = Math.min(ev.end, t + dt * speed * (inFast() ? FAST : 1));
    if (!inFast() && $("sound").checked && !src) startSound();   // 早送りが終わったら音を出す
  }
  if (t >= ev.end) setPlaying(false);
  else if (!inFast() && $("feel").checked && !hapticPlaying()) startFeel();   // 早送りが終わったら振動させる
  last = now;
  draw();
  if (playing) requestAnimationFrame(tick);
}
function setPlaying(on) {
  playing = on;
  $("play").textContent = on ? "❚❚ 止める" : "▶ 再生";
  if (on) { if (t >= ev.end) t = 0; last = null; startSound(); startFeel(); requestAnimationFrame(tick); }
  else { stopSound(); hapticStop(); }
}
function buttons(box, items, current, onPick) {
  for (const [key, text] of items) {
    const b = document.createElement("button");
    b.textContent = text; b.dataset.key = key;
    b.onclick = () => { onPick(key); mark(box, key); };
    box.appendChild(b);
  }
  mark(box, current);
}
function mark(box, key) { for (const b of box.querySelectorAll("button")) b.classList.toggle("on", b.dataset.key == key); }
function pick(no) {
  ev = META.events.find((e) => e.no == no);
  band = ev.band; mark($("bands"), band);
  t = 0; $("seek").max = ev.end;
  const firstRay = ev.rays.filter((r) => !r.weak).reduce((a, b) => (a.time < b.time ? a : b));
  $("info").textContent = `${ev.origin}(日本時間) ${ev.place} M${ev.M} 深さ ${Math.round(ev.depth)} km ・ 有珠山から ${Math.round(ev.deg)}°(${ev.km.toLocaleString()} km)` +
    ` ・ 最初に届く波: ${firstRay.label}(発生の ${mmss(firstRay.time)} 後)`;
  // 記録の無い拠点の音は選べない(選んでいた拠点に記録が無ければ、記録のある最初の拠点にする)
  //   選んだ拠点(prefStation)は覚えておき、記録のある地震ではその拠点にもどす
  for (const b of $("sstations").querySelectorAll("button")) b.disabled = !ev.sound.d[b.dataset.key];
  sndStation = ev.sound.d[prefStation] ? prefStation : (META.stations.map((s) => s.id).find((id) => ev.sound.d[id]) || prefStation);
  mark($("sstations"), sndStation);
  setPlaying(false); draw(); saveSettings();
}
// --- 設定をブラウザに覚える(このブラウザの localStorage。ほかの人やサーバーには送らない) ---
//   速さ・早送り・波の記号・波形を運ぶ・音を出す・音の拠点と、最後に見た地震。フィルタは地震ごとに見やすいものから始める
const STORE = "usu_shake_world:settings";
function loadSettings() { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch (_) { return {}; } }
function saveSettings() {
  try {
    localStorage.setItem(STORE, JSON.stringify({ speed, station: prefStation, event: ev && ev.no,
      fast: $("fast").checked, codes: $("codes").checked, carry: $("carry").checked, sound: $("sound").checked, feel: $("feel").checked }));
  } catch (_) {}
}
const saved = loadSettings();
if (SPEEDS.includes(saved.speed)) speed = saved.speed;
for (const id of ["fast", "codes", "carry", "sound", "feel"]) if (typeof saved[id] === "boolean") $(id).checked = saved[id];
let prefStation = META.stations.some((s) => s.id === saved.station) ? saved.station : META.events[0].sound_station;
const evs = [...META.events].sort((a, b) => a.deg - b.deg);
buttons($("events"), evs.map((e) => [e.no, `${e.place} ${Math.round(e.deg)}°`]), evs[0].no, (no) => { pick(no); $("evsel").value = no; });
for (const e of evs) $("evsel").add(new Option(`${e.place} ${Math.round(e.deg)}°`, e.no));
$("evsel").onchange = () => { pick($("evsel").value); mark($("events"), $("evsel").value); };
// 狭い画面: 設定はたたんでおき、フィルタは下に固定した欄に出す(設定を開かずに切り替え、波形の変わり方を見られるように)
const narrowMq = matchMedia("(max-width: 760px)");
if (narrowMq.matches) $("opts").open = false;
function placeBands() {
  if (narrowMq.matches) $("dockbands").appendChild($("bands"));
  else $("bandshome").after($("bands"));
  if (ev) draw();
}
narrowMq.addEventListener("change", placeBands);
placeBands();
buttons($("speeds"), SPEEDS.map((s) => [s, `×${s}`]), speed, (s) => { speed = +s; restartSound(); saveSettings(); });
buttons($("sstations"), META.stations.map((s) => [s.id, s.label]), prefStation, (nm) => { sndStation = prefStation = nm; restartSound(); draw(); saveSettings(); });
sndStation = prefStation;
buttons($("bands"), Object.entries(META.bands).map(([k, v]) => [k, `${v[0]}〜${v[1]} Hz`]), band, (b) => { band = b; draw(); });
$("play").onclick = () => setPlaying(!playing);
$("seek").oninput = () => { t = +$("seek").value; restartSound(); draw(); };
$("sound").onchange = () => { restartSound(); draw(); saveSettings(); };
$("feel").onchange = () => { restartFeel(); saveSettings(); };
$("fast").onchange = () => { restartSound(); saveSettings(); };
$("codes").onchange = () => { draw(); saveSettings(); };
$("carry").onchange = () => { draw(); saveSettings(); };
window.addEventListener("resize", draw);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
// 最後に見た地震から始める
const first = evs.find((e) => e.no === saved.event) || evs[0];
pick(first.no); $("evsel").value = first.no; mark($("events"), first.no);
</script>
</body>
</html>
"""

def main():
  from hypocenter import load_stations
  import jma_match
  ap = argparse.ArgumentParser(description="世界の地震が届く(地球の断面のアニメーションと 4 拠点の波形)のページを書き出す")
  ap.add_argument("-o", "--output", help=f"出力の HTML(省略すると {OUTPUT})")
  ap.add_argument("--quiz", type=int, nargs="*", help="使う問題の番号(省略するとクイズの海外の地震すべて)")
  args = ap.parse_args()
  center = jma_match.array_center(load_stations(app_paths.ETC_DIR))
  rows = [r for r in world_rows() if not args.quiz or int(r["問題"]) in args.quiz]
  events = [event_data(int(r["問題"]), find_quake(datetime.strptime(r["基準の時刻"], "%Y-%m-%d %H:%M:%S"), center),
                       PLACES.get(int(r["問題"]), "")) for r in rows]
  if not args.quiz:
    events += [event_data(100 + i, find_extra(when, center), place) for i, (when, place) in enumerate(EXTRA)]
  out = Path(args.output) if args.output else OUTPUT
  size = write_page(out, events)
  print(f"{out} {size / 1e6:.1f} MB")

if __name__ == "__main__":
  main()
