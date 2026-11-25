# haptic_page.py
# 地震の揺れを、スマートフォンの振動で感じるための部品(TODO 58)。教材のページ(HTML)に入れて使う
#   揺れの強さの曲線: 波形から、コマ(既定 20 ms)ごとの揺れの大きさ(包絡線)を求め、雑音の floor 倍を 0、基準の大きさを 1 として、
#     割合の gamma 乗にする。Python の haptic_* とページの JavaScript(HAPTIC_JS の hapticEnvelope など)は同じ計算(テストで比べる)
#   再生: テスト用の iPhone のアプリ(ios/UsuMantenFeel、WKWebView)の中で開いたときは、webkit.messageHandlers.usuHaptics に
#     {cmd: "play", interval: 秒, intensity: [0〜1, ...], sharpness: 0〜1} を送り、アプリが Core Haptics で振動させる(止めるときは {cmd: "stop"})。
#     Safari などでは、画面の要素を揺らし(と、選べば低い音)で代わりにする
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
// HAPTIC_CURVE_END
// --- 振動の再生(ページ共通) ---
//   テスト用の iPhone のアプリ(ios/UsuMantenFeel)の中では webkit.messageHandlers.usuHaptics に送って Core Haptics で振動させる。
//   アプリの外では、shake の要素を揺らして代わりにする(視差効果を減らす設定のときは揺らさない)。sound なら低い音も同じ強さで鳴らす
const HAPTIC_PLAYER = { cur: null, actx: null };
function hapticBridge() {
  return window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.usuHaptics;
}
function hapticNative() { return !!hapticBridge(); }
// levels ... 強さ 0〜1 の並び、dt ... 1 コマの秒
//   opts ... {sharpness, sound, shake: 揺らす要素, meter: 強さの棒(幅を変える要素), onTick(何コマ目か), onStop()}
function hapticPlay(levels, dt, opts) {
  hapticStop();
  const part = Array.from(levels, v => Math.round(v * 100) / 100);
  if (!part.length || !(dt > 0)) { if (opts.onStop) opts.onStop(); return; }
  const bridge = hapticBridge();
  if (bridge) bridge.postMessage({ cmd: "play", interval: dt, intensity: part, sharpness: opts.sharpness });
  const cur = { part, dt, opts, start: performance.now(), native: !!bridge, flip: 1, audio: opts.sound ? hapticSound(part, dt) : null };
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
  if (cur.opts.shake) cur.opts.shake.style.transform = "";
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
  if (cur.opts.shake && !cur.native && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
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
