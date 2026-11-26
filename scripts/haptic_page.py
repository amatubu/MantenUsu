# haptic_page.py
# 地震の揺れを、スマートフォンの振動で感じるための部品(TODO 58)。教材のページ(HTML)に入れて使う
#   揺れの強さの曲線: 波形から、コマ(既定 20 ms)ごとの揺れの大きさ(包絡線)を求め、雑音の floor 倍を 0、基準の大きさを 1 として、
#     割合の gamma 乗にする。Python の haptic_* とページの JavaScript(HAPTIC_JS の hapticEnvelope など)は同じ計算(テストで比べる)
#   再生: テスト用の iPhone のアプリ(ios/UsuMantenFeel、WKWebView)の中で開いたときは、webkit.messageHandlers.usuHaptics に
#     {cmd: "play", interval: 秒, intensity: [0〜1, ...], sharpness: 0〜1} を送り、アプリが Core Haptics で振動させる(止めるときは {cmd: "stop"})。
#     Safari などアプリの外では振動できないので、振動の操作はページに出さない(hapticHideOutsideApp)。
#     hapticPlay は、アプリの外でも画面の要素を揺らし(と、選べば低い音)で代わりにする
#   Android(Vibration API): opts.vibrate のページ(今は揺れを比べるページ)では、navigator.vibrate があれば
#     強さを VIBRATE_PERIOD ms ごとのオンの時間の割合にした並び(hapticPattern)で振動させる(強さは指定できないため)。
#     Chrome は 1 回に渡せる並びの長さに上限があるので、VIBRATE_CHUNK 個ずつに分けて続けて渡す
#   波形で揺らす: opts.motion(コマごとの -1〜1)があれば、画面を、でたらめではなくその値で上下に動かす
#     「視差効果を減らす」の設定のときは画面を動かさず、opts.marker(小さな点)を同じ値で上下に動かす
#     (hapticDisplacement で速度を積分した変位にし、0.5〜5 Hz にしぼる。画面の書き換えより速い揺れは見えないため)
#   使っているページ: quake_wave_view.py(有珠山付近の地震の波形)、shake_compare.py(揺れを比べる)、shake_world.py(世界の地震が届く)
import numpy as np

SAMPLE_RATE = 250

# 振動の強さの曲線の設定(ページにも渡す)
HAPTIC = {"frame": 5,           # 1 コマのサンプル数(250 Hz で 20 ms)
          "smoothFrames": 2,    # 前後何コマで平均するか(2 なら 5 コマ = 0.1 秒)
          "noiseBefore": -1.0,  # 発生時刻から何秒前までを雑音とみなすか
          "floor": 2.0,         # 雑音の何倍から振動させるか
          "gamma": 0.7,         # 強さ = (揺れの大きさの割合) の何乗か(1 なら比例。小さいほど弱い揺れも感じやすい)
          "sharpness": [0.3, 0.7]}  # 振動の鋭さ「やわらか」「くっきり」(Core Haptics の 0〜1。小さいほど鈍い)


# 振動の強さの曲線(ブラウザの hapticEnvelope・hapticNoise・hapticLevels と同じ計算。テストで2つを比べる)
#   traces ... [(フィルタをかけた値の並び [カウント], 1カウントの nm/s), ...](拠点の成分ごと)
#   n ... サンプル数、t0 ... 最初の点の発生時刻からの秒、accel ... True なら速度を微分した加速度で求める
# 揺れの大きさ: コマ(HAPTIC["frame"] 点)ごとの 3 成分の 2 乗の平均を、前後 smoothFrames コマで平均した平方根
#   [nm/s、加速度なら nm/s²]
def haptic_envelope(traces, n, rate=SAMPLE_RATE, p=HAPTIC, accel=False):
  f = p["frame"]
  k = n // f
  energy = np.zeros(k)
  for values, step in traces:
    x = np.asarray(values[:n], dtype=float) * step
    if accel:     # 中央の差分(両端は 0)
      d = np.zeros_like(x)
      d[1:-1] = (x[2:] - x[:-2]) * rate / 2
      x = d
    x = x[:k * f]
    energy += (x * x).reshape(k, f).mean(axis=1)
  half = p["smoothFrames"]
  return np.array([np.sqrt(energy[max(0, i - half):min(k, i + half + 1)].mean()) for i in range(k)])

