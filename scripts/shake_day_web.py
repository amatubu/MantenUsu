# shake_day_web.py
# 1日の記録から揺れを探す教材(TODO 51)の、画面の版を作る(紙の版は shake_day.py)
#   1) 1 時間の図: 紙と同じ形(1 段 = 1 分の 60 段)の画像。拠点と時を切り替え、図をタップするとその時刻の拡大へ
#   2) 部分の拡大: タップした時刻の前後(はじめは 30 秒前〜90 秒後)の波形。ピンチ・ホイールで拡大縮小、ドラッグで移動、
#      フィルタ(元・A・B・C・L)、拠点の切り替え(4 拠点を並べることもできる)、見えている範囲を速くして聴く
#   流れ: 地震だと思う所に印を付ける(1 時間の図で「印付け」にしてタップ、または拡大で「印付け」。ブラウザに日ごとに覚える)→「答え合わせ」
#   答え合わせ: 答え(etc/shake_day_<日>.csv)の揺れを 1 時間の図と拡大に色の帯と番号で重ね、地震のうち印が付いた数と、揺れごとの結果、
#   答えの一覧に無い所の印を出す(答えの揺れの始まりの 10 秒前〜終わりの 10 秒後の印を、その揺れの印とみる)
#   画像と波形はファイルに分かれるので、フォルダごとサーバーに置いて使う(手元で HTML を直接開くと、波形を読めないことがある)
#     <出力>/index.html … ページ
#     <出力>/img/<拠点>_<時>.png … 1 時間の図(紙と同じ 0.5〜20 Hz。紙の --no-clip と同じく、大きな揺れも切らずにとなりの段まではみ出させる)
#     <出力>/data/<拠点>_<時>.json … 拡大用の波形(0.05〜20 Hz、50 Hz。その 1 時間のふだんの揺れ(0.5〜20 Hz)の 1/4 を 1 とした整数)
#   拠点は発表用の名前(Hokudai → Kawaminami など)。ファイル名も同じ
#
#     python3 scripts/shake_day_web.py 2026-07-08 [--stations Mimatsu ...] [--hours 0-23] [-o 出力のフォルダ] [--page-only]
#   --page-only ... 画像と波形は作り直さず、ページ(index.html)だけを書き直す
#   省略すると batch_plots/quiz/day_web/<日>/ に書き、日の一覧のページ batch_plots/quiz/day_web/index.html も書き直す
import argparse
import html
import json
from datetime import datetime
from pathlib import Path

import numpy as np

import page_common
import shake_day as sd
import shake_quiz as sq
import station_names
from quake_wave_view import encode_samples, filter_sections, script_json, true_ranges

OUTPUT_DIR = sq.OUTPUT_DIR / "day_web"
IMAGE_BAND = (0.5, 20.0)        # 1 時間の図(紙と同じ)
DATA_BAND = (0.05, 20.0)        # 拡大用の波形(フィルタ L も使えるように、低いほうを広く)
FINE = 4                        # 拡大用の波形の細かさ: その 1 時間のふだんの揺れ(0.5〜20 Hz)の 1/FINE を 1 にする
FIG_SIZE = (7.2, 10.8)          # 1 時間の図の大きさ [インチ]
FIG_DPI = 140
AX_BOX = (0.10, 0.03, 0.885, 0.955)   # 図の中の波形の枠(左, 下, 幅, 高さ。図の大きさに対する割合)
# 拡大のフィルタ(50 Hz なので 25 Hz より高い D は無く、C は 20 Hz まで)
WEB_FILTERS = [("A", 0.5, 2.0, "とても低い音"), ("B", 2.0, 10.0, "低い音"), ("C", 8.0, 20.0, "高い音"), sq.LOW_FILTER]
SPEEDS = [20, 40, 80]


# 1 時間の図を PNG にする(色を減らして小さくする)
def hour_png(path, x, h):
  import io
  import matplotlib.pyplot as plt
  from PIL import Image
  fig = plt.figure(figsize=FIG_SIZE)
  ax = fig.add_axes(AX_BOX)
  sd.draw_hour(ax, x, h, clip=0, label_size=6.5, lw=0.45)      # 切らない(その 1 時間の最大が段の間隔の 3 倍。となりの段まではみ出す)
  buf = io.BytesIO()
  fig.savefig(buf, dpi=FIG_DPI, format="png")
  plt.close(fig)
  buf.seek(0)
  Image.open(buf).convert("RGB").quantize(colors=32).save(path, optimize=True)

# 拡大用の 1 時間の波形。x は DATA_BAND の物理量(nm/s)、level は同じ時間のふだんの揺れ(IMAGE_BAND)
#   戻り値 ... {"q": 1 にあたる nm/s, "d": 符号化した整数, "g": 記録の無い範囲}
def hour_data(x, level):
  miss = ~np.isfinite(x)
  q = level / FINE if level > 0 else 1.0
  counts = np.round(np.where(miss, 0.0, x) / q).astype(np.int64)
  return {"q": round(float(q), 6), "d": encode_samples(counts), "g": true_ranges(miss)}

