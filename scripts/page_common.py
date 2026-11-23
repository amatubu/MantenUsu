# page_common.py
# 教材のページ(クイズ・比べる・フィルタを試す・1日の記録の画面の版)で同じ JS の部品
#   ページはファイル 1 つで動くように、書き出すときにこの文字列を HTML に埋め込む(__COMMON_JS__ を PAGE_JS に置き換える)。
#   ここを直せば、ページを作り直すだけで全部のページに入る
#   - COMMON_JS: 波形を戻す・フィルタ・1本の波形を描く・音(WAV)にする
#   - VIEW_JS: 振幅の文字、時刻の目盛りの間隔、文字の白い箱
#   - AUDIO_JS: 音を鳴らす・止める(Audio の要素と、再生中に毎コマ呼ぶ関数)
#   - GESTURE_JS: 波形の拡大・移動の操作(ピンチ・ドラッグ・ホイール・タップ・ダブルタップ)
#   部品が使う、ページの側の名前: S.dpr(画面の点の細かさ)、ctx・canvas(波形の canvas)、colors(色。bg・wave・gap)、S.v0・S.span(drawTrace)


# 波形を戻す・フィルタ・1本の波形を描く・音にする(はじめはクイズ(shake_quiz.py)にあった)
COMMON_JS = r"""
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
// 往復でかける帯域通過フィルタ(時間のずれがない)。カウントに 16 倍の余裕を持たせて整数に丸める
const FINE = 16;
function applySections(x, sections) {
  const n = x.length;
  for (const [b0, b1, b2, a1, a2] of sections) {
    let z1 = 0, z2 = 0;
    for (let i = 0; i < n; i++) {
      const input = x[i], output = b0 * input + z1;
      z1 = b1 * input - a1 * output + z2;
      z2 = b2 * input - a2 * output;
      x[i] = output;
    }
  }
}
function filterSamples(v, sections) {
  const x = new Float64Array(v), out = new Int32Array(v.length);
  applySections(x, sections); x.reverse(); applySections(x, sections); x.reverse();
  for (let i = 0; i < x.length; i++) out[i] = Math.round(x[i] * FINE);
  return out;
}

// --- 1本の波形を描く(全体の S.v0・S.span・S.dpr と ctx・canvas・colors を使う) ---
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
  ctx.strokeStyle = colors.wave;
  ctx.beginPath();
  if (spp < 2) {
    const a = Math.max(0, Math.floor(S.v0) - 1), b = Math.min(n - 1, Math.ceil(S.v0 + S.span) + 1);
    for (let i = a; i <= b; i++) {
      const x = (i - S.v0) / spp, y = yOf(v[i]);
      if (i === a) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.lineWidth = Math.max(1, d * 0.8);
    ctx.lineJoin = "round";
    ctx.stroke();
  } else {
    const out = { lo: 0, hi: 0 };
    let prevTop = null, prevBottom = null;
    for (let px = 0; px < W; px++) {
      const a = Math.max(0, Math.floor(S.v0 + px * spp)), b = Math.min(n, Math.floor(S.v0 + (px + 1) * spp));
      if (b <= a) continue;
      rangeOf(tr, a, b, out);
      let y0 = yOf(out.hi), y1 = yOf(out.lo);
      const t0 = y0, t1 = y1;
      if (prevBottom !== null) {
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

// --- 音(WAV) ---
function zeroPhase(x, sections) { applySections(x, sections); x.reverse(); applySections(x, sections); x.reverse(); }
function wavBlob(y, rate) {
  const n = y.length, buf = new ArrayBuffer(44 + 2 * n), v = new DataView(buf);
  const w = (o, str) => { for (let i = 0; i < str.length; i++) v.setUint8(o + i, str.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + 2 * n, true); w(8, "WAVE"); w(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, 2 * n, true);
  for (let i = 0; i < n; i++) v.setInt16(44 + 2 * i, Math.round(Math.max(-1, Math.min(1, y[i])) * 32767), true);
  return new Blob([buf], { type: "audio/wav" });
}
// 帯域を制限した補間(sinc)で、fin [Hz] で再生する波形 x を音のサンプリングに直す。戻り値 ... {y, rate}
function resampleForSound(x, fin) {
  const n = x.length, rate = fin <= 16000 ? 16000 : 32000, stepIn = fin / rate, HALF = 8;
  const m = Math.floor(n / stepIn), y = new Float32Array(m);
  for (let i = 0; i < m; i++) {
    const u = i * stepIn, k = Math.floor(u);
    let sum = 0;
    for (let j = k - HALF + 1; j <= k + HALF; j++) {
      if (j < 0 || j >= n) continue;
      const dd = u - j, win = 0.5 + 0.5 * Math.cos(Math.PI * dd / HALF);
      sum += x[j] * (dd === 0 ? 1 : Math.sin(Math.PI * dd) / (Math.PI * dd)) * win;
    }
    y[i] = sum;
  }
  return { y, rate };
}
// 音の大きさを g 倍にし、始めと終わりを 30 ms で絞る
function finishSound(y, rate, g) {
  const m = y.length, fade = Math.floor(0.03 * rate);
  for (let i = 0; i < m; i++) {
    let s = y[i] * g;
    if (i < fade) s *= i / fade;
    if (m - 1 - i < fade) s *= (m - 1 - i) / fade;
    y[i] = s;
  }
  return y;
}
"""


