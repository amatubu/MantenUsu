# shake_filter_lab.py
# フィルタの帯域をスライダーで変えて、波形と音がどう変わるかを試すページの HTML(ファイル1つ)を書き出す(TODO 49)
#   「この揺れは何?」クイズ・揺れを比べるページとは別のページ。細かい知識が無くても、帯域を動かして
#   「低い音だけにすると発破のゆっくりした揺れが見える」などに自分で気づけるようにする
#   試す揺れの一覧は etc/shake_filter_lab.csv(1行が1つ)
#     番号, 名前, 種類(クイズと同じ種類の名前。P波・S波の線に使う。空でもよい), 基準の時刻, 基準(0 秒にする時刻の名前),
#     表示の始まり[秒], 表示の終わり[秒](基準からの秒), 拠点, 音の速さ(空白で区切った倍率),
#     見やすい帯域(「下限-上限」[Hz]。「おすすめ」のボタンでその帯域にする。空ならボタンを出さない), 説明, 試してみよう
#   画面でできること
#   - 下限・上限の 2 つのスライダー(対数の目盛り。左端は「下限なし」、右端は「上限なし」)。A〜D・L とフィルタなしのボタン、
#     「おすすめ」(見やすい帯域)と「じぶん」(スライダーで最後に作った帯域。揺れごとに、その端末のブラウザに覚える)のボタン
#   - 上の段に元の波形、下の段にフィルタをかけた波形(縦の大きさはそれぞれ)。ピンチで拡大・縮小、1本指で移動、ダブルタップで全体に戻す
#   - 「どの高さの揺れが入っているか」の帯(振幅スペクトル。Python で前もって計算)に、通している範囲を色で示す
#   - 音: 元の音・フィルタの音・交互に(元 → フィルタ)。速さを選べる。人の耳に聞こえる高さ(20 Hz 以上)になるかも出す
#   フィルタはブラウザで作る(Butterworth の高域通過と低域通過を 4 次ずつ。往復でかけて時間のずれを無くす。両端は絞ってからかける)
#   波形の読み込み・符号化・描画・音の部品は shake_quiz.py と同じ(COMMON_JS)。入れるのは満点の拠点の波形だけ
#
#     python3 scripts/shake_filter_lab.py [-o 出力の HTML] [--items 一覧の CSV]
#   省略すると batch_plots/quiz/filter_lab.html に書き出す
import argparse
import csv
import html
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import app_paths
import station_names
import shake_quiz as sq
from quake_wave_view import count_steps, load_stream, script_json, trace_samples

ITEM_FILE = "shake_filter_lab.csv"
OUTPUT_DIR = sq.OUTPUT_DIR
TITLE = "フィルタで揺れを分けてみよう"
SPEC_BINS = 120          # スペクトルの帯の数(対数の間隔)
SPEC_MIN = 0.05          # スペクトルの下の端 [Hz]
SLIDER_MIN, SLIDER_MAX = 0.1, 60.0


def read_items(path=None):
  path = Path(path) if path else app_paths.ETC_DIR / ITEM_FILE
  out = []
  with open(path, encoding="utf-8-sig", newline="") as f:
    for line, r in enumerate(csv.DictReader(f), start=2):
      r = {k: (v or "").strip() for k, v in r.items()}
      where = f"{path.name} の {line} 行目"
      if r["種類"] and r["種類"] not in sq.KINDS:
        raise ValueError(f"{where}: 種類が違います: {r['種類']}")
      if r["拠点"] not in sq.STATIONS:
        raise ValueError(f"{where}: 満点の拠点ではありません: {r['拠点']}")
      show = (float(r["表示の始まり[秒]"]), float(r["表示の終わり[秒]"]))
      best = None
      if r.get("見やすい帯域"):
        try:
          best = [float(v) for v in r["見やすい帯域"].split("-")]
        except ValueError:
          best = []
        if len(best) != 2 or not 0 < best[0] < best[1]:
          raise ValueError(f"{where}: 見やすい帯域は「下限-上限」(例: 22-29)で書きます: {r['見やすい帯域']}")
      if not show[0] < show[1]:
        raise ValueError(f"{where}: 表示の始まりが終わりより後です")
      out.append({"no": int(r["番号"]), "name": r["名前"], "kind": r["種類"],
                  "t": datetime.strptime(r["基準の時刻"], "%Y-%m-%d %H:%M:%S"), "ref": r["基準"], "show": show,
                  "station": r["拠点"], "speeds": [int(v) for v in r["音の速さ"].split()] or [sq.COMMON_SPEED],
                  "best": best, "text": r["説明"], "try": r["試してみよう"]})
  numbers = [it["no"] for it in out]
  if len(set(numbers)) != len(numbers):
    raise ValueError(f"{path.name}: 番号が重なっています")
  return sorted(out, key=lambda it: it["no"])


# 振幅スペクトル(対数の間隔の帯ごとの平均。いちばん大きい帯を 1)。戻り値 ... (帯の中心の周波数, 大きさ)
def spectrum(v, rate):
  x = np.where(np.isnan(v), 0.0, v - np.nanmean(v))
  x = x * np.hanning(len(x))
  amp = np.abs(np.fft.rfft(x))
  f = np.fft.rfftfreq(len(x), 1 / rate)
  edges = np.geomspace(SPEC_MIN, rate / 2, SPEC_BINS + 1)
  centers = np.sqrt(edges[:-1] * edges[1:])
  out = np.zeros(SPEC_BINS)
  for i in range(SPEC_BINS):
    m = (f >= edges[i]) & (f < edges[i + 1])
    out[i] = amp[m].mean() if m.any() else np.nan
  out = np.interp(np.arange(SPEC_BINS), np.flatnonzero(np.isfinite(out)), out[np.isfinite(out)])   # 線の無い低い帯は埋める
  return [round(float(c), 4) for c in centers], [round(float(a), 4) for a in out / (out.max() or 1)]


