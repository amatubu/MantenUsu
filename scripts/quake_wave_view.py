# quake_wave_view.py
# 有珠山付近の地震の波形を、スマートフォンのブラウザで見るための HTML(ファイル1つ)に書き出す(TODO 40)
#   一覧から地震を選ぶと、満点の4拠点の波形を縦に並べて出す。ピンチで時間軸の拡大・縮小、1本指で左右の移動、
#   ダブルタップで全体に戻す。描画は Canvas 2D で、外部のライブラリは読まない(インターネットが無くても開ける)
#
#   波形はアプリと同じ処理(win_pipeline の WIN → SAC。Mimatsu のチャンネルの読み替えを含む)で読む
#   SAC の値(nm/s)はチャンネル表の換算でカウントの整数に戻し、平均を引いて持つ
#   HTML に入れる形: 差分 → 32 ビット整数のバイトを桁ごとに並べ替え → gzip → base64
#     (桁ごとに並べると、上の桁がほとんど同じ値になるので gzip がよく効く。実データでそのままより約2割小さい)
#     地震を選んだときだけ、ブラウザの DecompressionStream で戻す(iOS は 16.4 以降)
#   表示の既定は、到着時刻の検出と同じ帯域のフィルタ(hypocenter.pick_filtered と同じ計算をブラウザで行う)。元の波形にも切り替えられる
#   入れるのは満点の拠点の波形だけ(V-net などの再配布できない外部の観測点の波形は入れない)
#
#     python3 scripts/quake_wave_view.py [-o 出力のHTML]
#   省略すると batch_plots/quakes/locate/usu_quakes_waves.html に書き出す。3Dの図(usu_quakes_3d.html)と同じフォルダにあると、
#   3Dの図で「波形」を「拡大できる波形」にして「波形を表示」を押す(または波形の枠のリンクを押す)と、別のタブでこのページを開く
import argparse
import base64
import gzip
import html
import json
import tempfile
from datetime import timedelta
from pathlib import Path

import numpy as np
import obspy
from scipy.signal import iirfilter, zpk2sos

import pick_store
import station_names
from hypocenter import LOCAL_KINDS, PICK_BAND, LocalProjection, depth_short
from quake_3d_view import USU_LIST_TITLE, WAVE_PAGE_FILE, event_key, load_hour_from_zips
from quake_list import TYPE_QUAKE
from quake_locate import candidate_window, event_type

BEFORE_SECONDS = 10    # 発生時刻の何秒前から入れるか
AFTER_SECONDS = 50     # 発生時刻の何秒後まで入れるか
SAMPLE_RATE = 250      # 入れる波形のサンプリング [Hz](記録の 250 Hz の約数にする。小さくすると区間の平均に間引く)
COMPONENTS = ("U", "N", "E")
CHANNEL_TABLE = "manten_ch.tbl"
NM_PER_METER = 1e9
FILTER_CORNERS = 4     # フィルタの次数(hypocenter.pick_filtered と同じ)


# --- 波形の符号化 ---

# 整数の並びを、HTML に入れる文字列にする(差分 → バイトを桁ごとに並べ替え → gzip → base64)
def encode_samples(values):
  v = np.asarray(values, dtype=np.int64)
  diff = np.diff(v, prepend=0)
  if diff.size and (diff.min() < -2**31 or diff.max() >= 2**31):
    raise ValueError("差分が 32 ビットの整数に収まりません")
  planes = np.frombuffer(diff.astype("<i4").tobytes(), dtype=np.uint8).reshape(-1, 4).T.tobytes()
  return base64.b64encode(gzip.compress(planes, 9, mtime=0)).decode("ascii")

# encode_samples の逆(ブラウザの側の decodeSamples と同じ計算)
def decode_samples(text):
  planes = np.frombuffer(gzip.decompress(base64.b64decode(text)), dtype=np.uint8).reshape(4, -1)
  diff = np.frombuffer(planes.T.tobytes(), dtype="<i4")
  return np.cumsum(diff, dtype=np.int64)

# チャンネル表から、1カウントの大きさ [nm/s] を読む。戻り値 ... {(拠点名, 成分): nm/s}
#   SAC の値 = カウント × 量子化ステップ幅 [V] / (感度 [V/(m/s)] × 増幅) を nm/s にしたもの
def count_steps(etc_dir):
  steps = {}
  for line in (Path(etc_dir) / CHANNEL_TABLE).read_text(encoding="utf-8").splitlines():
    c = line.split()
    if len(c) >= 13 and not c[0].startswith("#"):
      steps[(c[3], c[4])] = float(c[12]) / (float(c[7]) * 10 ** (float(c[11]) / 20)) * NM_PER_METER
  return steps

# True が続く範囲の並び [[開始, 終了(含まない)], ...]
def true_ranges(mask):
  edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
  return [[int(a), int(b)] for a, b in zip(edges[::2], edges[1::2])]