#   page_only ... 画像と波形は書かず、すでにあるファイルからページだけを書き直す(ページの手直し用)
def write_day(out, day, image_wf, data_wf, stations, hours, events, page_only=False):
  out = Path(out)
  (out / "img").mkdir(parents=True, exist_ok=True)
  (out / "data").mkdir(parents=True, exist_ok=True)
  have = {}
  n = 3600 * sd.RATE
  for nm in stations:
    label = station_names.full(nm)
    have[nm] = []
    for h in hours:
      if page_only:
        if (out / "img" / f"{label}_{h:02d}.png").exists() and (out / "data" / f"{label}_{h:02d}.json").exists():
          have[nm].append(h)
        continue
      xi = image_wf[nm][h * n:(h + 1) * n]
      if not np.isfinite(xi).any():
        continue
      hour_png(out / "img" / f"{label}_{h:02d}.png", xi, h)
      data = hour_data(data_wf[nm][h * n:(h + 1) * n], sd.hour_level(xi))
      (out / "data" / f"{label}_{h:02d}.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
      have[nm].append(h)
    print(f"  {label}: {len(have[nm])} 時間", flush=True)
  meta = {
    "day": f"{day:%Y-%m-%d}", "title": f"{day:%Y年%-m月%-d日}({'月火水木金土日'[day.weekday()]})の記録",
    "rate": sd.RATE, "imageBand": list(IMAGE_BAND), "dataBand": list(DATA_BAND),
    "stations": [{"id": nm, "label": station_names.full(nm), "hours": have[nm]} for nm in stations],
    "box": list(AX_BOX), "ylim": list(sd.HOUR_YLIM),
    "filters": [{"key": key, "low": lo, "high": hi, "sound": sound, "sections": filter_sections((lo, hi), sd.RATE)}
                for key, lo, hi, sound in WEB_FILTERS],
    "audio": {"sections": sq.audio_sections(sd.RATE, False), "speeds": SPEEDS},
    "events": [{**e, "color": sd.KIND_COLORS.get(e["kind"], sd.INK)} for e in events],
  }
  page = (PAGE.replace("__TITLE__", html.escape(meta["title"]))
          .replace("__IMAGE_NOTE__", f"{IMAGE_BAND[0]:g}〜{IMAGE_BAND[1]:g} Hz の揺れ。大きな揺れは、となりの段まではみ出しています")
          .replace("__COMMON_JS__", page_common.PAGE_JS)
          .replace("__META__", script_json(meta)))
  (out / "index.html").write_text(page, encoding="utf-8")
  return out / "index.html"


# 日の一覧のページ(<出力の親>/index.html)。フォルダにある日(<日>/index.html)を全部並べる
def write_index(folder):
  folder = Path(folder)
  items = []
  for page in sorted(folder.glob("*/index.html")):
    text = page.read_text(encoding="utf-8")
    meta = json.loads(text.split('id="meta">', 1)[1].split("</script>", 1)[0])
    kinds = {}
    for e in meta["events"]:
      kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    note = "、".join(f"{k} {n}" for k, n in kinds.items())
    items.append(f'<li><a href="{page.parent.name}/">{html.escape(meta["title"])}</a>'
                 f'<div class="note">答えの揺れ: {html.escape(note) or "なし"}</div></li>')
  page = INDEX.replace("__ITEMS__", "\n".join(items))
  (folder / "index.html").write_text(page, encoding="utf-8")
  return folder / "index.html"

INDEX = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>1日の記録から揺れを探す</title>
<style>
:root { color-scheme: light dark; --bg: #f6f7f9; --panel: #fff; --ink: #1c2126; --muted: #68727d; --line: #d5dae0; --accent: #1565c0; }
@media (prefers-color-scheme: dark) { :root { --bg: #15181b; --panel: #1f2327; --ink: #e8eaed; --muted: #9aa3ad; --line: #3a4046; --accent: #64a8f0; } }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; }
main { max-width: 720px; margin: 0 auto; padding: 16px; }
h1 { font-size: 19px; margin: 0 0 8px; }
.note { color: var(--muted); font-size: 13px; }
ul { list-style: none; padding: 0; }
li { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; margin: 8px 0; }
a { color: var(--accent); font-weight: 600; font-size: 16px; text-decoration: none; }
</style>
</head>
<body>
<main>
<h1>1日の記録から揺れを探す</h1>
<p class="note">有珠山のまわりの満点地震計 4 か所の、1 日の記録です。1 時間ずつの図から揺れを探し、拡大して音も聴いて、地震だと思う所に印を付けましょう。最後に「答え合わせ」をします。</p>
<ul>
__ITEMS__
</ul>
</main>
</body>
</html>
"""


PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #f6f7f9; --panel: #ffffff; --ink: #1c2126; --muted: #68727d; --line: #d5dae0; --accent: #1565c0;
  --wave: #1f5f99; --gap: #e4e7eb; --cursor: #2e9d4f; --sel: #e8f3ea;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #15181b; --panel: #1f2327; --ink: #e8eaed; --muted: #9aa3ad; --line: #3a4046; --accent: #64a8f0;
          --wave: #9fc6ee; --gap: #2b3035; --cursor: #5fd07f; --sel: #1f3326; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif; }
header { position: sticky; top: 0; z-index: 5; background: var(--bg); padding: 6px 12px 4px; border-bottom: 1px solid var(--line); }
/* 上のボタンの段は、スマートフォンでも図が見えるように低く詰める */
header .row { gap: 4px; }
header .seg { border-radius: 8px; padding: 1px; }
header .seg button { padding: 2px 8px; font-size: 13px; border-radius: 6px; }
header button.plain { padding: 2px 8px; font-size: 13px; border-radius: 8px; }
header button.main { padding: 3px 12px; font-size: 13px; }
header select { font-size: 14px; padding: 1px 2px; }
header .bar { margin-top: 4px; }
#markBar { margin: 3px 0 0; white-space: nowrap; overflow-x: auto; font-size: 12px; scrollbar-width: none; }
#markBar:empty { display: none; }
h1 { font-size: 13px; margin: 0 0 3px; color: var(--muted); font-weight: 600; }
details.note summary { cursor: pointer; color: var(--accent); }
main { max-width: 900px; margin: 0 auto; padding: 8px 12px 40px; }
.row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.seg { display: inline-flex; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 2px; }
.seg button { border: 0; background: none; color: var(--muted); padding: 5px 10px; border-radius: 8px; font: inherit; font-size: 14px; cursor: pointer; }
.seg button.on { background: var(--ink); color: var(--bg); font-weight: 600; }
button.plain { border: 1px solid var(--line); background: var(--panel); color: var(--ink); border-radius: 10px; padding: 5px 12px; font: inherit; font-size: 14px; cursor: pointer; }
button.plain.on { border-color: var(--accent); color: var(--accent); font-weight: 600; }
button.main { border: 0; background: var(--accent); color: #fff; border-radius: 20px; padding: 8px 18px; font: inherit; font-weight: 600; cursor: pointer; }
select { font: inherit; font-size: 15px; padding: 4px 6px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel); color: var(--ink); }
.note { color: var(--muted); font-size: 13px; margin: 6px 0; }
.bar { margin-top: 6px; }
.figWrap { position: relative; margin-top: 8px; background: #fff; border-radius: 6px; overflow: hidden; touch-action: manipulation; }
.figWrap img { display: block; width: 100%; height: auto; cursor: crosshair; transition: opacity 0.1s; }
.figWrap.dim img { opacity: 0.3; }
.overlay { position: absolute; inset: 0; pointer-events: none; }
.loading { position: absolute; top: 40px; left: 50%; transform: translateX(-50%); background: rgba(20, 24, 28, 0.8); color: #fff;
           padding: 6px 16px; border-radius: 16px; font-size: 15px; font-weight: 600; }
.mark { position: absolute; opacity: 0.22; }
.markLabel { position: absolute; transform: translate(-50%, -100%); font-size: 10px; line-height: 1.3; font-weight: 700; background: #fff; border: 1px solid; border-radius: 8px; padding: 0 4px; opacity: 0.95; white-space: nowrap; }
.markEdge { position: absolute; width: 2px; margin-left: -1px; }
.chip { display: inline-flex; align-items: center; border: 1px solid #d32f2f; border-radius: 10px; padding: 0 0 0 6px; margin: 0 3px 0 0; font-size: 12px; line-height: 1.5; }
.chip button.link { font-size: 12px; color: #d32f2f; }
.chip button.del { border: 0; background: none; color: #d32f2f; font-size: 13px; padding: 0 6px; cursor: pointer; }
.pin { position: absolute; width: 2px; background: #d32f2f; margin-left: -1px; }
.pinLabel { position: absolute; transform: translate(-50%, -100%); font-size: 11px; font-weight: 700; color: #fff; background: #d32f2f; border-radius: 4px; padding: 0 3px; white-space: nowrap; }
#answers h2 { font-size: 16px; margin: 10px 0 4px; }
#answers h3 { font-size: 15px; margin: 22px 0 0; padding-top: 8px; border-top: 1px solid var(--line); }
#answers ul { margin: 6px 0; padding-left: 0; list-style: none; }
#answers li { margin: 4px 0; font-size: 14px; }
button.link { border: 0; background: none; color: var(--accent); font: inherit; padding: 0; cursor: pointer; text-align: left; }
.ok { color: #2e7d32; font-weight: 700; } .ng { color: var(--muted); font-weight: 700; } .pinMark { color: #d32f2f; font-weight: 700; }
.tapDot { position: absolute; width: 14px; height: 14px; margin: -7px 0 0 -7px; border: 2px solid var(--cursor); border-radius: 50%; }
#zoom canvas { display: block; width: 100%; height: clamp(220px, calc(100svh - 300px), 480px); background: var(--panel); border-radius: 8px; touch-action: none; margin-top: 8px; }
#status { color: var(--muted); font-size: 13px; min-height: 1.4em; }
[hidden] { display: none !important; }
</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <div class="row">
    <div class="seg" id="stations" role="group" aria-label="拠点"></div>
  </div>
  <div class="bar" id="hourBar">
    <div class="row">
      <button class="plain" id="prevHour" aria-label="前の時間">◀</button>
      <select id="hourSel" aria-label="時"></select>
      <button class="plain" id="nextHour" aria-label="次の時間">▶</button>
      <div class="seg" id="tapMode" role="group" aria-label="図をタップしたとき">
        <button data-m="zoom">拡大</button><button data-m="mark">印付け</button>
      </div>
      <button class="plain" id="check">答え合わせ</button>
    </div>
  </div>
  <div class="bar" id="zoomBar" hidden>
    <div class="row">
      <button class="plain" id="back">← 戻る</button>
      <button class="plain" id="earlier" aria-label="前へ">◀ 1 分</button>
      <button class="plain" id="later" aria-label="次へ">1 分 ▶</button>
      <button class="plain" id="all4">4 拠点</button>
      <button class="plain" id="markZoom">印付け</button>
    </div>
    <div class="row" style="margin-top:6px">
      <div class="seg" id="filters" role="group" aria-label="フィルタ"></div>
      <button class="main" id="play">▶ 聴く</button>
      <div class="seg" id="speeds" role="group" aria-label="音の速さ"></div>
    </div>
  </div>
  <div class="note" id="markBar"></div>
  <div class="bar" id="answerBar" hidden>
    <div class="row">
      <button class="plain" id="backFromAnswers">← 探す画面に戻る</button>
      <button class="plain" id="hideAnswers">答えをかくして探し直す</button>
      <button class="plain" id="again">印を全部消してやり直す</button>
    </div>
  </div>
</header>
<main>
  <section id="hour">
    <details class="note"><summary>使い方</summary>
      1 段が 1 分で、左から右へ時間が進みます(__IMAGE_NOTE__)。上の ◀ ▶ で時間を、名前のボタンで拠点を切り替えます。
      「拡大」のときに図をタップすると、その時刻の前後を拡大して、音も聴けます。
      地震だと思う揺れには、「印付け」にしてタップして印を付けましょう(拡大の画面でも「印付け」で付けられます)。
      印を消すには、上の印の一覧の ✕ を押すか、「印付け」のまま印をもう一度タップします。
      印はこのブラウザに覚えておきます。24 時間ぜんぶを見終わったら「答え合わせ」。
    </details>
    <div class="figWrap" id="hourWrap"><img id="hourImg" alt="1 時間の記録"><div class="overlay" id="marks"></div>
      <div class="loading" id="loading" hidden>読み込み中…</div></div>
  </section>
  <section id="zoom" hidden>
    <canvas id="wave"></canvas>
    <div id="status"></div>
    <p class="note" id="zoomNote"></p>
    <p class="note">波形はピンチ(パソコンではホイール)で拡大・縮小、ドラッグで左右に移動、ダブルタップで初めの範囲に戻ります。「印付け」を押してから波形をタップすると、そこに印を付けます(印の近くをタップすると消えます)。縦の大きさは、見えている範囲の最大に合わせます。「聴く」は、見えている範囲を速く再生します(揺れの周波数も同じ倍の音になります)。「4 拠点」で 4 拠点を並べ、段をタップするとその拠点の音になります。</p>
  </section>
  <section id="answers" hidden></section>
</main>
<script type="application/json" id="meta">__META__</script>
<script>
__COMMON_JS__
const META = JSON.parse(document.getElementById("meta").textContent);
const RATE = META.rate, HOUR_N = 3600 * RATE;
const $ = id => document.getElementById(id);
const canvas = $("wave"), ctx = canvas.getContext("2d");
let colors = {};
const S = { st: META.stations[0].id, hour: 0, tapMode: "zoom", zoomMark: false, marks: [], checked: false, view: "hour", center: 0, v0: 0, span: 1, dpr: 1,
            filter: "B", speed: 40, all4: false, base: 0, n: 0, traces: {}, home: [0, 1], token: 0 };
const files = new Map();     // "拠点_時" → Promise<{v, q, gaps}>
const MATCH_SECONDS = 10;    // 答えの揺れの始まりの 10 秒前〜終わりの 10 秒後の印を、その揺れを見つけた印とみる

// --- 印(地震だと思う所。日の始まりからの秒)と答え合わせ。日ごとにブラウザに覚える ---
const STORE = `manten-day:${META.day}`;
function loadMarks() {
  try { const j = JSON.parse(localStorage.getItem(STORE)) || {}; S.marks = (j.marks || []).filter(Number.isFinite).sort((a, b) => a - b); S.checked = !!j.checked; }
  catch (_) { S.marks = []; S.checked = false; }
}
function saveMarks() { try { localStorage.setItem(STORE, JSON.stringify({ marks: S.marks, checked: S.checked })); } catch (_) {} }
// sec の近く(tol 秒以内)に印があれば消し、なければ付ける
function toggleMark(sec, tol) {
  const i = S.marks.findIndex(m => Math.abs(m - sec) <= tol);
  if (i >= 0) S.marks.splice(i, 1); else { S.marks.push(Math.round(sec * 10) / 10); S.marks.sort((a, b) => a - b); }
  saveMarks(); updateMarkBar();
}
function markNo(m) { return S.marks.indexOf(m) + 1; }
function isQuake(e) { return e.kind.includes("地震") && !e.kind.includes("ではない"); }
function marksIn(e) { return S.marks.filter(m => m >= e.start - MATCH_SECONDS && m <= e.end + MATCH_SECONDS); }
// 印の一覧(上のボタンの段の下)。時刻を押すとその時刻を拡大し、✕ で消す
function updateMarkBar() {
  const bar = $("markBar");
  bar.innerHTML = "";
  if (!S.marks.length) { bar.textContent = "印はまだありません。"; return; }
  bar.append(`印 ${S.marks.length} 個: `);
  for (const m of S.marks) {
    const chip = Object.assign(document.createElement("span"), { className: "chip" });
    const go = Object.assign(document.createElement("button"), { className: "link", textContent: `${markNo(m)}. ${clock(m)}`, title: "この時刻を拡大" });
    go.onclick = () => zoomFrom(S.view === "zoom" ? S.from : S.view, m - 20);
    const del = Object.assign(document.createElement("button"), { className: "del", textContent: "✕", title: "この印を消す" });
    del.setAttribute("aria-label", `印 ${markNo(m)} を消す`);
    del.onclick = () => { S.marks = S.marks.filter(x => x !== m); saveMarks(); updateMarkBar(); redrawMarks(); };
    chip.append(go, del);
    bar.appendChild(chip);
  }
}
function redrawMarks() {
  if (S.view === "hour") drawOverlay($("marks"), S.hour, S.checked);
  else if (S.view === "zoom") requestDraw();
}
function setTapMode(m) {
  S.tapMode = m;
  for (const b of $("tapMode").children) b.className = b.dataset.m === m ? "on" : "";
}
for (const b of $("tapMode").children) b.onclick = () => setTapMode(b.dataset.m);
function escapeHtml(t) { return String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }
function station(id) { return META.stations.find(s => s.id === id); }
function pad(n) { return String(n).padStart(2, "0"); }
function clock(sec) { sec = Math.max(0, sec); const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec - h * 3600 - m * 60;
  return `${pad(h)}:${pad(m)}:${pad(Math.floor(s))}`; }
function imageSrc(h) { const st = station(S.st); return st.hours.includes(h) ? `img/${st.label}_${pad(h)}.png` : ""; }

// 画面(hour・zoom・answers)を切り替える。上のボタンの段も、その画面のものだけを出す
function setView(v) {
  S.view = v;
  for (const id of ["hour", "zoom", "answers"]) { $(id).hidden = id !== v; $(id + "Bar") && ($(id + "Bar").hidden = id !== v); }
  $("answerBar").hidden = v !== "answers";
  $("markBar").hidden = v === "answers";
  if (v !== "zoom") stopAudio();
}

// --- 答え合わせ ---
$("check").onclick = () => {
  if (!S.checked) {
    const msg = S.marks.length
      ? `印は ${S.marks.length} 個です。\n24 時間ぜんぶを見終わりましたか?\n\n「OK」で答え合わせをします(答えが表示されます)。`
      : "印がまだありません。\n24 時間ぜんぶを見終わりましたか?\n\n「OK」で答え合わせをします(答えが表示されます)。";
    if (!confirm(msg)) return;
    S.checked = true; saveMarks();
  }
  showAnswers();
  window.scrollTo(0, 0);
};
$("backFromAnswers").onclick = () => showHour();
$("hideAnswers").onclick = () => { S.checked = false; saveMarks(); showHour(); };
$("again").onclick = () => { if (confirm("印を全部消して、初めからやり直しますか?")) { S.marks = []; S.checked = false; saveMarks(); updateMarkBar(); showHour(); } };
// 答えの画面: 上にまとめ、そのあとに答えの揺れか印のある時間ごとに、1 時間の図(答えと印を重ねる)とその時間の答えを交互に並べる
function showAnswers() {
  setView("answers");
  const box = $("answers");
  const quakes = META.events.filter(isQuake), found = quakes.filter(e => marksIn(e).length);
  const used = new Set();
  for (const e of META.events) marksIn(e).forEach(m => used.add(m));
  box.innerHTML = `<h2>地震 ${quakes.length} 個のうち ${found.length} 個に印が付いています</h2>`
    + `<div class="note">○ … 地震に印あり、― … 地震に印なし、△ … 地震ではない揺れに印あり。答えの揺れの始まりの ${MATCH_SECONDS} 秒前〜終わりの ${MATCH_SECONDS} 秒後にある印を、その揺れの印とみています。図や答えを押すと、その時刻を拡大します。拠点は上のボタンで切り替えられます。</div>`;
  const hours = new Set();
  for (const e of META.events) for (let h = Math.floor(e.start / 3600); h <= Math.floor((e.end - 1) / 3600); h++) hours.add(h);
  for (const m of S.marks) hours.add(Math.floor(m / 3600));
  for (const h of [...hours].sort((a, b) => a - b)) {
    const sec = document.createElement("div");
    sec.id = `ans-${h}`;
    sec.innerHTML = `<h3>${pad(h)}:00〜${pad(h + 1)}:00</h3>`;
    const wrap = Object.assign(document.createElement("div"), { className: "figWrap" });
    const img = Object.assign(document.createElement("img"), { loading: "lazy", alt: `${pad(h)}時の記録`, src: imageSrc(h) });
    img.addEventListener("click", ev => { const r = img.getBoundingClientRect(); zoomFrom("answers", h * 3600 + figToSec((ev.clientX - r.left) / r.width, (ev.clientY - r.top) / r.height)); });
    const over = Object.assign(document.createElement("div"), { className: "overlay" });
    wrap.append(img, over);
    drawOverlay(over, h, true);
    const ul = document.createElement("ul");
    for (const e of META.events) {
      if (Math.floor(e.start / 3600) !== h) continue;
      const ms = marksIn(e);
      const head = isQuake(e) ? (ms.length ? '<span class="ok">○</span>' : '<span class="ng">―</span>')
                              : (ms.length ? '<span class="ng">△</span>' : '<span class="ng">・</span>');
      const pins = ms.length ? ` <span class="pinMark">印 ${ms.map(markNo).join("・")}</span>` : "";
      const tail = !isQuake(e) && ms.length ? "(地震ではありません)" : "";
      const li = document.createElement("li"), b = document.createElement("button");
      b.className = "link";
      b.innerHTML = `${head} ${e.no}. <b style="color:${e.color}">${escapeHtml(e.kind)}</b> ${clock(e.start)}〜${clock(e.end)} ${escapeHtml(e.text)}${tail}${pins}`;
      b.onclick = () => zoomFrom("answers", e.start);
      li.appendChild(b);
      if (e.note) li.appendChild(Object.assign(document.createElement("div"), { className: "note", textContent: e.note }));
      ul.appendChild(li);
    }
    for (const m of S.marks) {
      if (Math.floor(m / 3600) !== h || used.has(m)) continue;
      const li = document.createElement("li"), b = document.createElement("button");
      b.className = "link";
      b.innerHTML = `<span class="pinMark">印 ${markNo(m)}</span> ${clock(m)} 答えの一覧に無い所です(列車・風・人の活動による揺れや、とても小さな地震かもしれません。拡大して確かめてみましょう)`;
      b.onclick = () => zoomFrom("answers", m);
      li.appendChild(b); ul.appendChild(li);
    }
    sec.append(wrap, ul);
    box.appendChild(sec);
  }
  setHash();
}

// --- 1 時間の図 ---
function buildStations() {
  const box = $("stations");
  box.innerHTML = "";
  for (const st of META.stations) {
    const b = document.createElement("button");
    b.textContent = st.label; b.className = st.id === S.st ? "on" : "";
    b.onclick = () => {
      S.st = st.id; buildStations();
      if (S.view === "hour") showHour();
      else if (S.view === "zoom") openZoom(viewMid(), true);
      else { const y = window.scrollY; showAnswers(); window.scrollTo(0, y); }
    };
    box.appendChild(b);
  }
}
function buildHours() {
  const sel = $("hourSel");
  sel.innerHTML = "";
  for (let h = 0; h < 24; h++) {
    const o = document.createElement("option");
    o.value = h; o.textContent = `${pad(h)}時`;
    sel.appendChild(o);
  }
  sel.onchange = () => { S.hour = +sel.value; showHour(); };
  $("prevHour").onclick = () => { if (S.hour > 0) { S.hour--; showHour(); } };
  $("nextHour").onclick = () => { if (S.hour < 23) { S.hour++; showHour(); } };
}
// 1 時間の図の画像を替える。読み込むまでは前の図をうすくして「読み込み中…」を出す。前後の時間も先に読んでおく
const preloaded = new Map();
function setHourImage(src) {
  const img = $("hourImg"), wrap = $("hourWrap"), load = $("loading");
  if (img.getAttribute("src") === src && img.complete) return;
  const done = () => { if (img.getAttribute("src") === src) { wrap.classList.remove("dim"); load.hidden = true; } };
  img.onload = done; img.onerror = done;
  wrap.classList.add("dim"); load.hidden = !src; load.textContent = "読み込み中…";
  img.src = src;
  if (!src) { wrap.classList.remove("dim"); }
  for (const h of [S.hour - 1, S.hour + 1]) {
    const next = h >= 0 && h < 24 ? imageSrc(h) : "";
    if (next && !preloaded.has(next)) { const im = new Image(); im.src = next; preloaded.set(next, im); }
  }
}
function showHour() {
  setView("hour");
  $("hourSel").value = S.hour;
  $("prevHour").disabled = S.hour === 0; $("nextHour").disabled = S.hour === 23;
  const st = station(S.st), src = imageSrc(S.hour);
  setHourImage(src);
  $("hourImg").alt = src ? `${st.label} ${pad(S.hour)}時の記録` : "この時間の記録はありません";
  $("check").textContent = S.checked ? "答えを見る" : "答え合わせ";
  $("check").classList.toggle("on", S.checked);
  drawOverlay($("marks"), S.hour, S.checked);
  setHash();
}
// 図の中の位置(図の幅・高さに対する割合)と、その時間の始まりからの秒
function figToSec(fx, fy) {
  const [bx, by, bw, bh] = META.box, [y0, y1] = META.ylim;
  const top = 1 - (by + bh);
  const row = Math.min(59, Math.max(0, Math.round(y1 + (fy - top) / bh * (y0 - y1))));
  const sec = Math.min(60, Math.max(0, (fx - bx) / bw * 60));
  return row * 60 + sec;
}
function secToFig(s) {   // 戻り値 ... [横の割合, 段の上の割合, 段の高さの割合]
  const [bx, by, bw, bh] = META.box, [y0, y1] = META.ylim;
  const top = 1 - (by + bh), row = Math.floor(s / 60), unit = bh / (y0 - y1);
  return [bx + (s - row * 60) / 60 * bw, top + (row - 0.5 - y1) * unit, unit];
}
// 1 時間の図に重ねる: 自分の印(赤い線と番号)と、withAnswers なら答えの揺れ(色の帯と番号)
function drawOverlay(box, hour, withAnswers) {
  box.innerHTML = "";
  const h0 = hour * 3600;
  if (withAnswers) for (const e of META.events) {
    const a = Math.max(e.start, h0), b = Math.min(e.end, h0 + 3600);
    if (a >= b) continue;
    let first = true;
    for (let r = Math.floor((a - h0) / 60); r <= Math.floor((b - 1 - h0) / 60); r++) {
      const x0 = Math.max(a - h0, r * 60), x1 = Math.min(b - h0, (r + 1) * 60);   // その時間の始まりからの秒
      const [fx0, ftop, unit] = secToFig(x0), fx1 = META.box[0] + (x1 - r * 60) / 60 * META.box[2];
      const div = Object.assign(document.createElement("div"), { className: "mark" });
      Object.assign(div.style, { left: fx0 * 100 + "%", width: Math.max(0.3, (fx1 - fx0) * 100) + "%", top: ftop * 100 + "%",
                                 height: unit * 100 + "%", background: e.color });
      box.appendChild(div);
      if (first) {          // 始まりに縦の線と番号(名前は下の答えの一覧に。波形をかくさないように小さく)
        const edge = Object.assign(document.createElement("div"), { className: "markEdge" });
        Object.assign(edge.style, { left: fx0 * 100 + "%", top: ftop * 100 + "%", height: unit * 100 + "%", background: e.color });
        const lab = Object.assign(document.createElement("div"), { className: "markLabel", textContent: e.no });
        lab.title = `${e.no}. ${e.kind}`;
        Object.assign(lab.style, { color: e.color, borderColor: e.color, left: fx0 * 100 + "%", top: ftop * 100 + "%" });
        box.append(edge, lab);
        first = false;
      }
    }
  }
  for (const m of S.marks) {
    if (m < h0 || m >= h0 + 3600) continue;
    const [fx, ftop, unit] = secToFig(m - h0);
    const pin = Object.assign(document.createElement("div"), { className: "pin" });
    Object.assign(pin.style, { left: fx * 100 + "%", top: ftop * 100 + "%", height: unit * 100 + "%" });
    const lab = Object.assign(document.createElement("div"), { className: "pinLabel", textContent: markNo(m) });
    Object.assign(lab.style, { left: fx * 100 + "%", top: ftop * 100 + "%" });
    box.append(pin, lab);
  }
}
$("hourImg").addEventListener("click", e => {
  const r = e.currentTarget.getBoundingClientRect();
  const s = figToSec((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
  if (S.tapMode === "mark") {          // 印: 同じ段の 2 秒以内の印は消す
    const tol = Math.max(2, 16 / r.width / META.box[2] * 60);      // 印の近く(16 px か 2 秒)をタップしたら消す
    const sec = S.hour * 3600 + s, near = S.marks.find(m => Math.floor(m / 60) === Math.floor(sec / 60) && Math.abs(m - sec) <= tol);
    toggleMark(near !== undefined ? near : sec, 0);
    drawOverlay($("marks"), S.hour, S.checked);
    return;
  }
  const dot = Object.assign(document.createElement("div"), { className: "tapDot" });
  Object.assign(dot.style, { left: (e.clientX - r.left) + "px", top: (e.clientY - r.top) + "px" });
  $("marks").appendChild(dot);
  setTimeout(() => zoomFrom("hour", S.hour * 3600 + s), 120);
});
// 拡大へ(戻るボタンで from の画面に戻る)
function zoomFrom(from, sec) { S.from = from; S.fromY = window.scrollY; openZoom(sec, false); }

// --- 拡大用の波形を読む ---
function loadHour(id, h) {
  const st = station(id), key = `${id}_${h}`;
  if (h < 0 || h > 23 || !st.hours.includes(h)) return Promise.resolve(null);
  if (!files.has(key)) {
    files.set(key, fetch(`data/${st.label}_${pad(h)}.json`).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(async j => ({ v: decodeSamples(await gunzipBase64(j.d)), q: j.q, gaps: j.g }))
      .catch(err => { files.delete(key); throw err; }));
  }
  return files.get(key);
}
// 拠点の 3 時間(前・その時・次)をつなぐ。値はそれぞれの時の q を共通の物差し(3 時間の最小の q)にそろえる
async function stationTrace(id, h) {
  const parts = await Promise.all([h - 1, h, h + 1].map(k => loadHour(id, k)));
  const q = Math.min(...parts.filter(Boolean).map(p => p.q));
  const v = new Float64Array(3 * HOUR_N), gaps = [];
  parts.forEach((p, k) => {
    if (!p) { gaps.push([k * HOUR_N, (k + 1) * HOUR_N]); return; }
    const f = p.q / q;
    for (let i = 0; i < HOUR_N; i++) v[k * HOUR_N + i] = p.v[i] * f;
    for (const [a, b] of p.gaps) gaps.push([k * HOUR_N + a, k * HOUR_N + b]);
  });
  return { raw: v, q, gaps };
}
function prepare(tr) {          // 今のフィルタをかけて、描くための形にする
  const f = META.filters.find(x => x.key === S.filter);
  const x = new Float64Array(tr.raw);
  for (const [a, b] of tr.gaps) x.fill(0, a, b);
  const v = new Int32Array(x.length);
  if (f) { const y = filterSamples(x, f.sections); v.set(y); }
  else for (let i = 0; i < x.length; i++) v[i] = Math.round(x[i] * FINE);
  tr.v = v; tr.levels = buildLevels(v); tr.step = tr.q / FINE;
}

// --- 部分の拡大 ---
async function openZoom(sec, keepView) {
  stopAudio();
  const token = ++S.token;
  S.view = "zoom"; S.center = sec;
  setView("zoom");
  const h = Math.min(23, Math.floor(sec / 3600));
  const keep = keepView && S.base === (h - 1) * HOUR_N ? [S.v0, S.span] : null;
  S.base = (h - 1) * HOUR_N; S.n = 3 * HOUR_N;
  const ids = S.all4 ? META.stations.map(s => s.id) : [S.st];
  $("status").textContent = "波形を読んでいます…";
  S.traces = {};
  resize();
  try {
    const got = await Promise.all(ids.map(id => stationTrace(id, h)));
    if (token !== S.token) return;
    ids.forEach((id, k) => { prepare(got[k]); S.traces[id] = got[k]; });
    $("status").textContent = "";
  } catch (err) {
    $("status").textContent = "波形を読めませんでした(サーバーに置いて開いてください): " + err.message;
    return;
  }
  const local = sec * RATE - S.base;
  S.home = [local - 30 * RATE, 120 * RATE];
  if (keep) [S.v0, S.span] = keep; else [S.v0, S.span] = S.home;
  clampView();
  updateZoomNote();
  setHash();
  requestDraw();
}
function clampView() {
  S.span = Math.min(Math.max(S.span, 2 * RATE), S.n);
  S.v0 = Math.min(Math.max(S.v0, 0), S.n - S.span);
}
function updateZoomNote() {
  const f = META.filters.find(x => x.key === S.filter);
  $("zoomNote").textContent = (f ? `フィルタ ${f.key} は ${f.low}〜${f.high} Hz(${f.sound})の揺れだけを通します。` : `フィルタなし(${META.dataBand[0]}〜${META.dataBand[1]} Hz)。`)
    + `×${S.speed} は ${S.speed} 倍の速さで、5 Hz の揺れが ${5 * S.speed} Hz の音になります。`;
}
function buildFilters() {
  const box = $("filters");
  box.innerHTML = "";
  for (const f of [{ key: "" }, ...META.filters]) {
    const b = document.createElement("button");
    b.textContent = f.key || "元"; b.className = f.key === S.filter ? "on" : "";
    b.title = f.key ? `${f.low}〜${f.high} Hz` : "フィルタなし";
    b.onclick = () => { S.filter = f.key; buildFilters(); stopAudio(); for (const tr of Object.values(S.traces)) prepare(tr); updateZoomNote(); requestDraw(); };
    box.appendChild(b);
  }
}
function buildSpeeds() {
  const box = $("speeds");
  box.innerHTML = "";
  for (const sp of META.audio.speeds) {
    const b = document.createElement("button");
    b.textContent = `×${sp}`; b.className = sp === S.speed ? "on" : "";
    b.onclick = () => { S.speed = sp; buildSpeeds(); stopAudio(); updateZoomNote(); };
    box.appendChild(b);
  }
}
$("back").onclick = () => {
  if (S.from === "answers" && S.checked) { showAnswers(); window.scrollTo(0, S.fromY || 0); return; }
  S.hour = Math.min(23, Math.floor(viewMid() / 3600)); showHour();
};
$("earlier").onclick = () => { S.v0 -= 60 * RATE; recenter(); };
$("later").onclick = () => { S.v0 += 60 * RATE; recenter(); };
$("markZoom").onclick = () => { S.zoomMark = !S.zoomMark; $("markZoom").classList.toggle("on", S.zoomMark); };
$("all4").onclick = () => { S.all4 = !S.all4; $("all4").classList.toggle("on", S.all4); openZoom(viewMid(), true); };
function viewMid() { return (S.base + S.v0 + S.span / 2) / RATE; }      // 見えている範囲の真ん中(日の始まりからの秒)
// 見えている範囲が読んだ 3 時間の端に近づいたら、その時刻を中心に読み直す
function recenter() {
  stopAudio();
  const mid = (S.base + S.v0 + S.span / 2) / RATE;
  if (S.v0 < 0 || S.v0 + S.span > S.n || Math.floor(mid / 3600) !== Math.floor(S.base / RATE / 3600) + 1) {
    const span = S.span, off = mid * RATE - S.base - S.v0;
    openZoom(mid, false).then(() => { S.span = span; S.v0 = mid * RATE - S.base - off; clampView(); requestDraw(); });
    return;
  }
  clampView(); requestDraw();
}

function readColors() {
  const cs = getComputedStyle(document.documentElement);
  for (const k of ["ink", "muted", "line", "wave", "gap", "cursor", "sel", "accent"]) colors[k] = cs.getPropertyValue("--" + k).trim();
}
let drawQueued = false;
function requestDraw() { if (!drawQueued) { drawQueued = true; requestAnimationFrame(() => { drawQueued = false; draw(); }); } }
function resize() {
  S.dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(r.width * S.dpr)); canvas.height = Math.max(1, Math.round(r.height * S.dpr));
  requestDraw();
}
function clockStep(seconds, most) {     // 時刻の目盛りの間隔(目盛りが most 個以下で、きりのよい時刻になるもの)
  for (const s of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]) if (seconds / s <= most) return s;
  return 3600;
}
function draw() {
  readColors();
  const W = canvas.width, H = canvas.height, d = S.dpr;
  ctx.clearRect(0, 0, W, H);
  const ids = Object.keys(S.traces);
  if (!ids.length) return;
  const axisH = 22 * d, rowH = (H - axisH) / ids.length;
  // 時刻の目盛り
  const t0 = (S.base + S.v0) / RATE, t1 = t0 + S.span / RATE, step = clockStep(t1 - t0, Math.max(2, Math.floor(W / (72 * d))));   // 時刻の文字が重ならないように
  ctx.font = `${11 * d}px -apple-system, sans-serif`;
  ctx.fillStyle = colors.muted; ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
  ctx.textAlign = "center"; ctx.textBaseline = "top";
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) {
    const x = Math.round((t - t0) / (t1 - t0) * W) + 0.5;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H - axisH); ctx.stroke();
    ctx.fillText(step < 1 ? clock(t) + "." + Math.round((t % 1) * 10) : clock(t), x, H - axisH + 4 * d);
  }
  ids.forEach((id, k) => {
    const tr = S.traces[id], top = k * rowH;
    if (ids.length > 1 && id === S.st) { ctx.fillStyle = colors.sel; ctx.fillRect(0, top, W, rowH); }
    const out = { lo: 0, hi: 0 };
    rangeOf(tr, Math.max(0, Math.floor(S.v0)), Math.min(S.n, Math.ceil(S.v0 + S.span)), out);
    const half = Math.max(Math.abs(out.lo), Math.abs(out.hi)) * tr.step * 1.05;
    drawTrace(tr, top, rowH, 0, half, S.n);
    ctx.fillStyle = colors.ink; ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.font = `600 ${12 * d}px -apple-system, sans-serif`;
    ctx.fillText((ids.length > 1 && id === S.st ? "♪ " : "") + station(id).label, 6 * d, top + 4 * d);
    ctx.textAlign = "right"; ctx.font = `${11 * d}px -apple-system, sans-serif`; ctx.fillStyle = colors.muted;
    ctx.fillText(`±${formatAmp(half)}`, W - 6 * d, top + 4 * d);
    ctx.strokeStyle = colors.line;
    ctx.beginPath(); ctx.moveTo(0, top + rowH + 0.5); ctx.lineTo(W, top + rowH + 0.5); ctx.stroke();
  });
  // 答え(答え合わせのあと)と印
  const xOf = sec => (sec * RATE - S.base - S.v0) / S.span * W, plotH = H - axisH;
  ctx.font = `600 ${11 * d}px -apple-system, sans-serif`; ctx.textBaseline = "top";
  if (S.checked) for (const e of META.events) {
    const x0 = xOf(e.start), x1 = xOf(e.end);
    if (x1 < 0 || x0 > W) continue;
    ctx.globalAlpha = 0.14; ctx.fillStyle = e.color; ctx.fillRect(x0, 0, Math.max(2, x1 - x0), plotH); ctx.globalAlpha = 1;
    ctx.fillStyle = e.color; ctx.textAlign = "left";
    ctx.fillText(`${e.no}. ${e.kind}`, Math.max(2 * d, x0 + 3 * d), plotH - 16 * d);
  }
  for (const m of S.marks) {
    const x = Math.round(xOf(m)) + 0.5;
    if (x < 0 || x > W) continue;
    ctx.strokeStyle = "#d32f2f"; ctx.lineWidth = 2 * d; ctx.setLineDash([5 * d, 3 * d]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke(); ctx.setLineDash([]);
    const t = `印 ${markNo(m)}`, w = ctx.measureText(t).width + 6 * d;
    ctx.fillStyle = "#d32f2f"; ctx.fillRect(x - w / 2, 20 * d, w, 15 * d);       // 印の番号は上に(答えの名前は下)
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.fillText(t, x, 22 * d);
  }
  // 再生中の位置
  if (AUD.url && !AUD.el.paused) {
    const x = (AUD.from + AUD.el.currentTime * RATE * AUD.speed - S.v0) / S.span * W;
    ctx.strokeStyle = colors.cursor; ctx.lineWidth = 2 * d;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H - axisH); ctx.stroke();
  }
}

// --- 音 ---
const AUD = { el: new Audio(), url: "", from: 0, speed: 0, raf: 0 };
AUD.el.addEventListener("ended", stopAudio);
function stopAudio() {
  audioStop(AUD);
  $("play").textContent = "▶ 聴く";
  requestDraw();
}
$("play").onclick = async () => {
  if (AUD.url) { stopAudio(); return; }
  const tr = S.traces[S.st];
  if (!tr) return;
  const a = Math.max(0, Math.floor(S.v0)), b = Math.min(S.n, Math.ceil(S.v0 + S.span));
  const x = new Float64Array(b - a);
  for (let i = a; i < b; i++) x[i - a] = tr.raw[i];
  for (const [g0, g1] of tr.gaps) if (g1 > a && g0 < b) x.fill(0, Math.max(0, g0 - a), Math.min(b, g1) - a);
  let mean = 0; for (const v of x) mean += v; mean /= x.length || 1;
  for (let i = 0; i < x.length; i++) x[i] -= mean;
  zeroPhase(x, META.audio.sections);
  const f = META.filters.find(z => z.key === S.filter);
  if (f) zeroPhase(x, f.sections);
  const s = resampleForSound(x, RATE * S.speed);
  let peak = 0; for (const v of s.y) if (Math.abs(v) > peak) peak = Math.abs(v);
  AUD.from = a; AUD.speed = S.speed;
  const play = audioPlay(AUD, wavBlob(finishSound(s.y, s.rate, peak > 0 ? 0.9 / peak : 0), s.rate), requestDraw);
  $("play").textContent = "■ 止める";
  try { await play; } catch (err) { stopAudio(); $("status").textContent = "音を鳴らせませんでした: " + err.message; }
};

// --- 拡大・移動(ピンチ・ホイール・ドラッグ・ダブルタップ。page_common の attachGestures) ---
attachGestures(canvas, {
  view: () => [S.v0, S.span],
  setView: (v0, span) => { S.v0 = v0; S.span = span; clampView(); requestDraw(); },
  spanLimits: () => [2 * RATE, Math.max(2 * RATE, S.n)],
  onTap: (x, y) => {
    const W = canvas.clientWidth;
    if (S.zoomMark) {                       // 印: タップした時刻に付ける(近くの印は消す)
      const sec = (S.base + S.v0 + x / W * S.span) / RATE, tol = 20 / W * S.span / RATE;
      toggleMark(sec, tol); requestDraw();
      return;
    }
    const ids = Object.keys(S.traces);      // 4 拠点のときは、タップした段の拠点の音にする
    if (ids.length > 1) {
      const rowH = (canvas.clientHeight - 22) / ids.length, id = ids[Math.min(ids.length - 1, Math.floor(y / rowH))];
      if (id && id !== S.st) { S.st = id; stopAudio(); buildStations(); requestDraw(); }
    }
  },
  onDoubleTap: () => { if (!S.zoomMark) { [S.v0, S.span] = S.home; clampView(); requestDraw(); } },
  onPanEnd: recenter,
});

// --- 場所を URL に残す(#拠点/時 または #拠点/z/秒) ---
function setHash() {
  const lab = station(S.st).label;
  const h = S.view === "hour" ? `#${lab}/${pad(S.hour)}` : S.view === "answers" ? `#${lab}/answers` : `#${lab}/z/${Math.round(S.center)}`;
  if (location.hash !== h) history.replaceState(null, "", h);
}
function route() {
  const [lab, a, b] = decodeURIComponent(location.hash.slice(1)).split("/");
  const st = META.stations.find(s => s.label === lab);
  if (st) S.st = st.id;
  buildStations();
  if (a === "z" && isFinite(+b)) openZoom(+b, false);
  else if (a === "answers" && S.checked) showAnswers();
  else { if (a !== undefined && isFinite(+a)) S.hour = Math.min(23, Math.max(0, +a)); showHour(); }
}

$("title").textContent = META.title;
loadMarks(); updateMarkBar(); setTapMode("zoom");
buildHours(); buildFilters(); buildSpeeds();
window.addEventListener("resize", () => { if (S.view === "zoom") resize(); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", requestDraw);
route();
</script>
</body>
</html>
"""


def main():
  from plot_common import setup_japanese_font
  parser = argparse.ArgumentParser(description="1日の記録から揺れを探す教材の、画面の版(1 時間の図と部分の拡大)を作る(TODO 51)")
  parser.add_argument("day", help="日(例: 2026-07-08)")
  parser.add_argument("--stations", nargs="*", default=sq.STATIONS, help="拠点(既定は 4 拠点)")
  parser.add_argument("--hours", default="0-23", help="時(例: 0-23、19、1,11,16-17)")
  parser.add_argument("-o", "--output", help="出力のフォルダ(省略すると batch_plots/quiz/day_web/<日>)")
  parser.add_argument("--page-only", action="store_true", help="画像と波形は作り直さず、ページだけを書き直す")
  args = parser.parse_args()
  setup_japanese_font()
  day = datetime.strptime(args.day, "%Y-%m-%d")
  if args.page_only:
    image_wf = data_wf = {nm: None for nm in sq.STATIONS}
  else:
    image_wf = sd.cached_waveforms(day, IMAGE_BAND)
    data_wf = sd.cached_waveforms(day, DATA_BAND)
  out = Path(args.output) if args.output else OUTPUT_DIR / f"{day:%Y%m%d}"
  order = sq.FIRST_SOUND + [nm for nm in sq.STATIONS if nm not in sq.FIRST_SOUND]    # Kawaminami から(クイズの音の順)
  stations = sorted((nm for nm in args.stations if nm in image_wf and nm in data_wf), key=order.index)
  page = write_day(out, day, image_wf, data_wf, stations, sd.parse_hours(args.hours), sd.read_answers(day), page_only=args.page_only)
  size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
  print(f"書き出しました({size / 1e6:.1f} MB): {page}")
  if not args.output:
    print(f"日の一覧: {write_index(OUTPUT_DIR)}")

if __name__ == "__main__":
  main()