# 1つ分のデータ。load(t0, t1) で波形を読む。戻り値 ... (ページに入れる情報, 波形)。記録が無ければ (情報, None)
def item_data(it, load, steps, stations):
  from quake_wave_view import decode_samples
  before, after = it["show"]
  rate = 125 if after - before > sq.LONG_SECONDS else 250
  n = int(round((after - before) * rate))
  start = it["t"] + timedelta(seconds=before)
  st = load(start, start + timedelta(seconds=after - before))
  nm = it["station"]
  wave = trace_samples(st, nm, "U", start, n, steps.get((nm, "U"), 1.0), rate)
  phases = {}
  if it["kind"]:
    q = {"kind": it["kind"], "t": it["t"], "second": ""}
    phases = sq.phase_lines(q, {nm: stations[nm]} if nm in stations else {}).get(nm, {})
  info = {"no": it["no"], "name": it["name"], "rate": rate, "n": n, "t0": before, "ref": sq.display_names(it["ref"]),
          "station": station_names.full(nm), "speeds": it["speeds"], "best": it.get("best"), "text": sq.display_names(it["text"]),
          "try": sq.display_names(it["try"]), "phases": phases, "spec": None, "have": wave is not None}
  if wave is not None:
    v = decode_samples(wave["d"]).astype(float) * wave["s"]
    for a, b in wave["g"]:
      v[a:b] = np.nan
    info["spec"] = dict(zip(("f", "a"), spectrum(v, rate)))
  return info, wave


def write_page(path, items):
  path = Path(path)
  rates = sorted({info["rate"] for info, _ in items})
  from scipy import signal
  notches = {}
  for rate in rates:
    rows = []
    for f0 in (50.0, 100.0):
      if f0 < rate / 2 * 0.98:
        b, a = signal.iirnotch(f0, 30.0, rate)
        rows.append([float(b[0] / a[0]), float(b[1] / a[0]), float(b[2] / a[0]), float(a[1] / a[0]), float(a[2] / a[0])])
    notches[str(rate)] = rows
  meta = {
    "title": TITLE,
    "presets": [{"key": key, "low": lo, "high": hi, "sound": sound} for key, lo, hi, sound in sq.ALL_FILTERS],
    "slider": [SLIDER_MIN, SLIDER_MAX],
    "items": [info for info, _ in items],
    "notch": notches,
  }
  blocks = "\n".join(f'<script type="application/json" id="w{info["no"]}">{script_json(wave)}</script>'
                     for info, wave in items if wave is not None)
  page = (PAGE.replace("__TITLE__", html.escape(TITLE))
          .replace("__COMMON_JS__", sq.COMMON_JS)
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
  --accent: #1565c0; --ref: #7b4fb5; --p: #1565c0; --s: #d84e00; --cursor: #1b7f3b; --gap: #e4e7eb; --wave: #1c2126;
  --raw: #8a949e; --band: rgba(21, 101, 192, 0.16); --spec: #1c2126;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1f2428; --ink: #e8ecef; --muted: #9aa5af; --line: #333a41; --grid: #24292e;
    --accent: #6cb0f5; --ref: #c39cf0; --p: #6cb0f5; --s: #ff9a57; --cursor: #5cc27e; --gap: #2b3137; --wave: #e8ecef;
    --raw: #7d8790; --band: rgba(108, 176, 245, 0.22); --spec: #e8ecef;
  }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink);
  font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
  -webkit-text-size-adjust: 100%; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
[hidden] { display: none !important; }
#list { max-width: 640px; margin: 0 auto; padding: 0 16px calc(24px + env(safe-area-inset-bottom)); }
#list h1 { font-size: 22px; margin: 0; padding: calc(18px + env(safe-area-inset-top)) 0 4px; }
.note { color: var(--muted); font-size: 13px; }
#items { list-style: none; margin: 14px 0; padding: 0; display: grid; gap: 8px; }
#items button { width: 100%; text-align: left; padding: 10px 12px; border-radius: 10px; background: var(--panel); }
#items b { display: block; font-size: 15px; }
#items small { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
#view { position: fixed; inset: 0; display: flex; flex-direction: column; height: 100vh; height: 100dvh;
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
header { display: flex; align-items: center; gap: 2px; min-height: 48px; padding: 0 4px; border-bottom: 1px solid var(--line); }
header button { min-width: 44px; height: 44px; color: var(--accent); font-size: 17px; }
header button:disabled { color: var(--line); cursor: default; }
#back { padding: 0 6px; white-space: nowrap; }
#heading { flex: 1; min-width: 0; text-align: center; font-weight: 600; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#stage { position: relative; flex: 1; min-height: 180px; }
#wave { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none;
  -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