# 拠点・成分の波形を、t0 から n 点の整数にする
#   戻り値 ... {"d": 符号化した値, "s": 1カウントの nm/s, "g": 欠測の範囲}
#   記録が無い・値が変わらないとき、サンプリングが SAMPLE_RATE の整数倍でないときは None
def trace_samples(st, station, comp, t0, n, step, rate=SAMPLE_RATE):
  traces = [tr for tr in st if tr.stats.station == station and tr.stats.channel.endswith(comp)]
  if not traces:
    return None
  tr = traces[0]
  ratio = tr.stats.sampling_rate / rate
  k = int(round(ratio))
  if k < 1 or abs(ratio - k) > 1e-6:
    return None
  grid = np.full(n * k, np.nan)
  data = np.ma.filled(np.ma.asarray(tr.data, dtype=float), np.nan)
  first = int(round((tr.stats.starttime - obspy.UTCDateTime(t0)) * tr.stats.sampling_rate))
  a, b = max(first, 0), min(first + len(data), len(grid))
  if a >= b:
    return None
  grid[a:b] = data[a - first:b - first]
  if k > 1:
    grid = np.ma.masked_invalid(grid).reshape(n, k).mean(axis=1).filled(np.nan)
  missing = np.isnan(grid)
  if missing.all() or np.ptp(grid[~missing]) == 0:
    return None
  counts = np.zeros(n, dtype=np.int64)
  counts[~missing] = np.round(grid[~missing] / step).astype(np.int64)
  counts[~missing] -= int(round(counts[~missing].mean()))
  return {"d": encode_samples(counts), "s": round(step, 6), "g": true_ranges(missing)}


# ブラウザで使うフィルタの係数: [[b0, b1, b2, a1, a2], ...](a0 は 1。obspy の bandpass と同じ設計)
def filter_sections(band=PICK_BAND, rate=SAMPLE_RATE):
  nyquist = rate / 2
  z, p, k = iirfilter(FILTER_CORNERS, [band[0] / nyquist, band[1] / nyquist], btype="band", ftype="butter", output="zpk")
  return [[float(c) for c in (*row[:3], *row[4:])] for row in zpk2sos(z, p, k)]


# --- 地震ごとのデータ ---

# 期間の波形を読む(アプリと同じ WIN → SAC の処理。WIN は1分ごとなので、その分の範囲だけ変換する)
def load_stream(t0, t1):
  import app_paths
  import win_pipeline
  first, last = t0.replace(second=0, microsecond=0), t1.replace(second=0, microsecond=0)
  with tempfile.TemporaryDirectory(prefix="UsuMantenViewer_waves_") as work:
    work = Path(work)
    zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(first, last + timedelta(minutes=1)))
    st = obspy.Stream()
    if win_pipeline.zips_to_sacs(zips, first, last, work, app_paths.TOOLS_DIR, app_paths.ETC_DIR):
      for p in win_pipeline.find_sacs(work):
        st += obspy.read(str(p))
  st.merge(method=1, fill_value=None)
  return st

# 発生時刻(結果の基準の時刻 + 震源の発生時刻の秒)
def origin_time(r):
  return r["ref"] + timedelta(seconds=r["loc"]["origin"])

# 波形の最初の点の時刻(発生時刻の BEFORE_SECONDS 前を、サンプルの刻みに切り下げたもの)
def window_start(origin, rate=SAMPLE_RATE):
  t = origin - timedelta(seconds=BEFORE_SECONDS)
  tick = 1_000_000 // rate
  return t - timedelta(microseconds=t.microsecond % tick)

# 満点の拠点の揺れから M を推定する(震源推定の画面と同じ magnitude.estimate)。求められなければ None
def estimated_magnitude(st, r, stations, proj):
  import magnitude
  loc, origin = r["loc"], obspy.UTCDateTime(origin_time(r))
  traces = {}
  for tr in st:
    if tr.stats.station in stations and tr.stats.channel.endswith("U"):
      tr = tr.copy()
      tr.data = np.ma.filled(tr.data, 0)
      traces[tr.stats.station] = tr
  filtered = magnitude.filtered_traces(traces)
  depth = loc["depth"]
  if loc.get("at_top") and loc.get("depth_max") is not None:
    depth = (loc["depth_min"] + loc["depth_max"]) / 2
  distances = {}
  for nm in filtered:
    x, y = proj.to_xy(*stations[nm][:2])
    distances[nm] = float(np.sqrt((loc["x"] - x) ** 2 + (loc["y"] - y) ** 2 + (depth + stations[nm][2] / 1000) ** 2))
  m, _values = magnitude.estimate(filtered, origin - magnitude.SIGNAL_BEFORE, origin + magnitude.SIGNAL_AFTER, distances)
  return m

# 図に引く到着時刻: {拠点名: {"p": 発生時刻からの秒, "pm": 手で指定したか, "s": …, "sm": …}}
#   保存した記録には、手で直した値と、自動の検出のまま保存された値が混ざっている
def pick_lines(r, stations, origin, p_manual, s_manual):
  lines = {}
  for key, values, manual in (("p", r.get("picks") or {}, p_manual), ("s", r.get("s_picks") or {}, s_manual)):
    for name, sec in values.items():
      if name in stations:
        t = (r["ref"] + timedelta(seconds=sec) - origin).total_seconds()
        lines.setdefault(name, {}).update({key: round(t, 3), key + "m": name in manual})
  return lines