# 振幅の文字・時刻の目盛り・文字の白い箱
VIEW_JS = r"""
// --- 振幅の文字・時刻の目盛り・文字の白い箱 ---
function formatAmp(nm) {
  if (nm >= 1e6) return (nm / 1e6).toPrecision(3) + " mm/s";
  if (nm >= 1e3) return (nm / 1e3).toPrecision(3) + " µm/s";
  return nm.toPrecision(3) + " nm/s";
}
// 目盛りの間隔: seconds 以上の、きりのよい秒
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
"""

# 音を鳴らす・止める。aud は { el: new Audio(), url: "", raf: 0, ... }(ほかの項目はページで自由に足す)
AUDIO_JS = r"""
// --- 音を鳴らす・止める ---
// blob(WAV)を鳴らし、鳴っている間は毎コマ onFrame を呼ぶ(再生中の線を描く)。鳴らせなければ例外を投げる
async function audioPlay(aud, blob, onFrame) {
  if (aud.url) URL.revokeObjectURL(aud.url);
  aud.url = URL.createObjectURL(blob);
  aud.el.src = aud.url;
  await aud.el.play();
  cancelAnimationFrame(aud.raf);
  const tick = () => { onFrame(); if (!aud.el.paused) aud.raf = requestAnimationFrame(tick); };
  tick();
}
function audioStop(aud) {
  aud.el.pause();
  cancelAnimationFrame(aud.raf);
  if (aud.url) URL.revokeObjectURL(aud.url);
  aud.url = "";
}
"""