# 雑音の大きさ: 発生の noiseBefore 秒前までのコマの揺れの大きさの中央値(そのコマが無ければ全体の中央値)。求められなければ None
def haptic_noise(env, t0, rate=SAMPLE_RATE, p=HAPTIC):
  ends = t0 + (np.arange(env.size) + 1) * p["frame"] / rate
  quiet = env[ends <= p["noiseBefore"]]
  pool = quiet if quiet.size else env
  return float(np.median(pool)) if pool.size else None

# 強さ 0〜1: 雑音の floor 倍を 0、ref(地震ごとならその地震のいちばん大きい所)を 1 とした割合の gamma 乗
def haptic_levels(env, noise, ref, p=HAPTIC):
  out = np.zeros(env.size)
  if noise is None or not noise > 0:
    return out
  low = p["floor"] * noise
  if not ref > low:
    return out
  on = env > low
  out[on] = np.minimum(1, (env[on] - low) / (ref - low)) ** p["gamma"]
  return out

# 強さの曲線(ref を省くと、その地震のいちばん大きい所を 1 にする)
def haptic_curve(traces, n, t0, rate=SAMPLE_RATE, p=HAPTIC, accel=False, ref=None):
  env = haptic_envelope(traces, n, rate, p, accel)
  if not env.size:
    return env
  return haptic_levels(env, haptic_noise(env, t0, rate, p), env.max() if ref is None else ref, p)