# 地震1件: (一覧に出す情報, {内部の拠点名: {成分: 波形}})
#   到着時刻は震源を求めるのに使った値。保存した記録が無い地震は、すべて自動の検出の値になる
def event_data(no, cand, r, st, stations, steps, proj):
  origin = origin_time(r)
  start = window_start(origin)
  n = int(round((BEFORE_SECONDS + AFTER_SECONDS) * SAMPLE_RATE))
  waves = {}
  for name in sorted(stations):
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
  picks = pick_lines(r, stations, origin, p_manual, s_manual)
  q = cand["jma"]
  if q and q.get("M") is not None:
    mag, mag_source = f"{q['M']:.1f}", "気象庁"
  else:
    m = estimated_magnitude(st, r, stations, proj)
    mag, mag_source = (f"{m:.1f}", "推定") if m is not None else ("", "")
  etype = event_type(cand)
  info = {"no": no, "key": event_key(cand), "time": f"{origin:%Y-%m-%d %H:%M:%S}.{origin.microsecond // 100000}",
          "type": "" if etype == TYPE_QUAKE else etype, "m": mag, "mSource": mag_source,
          "depth": f"{depth_short(r['loc'])} km", "t0": round((start - origin).total_seconds(), 6), "n": n, "picks": picks}
  return info, waves


# --- HTML ---

# <script> の中に安全に置ける JSON
def script_json(value):
  return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

# HTML を書き出す
#   events ... [(一覧に出す情報, 波形), ...](event_data の戻り値の並び)
#   stations ... 内部の拠点名の並び(表示の名前は station_names の設定に従う)
def write_wave_view(path, events, stations, title):
  path = Path(path)
  meta = {"title": title, "rate": SAMPLE_RATE, "comps": list(COMPONENTS),
          "filter": {"low": PICK_BAND[0], "high": PICK_BAND[1], "sections": filter_sections()},
          "stations": [{"id": nm, "label": station_names.short(nm)} for nm in stations],
          "events": [info for info, _waves in events]}
  # 波形の間に小さなスクリプトを置き、読み込めた地震の数を「読み込み中」の表示に出す(回線が遅いと何も出ないため)
  blocks = "\n".join(f'<script type="application/json" id="w{info["no"]}">{script_json(waves)}</script>\n'
                      f'<script>loadingStep({k})</script>'
                      for k, (info, waves) in enumerate(events, 1))
  page = (PAGE.replace("__TITLE__", html.escape(title))
          .replace("__META__", script_json(meta))
          .replace("__WAVES__", blocks)
          .replace("__COUNT__", str(len(events))))
  mb = len(page.encode("utf-8")) / 1e6
  page = page.replace("__SIZE__", f"{mb:.1f}" if mb >= 1 else f"{mb:.2f}")
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(page, encoding="utf-8")
  return path.stat().st_size

PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__TITLE__の波形</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #ffffff; --panel: #f4f5f7; --ink: #1c2126; --muted: #68727d; --line: #dfe3e8; --grid: #eceff2;
  --accent: #1565c0; --p: #1565c0; --s: #d84e00; --gap: #e4e7eb; --wave: #1c2126;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1f2428; --ink: #e8ecef; --muted: #9aa5af; --line: #333a41; --grid: #24292e;
    --accent: #6cb0f5; --p: #6cb0f5; --s: #ff9a57; --gap: #2b3137; --wave: #e8ecef;
  }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink);
  font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
  -webkit-text-size-adjust: 100%; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
[hidden] { display: none !important; }
#list { max-width: 640px; margin: 0 auto; padding: 0 16px calc(24px + env(safe-area-inset-bottom)); }
#list h1 { font-size: 20px; margin: 0; padding: calc(16px + env(safe-area-inset-top)) 0 4px; }
#count, .note { color: var(--muted); font-size: 13px; }
#events { list-style: none; margin: 12px 0; padding: 0; border-top: 1px solid var(--line); }
#events button { display: flex; width: 100%; min-height: 56px; align-items: center; gap: 12px; padding: 8px 0;
  text-align: left; border-bottom: 1px solid var(--line); }
.no { width: 2em; color: var(--muted); font-size: 13px; font-variant-numeric: tabular-nums; }
.when { flex: 1; min-width: 0; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; margin-left: 6px; padding: 0 6px; border-radius: 4px; background: var(--panel);
  color: var(--muted); font-size: 12px; }
.size { text-align: right; font-size: 14px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.size small { display: block; color: var(--muted); font-size: 12px; }
#view { position: fixed; inset: 0; display: flex; flex-direction: column; height: 100vh; height: 100dvh;
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
header { display: flex; align-items: center; gap: 2px; min-height: 48px; padding: 0 4px; border-bottom: 1px solid var(--line); }
header button { min-width: 44px; height: 44px; color: var(--accent); font-size: 17px; }
header button:disabled { color: var(--line); cursor: default; }
#back { padding: 0 6px; white-space: nowrap; }
#heading { flex: 1; min-width: 0; text-align: center; line-height: 1.2; }
#headTime { font-size: 14px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 360px) { .backText { display: none; } #headTime { font-size: 13px; } header button { min-width: 38px; } }
#headInfo { color: var(--muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#tools { display: flex; gap: 6px; padding: 6px 8px; }
.seg { flex: 1 1 auto; display: flex; padding: 2px; border-radius: 8px; background: var(--panel); }
.seg button { flex: 1 1 auto; height: 36px; padding: 0 5px; border-radius: 6px; font-size: 12px; color: var(--muted); white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--bg); color: var(--ink); font-weight: 600;
  box-shadow: 0 0 0 1px var(--line); }
#stage { position: relative; flex: 1; min-height: 0; }
#wave { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none;
  -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