# 波形の拡大・移動の操作
GESTURE_JS = r"""
// --- 波形の拡大・移動の操作(指・マウス) ---
// opts:
//   view() → [v0, span] ... 今の表示(左端と幅。単位はページの自由。ふつうはサンプル)
//   setView(v0, span) ... 表示を変える(範囲に収めるのはページの側)
//   spanLimits() → [最小, 最大] ... 幅の範囲(ピンチ・ホイールで、指やマウスの下の時刻を動かさないように先に収める)
//   onTap(x, y) ... 1 回タップした(x・y は canvas の中の CSS の px)。ダブルタップの 2 回目でも先に呼ぶ
//   onDoubleTap() ... ダブルタップした
//   onPanEnd() ... 1 本指・マウスで動かし終えた
// 1 本指・マウスのドラッグで左右に動かす。2 本指は、指の横の開き(縦は使わない)が 1 割以上変わるまでは左右に動かすだけにし、
// 変わったら拡大縮小する。新しいタッチが始まったら、終わりの知らせが来なかった古い指を消す(残ると 1 本指の移動が拡大縮小になる)
function attachGestures(canvas, opts) {
  const pointers = new Map();
  let gesture = null, lastTap = null;
  const noop = () => {};
  const onTap = opts.onTap || noop, onDoubleTap = opts.onDoubleTap || noop, onPanEnd = opts.onPanEnd || noop;
  const localX = e => e.clientX - canvas.getBoundingClientRect().left;
  const clampSpan = span => { const [lo, hi] = opts.spanLimits(); return Math.min(hi, Math.max(lo, span)); };
  function start() {
    const p = [...pointers.values()], w = canvas.clientWidth, [v0, span] = opts.view();
    if (p.length === 1) gesture = { kind: "pan", x: p[0].x, v0, span, w };
    else if (p.length >= 2) {
      const mid = (p[0].x + p[1].x) / 2;
      gesture = { kind: "pinch", dist: Math.max(20, Math.abs(p[0].x - p[1].x)), zooming: false, anchor: v0 + mid / w * span, span, w };
    } else gesture = null;
  }
  canvas.addEventListener("pointerdown", e => {
    e.preventDefault();
    if (e.isPrimary) pointers.clear();
    try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
    pointers.set(e.pointerId, { x: localX(e), y: e.clientY, x0: localX(e), y0: e.clientY, t0: e.timeStamp, moved: false });
    if (pointers.size > 1) for (const p of pointers.values()) p.moved = true;
    start();
  });
  canvas.addEventListener("pointermove", e => {
    const me = pointers.get(e.pointerId);
    if (!me) return;
    e.preventDefault();
    me.x = localX(e); me.y = e.clientY;
    if (Math.hypot(me.x - me.x0, me.y - me.y0) > 10) me.moved = true;
    const p = [...pointers.values()];
    if (gesture && gesture.kind === "pan") opts.setView(gesture.v0 - (me.x - gesture.x) / gesture.w * gesture.span, gesture.span);
    else if (gesture && gesture.kind === "pinch" && p.length >= 2) {
      const dist = Math.max(20, Math.abs(p[0].x - p[1].x)), mid = (p[0].x + p[1].x) / 2;
      if (!gesture.zooming && Math.abs(dist / gesture.dist - 1) > 0.1) gesture.zooming = true;
      const span = gesture.zooming ? clampSpan(gesture.span * gesture.dist / dist) : gesture.span;
      opts.setView(gesture.anchor - mid / gesture.w * span, span);
    }
  });
  function end(e) {
    const me = pointers.get(e.pointerId);
    if (!me) return;
    const wasPan = gesture && gesture.kind === "pan";
    pointers.delete(e.pointerId);
    if (e.type === "pointerup" && !me.moved && pointers.size === 0 && e.timeStamp - me.t0 < 300) {
      const r = canvas.getBoundingClientRect();
      onTap(me.x, e.clientY - r.top);
      if (lastTap && e.timeStamp - lastTap.t < 350 && Math.hypot(me.x - lastTap.x, me.y - lastTap.y) < 30) { lastTap = null; onDoubleTap(); }
      else lastTap = { t: e.timeStamp, x: me.x, y: me.y };
    } else if (wasPan && me.moved && pointers.size === 0) onPanEnd();
    start();
  }
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("lostpointercapture", end);
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    const x = localX(e), w = canvas.clientWidth, [v0, span] = opts.view();
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return opts.setView(v0 + e.deltaX / w * span, span);
    const anchor = v0 + x / w * span, next = clampSpan(span * Math.exp(e.deltaY * 0.002));
    opts.setView(anchor - x / w * next, next);
  }, { passive: false });
  canvas.addEventListener("touchmove", e => e.preventDefault(), { passive: false });
  for (const type of ["gesturestart", "gesturechange"]) canvas.addEventListener(type, e => e.preventDefault(), { passive: false });
}
"""

# ページに埋め込む全部
PAGE_JS = COMMON_JS + VIEW_JS + AUDIO_JS + GESTURE_JS