#status { position: absolute; left: 16px; right: 16px; top: 40%; text-align: center; color: var(--muted); font-size: 14px; pointer-events: none; }
#controls { border-top: 1px solid var(--line); padding: 6px 10px 8px; max-height: 52vh; overflow-y: auto; }
#spec { width: 100%; height: 76px; display: block; }
.range { display: grid; grid-template-columns: 3.2em 1fr 6.6em; align-items: center; gap: 6px; font-size: 13px; }
.range input { width: 100%; margin: 0; height: 32px; accent-color: var(--accent); }
.range output { text-align: right; font-variant-numeric: tabular-nums; }
#bandText { font-size: 14px; margin: 2px 0 6px; }
#bandText b { color: var(--accent); }
.row { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; margin: 6px 0; }
.seg { display: flex; padding: 2px; border-radius: 8px; background: var(--panel); }
.seg button { height: 34px; padding: 0 10px; border-radius: 6px; font-size: 13px; color: var(--muted); white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--bg); color: var(--ink); font-weight: 600; box-shadow: 0 0 0 1px var(--line); }
.seg button:disabled { opacity: 0.35; cursor: default; }
#presets { flex-wrap: wrap; }
.listen { height: 38px; padding: 0 12px; border-radius: 19px; color: var(--accent); box-shadow: inset 0 0 0 1.5px var(--accent); font-size: 14px; font-weight: 600; white-space: nowrap; }
.listen.main { color: var(--bg); background: var(--accent); box-shadow: none; }
#hear { font-size: 12px; color: var(--muted); margin: 0 0 4px; }
.try { padding: 8px 10px; border-radius: 8px; background: var(--panel); font-size: 14px; margin: 6px 0; }
.try b { color: var(--accent); }
#text { font-size: 14px; margin: 4px 0; }
.warn { margin: 16px 0; padding: 12px; border-radius: 8px; background: var(--panel); font-size: 14px; }
</style>
</head>
<body>
<main id="list">
  <h1>__TITLE__</h1>
  <p class="note">地震計の記録には、ゆっくりした揺れ(低い音)から細かい揺れ(高い音)まで、いろいろな揺れがまざっています。
  「フィルタ」は、決めた範囲の高さの揺れだけを通すしくみです。下限と上限を動かして、波形と音がどう変わるか試してみましょう。</p>
  <noscript><p class="warn">プレビューの画面では動きません。Safari などのブラウザで開いてください。</p></noscript>
  <p class="warn" id="unsupported" hidden>このブラウザは圧縮の展開(DecompressionStream)に対応していません。iOS 16.4 以降の Safari で開いてください。</p>
  <ul id="items"></ul>
  <p class="note">「おすすめ」は、その揺れが見やすい帯域です。「じぶん」を押すと、スライダーで最後に作った帯域に戻ります(揺れごとに、この端末のブラウザに覚えます)。A〜D などを試したあとに、自分の帯域と比べられます。</p>
  <p class="note">波形はピンチで拡大・縮小、1本指で左右に移動、ダブルタップで全体に戻ります。音は、波形を速く再生したものです(×20 なら 20 倍の速さで、揺れの周波数も 20 倍の音になります)。iPhone では、消音モード(マナーモード)でも鳴ります。満点地震計の上下動の記録です。</p>
</main>
<section id="view" hidden>
  <header>
    <button id="back" type="button" aria-label="一覧に戻る">‹ 一覧</button>
    <div id="heading"></div>
    <button id="prev" type="button" aria-label="前">◀</button>
    <button id="next" type="button" aria-label="次">▶</button>
  </header>
  <div id="stage"><canvas id="wave" aria-label="上が元の波形、下がフィルタをかけた波形"></canvas><div id="status"></div></div>
  <div id="controls">
    <canvas id="spec" aria-label="どの高さの揺れが入っているか。色の付いた範囲を通す"></canvas>
    <div class="range"><label for="low">下限</label><input id="low" type="range" min="0" max="1000" step="1"><output id="lowOut"></output></div>
    <div class="range"><label for="high">上限</label><input id="high" type="range" min="0" max="1000" step="1"><output id="highOut"></output></div>
    <p id="bandText"></p>
    <div class="row"><div class="seg" id="presets" role="group" aria-label="決まった帯域"></div></div>
    <div class="row">
      <button class="listen main" id="playFilt" type="button"></button>
      <button class="listen" id="playRaw" type="button"></button>
      <button class="listen" id="playBoth" type="button"></button>
      <div class="seg" id="speedSeg" role="group" aria-label="速さ"></div>
    </div>
    <p id="hear"></p>
    <p class="try" id="try"></p>
    <p id="text"></p>
    <p class="note" id="more"></p>
  </div>
</section>
<script type="application/json" id="meta">__META__</script>
__WAVES__
<script>
"use strict";
const META = JSON.parse(document.getElementById("meta").textContent);
const MIN_SPAN_SECONDS = 0.1;
const $ = id => document.getElementById(id);
const canvas = $("wave"), ctx = canvas.getContext("2d"), spec = $("spec"), sctx = spec.getContext("2d");
const S = { idx: -1, raw: null, filt: null, low: 0, high: 0, speed: 0, v0: 0, span: 1, dpr: 1, token: 0, listY: 0 };
// 「じぶん」の帯域(揺れごと。この端末のブラウザに覚える)。{番号: [下限の位置, 上限の位置]}
const STORE = "manten-filter-lab-v1";
function loadMine() { try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch (_) { return {}; } }
function saveMine(m) { try { localStorage.setItem(STORE, JSON.stringify(m)); } catch (_) {} }
let mine = loadMine();
let colors = {}, drawQueued = false, filterQueued = false;
__COMMON_JS__