#status { position: absolute; left: 16px; right: 16px; top: 40%; text-align: center; color: var(--muted); font-size: 14px; pointer-events: none; }
#loading { display: flex; align-items: center; gap: 12px; margin: 12px 0; padding: 12px; border-radius: 8px;
  background: var(--panel); font-size: 14px; }
#loading .spin { flex: none; width: 24px; height: 24px; border: 3px solid var(--line); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.9s linear infinite; }
#loading small { display: block; color: var(--muted); font-size: 12px; }
#loadingBar { height: 4px; margin-top: 6px; border-radius: 2px; background: var(--line); overflow: hidden; }
#loadingBar div { width: 0; height: 100%; background: var(--accent); }
@keyframes spin { to { transform: rotate(360deg); } }
.warn { margin: 16px 0; padding: 12px; border-radius: 8px; background: var(--panel); font-size: 14px; }
</style>
</head>
<body>
<main id="list">
  <h1>__TITLE__</h1>
  <div id="loading"><div class="spin"></div><div style="flex:1">
    <div id="loadingText">波形を読み込み中… 0 / __COUNT__ 件</div>
    <small>ページ全体で約 __SIZE__ MB あります。回線が遅いと少し時間がかかります</small>
    <div id="loadingBar"><div></div></div></div></div>
  <script>
  function loadingStep(k) {
    document.getElementById("loadingText").textContent = `波形を読み込み中… ${k} / __COUNT__ 件`;
    document.querySelector("#loadingBar div").style.width = (100 * k / __COUNT__) + "%";
  }
  </script>
  <div id="count"></div>
  <noscript><p class="warn">プレビューの画面では動きません。Safari などのブラウザで開いてください。</p></noscript>
  <p class="warn" id="unsupported" hidden>このブラウザは圧縮の展開(DecompressionStream)に対応していません。iOS 16.4 以降の Safari で開いてください。</p>
  <ul id="events"></ul>
  <p class="note">時刻は日本時間の発生時刻(満点の到着時刻から求めたもの)。M は気象庁の値で、無い地震は満点の揺れからの推定(±0.5 程度)です。P・S の線の実線は手で直した時刻、破線で「自動」と付くものは自動の検出の時刻です。</p>
  <p class="note">波形はピンチで拡大・縮小、1本指で左右に移動、ダブルタップで全体に戻ります。振幅は見えている範囲に合わせます。</p>
  <p class="note" id="filterNote"></p>
</main>
<section id="view" hidden>
  <header>
    <button id="back" type="button" aria-label="一覧に戻る">‹<span class="backText"> 一覧</span></button>
    <div id="heading"><div id="headTime"></div><div id="headInfo"></div></div>
    <button id="prev" type="button" aria-label="前の地震">◀</button>
    <button id="next" type="button" aria-label="次の地震">▶</button>
  </header>
  <div id="tools">
    <div class="seg" role="group" aria-label="成分">
      <button id="compU" type="button">上下動</button><button id="comp3" type="button">3成分</button>
    </div>
    <div class="seg" role="group" aria-label="振幅">
      <button id="ampEach" type="button">拠点別</button><button id="ampCommon" type="button">共通</button>
    </div>
    <div class="seg" role="group" aria-label="フィルタ">
      <button id="filterOn" type="button">フィルタ</button><button id="filterOff" type="button">元波形</button>
    </div>
  </div>
  <div id="stage"><canvas id="wave" aria-label="波形。ピンチで拡大・縮小、ドラッグで移動、ダブルタップで全体に戻す"></canvas><div id="status"></div></div>
</section>
<script type="application/json" id="meta">__META__</script>
__WAVES__
<script>
"use strict";
const META = JSON.parse(document.getElementById("meta").textContent);
const COMP_NAMES = { U: "上下", N: "南北", E: "東西" };
const MIN_SPAN_SECONDS = 0.1;    // 拡大の限度(画面の幅に入る秒)
const CACHE_SIZE = 4;            // 戻した波形を覚えておく地震の数
const $ = id => document.getElementById(id);
const canvas = $("wave"), ctx = canvas.getContext("2d");
const S = { idx: -1, data: null, three: false, common: false, filtered: true, v0: 0, span: 1, dpr: 1, token: 0, fromList: false, listY: 0 };
const cache = new Map();
let colors = {}, drawQueued = false;