# ページに入れる JavaScript(曲線の計算と再生)
HAPTIC_JS = r"""// HAPTIC_CURVE_BEGIN
// 振動の強さの曲線(haptic_page.haptic_envelope・haptic_noise・haptic_levels と同じ計算)
//   traces ... [{v: フィルタをかけた値, step: 1カウントの nm/s}]、accel ... true なら速度を微分した加速度で求める
function median(values) {
  const s = Float64Array.from(values).sort(), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
function hapticEnvelope(traces, n, rate, P, accel) {
  const F = P.frame, K = Math.floor(n / F), energy = new Float64Array(K), env = new Float64Array(K);
  for (const tr of traces) {
    let x = Float64Array.from(tr.v.subarray ? tr.v.subarray(0, n) : tr.v.slice(0, n), v => v * tr.step);
    if (accel) {     // 中央の差分(両端は 0)
      const d = new Float64Array(x.length);
      for (let i = 1; i < x.length - 1; i++) d[i] = (x[i + 1] - x[i - 1]) * rate / 2;
      x = d;
    }
    for (let k = 0; k < K; k++) {
      let sum = 0;
      for (let i = k * F; i < (k + 1) * F; i++) sum += x[i] * x[i];
      energy[k] += sum / F;
    }
  }
  for (let k = 0; k < K; k++) {
    let sum = 0, count = 0;
    for (let j = Math.max(0, k - P.smoothFrames); j <= Math.min(K - 1, k + P.smoothFrames); j++) { sum += energy[j]; count++; }
    env[k] = Math.sqrt(sum / count);
  }
  return env;
}
function hapticNoise(env, t0, rate, P) {
  const quiet = [];
  for (let k = 0; k < env.length; k++) if (t0 + (k + 1) * P.frame / rate <= P.noiseBefore) quiet.push(env[k]);
  const pool = quiet.length ? quiet : Array.from(env);
  return pool.length ? median(pool) : null;
}
function hapticLevels(env, noise, ref, P) {
  const out = new Float64Array(env.length);
  if (noise === null || !(noise > 0)) return out;
  const low = P.floor * noise;
  if (!(ref > low)) return out;
  for (let k = 0; k < env.length; k++) if (env[k] > low) out[k] = Math.min(1, (env[k] - low) / (ref - low)) ** P.gamma;
  return out;
}
function hapticCurve(traces, n, t0, rate, P, accel, ref) {
  const env = hapticEnvelope(traces, n, rate, P, accel);
  if (!env.length) return env;
  let peak = 0;
  for (const v of env) peak = Math.max(peak, v);
  return hapticLevels(env, hapticNoise(env, t0, rate, P), ref === undefined || ref === null ? peak : ref, P);
}
// Vibration API の並び(ms。振動・止まるを交互に、振動から始める): 強さを VIBRATE_PERIOD ms ごとのオンの時間の割合にする
//   オンが VIBRATE_MIN_ON ms より短いと感じにくいので、強さ 0.05 以上なら VIBRATE_MIN_ON ms にし、止まりが短すぎるときは振動のままにする
const VIBRATE_PERIOD = 40, VIBRATE_MIN_ON = 10;
function hapticPattern(part, dt) {
  const total = Math.round(part.length * dt * 1000), out = [];
  const push = (on, ms) => {
    if (ms <= 0) return;
    if (out.length && (out.length % 2 === 1) === on) out[out.length - 1] += ms;   // 同じ状態はつなぐ
    else { if (!out.length && !on) out.push(0); out.push(ms); }
  };
  for (let t = 0; t < total; t += VIBRATE_PERIOD) {
    const len = Math.min(VIBRATE_PERIOD, total - t);
    const a = Math.min(part.length - 1, Math.floor(t / 1000 / dt)), b = Math.min(part.length, Math.max(a + 1, Math.ceil((t + len) / 1000 / dt)));
    let sum = 0;
    for (let i = a; i < b; i++) sum += part[i];
    const level = sum / (b - a);
    let on = Math.round(level * len);
    if (on < VIBRATE_MIN_ON) on = level >= 0.05 ? Math.min(VIBRATE_MIN_ON, len) : 0;
    if (on > 0 && len - on < VIBRATE_MIN_ON) on = len;
    push(true, on); push(false, len - on);
  }
  return out;
}
// 速度の波形 [nm/s](欠測は 0)から、画面を揺らす変位 [nm] を作る: 0.5 Hz より遅い揺れを除いて積分し、もう一度除いて、5 Hz より速い揺れを除く
function hapticBiquad(type, f0, fs) {
  const w = 2 * Math.PI * f0 / fs, cs = Math.cos(w), alpha = Math.sin(w) / (2 * Math.SQRT1_2), a0 = 1 + alpha;
  const b = type === "low" ? [(1 - cs) / 2, 1 - cs, (1 - cs) / 2] : [(1 + cs) / 2, -(1 + cs), (1 + cs) / 2];
  return [b[0] / a0, b[1] / a0, b[2] / a0, -2 * cs / a0, (1 - alpha) / a0];
}
function hapticFiltfilt(x, c) {
  const run = () => {
    let z1 = 0, z2 = 0;
    for (let i = 0; i < x.length; i++) {
      const y = c[0] * x[i] + z1;
      z1 = c[1] * x[i] - c[3] * y + z2;
      z2 = c[2] * x[i] - c[4] * y;
      x[i] = y;
    }
  };
  run(); x.reverse(); run(); x.reverse();
}
function hapticDisplacement(v, rate) {
  const x = Float64Array.from(v), hp = hapticBiquad("high", 0.5, rate);
  hapticFiltfilt(x, hp);
  let sum = 0;
  for (let i = 0; i < x.length; i++) { sum += x[i] / rate; x[i] = sum; }
  hapticFiltfilt(x, hp);
  hapticFiltfilt(x, hapticBiquad("low", Math.min(5, rate / 4), rate));
  return x;
}
// HAPTIC_CURVE_END
// --- 振動の再生(ページ共通) ---
//   テスト用の iPhone のアプリ(ios/UsuMantenFeel)の中では webkit.messageHandlers.usuHaptics に送って Core Haptics で振動させる。
//   アプリの外では、shake の要素を揺らして代わりにする(視差効果を減らす設定のときは揺らさない)。sound なら低い音も同じ強さで鳴らす
const HAPTIC_PLAYER = { cur: null, actx: null };
const VIBRATE_CHUNK = 80;     // 1 回の navigator.vibrate に渡す並びの数(偶数。振動から始まるように)
// この端末で振動できるか(アプリの中か、タッチの画面で Vibration API があるとき)
function hapticCanVibrate() {
  return hapticNative() || (typeof navigator.vibrate === "function" && matchMedia("(pointer: coarse)").matches);
}
function hapticBridge() {
  return window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.usuHaptics;
}
function hapticNative() { return !!hapticBridge(); }
// アプリの外(Safari など)では振動できないので、振動の操作の要素を隠す
function hapticHideOutsideApp(...els) {
  if (hapticNative()) return;
  for (const el of els) if (el) el.style.display = "none";
}
// levels ... 強さ 0〜1 の並び、dt ... 1 コマの秒
//   opts ... {sharpness, sound, shake: 揺らす要素, meter: 強さの棒(幅を変える要素), onTick(何コマ目か), onStop(),
//             vibrate: アプリの外で Vibration API を使うか, motion: コマごとの -1〜1(あれば画面をその値で上下に動かす), shakeMax: 揺らす最大 [px],
//             marker: 視差効果を減らす設定のとき、画面の代わりに上下に動かす点, markerMax: 点を動かす最大 [px]}
function hapticPlay(levels, dt, opts) {
  hapticStop();
  const part = Array.from(levels, v => Math.round(v * 100) / 100);
  if (!part.length || !(dt > 0)) { if (opts.onStop) opts.onStop(); return; }
  const bridge = hapticBridge();
  if (bridge) bridge.postMessage({ cmd: "play", interval: dt, intensity: part, sharpness: opts.sharpness });
  const cur = { part, dt, opts, start: performance.now(), native: !!bridge, flip: 1, audio: opts.sound ? hapticSound(part, dt) : null, timers: [] };
  if (!bridge && opts.vibrate && typeof navigator.vibrate === "function") {
    const pattern = hapticPattern(part, dt);
    let at = 0;
    for (let i = 0; i < pattern.length; i += VIBRATE_CHUNK) {
      const chunk = pattern.slice(i, i + VIBRATE_CHUNK);
      if (at === 0) navigator.vibrate(chunk);      // 最初は押したその場で(画面を押したあとでないと振動できない)
      else cur.timers.push(setTimeout(() => navigator.vibrate(chunk), at));
      for (const ms of chunk) at += ms;
    }
    cur.vibrating = true;
  }
  HAPTIC_PLAYER.cur = cur;
  requestAnimationFrame(now => hapticTick(cur, now));
}
function hapticStop() {
  const cur = HAPTIC_PLAYER.cur;
  if (!cur) return;
  HAPTIC_PLAYER.cur = null;
  const bridge = hapticBridge();
  if (bridge) bridge.postMessage({ cmd: "stop" });
  if (cur.audio) { try { cur.audio.stop(); } catch (_) {} }
  for (const id of cur.timers) clearTimeout(id);
  if (cur.vibrating) navigator.vibrate(0);
  if (cur.opts.shake) cur.opts.shake.style.transform = "";
  if (cur.opts.marker) cur.opts.marker.style.transform = "";
  if (cur.opts.meter) cur.opts.meter.style.width = "0";
  if (cur.opts.onStop) cur.opts.onStop();
}
function hapticPlaying() { return !!HAPTIC_PLAYER.cur; }
function hapticTick(cur, now) {
  if (HAPTIC_PLAYER.cur !== cur) return;
  const k = (now - cur.start) / 1000 / cur.dt;
  if (k >= cur.part.length) { hapticStop(); return; }
  const level = cur.part[Math.floor(k)];
  if (cur.opts.meter) cur.opts.meter.style.width = (100 * level) + "%";
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (cur.opts.motion) {     // 波形の変位で上下に動かす(コマの間はつなぐ)。視差効果を減らす設定なら、画面ではなく点を動かす
    const m = cur.opts.motion, i = Math.min(m.length - 1, Math.floor(k)), j = Math.min(m.length - 1, i + 1), f = k - Math.floor(k);
    const y = m[i] * (1 - f) + m[j] * f;
    if (cur.opts.shake && !still) cur.opts.shake.style.transform = `translateY(${-y * (cur.opts.shakeMax || 12)}px)`;
    if (cur.opts.marker && still) cur.opts.marker.style.transform = `translateY(${-y * (cur.opts.markerMax || 12)}px)`;
  } else if (cur.opts.shake && !cur.native && !still) {
    cur.flip = -cur.flip;
    const a = 10 * level;
    cur.opts.shake.style.transform = `translate(${cur.flip * a * (0.5 + Math.random() / 2)}px, ${(Math.random() - 0.5) * a}px)`;
  }
  if (cur.opts.onTick) cur.opts.onTick(k);
  requestAnimationFrame(t => hapticTick(cur, t));
}
// 低い音(茶色の雑音を 250 Hz で切ったもの)の大きさを、振動の強さに合わせる
function hapticSound(part, dt) {
  if (part.length < 2) return null;
  if (!HAPTIC_PLAYER.actx) HAPTIC_PLAYER.actx = new (window.AudioContext || window.webkitAudioContext)();
  const actx = HAPTIC_PLAYER.actx, len = actx.sampleRate * 2, buf = actx.createBuffer(1, len, actx.sampleRate), x = buf.getChannelData(0);
  if (actx.state === "suspended") actx.resume();
  let last = 0;
  for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; x[i] = last * 3.5; }
  const src = actx.createBufferSource(), lp = actx.createBiquadFilter(), gain = actx.createGain();
  src.buffer = buf; src.loop = true;
  lp.type = "lowpass"; lp.frequency.value = 250;
  const t = actx.currentTime + 0.02;
  gain.gain.setValueAtTime(0, actx.currentTime);
  gain.gain.setValueCurveAtTime(Float32Array.from(part), t, part.length * dt);
  src.connect(lp).connect(gain).connect(actx.destination);
  src.start(t); src.stop(t + part.length * dt);
  return src;
}
"""