function item() { return META.items[S.idx]; }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]); }

// --- スライダー(対数の目盛り。0 は「下限なし」・1000 は「上限なし」) ---
const [FMIN, FMAX] = META.slider;
function freqOf(pos) { return FMIN * Math.pow(FMAX / FMIN, pos / 1000); }
function posOf(f) { return Math.round(1000 * Math.log(f / FMIN) / Math.log(FMAX / FMIN)); }
function nyquist() { return item().rate / 2; }
function lowHz() { return S.low <= 0 ? 0 : freqOf(S.low); }
function highHz() { return S.high >= 1000 ? 0 : Math.min(freqOf(S.high), nyquist() * 0.95); }
function fmt(f) { return f >= 10 ? f.toFixed(0) : f >= 1 ? f.toFixed(1) : f.toFixed(2); }
function soundWord(lo, hi) {
  // 帯域のまん中の高さを言葉にする(A〜D・L と同じ区切り)
  const mid = lo && hi ? Math.sqrt(lo * hi) : lo ? lo * 2 : hi ? hi / 2 : 0;
  if (!mid) return "";
  if (mid < 0.5) return "もっと低い音";
  if (mid < 2) return "とても低い音";
  if (mid < 9) return "低い音";
  if (mid < 22) return "高い音";
  return "とても高い音";
}

// --- フィルタ(ブラウザで作る Butterworth。高域通過・低域通過を 2 次の段 2 つずつ) ---
const Q4 = [0.54119610, 1.30656296];
function biquad(kind, f, rate, q) {
  const w = 2 * Math.PI * f / rate, c = Math.cos(w), al = Math.sin(w) / (2 * q), a0 = 1 + al;
  const b = kind === "lp" ? [(1 - c) / 2, 1 - c, (1 - c) / 2] : [(1 + c) / 2, -(1 + c), (1 + c) / 2];
  return [b[0] / a0, b[1] / a0, b[2] / a0, -2 * c / a0, (1 - al) / a0];
}
function bandSections(lo, hi, rate) {
  const out = [];
  if (lo > 0) for (const q of Q4) out.push(biquad("hp", lo, rate, q));
  if (hi > 0) for (const q of Q4) out.push(biquad("lp", hi, rate, q));
  return out;
}
// 平均を引き、記録の無い所は 0、両端を絞ってから往復でかける(長い周期で、端の段差が揺れのように残らないように)
function prepared(tr, lo, rate) {
  const n = tr.v.length, x = new Float64Array(n);
  let mean = 0;
  for (let i = 0; i < n; i++) mean += tr.v[i];
  mean /= n;
  for (let i = 0; i < n; i++) x[i] = (tr.v[i] - mean) * tr.step;
  for (const [a, b] of tr.gaps) x.fill(0, a, b);
  const m = Math.min(Math.floor(n / 10), Math.round((lo > 0 ? 2 / lo : 2) * rate));
  for (let i = 0; i < m; i++) { const g = 0.5 - 0.5 * Math.cos(Math.PI * i / m); x[i] *= g; x[n - 1 - i] *= g; }
  return x;
}
function toTrace(x, gaps) {
  let peak = 0;
  for (let i = 0; i < x.length; i++) if (Math.abs(x[i]) > peak) peak = Math.abs(x[i]);
  const step = peak > 0 ? peak / 1e6 : 1, v = new Int32Array(x.length);
  for (let i = 0; i < x.length; i++) v[i] = Math.round(x[i] / step);
  return { v, levels: buildLevels(v), step, gaps };
}
function applyFilter() {
  const it = item();
  if (!S.raw) return;
  const lo = lowHz(), hi = highHz(), x = prepared(S.raw, lo, it.rate);
  const sections = bandSections(lo, hi, it.rate);
  if (sections.length) zeroPhase(x, sections);
  S.filt = toTrace(x, S.raw.gaps);
  requestDraw();
}
function requestFilter() {
  if (filterQueued) return;
  filterQueued = true;
  requestAnimationFrame(() => { filterQueued = false; applyFilter(); });
}