// --- 波形を戻す ---
async function gunzipBase64(text) {
  const bin = atob(text), bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
// バイトの桁ごとの並びから差分に戻し、足し上げる(quake_wave_view.decode_samples と同じ計算)
function decodeSamples(b) {
  const n = b.length / 4, out = new Int32Array(n);
  let sum = 0;
  for (let i = 0; i < n; i++) {
    sum = (sum + (b[i] | b[n + i] << 8 | b[2 * n + i] << 16 | b[3 * n + i] << 24)) | 0;
    out[i] = sum;
  }
  return out;
}
// 最小・最大の段階データ(4 点ずつまとめていく)。levels[k] は 4^(k+1) 点ごとの値
function buildLevels(v) {
  const levels = [];
  let mins = v, maxs = v, size = 1;
  while (mins.length > 16) {
    const n = Math.floor(mins.length / 4), lo = new Int32Array(n), hi = new Int32Array(n);
    for (let i = 0; i < n; i++) {
      let a = mins[4 * i], b = maxs[4 * i];
      for (let j = 1; j < 4; j++) {
        if (mins[4 * i + j] < a) a = mins[4 * i + j];
        if (maxs[4 * i + j] > b) b = maxs[4 * i + j];
      }
      lo[i] = a; hi[i] = b;
    }
    size *= 4;
    levels.push({ size, lo, hi });
    mins = lo; maxs = hi;
  }
  return levels;
}
// [a, b) の最小・最大を out に入れる(そろった区間は上の段階の値を使う)
function rangeOf(tr, a, b, out) {
  const v = tr.v, levels = tr.levels;
  let lo = Infinity, hi = -Infinity, i = a;
  while (i < b) {
    let done = false;
    for (let k = levels.length - 1; k >= 0; k--) {
      const L = levels[k], s = L.size;
      if (i % s === 0 && i + s <= b && i / s < L.lo.length) {
        const j = i / s;
        if (L.lo[j] < lo) lo = L.lo[j];
        if (L.hi[j] > hi) hi = L.hi[j];
        i += s; done = true;
        break;
      }
    }
    if (!done) {
      if (v[i] < lo) lo = v[i];
      if (v[i] > hi) hi = v[i];
      i++;
    }
  }
  out.lo = lo; out.hi = hi;
}
// 到着時刻の検出と同じフィルタ(往復でかけるので時間のずれがない)。結果はカウントの整数に丸める
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
  const x = new Float64Array(v), out = new Int32Array(v.length);
  applySections(x); x.reverse(); applySections(x); x.reverse();
  for (let i = 0; i < x.length; i++) out[i] = Math.round(x[i]);
  return out;
}
// 今の表示に使う波形(フィルタの結果は、初めて使うときに作って覚えておく)
function traceOf(stationId, comp) {
  const tr = S.data[stationId] && S.data[stationId][comp];
  if (!tr || !S.filtered) return tr;
  if (!tr.filtered) {
    const v = filterSamples(tr.v);
    tr.filtered = { v, levels: buildLevels(v), step: tr.step, gaps: tr.gaps };
  }
  return tr.filtered;
}
async function loadWaves(ev) {
  if (cache.has(ev.no)) {
    const hit = cache.get(ev.no);
    cache.delete(ev.no); cache.set(ev.no, hit);
    return hit;
  }
  const raw = JSON.parse($("w" + ev.no).textContent), jobs = [], data = {};
  for (const [name, comps] of Object.entries(raw)) {
    data[name] = {};
    for (const [comp, item] of Object.entries(comps)) {
      jobs.push(gunzipBase64(item.d).then(bytes => {
        const v = decodeSamples(bytes);
        data[name][comp] = { v, levels: buildLevels(v), step: item.s, gaps: item.g };
      }));
    }
  }
  await Promise.all(jobs);
  cache.set(ev.no, data);
  while (cache.size > CACHE_SIZE) cache.delete(cache.keys().next().value);
  return data;
}