// --- 描く ---
function readColors() {
  const css = getComputedStyle(document.documentElement);
  for (const k of ["bg", "ink", "muted", "line", "grid", "ref", "p", "s", "gap", "wave", "cursor", "raw", "band", "spec", "accent"]) colors[k] = css.getPropertyValue("--" + k).trim();
}
function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); drawCursor(); drawSpec(); });
}
function resize() {
  for (const c of [canvas, spec]) {
    const r = c.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    S.dpr = window.devicePixelRatio || 1;
    c.width = Math.round(r.width * S.dpr);
    c.height = Math.round(r.height * S.dpr);
  }
  draw(); drawSpec();
}
function formatAmp(nm) {
  if (nm >= 1e6) return (nm / 1e6).toPrecision(3) + " mm/s";
  if (nm >= 1e3) return (nm / 1e3).toPrecision(3) + " µm/s";
  return nm.toPrecision(3) + " nm/s";
}
function tickStep(seconds) {
  for (const m of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200]) if (m >= seconds) return m;
  return 500;
}
function labelBox(text, x, y, align, color, c = ctx) {
  const d = S.dpr, w = c.measureText(text).width, left = align === "right" ? x - w : x;
  c.fillStyle = colors.bg; c.globalAlpha = 0.78;
  c.fillRect(left - 2 * d, y - 1 * d, w + 4 * d, 14 * d);
  c.globalAlpha = 1; c.fillStyle = color; c.textAlign = "left"; c.textBaseline = "top";
  c.fillText(text, left, y);
}
const FONT = `-apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
function rowsOf() { return [[S.raw, "元の波形(フィルタなし)", colors.raw], [S.filt, "フィルタをかけた波形", colors.wave]]; }
function draw() {
  const W = canvas.width, H = canvas.height, d = S.dpr;
  if (!W || !H || !colors.bg) return;
  ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, W, H);
  const it = item();
  if (!it || !S.raw || !S.filt) return;
  const axisH = Math.round(22 * d), plotH = H - axisH, blockH = plotH / 2, rate = it.rate, n = it.n;
  const timeOf = i => it.t0 + i / rate, xOfTime = t => ((t - it.t0) * rate - S.v0) / S.span * W;
  ctx.font = `${11 * d}px ${FONT}`;
  const step = tickStep(S.span / rate / (W / d / 64));
  ctx.lineWidth = 1; ctx.textBaseline = "middle"; ctx.textAlign = "center";
  const digits = step < 0.1 ? 2 : step < 1 ? 1 : 0;
  for (let k = Math.ceil(timeOf(S.v0) / step); k * step <= timeOf(S.v0 + S.span); k++) {
    const t = k * step, x = Math.round(xOfTime(t)) + 0.5;
    ctx.strokeStyle = colors.grid;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke();
    ctx.fillStyle = colors.muted;
    ctx.fillText(t.toFixed(digits) + " 秒", Math.min(W - 20 * d, Math.max(20 * d, x)), plotH + axisH / 2);
  }
  const x0 = Math.round(xOfTime(0)) + 0.5;
  if (x0 >= 0 && x0 <= W) {
    ctx.strokeStyle = colors.ref; ctx.lineWidth = Math.max(1, d); ctx.setLineDash([4 * d, 3 * d]);
    ctx.beginPath(); ctx.moveTo(x0, 0); ctx.lineTo(x0, plotH); ctx.stroke(); ctx.setLineDash([]);
  }
  const a = Math.max(0, Math.floor(S.v0)), b = Math.min(n, Math.ceil(S.v0 + S.span)), out = { lo: 0, hi: 0 };
  rowsOf().forEach(([tr, name, color], k) => {
    const top = k * blockH;
    rangeOf(tr, a, b, out);
    const lo = out.lo * tr.step, hi = out.hi * tr.step, half = (hi - lo) / 2;
    const keep = colors.wave; colors.wave = color;
    drawTrace(tr, top, blockH, (lo + hi) / 2, half, n);
    colors.wave = keep;
    labelBox(name, 6 * d, top + 2 * d, "left", k ? colors.ink : colors.muted);
    labelBox("±" + formatAmp(half), W - 6 * d, top + 2 * d, "right", colors.muted);
    for (const key of ["p", "s"]) {
      const t = it.phases[key];
      if (t === undefined) continue;
      const x = Math.round(xOfTime(t)) + 0.5, c = key === "p" ? colors.p : colors.s;
      if (x < 0 || x > W) continue;
      ctx.strokeStyle = c; ctx.lineWidth = Math.max(1, 1.5 * d); ctx.setLineDash([4 * d, 3 * d]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + blockH); ctx.stroke(); ctx.setLineDash([]);
      ctx.font = `600 ${11 * d}px ${FONT}`;
      labelBox(key.toUpperCase(), key === "p" ? x - 3 * d : x + 3 * d, top + blockH - 15 * d, key === "p" ? "right" : "left", c);
      ctx.font = `${11 * d}px ${FONT}`;
    }
    ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
    const y = Math.round(top + blockH) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  });
}
function drawCursor() {
  const it = item();
  if (!it || AUD.el.paused || AUD.row < 0) return;
  const W = canvas.width, d = S.dpr, plotH = canvas.height - Math.round(22 * d), blockH = plotH / 2;
  const i = AUD.el.currentTime * AUD.speed * it.rate, x = Math.round((i - S.v0) / S.span * W) + 0.5;
  if (x < 0 || x > W) return;
  ctx.strokeStyle = colors.cursor; ctx.lineWidth = Math.max(2, 2 * d);
  ctx.beginPath(); ctx.moveTo(x, AUD.row * blockH); ctx.lineTo(x, (AUD.row + 1) * blockH); ctx.stroke();
}
// どの高さの揺れが入っているか(横は対数の周波数、縦は大きさ)。通している範囲に色を付ける
function drawSpec() {
  const W = spec.width, H = spec.height, d = S.dpr, it = item();
  if (!W || !H || !colors.bg || !it) return;
  sctx.fillStyle = colors.bg; sctx.fillRect(0, 0, W, H);
  if (!it.spec) return;
  const fLo = Math.min(FMIN, it.spec.f[0]), fHi = nyquist(), axisH = Math.round(14 * d), plotH = H - axisH;
  const xOf = f => Math.log(f / fLo) / Math.log(fHi / fLo) * W;
  const lo = lowHz(), hi = highHz();
  sctx.fillStyle = colors.band;
  sctx.fillRect(lo ? xOf(lo) : 0, 0, (hi ? xOf(hi) : W) - (lo ? xOf(lo) : 0), plotH);
  sctx.strokeStyle = colors.spec; sctx.lineWidth = Math.max(1, 1.2 * d);
  sctx.beginPath();
  it.spec.f.forEach((f, i) => {
    const y = plotH - Math.sqrt(it.spec.a[i]) * (plotH - 4 * d);
    if (i === 0) sctx.moveTo(xOf(f), y); else sctx.lineTo(xOf(f), y);
  });
  sctx.stroke();
  sctx.font = `${10 * d}px ${FONT}`; sctx.fillStyle = colors.muted; sctx.textBaseline = "top"; sctx.textAlign = "center";
  for (const f of [0.1, 0.3, 1, 3, 10, 30]) if (f >= fLo && f <= fHi) sctx.fillText(`${f} Hz`, Math.min(W - 16 * d, Math.max(16 * d, xOf(f))), plotH + 2 * d);
  sctx.textAlign = "left"; sctx.fillText("← 低い音", 2 * d, 2 * d);
  sctx.textAlign = "right"; sctx.fillText("高い音 →", W - 2 * d, 2 * d);
}

// --- 音 ---
//   元の音: 50/100 Hz のノッチと、とてもゆっくりしたずれ(0.05 Hz より下)を除くだけ。フィルタの音: ノッチとフィルタ
const AUD = { el: new Audio(), url: "", row: -1, speed: 0, mode: "", queue: [], raf: 0 };
AUD.el.addEventListener("ended", () => {
  if (AUD.queue.length) { const next = AUD.queue.shift(); AUD.el.pause(); AUD.row = -1; requestDraw(); setTimeout(() => startSound(next), 500); }
  else stopAudio();
});
function soundBlob(row) {
  const it = item(), lo = row ? lowHz() : 0, x = prepared(S.raw, lo || 0.05, it.rate);
  zeroPhase(x, META.notch[String(it.rate)]);
  const sections = row ? bandSections(lo, highHz(), it.rate) : bandSections(0.05, 0, it.rate);
  if (sections.length) zeroPhase(x, sections);
  const s = resampleForSound(x, it.rate * S.speed);
  let peak = 0;
  for (let i = 0; i < s.y.length; i++) if (Math.abs(s.y[i]) > peak) peak = Math.abs(s.y[i]);
  return wavBlob(finishSound(s.y, s.rate, peak > 0 ? 0.9 / peak : 0), s.rate);
}
async function startSound(row) {
  if (!S.raw) return;
  if (AUD.url) URL.revokeObjectURL(AUD.url);
  AUD.url = URL.createObjectURL(soundBlob(row)); AUD.el.src = AUD.url; AUD.row = row; AUD.speed = S.speed;
  const play = AUD.el.play();
  updateButtons();
  try { await play; } catch (err) { stopAudio(); $("status").textContent = "音を鳴らせませんでした: " + err.message; return; }
  cancelAnimationFrame(AUD.raf);
  const tick = () => { requestDraw(); if (!AUD.el.paused) AUD.raf = requestAnimationFrame(tick); };
  tick();
}
function play(mode) {
  const same = AUD.mode === mode && (!AUD.el.paused || AUD.queue.length);
  stopAudio();
  if (same) return;
  AUD.mode = mode;
  AUD.queue = mode === "both" ? [1] : [];
  startSound(mode === "raw" || mode === "both" ? 0 : 1);
}
function stopAudio() {
  AUD.el.pause();
  cancelAnimationFrame(AUD.raf);
  if (AUD.url) URL.revokeObjectURL(AUD.url);
  AUD.url = ""; AUD.row = -1; AUD.speed = 0; AUD.mode = ""; AUD.queue = [];
  updateButtons();
  requestDraw();
}
function updateButtons() {
  const on = m => AUD.mode === m;
  $("playFilt").textContent = on("filt") ? "■ 止める" : "▶ フィルタの音";
  $("playRaw").textContent = on("raw") ? "■ 止める" : "▶ 元の音";
  $("playBoth").textContent = on("both") ? "■ 止める" : "▶ 元 → フィルタ";
  for (const b of $("speedSeg").children) b.setAttribute("aria-pressed", Number(b.dataset.speed) === S.speed);
}
$("playFilt").addEventListener("click", () => play("filt"));
$("playRaw").addEventListener("click", () => play("raw"));
$("playBoth").addEventListener("click", () => play("both"));

// --- 帯域の表示 ---
function showBand() {
  const lo = lowHz(), hi = highHz(), word = soundWord(lo, hi);
  $("lowOut").textContent = lo ? `${fmt(lo)} Hz` : "下限なし";
  $("highOut").textContent = hi ? `${fmt(hi)} Hz` : "上限なし";
  $("bandText").innerHTML = !lo && !hi ? "<b>フィルタなし</b>(全部の揺れ)"
    : `<b>${lo ? fmt(lo) : "0"}〜${hi ? fmt(hi) : fmt(nyquist())} Hz</b> の揺れだけを通す` + (word ? `(${word})` : "")
      + (lo && 1 / lo >= 1 ? `。周期 ${fmt(1 / (hi || nyquist()))}〜${fmt(1 / lo)} 秒の揺れ` : "");
  const it = item(), own = mine[it.no];
  for (const b of $("presets").children) {
    let match;
    if (b.dataset.key === "mine") { b.disabled = !own; match = !!own && own[0] === S.low && own[1] === S.high; }
    else if (b.dataset.key === "best") { b.hidden = !it.best; match = !!it.best && near(lo, it.best[0]) && near(hi, it.best[1]); }
    else {
      const p = META.presets.find(p => p.key === b.dataset.key);
      match = p ? near(lo, p.low) && near(hi, p.high) : !lo && !hi;
    }
    b.setAttribute("aria-pressed", match);
  }
  // 音の高さ: 通している揺れ × 速さ。人の耳に聞こえるのはおよそ 20 Hz 以上
  const top = (hi || nyquist()) * S.speed, bottom = (lo || 0.05) * S.speed;
  $("hear").textContent = `×${S.speed} だと、${fmt(bottom)}〜${fmt(top)} Hz の音になります。`
    + (top < 20 ? "人の耳にはほとんど聞こえない低さです。速さを上げてみよう。" : bottom < 20 ? "20 Hz より低いところは、耳には聞こえにくい音です。" : "");
}
function near(a, b) { return Math.abs(a - b) < b * 0.03; }
function setBand(lo, hi) {
  S.low = lo > 0 ? Math.max(1, Math.min(999, posOf(lo))) : 0;
  S.high = hi > 0 ? Math.max(1, Math.min(999, posOf(hi))) : 1000;
  $("low").value = S.low; $("high").value = S.high;
  bandChanged();
}
function bandChanged() {
  if (AUD.mode) stopAudio();
  showBand(); requestFilter(); requestDraw();
}
// スライダーで動かした帯域を「じぶん」として覚える
function rememberMine() { mine[item().no] = [S.low, S.high]; saveMine(mine); }
$("low").addEventListener("input", () => {
  S.low = Number($("low").value);
  if (S.low >= S.high - 20) { S.high = Math.min(1000, S.low + 20); $("high").value = S.high; }
  rememberMine(); bandChanged();
});
$("high").addEventListener("input", () => {
  S.high = Number($("high").value);
  if (S.high <= S.low + 20) { S.low = Math.max(0, S.high - 20); $("low").value = S.low; }
  rememberMine(); bandChanged();
});
function setPositions(low, high) { S.low = low; S.high = high; $("low").value = low; $("high").value = high; bandChanged(); }
function buildPresets() {
  const none = document.createElement("button");
  none.type = "button"; none.textContent = "なし"; none.dataset.key = "";
  none.setAttribute("aria-label", "フィルタなし");
  none.addEventListener("click", () => setBand(0, 0));
  $("presets").append(none);
  for (const p of META.presets) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = p.key; b.dataset.key = p.key;
    b.setAttribute("aria-label", `${p.key}: ${p.low}〜${p.high} Hz(${p.sound})`);
    b.addEventListener("click", () => setBand(p.low, p.high));
    $("presets").append(b);
  }
  const best = document.createElement("button");
  best.type = "button"; best.textContent = "おすすめ"; best.dataset.key = "best";
  best.setAttribute("aria-label", "この揺れが見やすい帯域");
  best.addEventListener("click", () => { const b = item().best; if (b) setBand(b[0], b[1]); });
  const own = document.createElement("button");
  own.type = "button"; own.textContent = "じぶん"; own.dataset.key = "mine";
  own.setAttribute("aria-label", "スライダーで最後に作った帯域に戻す");
  own.addEventListener("click", () => { const m = mine[item().no]; if (m) setPositions(m[0], m[1]); });
  $("presets").append(best, own);
}

// --- 拡大・移動 ---
function setView(v0, span) {
  const it = item();
  if (!it) return;
  S.span = Math.min(it.n, Math.max(MIN_SPAN_SECONDS * it.rate, span));
  S.v0 = Math.min(it.n - S.span, Math.max(0, v0));
  requestDraw();
}
function resetView() { const it = item(); if (it) setView(0, it.n); }
const pointers = new Map();
let gesture = null, lastTap = null;
function localX(e) { return e.clientX - canvas.getBoundingClientRect().left; }
function startGesture() {
  const p = [...pointers.values()], w = canvas.clientWidth;
  if (p.length === 1) gesture = { kind: "pan", x: p[0].x, v0: S.v0, span: S.span, w };
  else if (p.length >= 2) {
    const mid = (p[0].x + p[1].x) / 2;
    // 2 本指: 指の横の開き(縦の開きは使わない)が 1 割以上変わるまでは、拡大縮小せずに左右に動かすだけにする
    gesture = { kind: "pinch", dist: Math.max(20, Math.abs(p[0].x - p[1].x)), zooming: false, anchor: S.v0 + mid / w * S.span, span: S.span, w };
  } else gesture = null;
}
canvas.addEventListener("pointerdown", e => {
  e.preventDefault();
  if (e.isPrimary) pointers.clear();     // 新しいタッチの始まり: 終わりの知らせが来なかった古い指を消す(残ると 1 本指の移動が拡大縮小になる)
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
  const p = [...pointers.values()], it = item();
  if (gesture && gesture.kind === "pan") setView(gesture.v0 - (me.x - gesture.x) / gesture.w * gesture.span, gesture.span);
  else if (gesture && gesture.kind === "pinch" && p.length >= 2) {
    const dist = Math.max(20, Math.abs(p[0].x - p[1].x)), mid = (p[0].x + p[1].x) / 2;
    if (!gesture.zooming && Math.abs(dist / gesture.dist - 1) > 0.1) gesture.zooming = true;
    const span = gesture.zooming ? Math.min(it.n, Math.max(MIN_SPAN_SECONDS * it.rate, gesture.span * gesture.dist / dist)) : gesture.span;
    setView(gesture.anchor - mid / gesture.w * span, span);
  }
});
function endPointer(e) {
  const me = pointers.get(e.pointerId);
  if (!me) return;
  pointers.delete(e.pointerId);
  if (e.type === "pointerup" && !me.moved && pointers.size === 0 && e.timeStamp - me.t0 < 300) {
    if (lastTap && e.timeStamp - lastTap.t < 350 && Math.hypot(me.x - lastTap.x, me.y - lastTap.y) < 30) { resetView(); lastTap = null; }
    else lastTap = { t: e.timeStamp, x: me.x, y: me.y };
  }
  startGesture();
}
canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
canvas.addEventListener("lostpointercapture", endPointer);
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const x = localX(e), w = canvas.clientWidth, it = item();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return setView(S.v0 + e.deltaX / w * S.span, S.span);
  const anchor = S.v0 + x / w * S.span, next = Math.min(it.n, Math.max(MIN_SPAN_SECONDS * it.rate, S.span * Math.exp(e.deltaY * 0.002)));
  setView(anchor - x / w * next, next);
}, { passive: false });
canvas.addEventListener("touchmove", e => e.preventDefault(), { passive: false });
for (const type of ["gesturestart", "gesturechange"]) canvas.addEventListener(type, e => e.preventDefault(), { passive: false });

// --- 画面の切り替え ---
async function openItem(idx) {
  stopAudio();
  const it = META.items[idx], token = ++S.token;
  if ($("view").hidden) S.listY = window.scrollY;
  S.idx = idx; S.raw = null; S.filt = null;
  $("list").hidden = true; $("view").hidden = false;
  $("heading").textContent = `${it.no}. ${it.name}`;
  $("prev").disabled = idx === 0;
  $("next").disabled = idx === META.items.length - 1;
  $("speedSeg").textContent = "";
  for (const sp of it.speeds) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = `×${sp}`; b.dataset.speed = sp;
    b.addEventListener("click", () => { stopAudio(); S.speed = sp; updateButtons(); showBand(); });
    $("speedSeg").append(b);
  }
  S.speed = it.speeds[0];
  $("try").innerHTML = `<b>試してみよう</b> ${escapeHtml(it.try)}`;
  $("text").textContent = it.text;
  $("more").textContent = `${it.station} の上下動です。0 秒は「${it.ref}」(紫の破線)。`
    + (Object.keys(it.phases).length ? "青の P・赤の S の破線は、震源から計算した到着の見込みです。" : "")
    + "下の帯は、どの高さ(周波数)の揺れがどれくらい入っているかを示します。色の付いた範囲を、フィルタが通します。";
  $("controls").scrollTop = 0;
  setBand(0, 0);
  updateButtons();
  $("status").textContent = it.have ? "波形を読み込んでいます…" : "この揺れの記録がありません";
  resize();
  if (!it.have) return;
  try {
    const raw = JSON.parse($("w" + it.no).textContent), bytes = await gunzipBase64(raw.d);
    if (token !== S.token) return;
    const v = decodeSamples(bytes);
    S.raw = { v, levels: buildLevels(v), step: raw.s, gaps: raw.g };
    $("status").textContent = "";
    applyFilter(); resetView();
  } catch (err) {
    if (token === S.token) $("status").textContent = "波形を読み込めませんでした: " + err.message;
  }
}
function showList() {
  stopAudio();
  S.token++; S.idx = -1; S.raw = null;
  $("view").hidden = true; $("list").hidden = false;
  window.scrollTo(0, S.listY);
}
function route() {
  const idx = META.items.findIndex(t => String(t.no) === location.hash.slice(1));
  if (idx >= 0 && typeof DecompressionStream !== "undefined") openItem(idx); else showList();
}
function buildList() {
  for (const it of META.items) {
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `<b>${it.no}. ${escapeHtml(it.name)}</b><small>${escapeHtml(it.text)}</small>`;
    b.addEventListener("click", () => { location.hash = String(it.no); });
    li.append(b); $("items").append(li);
  }
}
$("back").addEventListener("click", () => { location.hash = ""; });
function step(delta) { const it = META.items[S.idx + delta]; if (it) location.replace("#" + it.no); }
$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
window.addEventListener("hashchange", route);
new ResizeObserver(resize).observe(canvas);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); requestDraw(); });
if (typeof DecompressionStream === "undefined") $("unsupported").hidden = false;
readColors(); buildPresets(); buildList(); route();
</script>
</body>
</html>
"""


def main():
  from hypocenter import load_stations
  parser = argparse.ArgumentParser(description="フィルタの帯域をスライダーで変えて試すページの HTML を書き出す(一覧は etc/shake_filter_lab.csv)")
  parser.add_argument("-o", "--output", help="出力する HTML(省略すると batch_plots/quiz/filter_lab.html)")
  parser.add_argument("--items", help="一覧の CSV(省略すると etc/shake_filter_lab.csv)")
  args = parser.parse_args()
  steps = count_steps(app_paths.ETC_DIR)
  stations = {nm: c for nm, c in load_stations(app_paths.ETC_DIR).items() if nm in sq.STATIONS}
  items = []
  for it in read_items(args.items):
    info, wave = item_data(it, load_stream, steps, stations)
    items.append((info, wave))
    print(f"  {it['no']:2d} {it['name']}  {station_names.full(it['station'])} {info['rate']} Hz" + ("" if wave else "  記録なし"))
  out = Path(args.output) if args.output else OUTPUT_DIR / "filter_lab.html"
  size = write_page(out, items)
  print(f"書き出しました({len(items)} 件、{size / 1e6:.1f} MB): {out}")

if __name__ == "__main__":
  main()