// --- 描く ---
function readColors() {
  const css = getComputedStyle(document.documentElement);
  for (const k of ["bg", "ink", "muted", "line", "grid", "p", "s", "gap", "wave"]) colors[k] = css.getPropertyValue("--" + k).trim();
}
function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); });
}
function resize() {
  const r = canvas.getBoundingClientRect();
  if (!r.width || !r.height) return;
  S.dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * S.dpr);
  canvas.height = Math.round(r.height * S.dpr);
  draw();
}
function formatAmp(nm) {
  if (nm >= 1e6) return (nm / 1e6).toPrecision(3) + " mm/s";
  if (nm >= 1e3) return (nm / 1e3).toPrecision(3) + " µm/s";
  return nm.toPrecision(3) + " nm/s";
}
function tickStep(seconds) {
  for (const m of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50]) if (m >= seconds) return m;
  return 100;
}
function labelBox(text, x, y, align, color) {
  const d = S.dpr, w = ctx.measureText(text).width, left = align === "right" ? x - w : x;
  ctx.fillStyle = colors.bg; ctx.globalAlpha = 0.78;
  ctx.fillRect(left - 2 * d, y - 1 * d, w + 4 * d, 14 * d);
  ctx.globalAlpha = 1; ctx.fillStyle = color; ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(text, left, y);
}
function drawTrace(tr, top, h, center, half, n) {
  const W = canvas.width, d = S.dpr, v = tr.v, spp = S.span / W;
  const mid = top + h / 2, gain = half > 0 ? (h / 2 - 2 * d) / (half / tr.step) : 0, c = center / tr.step;
  const yOf = value => mid - (value - c) * gain;
  ctx.save();
  ctx.beginPath(); ctx.rect(0, top, W, h); ctx.clip();
  ctx.fillStyle = colors.gap;
  for (const [a, b] of tr.gaps) {
    const x0 = (a - S.v0) / spp, x1 = (b - S.v0) / spp;
    if (x1 > 0 && x0 < W) ctx.fillRect(x0, top, x1 - x0, h);
  }
  ctx.strokeStyle = colors.wave; ctx.fillStyle = colors.wave;
  ctx.beginPath();
  if (spp < 2) {           // 拡大したときは実サンプルの折れ線
    const a = Math.max(0, Math.floor(S.v0) - 1), b = Math.min(n - 1, Math.ceil(S.v0 + S.span) + 1);
    for (let i = a; i <= b; i++) {
      const x = (i - S.v0) / spp, y = yOf(v[i]);
      if (i === a) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.lineWidth = Math.max(1, d * 0.8);
    ctx.lineJoin = "round";
    ctx.stroke();
    if (1 / spp >= 6 * d) {   // 点が離れて見えるほど拡大したら、サンプルの位置も示す
      ctx.beginPath();
      for (let i = a; i <= b; i++) {
        const x = (i - S.v0) / spp, y = yOf(v[i]);
        ctx.moveTo(x + 1.5 * d, y); ctx.arc(x, y, 1.5 * d, 0, 6.3);
      }
      ctx.fill();
    }
  } else {                 // 画面の1ピクセルごとの最小・最大
    const out = { lo: 0, hi: 0 };
    let prevTop = null, prevBottom = null;
    for (let px = 0; px < W; px++) {
      const a = Math.max(0, Math.floor(S.v0 + px * spp)), b = Math.min(n, Math.floor(S.v0 + (px + 1) * spp));
      if (b <= a) continue;
      rangeOf(tr, a, b, out);
      let y0 = yOf(out.hi), y1 = yOf(out.lo);
      const t0 = y0, t1 = y1;
      if (prevBottom !== null) {   // となりの列と線がつながるようにのばす
        if (y0 > prevBottom) y0 = prevBottom;
        if (y1 < prevTop) y1 = prevTop;
      }
      if (y1 - y0 < 1) y1 = y0 + 1;
      ctx.moveTo(px + 0.5, y0); ctx.lineTo(px + 0.5, y1);
      prevTop = t0; prevBottom = t1;
    }
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  ctx.restore();
}
function draw() {
  const W = canvas.width, H = canvas.height, d = S.dpr;
  if (!W || !H || !colors.bg) return;
  ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, W, H);
  const ev = META.events[S.idx];
  if (!ev || !S.data) return;
  const axisH = Math.round(22 * d), plotH = H - axisH, rate = META.rate, n = ev.n;
  const comps = S.three ? META.comps : ["U"], blockH = plotH / META.stations.length, rowH = blockH / comps.length;
  const timeOf = i => ev.t0 + i / rate, xOfTime = t => ((t - ev.t0) * rate - S.v0) / S.span * W;
  ctx.font = `${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;

  // 目盛り
  const step = tickStep(S.span / rate / (W / d / 64));
  ctx.lineWidth = 1; ctx.textBaseline = "middle"; ctx.textAlign = "center";
  const digits = step < 0.1 ? 2 : step < 1 ? 1 : 0;
  for (let k = Math.ceil(timeOf(S.v0) / step); k * step <= timeOf(S.v0 + S.span); k++) {
    const t = k * step, x = Math.round(xOfTime(t)) + 0.5;
    ctx.strokeStyle = k === 0 ? colors.muted : colors.grid;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke();
    ctx.fillStyle = colors.muted;
    ctx.fillText(t.toFixed(digits) + " 秒", Math.min(W - 20 * d, Math.max(20 * d, x)), plotH + axisH / 2);
  }

  // 見えている範囲の振幅
  const a = Math.max(0, Math.floor(S.v0)), b = Math.min(n, Math.ceil(S.v0 + S.span));
  const out = { lo: 0, hi: 0 }, ranges = {}, stationHalf = {};
  let allHalf = 0;
  for (const st of META.stations) {
    let half = 0;
    for (const c of comps) {
      const tr = traceOf(st.id, c);
      if (!tr) continue;
      rangeOf(tr, a, b, out);
      const lo = out.lo * tr.step, hi = out.hi * tr.step;
      ranges[st.id + c] = (lo + hi) / 2;
      half = Math.max(half, (hi - lo) / 2);
    }
    stationHalf[st.id] = half;
    allHalf = Math.max(allHalf, half);
  }

  // 拠点ごとの波形
  META.stations.forEach((st, si) => {
    const top = si * blockH, picks = ev.picks[st.id] || {};
    const half = S.common ? allHalf : stationHalf[st.id];
    let any = false;
    comps.forEach((c, ci) => {
      const tr = traceOf(st.id, c), y = top + ci * rowH;
      if (tr) { any = true; drawTrace(tr, y, rowH, ranges[st.id + c], half, n); }
      if (S.three) {
        const text = COMP_NAMES[c] + (tr ? "" : " データなし");
        const x = 6 * d + (ci === 0 ? ctx.measureText(st.label + "  ").width : 0);
        labelBox(text, x, y + 2 * d, "left", colors.muted);
      }
    });
    for (const [key, color] of [["p", colors.p], ["s", colors.s]]) {
      if (picks[key] === undefined) continue;
      const x = Math.round(xOfTime(picks[key])) + 0.5;
      if (x < 0 || x > W) continue;
      const manual = picks[key + "m"], name = key.toUpperCase() + (manual ? "" : "自動");
      ctx.strokeStyle = color; ctx.lineWidth = Math.max(1, d);
      ctx.setLineDash(manual ? [] : [4 * d, 3 * d]);   // 手で直した時刻は実線、自動のままは破線
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + blockH); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = `600 ${11 * d}px -apple-system, BlinkMacSystemFont, sans-serif`;
      // P は線の左、S は線の右に書く(2本が近くても重ならないように)
      if (key === "p") labelBox(name, x - 3 * d, top + blockH - 15 * d, "right", color);
      else labelBox(name, x + 3 * d, top + blockH - 15 * d, "left", color);
      ctx.font = `${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
    }
    labelBox(st.label, 6 * d, top + 2 * d, "left", colors.ink);
    if (any) labelBox("±" + formatAmp(half), W - 6 * d, top + 2 * d, "right", colors.muted);
    else {
      ctx.fillStyle = colors.muted; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("データなし", W / 2, top + blockH / 2);
    }
    ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
    const yLine = Math.round(top + blockH) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, yLine); ctx.lineTo(W, yLine); ctx.stroke();
  });
}

// --- 拡大・移動 ---
function setView(v0, span) {
  const ev = META.events[S.idx];
  if (!ev) return;
  S.span = Math.min(ev.n, Math.max(MIN_SPAN_SECONDS * META.rate, span));
  S.v0 = Math.min(ev.n - S.span, Math.max(0, v0));
  requestDraw();
}
function resetView() { const ev = META.events[S.idx]; if (ev) setView(0, ev.n); }
const pointers = new Map();
let gesture = null, lastTap = null;
function localX(e) { return e.clientX - canvas.getBoundingClientRect().left; }
function startGesture() {
  const p = [...pointers.values()], w = canvas.clientWidth;
  if (p.length === 1) gesture = { kind: "pan", x: p[0].x, v0: S.v0, span: S.span, w };
  else if (p.length >= 2) {
    const mid = (p[0].x + p[1].x) / 2;
    gesture = { kind: "pinch", dist: Math.max(20, Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y)),
                anchor: S.v0 + mid / w * S.span, span: S.span, w };
  } else gesture = null;
}
canvas.addEventListener("pointerdown", e => {
  e.preventDefault();
  try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
  pointers.set(e.pointerId, { x: localX(e), y: e.clientY, x0: localX(e), y0: e.clientY, t0: e.timeStamp, moved: false });
  if (pointers.size > 1) for (const p of pointers.values()) p.moved = true;
  startGesture();
});
canvas.addEventListener("pointermove", e => {
  const me = pointers.get(e.pointerId);
  if (!me) return;
  e.preventDefault();
  me.x = localX(e); me.y = e.clientY;
  if (Math.hypot(me.x - me.x0, me.y - me.y0) > 10) me.moved = true;
  const p = [...pointers.values()];
  if (gesture && gesture.kind === "pan") setView(gesture.v0 - (me.x - gesture.x) / gesture.w * gesture.span, gesture.span);
  else if (gesture && gesture.kind === "pinch" && p.length >= 2) {
    const dist = Math.max(20, Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y)), mid = (p[0].x + p[1].x) / 2;
    const span = Math.min(META.events[S.idx].n, Math.max(MIN_SPAN_SECONDS * META.rate, gesture.span * gesture.dist / dist));
    setView(gesture.anchor - mid / gesture.w * span, span);
  }
});
function endPointer(e) {
  const me = pointers.get(e.pointerId);
  if (!me) return;
  pointers.delete(e.pointerId);
  if (e.type === "pointerup" && !me.moved && pointers.size === 0 && e.timeStamp - me.t0 < 300) {
    if (lastTap && e.timeStamp - lastTap.t < 350 && Math.hypot(me.x - lastTap.x, me.y - lastTap.y) < 30) {
      resetView(); lastTap = null;
    } else lastTap = { t: e.timeStamp, x: me.x, y: me.y };
  }
  startGesture();
}
canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
canvas.addEventListener("wheel", e => {     // パソコンで確かめるとき
  e.preventDefault();
  const x = localX(e), w = canvas.clientWidth;
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return setView(S.v0 + e.deltaX / w * S.span, S.span);
  const anchor = S.v0 + x / w * S.span, span = S.span * Math.exp(e.deltaY * 0.002);
  const next = Math.min(META.events[S.idx].n, Math.max(MIN_SPAN_SECONDS * META.rate, span));
  setView(anchor - x / w * next, next);
}, { passive: false });
// iOS の画面全体の拡大・スクロールを止める
canvas.addEventListener("touchmove", e => e.preventDefault(), { passive: false });
for (const type of ["gesturestart", "gesturechange"]) canvas.addEventListener(type, e => e.preventDefault(), { passive: false });

// --- 画面の切り替え ---
function pressed(on, off, value) {
  on.setAttribute("aria-pressed", value); off.setAttribute("aria-pressed", !value);
}
function updateTools() {
  pressed($("comp3"), $("compU"), S.three);
  pressed($("ampCommon"), $("ampEach"), S.common);
  pressed($("filterOn"), $("filterOff"), S.filtered);
  updateHeading();
}
function updateHeading() {
  const ev = META.events[S.idx];
  if (!ev) return;
  $("headInfo").textContent = eventSize(ev) + (ev.type ? ` ・ ${ev.type}` : "")
    + (S.filtered ? ` ・ ${META.filter.low}〜${META.filter.high} Hz` : " ・ 元波形");
}
function eventSize(ev) {
  return (ev.m ? `M${ev.m}` + (ev.mSource === "推定" ? "(推定)" : "") : "M なし") + ` ・ 深さ ${ev.depth}`;
}
async function openEvent(idx) {
  const ev = META.events[idx], token = ++S.token;
  if ($("view").hidden) S.listY = window.scrollY;
  S.idx = idx; S.data = null;
  $("list").hidden = true; $("view").hidden = false;
  $("headTime").textContent = `No.${ev.no}  ${ev.time.slice(2)}`;
  updateHeading();
  $("prev").disabled = idx === 0;
  $("next").disabled = idx === META.events.length - 1;
  $("status").textContent = "波形を読み込んでいます…";
  resize();
  try {
    const data = await loadWaves(ev);
    if (token !== S.token) return;
    S.data = data;
    $("status").textContent = "";
    resetView();
  } catch (err) {
    if (token === S.token) $("status").textContent = "波形を読み込めませんでした: " + err.message;
  }
}
function showList() {
  S.token++; S.idx = -1; S.data = null; S.fromList = false;
  $("view").hidden = true; $("list").hidden = false;
  window.scrollTo(0, S.listY);
}
function route() {
  // #番号 か #地震の見分け(地震リストの開始の時刻。3Dの図から開くとき)
  const tag = location.hash.slice(1), idx = META.events.findIndex(e => e.key === tag || String(e.no) === tag);
  if (idx >= 0 && typeof DecompressionStream !== "undefined") openEvent(idx); else showList();
}
function buildList() {
  $("count").textContent = `${META.events.length} 件`;
  const list = $("events");
  for (const ev of META.events) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    const no = document.createElement("span"); no.className = "no"; no.textContent = ev.no;
    const when = document.createElement("span"); when.className = "when"; when.textContent = ev.time.slice(0, 19);
    if (ev.type) { const t = document.createElement("span"); t.className = "badge"; t.textContent = ev.type; when.append(t); }
    const size = document.createElement("span"); size.className = "size";
    size.textContent = ev.m ? `M${ev.m}` : "M なし";
    const small = document.createElement("small");
    small.textContent = (ev.mSource === "推定" ? "推定 ・ " : "") + `深さ ${ev.depth}`;
    size.append(small);
    b.append(no, when, size);
    b.addEventListener("click", () => { S.fromList = true; location.hash = ev.key; });
    li.append(b); list.append(li);
  }
}
$("back").addEventListener("click", () => { if (S.fromList) history.back(); else location.hash = ""; });
function step(delta) {
  const ev = META.events[S.idx + delta];
  if (ev) location.replace("#" + ev.key);
}
$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("compU").addEventListener("click", () => { S.three = false; updateTools(); requestDraw(); });
$("comp3").addEventListener("click", () => { S.three = true; updateTools(); requestDraw(); });
$("ampEach").addEventListener("click", () => { S.common = false; updateTools(); requestDraw(); });
$("ampCommon").addEventListener("click", () => { S.common = true; updateTools(); requestDraw(); });
$("filterOn").addEventListener("click", () => { S.filtered = true; updateTools(); requestDraw(); });
$("filterOff").addEventListener("click", () => { S.filtered = false; updateTools(); requestDraw(); });
$("filterNote").textContent = `フィルタは到着時刻の検出と同じ ${META.filter.low}〜${META.filter.high} Hz です。時間のずれはありませんが、急な立ち上がりの少し前にも小さな揺れが出ることがあります。`;
window.addEventListener("hashchange", route);
new ResizeObserver(resize).observe(canvas);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); requestDraw(); });
if (typeof DecompressionStream === "undefined") $("unsupported").hidden = false;
readColors(); updateTools(); buildList(); route();
$("loading").remove();
</script>
</body>
</html>
"""


# --- 単体で実行するとき: 有珠山付近の地震の一覧 ---

def main():
  import app_paths
  import quake_list
  import quake_locate
  from hypocenter import load_stations
  parser = argparse.ArgumentParser(description="有珠山付近の地震の波形を、スマートフォンで見る HTML に書き出す")
  parser.add_argument("-o", "--output", help=f"出力する HTML(省略すると batch_plots/quakes/locate/{WAVE_PAGE_FILE}。3Dの図から開けるよう、同じフォルダに置く)")
  args = parser.parse_args()
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  steps = count_steps(app_paths.ETC_DIR)
  with tempfile.TemporaryDirectory() as d:
    csv_path = Path(d) / "usu.csv"
    quake_list.write_usu_candidates(csv_path)
    candidates = quake_locate.read_candidates(csv_path)
  items, computed = quake_locate.locate_all_cached(candidates, load_hour_from_zips, stations, proj)
  print(f"震源: {len(items)}件(新しく計算したもの {computed}件)")
  local = [(c, r) for c, r in items if r["kind"] in LOCAL_KINDS and r.get("lat") is not None]
  events = []
  for no, (cand, r) in enumerate(local, 1):
    origin = origin_time(r)
    st = load_stream(window_start(origin), origin + timedelta(seconds=AFTER_SECONDS))
    info, waves = event_data(no, cand, r, st, stations, steps, proj)
    events.append((info, waves))
    missing = [nm for nm in sorted(stations) if nm not in waves]
    gaps = sum(len(item["g"]) for comps in waves.values() for item in comps.values())
    print(f"  No.{no} {info['time']}  M {info['m'] or 'なし'}  " + (f"データなし: {', '.join(missing)}  " if missing else "")
          + (f"欠測 {gaps} か所" if gaps else ""))
  out = Path(args.output) if args.output else quake_locate.OUTPUT_DIR / WAVE_PAGE_FILE
  size = write_wave_view(out, events, sorted(stations), USU_LIST_TITLE)
  print(f"書き出しました({len(events)}件、{size / 1e6:.1f} MB): {out}")

if __name__ == "__main__":
  main()
