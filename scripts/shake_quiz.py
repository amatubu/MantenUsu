# shake_quiz.py
# 「この揺れは何?」クイズの HTML(ファイル1つ)を書き出す(TODO 41・44)
#   満点の4拠点の上下動を見せたり聴かせたりして、揺れの種類(有珠山の地震・発破・花火・列車・少し遠い地震・遠い地震・海外の地震)を当ててもらう
#   問題の一覧と問題ごとの設定は etc/shake_quiz.csv(1行が1問。問題の番号もこのファイルで決める)
#     問題, 種類, 基準の時刻, 基準(0 秒にする時刻の名前), 表示の始まり[秒], 表示の終わり[秒](基準からの秒), 内容(答えのあとに出す説明),
#     この揺れの印(書いてあれば、答える前から「この揺れ」の印を基準の時刻に出す。同じ画面に列車などのほかの揺れが入っている問題),
#     注意(答えのあとに出す補足), 2つめの地震(同じ画面に入っているもう1つの地震の気象庁の発震時刻。P2・S2 の線を引く),
#     答えの音の拠点(空なら自動で選ぶ), 答えのフィルタ(答えたあとに切り替えるフィルタ。空なら種類ごとの見やすいフィルタ。L は海外の地震だけ)
#   画面でできること
#   - 答え方: 波形だけ・音だけ・両方(一覧の画面で選ぶ。正解の数は答え方ごとに、その端末のブラウザに覚える)
#   - 波形: ピンチで拡大・縮小、1本指で移動、ダブルタップで全体に戻す。最初はフィルタなしで、フィルタ A〜D(帯域は答えたあとに明かす)と
#     「ヒント」(その揺れが見やすいフィルタにする)がある
#   - 音: 波形を速く再生した音(答える前は全部 ×40、答えたあとは種類ごとの倍率と ×40)。再生中は波形の上を緑の線が動く。
#     音は「♪」の拠点の記録で、波形をタップすると拠点を変えられる(答える前はどの問題も Kawaminami から)
#   - 海外の地震は、答えたあとだけ L(0.1〜0.5 Hz、もっと低い音)のフィルタも使える(近い揺れは海の波のゆっくりした揺れに埋もれるので、答える前には出さない)
#   - 答えると、正解・説明・A〜D の帯域(とても低い音〜とても高い音)、基準の時刻の線、P波・S波の到着の見込みの線(気象庁の震源リストと
#     USGS の地震)を出し、その揺れが見やすいフィルタに切り替える
#   波形の読み込み・符号化・描画は quake_wave_view.py と同じ。入れるのは満点の拠点の波形だけ(再配布できない外部の観測点の波形は入れない)
#
#     python3 scripts/shake_quiz.py [-o 出力の HTML]
#   省略すると batch_plots/quiz/quiz.html に書き出し、同じフォルダに答えの一覧(quiz_answers.csv)も書く
import argparse
import csv
import html
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import app_paths
import station_names
from quake_wave_view import count_steps, filter_sections, load_stream, script_json, trace_samples

QUIZ_FILE = "shake_quiz.csv"
OUTPUT_DIR = app_paths.BATCH_PLOTS_DIR / "quiz"
STATIONS = ["Bamba", "Hokudai", "Mimatsu", "Nizaemn"]
TITLE = "この揺れは何?"
LONG_SECONDS = 150    # これより長い表示は 125 Hz に間引く(ファイルを小さくする。D の 40 Hz まで見える)

# フィルタ: (記号, 低, 高, 音のたとえ)。帯域は答えたあとに明かす
FILTERS = [("A", 0.5, 2.0, "とても低い音"), ("B", 2.0, 10.0, "低い音"), ("C", 8.0, 25.0, "高い音"), ("D", 20.0, 40.0, "とても高い音")]
# 答えのあとだけに出すフィルタ(どの問題でも。海外の地震の問題だけに出すと、出たことで答えがわかってしまう)。とても長い周期の揺れ。近い揺れは、海の波が起こすゆっくりした揺れ(脈動)に埋もれて見えなくなる
LOW_FILTER = ("L", 0.1, 0.5, "もっと低い音")
ALL_FILTERS = FILTERS + [LOW_FILTER]

# 種類: (選択肢の名前, いちばん見やすいフィルタ, 説明)。{b} は Bamba の表示の名前
KINDS = {
  "有珠山の地震": ("有珠山の地震", "B", "有珠山の下、深さ 2〜4 km で起きた小さな地震。P波と S波の間が 1〜2 秒と短く、揺れは数秒で終わる。"
             "B(低い音)でよく見える。"),
  "発破": ("発破(爆破)", "A", "採石場の発破(岩を切り出すための爆破)。始まりは地震に似ているが、A(とても低い音)にすると、10〜20 秒遅れてゆっくりした揺れが届く。"
         "地表の近くで起きたので、地表を伝わる波(表面波)が強く出る。有珠山の地震ではこの揺れは出ない。"),
  "花火": ("花火", "D", "洞爺湖の花火大会の最後の 1 発の音。音は空気を伝わる(秒速約 340 m)ので、湖に近い拠点から順に、数秒ずつ遅れて届く。"
         "D(とても高い音)でくっきり見え、A ではまったく見えない。"),
  "鉄道": ("列車", "C", "{b}(JR の線路のそば)の横を通る列車。{b} だけが大きく揺れ、ほかの拠点はほとんど揺れない。C(高い音)でよく見える。"
         "貨物列車は車両が多く、揺れが長く続く。"),
  "少し遠い地震": ("少し遠い地震(〜200 km)", "B", "北海道や青森県の近くで起きた地震(このクイズでは、震央までの距離が 200 km 未満のもの)。"
             "P波から数秒〜20 秒ほどで S波が届き(遠いほど間があく)、揺れは 1 分ほど続く。"
             "B(低い音)でよく見え、大きな地震は A でも見える。"),
  "より遠い地震": ("遠い地震(250 km〜)", "B", "東北地方の沖など、日本の中で遠くに起きた地震(このクイズでは、震央までの距離が 250 km 以上のもの)。"
             "P波と S波の間が 30 秒以上あき、揺れは数分続く。"
             "B(低い音)にすると P波と S波の到着がはっきりする。A(とても低い音)でも大きく見えるが、P波・S波の始まりはぼやける。"
             "C・D(高い音)では小さくなる(遠くから来る揺れは、高い音の成分が途中で弱まる)。"),
  "さらに遠い地震(海外)": ("海外の地震", "A", "数千 km 離れた外国の大きな地震。地球の中を通って P波が届く。A(とても低い音)にすると 4 拠点で同じような揺れが見え、"
                    "近くの車などの揺れ(高い音)は消える。答えのあとに使える L(もっと低い音)にすると、最初の波の始まりがさらにはっきりし、"
                    "4 拠点の揺れの形がよく似てくる。"),
}
CHOICES = list(KINDS)
# 選択肢の区切りの説明(一覧の画面に出す)
CHOICE_NOTE = ("「少し遠い地震」は震央までの距離が 200 km 未満、「遠い地震」は 250 km 以上の日本の中の地震、"
               "「海外の地震」は外国(数千 km 先)の地震です。")
JMA_KINDS = ("少し遠い地震", "より遠い地震")
WORLD_KIND = "さらに遠い地震(海外)"
LOW_KINDS = (WORLD_KIND,)      # 答えのフィルタに L を選べ、答えのあとに「L で聴く」を出す種類

# --- 音で聴く(TODO 44) ---
#   答える前は全部 COMMON_SPEED 倍、答えたあとは種類ごとの倍率でも聴ける(実際に聴いて決めた値、2026-11-19)
COMMON_SPEED = 40
SPEEDS = {"有珠山の地震": 20, "発破": 20, "花火": 5, "鉄道": 10, "少し遠い地震": 40, "より遠い地震": 40, "さらに遠い地震(海外)": 40}
# 音にする前の処理: 0.2 Hz の高域通過、電気の雑音(北海道の交流 50 Hz と 100 Hz)のノッチ。遠くの地震はさらに低域通過
#   (記録には 23・40・55 Hz などの細い雑音の線もあり、速めると「ピー」という音になる。花火は 40 Hz が帯域内なので低域通過しない)
FAR_KINDS = ("少し遠い地震", "より遠い地震", "さらに遠い地震(海外)")
FAR_LOWPASS = 15.0
# 答えの音の拠点を自動で選ぶときの区間(基準からの秒): (雑音の区間, 揺れの区間)
SNR_WINDOWS = {
  "有珠山の地震": ((-10, -1), (0, 15)),
  "発破": ((-10, -1), (0, 30)),
  "少し遠い地震": ((-10, 5), (10, 110)),
  "より遠い地震": ((-10, 25), (30, 240)),
  "さらに遠い地震(海外)": ((-60, -5), (-2, 600)),
}
# 答える前の音の拠点(どの問題も同じ。拠点の選び方が答えのヒントにならないように)。記録が無ければ次の拠点
FIRST_SOUND = ["Hokudai", "Mimatsu", "Nizaemn", "Bamba"]


# --- 問題の一覧 ---

# etc/shake_quiz.csv を読む。戻り値 ... 問題の番号の順の [dict]
def read_quiz(path=None):
  path = Path(path) if path else app_paths.ETC_DIR / QUIZ_FILE
  with open(path, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
  out = []
  for r in rows:
    if r["種類"] not in KINDS:
      raise ValueError(f"問題 {r['問題']}: 種類「{r['種類']}」は使えません({', '.join(KINDS)} のどれか)")
    answer_filter = (r.get("答えのフィルタ") or "").strip()
    if answer_filter and answer_filter not in [k for k, *_ in FILTERS] + ([LOW_FILTER[0]] if r["種類"] in LOW_KINDS else []):
      raise ValueError(f"問題 {r['問題']}: 答えのフィルタ「{answer_filter}」は使えません")
    station = (r.get("答えの音の拠点") or "").strip()
    if station and station not in STATIONS:
      raise ValueError(f"問題 {r['問題']}: 答えの音の拠点「{station}」は満点の拠点ではありません")
    out.append({"no": int(r["問題"]), "kind": r["種類"], "t": datetime.strptime(r["基準の時刻"], "%Y-%m-%d %H:%M:%S"),
                "ref": r["基準"], "show": (float(r["表示の始まり[秒]"]), float(r["表示の終わり[秒]"])), "label": r["内容"],
                "focus": (r.get("この揺れの印") or "").strip(), "note": (r.get("注意") or "").strip(),
                "second": (r.get("2つめの地震") or "").strip(), "audio_station": station, "answer_filter": answer_filter})
  numbers = [q["no"] for q in out]
  if len(set(numbers)) != len(numbers):
    raise ValueError("問題の番号が重なっています")
  return sorted(out, key=lambda q: q["no"])

# 画面に出す文の中の拠点の内部名を、読み替えの表の正式名にする(例: Hokudai → Kawaminami)
def display_names(text):
  for nm in STATIONS:
    text = re.sub(rf"\b{nm}\b", station_names.full(nm), text)
  return text


# --- P波・S波の到着の見込み(拠点ごと。基準の時刻からの秒) ---

# 気象庁の震源リストから、発震時刻 t(2 秒以内)の地震を探す
def jma_quake(t):
  import jma_hypocenter
  best = None
  day = t.replace(hour=0, minute=0, second=0, microsecond=0)
  for d in (day - timedelta(days=1), day):
    for q in jma_hypocenter.load_day(d):
      dt = abs((q["日時"] - t).total_seconds())
      if dt <= 2 and (best is None or dt < best[0]):
        best = (dt, q)
  return best[1] if best else None

# 気象庁の地震 q の到着の見込み(iasp91 の走時。アプリの到達見込みと同じ jma_match.travel_times)
def jma_phases(q, ref, stations):
  from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
  import jma_match
  out = {}
  for nm, (lat, lon, _alt) in stations.items():
    dist = gps2dist_azimuth(lat, lon, q["緯度"], q["経度"])[0] / 1000
    p, s = jma_match.travel_times(round(kilometers2degrees(dist), 2), round(q["深さ[km]"]))
    base = (q["日時"] - ref).total_seconds()
    out[nm] = {k: round(base + v, 2) for k, v in (("p", p), ("s", s)) if v is not None}
  return out

# 海外の地震(基準の時刻 = P波の到着の見込み): USGS の一覧(world_quakes)から、最初の波が 10 秒以内に届く地震を探す
def world_phases(ref, stations):
  import jma_match
  import world_quakes
  center = jma_match.array_center(stations)
  best = None
  candidates = world_quakes.quakes_between(ref - timedelta(minutes=30), ref)
  if not candidates:     # 保存してある一覧(M7 以上)に無ければ、USGS から M6 以上の月の一覧を取る(地球の反対側の M6.4 など)
    try:
      month = ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
      candidates = [q for q in world_quakes.parse_usgs(world_quakes.fetch_month(month, 6.0))
                    if ref - timedelta(minutes=30) <= q["日時"] <= ref]
    except Exception:
      candidates = []
  for q in candidates:
    a = world_quakes.arrivals(q, center)
    dt = abs((a["waves"][0][1] - ref).total_seconds())
    if best is None or dt < best[0]:
      best = (dt, q)
  if best is None or best[0] > 10:
    return {}
  out = {}
  for nm, (lat, lon, _alt) in stations.items():
    a = world_quakes.arrivals(best[1], (lat, lon))
    waves = [(name, t) for name, t in a["waves"] if name != "表面波"]
    for key, (_name, t) in zip(("p", "s"), waves):
      out.setdefault(nm, {})[key] = round((t - ref).total_seconds(), 2)
  return out

def phase_lines(q, stations):
  if q["kind"] in JMA_KINDS:
    jq = jma_quake(q["t"])
    return jma_phases(jq, q["t"], stations) if jq else {}
  if q["kind"] == WORLD_KIND:
    return world_phases(q["t"], stations)
  return {}

def second_phases(q, stations):
  if not q["second"]:
    return {}
  jq = jma_quake(datetime.strptime(q["second"], "%Y-%m-%d %H:%M:%S.%f" if "." in q["second"] else "%Y-%m-%d %H:%M:%S"))
  return jma_phases(jq, q["t"], stations) if jq else {}


# --- 音 ---

# 音にする前の処理の係数: [[b0, b1, b2, a1, a2], ...](ブラウザで往復にかける)
def audio_sections(rate, far):
  from scipy import signal
  sos = [signal.butter(2, 0.2, "highpass", fs=rate, output="sos")]
  for f0 in (50.0, 100.0):
    if f0 < rate / 2 * 0.98:
      b, a = signal.iirnotch(f0, 30.0, rate)
      sos.append(signal.tf2sos(b, a))
  if far:
    sos.append(signal.butter(4, FAR_LOWPASS, "lowpass", fs=rate, output="sos"))
  rows = np.concatenate(sos)
  rows = rows / rows[:, 3:4]
  return [[float(r[0]), float(r[1]), float(r[2]), float(r[4]), float(r[5])] for r in rows]

# 答えの音の拠点: 音にするのと同じ処理をした波形の S/N(揺れの区間の RMS ÷ 雑音の区間の RMS)がいちばん大きい拠点
#   列車は Bamba、花火は Hokudai(0 秒の拠点。ほかの拠点は数秒〜十数秒遅れて届く)。etc の「答えの音の拠点」があればそれ
#   「この揺れ」の印の問題では Bamba を選ばない(Bamba に入っている列車の揺れで S/N が大きく見えるため)
def audio_station(q, st):
  from scipy import signal
  if q["audio_station"]:
    return q["audio_station"]
  if q["kind"] == "鉄道":
    return "Bamba"
  if q["kind"] == "花火":
    return "Hokudai"
  noise, sig = SNR_WINDOWS[q["kind"]]
  best = None
  for tr in st:
    if tr.stats.station not in STATIONS or not tr.stats.channel.endswith("U"):
      continue
    if q["focus"] and tr.stats.station == "Bamba":
      continue
    fs = tr.stats.sampling_rate
    x = np.ma.filled(np.ma.asarray(tr.data, dtype=float), np.nan)
    if not np.isfinite(x).any():
      continue
    sec = np.arange(len(x)) / fs + (tr.stats.starttime.datetime - q["t"]).total_seconds()
    x = np.nan_to_num(x - np.nanmean(x))
    sos = np.array([[b0, b1, b2, 1.0, a1, a2] for b0, b1, b2, a1, a2 in audio_sections(fs, q["kind"] in FAR_KINDS)])
    y = signal.sosfiltfilt(sos, x)
    n, s = y[(sec >= noise[0]) & (sec <= noise[1])], y[(sec >= sig[0]) & (sec <= sig[1])]
    if len(n) < fs or len(s) < fs:
      continue
    snr = float(np.sqrt(np.mean(s ** 2)) / max(np.sqrt(np.mean(n ** 2)), 1e-12))
    if best is None or snr > best[0]:
      best = (snr, tr.stats.station)
  return best[1] if best else FIRST_SOUND[0]


# --- 1問ぶんのデータと HTML ---

# 戻り値 ... (一覧に出す情報, {内部の拠点名: {"U": 波形}})
def event_data(q, st, steps, stations):
  before, after = q["show"]
  rate = 125 if after - before > LONG_SECONDS else 250
  start = q["t"] + timedelta(seconds=before)
  n = int(round((after - before) * rate))
  waves = {}
  for nm in STATIONS:
    item = trace_samples(st, nm, "U", start, n, steps.get((nm, "U"), 1.0), rate)
    if item:
      waves[nm] = {"U": item}
  info = {"no": q["no"], "kind": CHOICES.index(q["kind"]), "rate": rate, "n": n, "t0": before,
          "time": f"{q['t']:%Y-%m-%d %H:%M:%S}", "ref": display_names(q["ref"]), "label": display_names(q["label"]),
          "best": q.get("answer_filter") or KINDS[q["kind"]][1], "low": q["kind"] in LOW_KINDS, "phases": phase_lines(q, stations), "phases2": second_phases(q, stations),
          "focus": bool(q["focus"]), "note": display_names(q["note"]), "disabled": [], "have": list(waves),
          "audio": {"station": audio_station(q, st), "speed": SPEEDS[q["kind"]], "far": q["kind"] in FAR_KINDS}}
  return info, waves

def write_page(path, events):
  path = Path(path)
  b = station_names.full("Bamba")
  rates = sorted({info["rate"] for info, _ in events})
  meta = {
    "title": TITLE,
    "stations": [{"id": nm, "label": station_names.full(nm)} for nm in STATIONS],
    "choiceNote": CHOICE_NOTE,
    "choices": [{"name": KINDS[k][0], "best": KINDS[k][1], "text": KINDS[k][2].format(b=b)} for k in CHOICES],
    "filters": [{"key": key, "low": lo, "high": hi, "sound": sound, "extra": key == LOW_FILTER[0],
                 "sections": {str(rate): filter_sections((lo, hi), rate) for rate in rates}} for key, lo, hi, sound in ALL_FILTERS],
    "events": [info for info, _ in events],
    "audio": {"common": COMMON_SPEED, "first": FIRST_SOUND,
              "sections": {str(rate): {"base": audio_sections(rate, False), "far": audio_sections(rate, True)} for rate in rates}},
  }
  blocks = "\n".join(f'<script type="application/json" id="w{info["no"]}">{script_json(waves)}</script>' for info, waves in events)
  page = (PAGE.replace("__FIRST__", html.escape(station_names.full(FIRST_SOUND[0])))
          .replace("__TITLE__", html.escape(TITLE))
          .replace("__COMMON_JS__", COMMON_JS)
          .replace("__META__", script_json(meta))
          .replace("__WAVES__", blocks))
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(page, encoding="utf-8")
  return path.stat().st_size

# 答えの一覧(先生用。紙に印刷する問題と答えにも使う)
def write_answers(path, events):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["問題", "答え", "基準の時刻", "内容", "見やすいフィルタ", "答えの音の拠点", "答えの音の速さ"])
    for info, _ in events:
      w.writerow([info["no"], KINDS[CHOICES[info["kind"]]][0], info["time"], info["label"], info["best"],
                  station_names.full(info["audio"]["station"]), f"×{info['audio']['speed']}"])


# クイズと、揺れを並べて比べるページ(shake_compare.py)で同じ JS(波形を戻す・フィルタ・1本の波形を描く・音にする)
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
  --accent: #1565c0; --ok: #1b7f3b; --ng: #c62828; --ref: #7b4fb5; --p: #1565c0; --s: #d84e00; --cursor: #1b7f3b; --gap: #e4e7eb; --wave: #1c2126;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1f2428; --ink: #e8ecef; --muted: #9aa5af; --line: #333a41; --grid: #24292e;
    --accent: #6cb0f5; --ok: #5cc27e; --ng: #ff7b72; --ref: #c39cf0; --p: #6cb0f5; --s: #ff9a57; --cursor: #5cc27e; --gap: #2b3137; --wave: #e8ecef;
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
#score { font-size: 15px; margin: 6px 0 0; font-variant-numeric: tabular-nums; }
#qs { display: grid; grid-template-columns: repeat(auto-fill, minmax(64px, 1fr)); gap: 8px; list-style: none; margin: 14px 0; padding: 0; }
#qs button { width: 100%; height: 56px; border-radius: 10px; background: var(--panel); font-size: 15px; font-variant-numeric: tabular-nums; }
#qs button.ok { box-shadow: inset 0 0 0 2px var(--ok); }
#qs button.ng { box-shadow: inset 0 0 0 2px var(--ng); }
#qs small { display: block; font-size: 11px; color: var(--muted); }
.row { display: flex; gap: 8px; flex-wrap: wrap; }
.plain { color: var(--accent); font-size: 14px; padding: 6px 0; }
#view { position: fixed; inset: 0; display: flex; flex-direction: column; height: 100vh; height: 100dvh;
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
header { display: flex; align-items: center; gap: 2px; min-height: 48px; padding: 0 4px; border-bottom: 1px solid var(--line); }
header button { min-width: 44px; height: 44px; color: var(--accent); font-size: 17px; }
header button:disabled { color: var(--line); cursor: default; }
#back { padding: 0 6px; white-space: nowrap; }
#heading { flex: 1; min-width: 0; text-align: center; font-weight: 600; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#tools { display: flex; gap: 6px; padding: 6px 8px; align-items: center; }
.seg { flex: 1 1 auto; display: flex; padding: 2px; border-radius: 8px; background: var(--panel); }
.seg button { flex: 1 1 0; height: 36px; padding: 0 4px; border-radius: 6px; font-size: 13px; color: var(--muted); white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--bg); color: var(--ink); font-weight: 600; box-shadow: 0 0 0 1px var(--line); }
#amp { flex: 0 0 auto; }
#hint { flex: 0 0 auto; height: 40px; padding: 0 10px; border-radius: 8px; color: var(--accent); box-shadow: inset 0 0 0 1px var(--line); font-size: 13px; white-space: nowrap; }
#hint:disabled { color: var(--muted); }
#stage { position: relative; flex: 1; min-height: 160px; }
#wave { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none;
  -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
#status { position: absolute; left: 16px; right: 16px; top: 40%; text-align: center; color: var(--muted); font-size: 14px; pointer-events: none; }
#bottom { border-top: 1px solid var(--line); padding: 8px; max-height: 46vh; overflow-y: auto; }
#ask { font-size: 13px; color: var(--muted); margin: 0 0 6px; }
#choices { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
@media (max-width: 420px) { #choices { grid-template-columns: repeat(2, 1fr); } }
.listenRow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0 0 8px; }
.listen { height: 40px; padding: 0 14px; border-radius: 20px; color: var(--bg); background: var(--accent); font-size: 14px; font-weight: 600; white-space: nowrap; }
.listen.alt { color: var(--accent); background: none; box-shadow: inset 0 0 0 1.5px var(--accent); }
#speedSeg { flex: 0 0 auto; }
#speedSeg button { flex: 0 0 auto; padding: 0 10px; }
.modeBox { display: flex; align-items: center; gap: 10px; margin: 12px 0 0; }
.modeBox > span { font-size: 14px; white-space: nowrap; }
#hiddenWave { position: absolute; left: 16px; right: 16px; top: 45%; text-align: center; color: var(--muted); font-size: 14px; pointer-events: none; }
#choices button:disabled { opacity: 0.35; cursor: default; }
#choices button { min-height: 44px; padding: 4px; border-radius: 8px; background: var(--panel); font-size: 14px; line-height: 1.2; }
#result h2 { margin: 0 0 4px; font-size: 18px; }
#result h2.ok { color: var(--ok); } #result h2.ng { color: var(--ng); }
#result p { margin: 4px 0; font-size: 14px; }
#result table { border-collapse: collapse; font-size: 13px; margin: 6px 0; width: 100%; }
#result td, #result th { border-bottom: 1px solid var(--line); padding: 3px 6px; text-align: left; font-weight: normal; }
#result tr.best td { font-weight: 600; }
#nextQ { margin-top: 6px; width: 100%; height: 44px; border-radius: 8px; background: var(--accent); color: var(--bg); font-weight: 600; }
.warn { margin: 16px 0; padding: 12px; border-radius: 8px; background: var(--panel); font-size: 14px; }
</style>
</head>
<body>
<main id="list">
  <h1>__TITLE__</h1>
  <p class="note">有珠山のまわりの満点地震計 4 か所の記録です。地震・発破・花火・列車など、いろいろな揺れが入っています。波形を見て、何の揺れかを当ててください。</p>
  <noscript><p class="warn">プレビューの画面では動きません。Safari などのブラウザで開いてください。</p></noscript>
  <p class="warn" id="unsupported" hidden>このブラウザは圧縮の展開(DecompressionStream)に対応していません。iOS 16.4 以降の Safari で開いてください。</p>
  <p class="note" id="choiceNote"></p>
  <div class="modeBox"><span>答え方</span><div class="seg" id="modeSeg" role="group" aria-label="答え方">
    <button type="button" data-mode="wave">波形だけ</button><button type="button" data-mode="sound">音だけ</button><button type="button" data-mode="both">両方</button>
  </div></div>
  <p class="note" id="modeNote"></p>
  <p id="score"></p>
  <ul id="qs"></ul>
  <div class="row"><button class="plain" id="reset" type="button">この答え方の答えをすべて消す</button></div>
  <p class="note">波形はピンチで拡大・縮小、1本指で左右に移動、ダブルタップで全体に戻ります。最初はフィルタなしです。A〜D のフィルタを切り替えると、見えてくる揺れがあるかもしれません。「ヒント」を押すと、その揺れが見やすいフィルタになります。</p>
  <p class="note">「聴く」を押すと、波形を速く再生した音が鳴り、波形の上を緑の線が動きます。音は「♪」の付いた拠点の記録で、波形をタップするとほかの拠点の音に変えられます。答える前は、どの問題も __FIRST__ の音から始まります(記録が無いときは次の拠点)。答えたあとは、その揺れがいちばんよく聞こえる拠点の音になります。答える前は、どの揺れも 40 倍の速さです。選んでいるフィルタ(元・A〜D)の音になります。iPhone では、消音モード(マナーモード)でも鳴ります。</p>
  <p class="note">答えは、この端末のブラウザにだけ覚えます。</p>
</main>
<section id="view" hidden>
  <header>
    <button id="back" type="button" aria-label="一覧に戻る">‹ 一覧</button>
    <div id="heading"></div>
    <button id="prev" type="button" aria-label="前の問題">◀</button>
    <button id="next" type="button" aria-label="次の問題">▶</button>
  </header>
  <div id="tools">
    <div class="seg" id="filters" role="group" aria-label="フィルタ"></div>
    <div class="seg" id="amp" role="group" aria-label="振幅">
      <button id="ampEach" type="button">拠点別</button><button id="ampCommon" type="button">共通</button>
    </div>
    <button id="hint" type="button">ヒント</button>
  </div>
  <div id="stage"><canvas id="wave" aria-label="波形。ピンチで拡大・縮小、ドラッグで移動、ダブルタップで全体に戻す"></canvas><div id="status"></div><div id="hiddenWave" hidden>音だけで答えるモードです。「聴く」で聴いてください(拠点の段をタップすると、その拠点の音に変わります)</div></div>
  <div id="bottom">
    <div id="askBox">
      <div class="listenRow"><button class="listen" id="listen" type="button"></button><span class="note">音にして聴く(速さは全部同じ)</span></div>
      <p id="ask">何の揺れでしょう?</p><div id="choices"></div>
    </div>
    <div id="result" hidden></div>
  </div>
</section>
<script type="application/json" id="meta">__META__</script>
__WAVES__
<script>
"use strict";
const META = JSON.parse(document.getElementById("meta").textContent);
const MIN_SPAN_SECONDS = 0.1;
const CACHE_SIZE = 4;
const STORE = "manten-quiz-v1";
const $ = id => document.getElementById(id);
const canvas = $("wave"), ctx = canvas.getContext("2d");
const S = { idx: -1, data: null, filter: "", common: false, v0: 0, span: 1, dpr: 1, token: 0, listY: 0 };
const cache = new Map();
let colors = {}, drawQueued = false;
__COMMON_JS__

// --- 答えの記録(この端末だけ) ---
// 答え方: wave(波形だけ)・sound(音だけ)・both(両方)。答えの記録は答え方ごと(両方は以前と同じ場所)
const MODES = { wave: "波形だけ", sound: "音だけ", both: "両方" };
const MODE_NOTES = { wave: "波形だけを見て答えます(答えるまで音は聴けません)。", sound: "音だけを聴いて答えます(答えるまで波形は見えません)。",
                     both: "波形を見て、音も聴いて答えます。" };
function loadMode() { try { const m = localStorage.getItem(STORE + ":mode"); return MODES[m] ? m : "both"; } catch (_) { return "both"; } }
function storeKey() { return S.mode === "both" ? STORE : `${STORE}:${S.mode}`; }
function loadAnswers() { try { return JSON.parse(localStorage.getItem(storeKey())) || {}; } catch (_) { return {}; } }
function saveAnswers(a) { try { localStorage.setItem(storeKey(), JSON.stringify(a)); } catch (_) {} }
S.mode = loadMode();
let answers = loadAnswers();
function setMode(mode) {
  S.mode = mode;
  try { localStorage.setItem(STORE + ":mode", mode); } catch (_) {}
  answers = loadAnswers();
  for (const b of document.querySelectorAll("#modeSeg button")) b.setAttribute("aria-pressed", b.dataset.mode === mode);
  $("modeNote").textContent = MODE_NOTES[mode] + "答えたあとは、どの答え方でも波形と音の両方が使えます。正解の数は答え方ごとに数えます。";
  buildList();
}
for (const b of document.querySelectorAll("#modeSeg button")) b.addEventListener("click", () => setMode(b.dataset.mode));
// 答える前に波形・音を隠すか
function waveHidden() { return S.mode === "sound" && !answered(); }
function soundHidden() { return S.mode === "wave" && !answered(); }

function traceOf(stationId) {
  const tr = S.data[stationId] && S.data[stationId].U;
  if (!tr || !S.filter) return tr;
  if (!tr.f[S.filter]) {
    const f = META.filters.find(f => f.key === S.filter), ev = META.events[S.idx];
    const v = filterSamples(tr.v, f.sections[String(ev.rate)]);
    tr.f[S.filter] = { v, levels: buildLevels(v), step: tr.step / FINE, gaps: tr.gaps };
  }
  return tr.f[S.filter];
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
        data[name][comp] = { v, levels: buildLevels(v), step: item.s, gaps: item.g, f: {} };
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
  for (const k of ["bg", "ink", "muted", "line", "grid", "ref", "p", "s", "gap", "wave", "cursor"]) colors[k] = css.getPropertyValue("--" + k).trim();
}
function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); drawCursor(); });
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
  for (const m of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]) if (m >= seconds) return m;
  return 200;
}
function labelBox(text, x, y, align, color) {
  const d = S.dpr, w = ctx.measureText(text).width, left = align === "right" ? x - w : x;
  ctx.fillStyle = colors.bg; ctx.globalAlpha = 0.78;
  ctx.fillRect(left - 2 * d, y - 1 * d, w + 4 * d, 14 * d);
  ctx.globalAlpha = 1; ctx.fillStyle = color; ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(text, left, y);
}
function answered() { const ev = META.events[S.idx]; return ev && answers[ev.no] !== undefined; }
function draw() {
  const W = canvas.width, H = canvas.height, d = S.dpr;
  if (!W || !H || !colors.bg) return;
  ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, W, H);
  const ev = META.events[S.idx];
  if (!ev || !S.data) return;
  const axisH = Math.round(22 * d), plotH = H - axisH, rate = ev.rate, n = ev.n;
  const blockH = plotH / META.stations.length;
  // 答えるまでは表示の始まりを 0 秒、答えたあとは基準の時刻を 0 秒にする
  const shift = answered() ? ev.t0 : 0;
  const timeOf = i => shift + i / rate, xOfTime = t => ((t - shift) * rate - S.v0) / S.span * W;
  ctx.font = `${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;

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

  const a = Math.max(0, Math.floor(S.v0)), b = Math.min(n, Math.ceil(S.v0 + S.span));
  const out = { lo: 0, hi: 0 }, centers = {}, halfOf = {};
  let allHalf = 0;
  for (const st of META.stations) {
    const tr = traceOf(st.id);
    if (!tr) continue;
    rangeOf(tr, a, b, out);
    const lo = out.lo * tr.step, hi = out.hi * tr.step;
    centers[st.id] = (lo + hi) / 2;
    halfOf[st.id] = (hi - lo) / 2;
    allHalf = Math.max(allHalf, halfOf[st.id]);
  }
  META.stations.forEach((st, si) => {
    const top = si * blockH, tr = traceOf(st.id);
    const half = S.common ? allHalf : halfOf[st.id];
    if (tr && !waveHidden()) drawTrace(tr, top, blockH, centers[st.id], half, n);
    const sound = st.id === S.audioStation && !soundHidden();
    if (sound) ctx.font = `600 ${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
    labelBox((sound ? "♪ " : "") + st.label + (sound ? "(音)" : ""), 6 * d, top + 2 * d, "left", sound ? colors.cursor : colors.ink);
    ctx.font = `${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
    if (tr && !waveHidden()) labelBox("±" + formatAmp(half), W - 6 * d, top + 2 * d, "right", colors.muted);
    else if (!tr) {
      ctx.fillStyle = colors.muted; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText("記録なし", W / 2, top + blockH / 2);
    }
    // 答えたあとは P波・S波の到着の見込み(震源からの計算)
    const lines = [];
    if (answered()) {
      for (const [set, suffix] of [[ev.phases, ""], [ev.phases2, "2"]]) {
        const ph = set ? set[st.id] : null;
        if (ph) for (const key of ["p", "s"]) if (ph[key] !== undefined) lines.push([key, ph[key], suffix]);
      }
    }
    if (lines.length) {
      for (const [key, t, suffix] of lines) {
        const color = key === "p" ? colors.p : colors.s, ph = { [key]: t };
        const x = Math.round(xOfTime(ph[key])) + 0.5;
        if (x < 0 || x > W) continue;
        ctx.strokeStyle = color; ctx.lineWidth = Math.max(1, 1.5 * d); ctx.setLineDash([4 * d, 3 * d]);
        ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + blockH); ctx.stroke(); ctx.setLineDash([]);
        ctx.font = `600 ${11 * d}px -apple-system, BlinkMacSystemFont, sans-serif`;
        const name = key.toUpperCase() + suffix;
        if (key === "p") labelBox(name, x - 3 * d, top + blockH - 15 * d, "right", color);
        else labelBox(name, x + 3 * d, top + blockH - 15 * d, "left", color);
        ctx.font = `${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
      }
    }
    ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
    const yLine = Math.round(top + blockH) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, yLine); ctx.lineTo(W, yLine); ctx.stroke();
  });
  // 答える前でも、ほかの揺れが入っている問題は「この揺れ」の始まりに印を付ける
  if (!answered() && ev.focus) {
    const x = Math.round(xOfTime(-ev.t0)) + 0.5;
    if (x >= 0 && x <= W) {
      ctx.strokeStyle = colors.ref; ctx.lineWidth = Math.max(1, d); ctx.setLineDash([2 * d, 4 * d]);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = colors.ref;
      ctx.beginPath(); ctx.moveTo(x - 6 * d, 0); ctx.lineTo(x + 6 * d, 0); ctx.lineTo(x, 9 * d); ctx.closePath(); ctx.fill();
      ctx.font = `600 ${12 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
      labelBox("この揺れ", Math.min(x + 8 * d, W - 6 * d - ctx.measureText("この揺れ").width), 18 * d, "left", colors.ref);
      ctx.font = `${11 * d}px -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
    }
  }
  // 答えたあとは基準の時刻(0 秒)に線を引く
  if (answered()) {
    const x = Math.round(xOfTime(0)) + 0.5;
    if (x >= 0 && x <= W) {
      ctx.strokeStyle = colors.ref; ctx.lineWidth = Math.max(1, d); ctx.setLineDash([4 * d, 3 * d]);
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke(); ctx.setLineDash([]);
      labelBox(ev.ref, Math.min(x + 3 * d, W - 6 * d - ctx.measureText(ev.ref).width), plotH - 16 * d, "left", colors.ref);
    }
  }
}

// 再生位置の線(draw のあとに重ねる)
function drawCursor() {
  const ev = META.events[S.idx];
  if (!ev || AUD.ev !== S.idx || AUD.el.paused) return;
  const W = canvas.width, d = S.dpr, plotH = canvas.height - Math.round(22 * d);
  const i = AUD.el.currentTime * AUD.speed * ev.rate, x = Math.round((i - S.v0) / S.span * W) + 0.5;
  if (x < 0 || x > W) return;
  ctx.strokeStyle = colors.cursor; ctx.lineWidth = Math.max(2, 2 * d);
  ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke();
}

// --- 音で聴く(TODO 44) ---
//   波形を speed 倍の速さで再生する音を作る(周波数も speed 倍)。WAV にして audio 要素で鳴らす
//   (Web Audio は iPhone の消音モードで鳴らないため)。前処理は 0.2 Hz の高域通過・50/100 Hz のノッチ(遠くの地震は 20 Hz の低域通過も)、
//   フィルタ(A〜D)を選んでいればそれもかける。最大で 0.9 にそろえ、始めと終わりを 30 ms で絞る
const AUD = { el: new Audio(), url: "", ev: -1, speed: 0, raf: 0 };
AUD.el.addEventListener("ended", stopAudio);
function makeSound(ev, speed) {
  const tr = S.data && S.data[S.audioStation] && S.data[S.audioStation].U;
  if (!tr) return null;
  const n = tr.v.length, x = new Float64Array(n);
  let mean = 0;
  for (let i = 0; i < n; i++) mean += tr.v[i];
  mean /= n;
  for (let i = 0; i < n; i++) x[i] = tr.v[i] - mean;
  for (const [a, b] of tr.gaps) x.fill(0, a, b);
  zeroPhase(x, META.audio.sections[String(ev.rate)][ev.audio.far ? "far" : "base"]);
  if (S.filter) zeroPhase(x, META.filters.find(f => f.key === S.filter).sections[String(ev.rate)]);
  const s = resampleForSound(x, ev.rate * speed);
  let peak = 0;
  for (let i = 0; i < s.y.length; i++) if (Math.abs(s.y[i]) > peak) peak = Math.abs(s.y[i]);
  return wavBlob(finishSound(s.y, s.rate, peak > 0 ? 0.9 / peak : 0), s.rate);
}
// key ... 音にするフィルタ(undefined なら今の表示と同じ)。指定したときは、表示もそのフィルタに切り替える
async function playAudio(speed, key) {
  if (key !== undefined && key !== S.filter) setFilter(key);
  const same = AUD.ev === S.idx && AUD.speed === speed && AUD.key === S.filter && !AUD.el.paused;
  stopAudio();
  if (same || !S.data) return;           // 鳴っている同じボタンをもう一度押したら止める
  const ev = META.events[S.idx], blob = makeSound(ev, speed);
  if (!blob) return;
  AUD.url = URL.createObjectURL(blob); AUD.el.src = AUD.url; AUD.ev = S.idx; AUD.speed = speed; AUD.key = S.filter;
  const play = AUD.el.play();
  updateListenButtons();
  try { await play; } catch (err) { stopAudio(); $("status").textContent = "音を鳴らせませんでした: " + err.message; return; }
  const tick = () => { requestDraw(); if (!AUD.el.paused) AUD.raf = requestAnimationFrame(tick); };
  tick();
}
function stopAudio() {
  AUD.el.pause();
  cancelAnimationFrame(AUD.raf);
  if (AUD.url) URL.revokeObjectURL(AUD.url);
  AUD.url = ""; AUD.ev = -1; AUD.speed = 0; AUD.key = null;
  updateListenButtons();
  requestDraw();
}
const LOW_KEY = (META.filters.find(f => f.extra) || {}).key;
function filterName() { return S.filter ? `フィルタ ${S.filter}` : "フィルタなし"; }
function updateListenButtons() {
  const playing = (speed, key) => AUD.ev === S.idx && AUD.speed === speed && AUD.key === key && !AUD.el.paused;
  const ev = META.events[S.idx], common = META.audio.common;
  if (!ev) return;
  $("listen").textContent = playing(common, S.filter) ? "■ 止める" : `▶ 聴く(${filterName()})`;
  // 答えの画面: 速さの切り替えと、フィルタなし・見やすいフィルタの 2 つのボタン
  const best = ev.best, sp = S.ansSpeed || ev.audio.speed;
  const raw = $("listenRaw"), filt = $("listenBest"), low = $("listenLow");
  if (low) low.textContent = playing(sp, LOW_KEY) ? "■ 止める" : `▶ フィルタ ${LOW_KEY}`;
  if (raw) raw.textContent = playing(sp, "") ? "■ 止める" : "▶ フィルタなし";
  if (filt) filt.textContent = playing(sp, best) ? "■ 止める" : `▶ フィルタ ${best}`;
  for (const b of document.querySelectorAll("#speedSeg button")) b.setAttribute("aria-pressed", Number(b.dataset.speed) === sp);
}
$("listen").addEventListener("click", () => playAudio(META.audio.common));

// --- 拡大・移動(quake_wave_view と同じ) ---
function setView(v0, span) {
  const ev = META.events[S.idx];
  if (!ev) return;
  S.span = Math.min(ev.n, Math.max(MIN_SPAN_SECONDS * ev.rate, span));
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
  const p = [...pointers.values()], ev = META.events[S.idx];
  if (gesture && gesture.kind === "pan") setView(gesture.v0 - (me.x - gesture.x) / gesture.w * gesture.span, gesture.span);
  else if (gesture && gesture.kind === "pinch" && p.length >= 2) {
    const dist = Math.max(20, Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y)), mid = (p[0].x + p[1].x) / 2;
    const span = Math.min(ev.n, Math.max(MIN_SPAN_SECONDS * ev.rate, gesture.span * gesture.dist / dist));
    setView(gesture.anchor - mid / gesture.w * span, span);
  }
});
// 答える前の音の拠点: どの問題も同じ順(拠点の選び方が答えのヒントにならないように)
const FIRST_SOUND = META.audio.first;
function firstSoundStation(ev) { return FIRST_SOUND.find(id => ev.have.includes(id)) || ev.audio.station; }
// 1 回タップした拠点を、音にする拠点にする(記録の無い拠点は選ばない)
function selectSoundStation(y) {
  const plotH = canvas.clientHeight - 22, k = Math.floor(y / (plotH / META.stations.length));
  const st = META.stations[k];
  if (!st || !S.data || !S.data[st.id] || st.id === S.audioStation) return;
  stopAudio();
  S.audioStation = st.id;
  requestDraw();
}
function endPointer(e) {
  const me = pointers.get(e.pointerId);
  if (!me) return;
  pointers.delete(e.pointerId);
  if (e.type === "pointerup" && !me.moved && pointers.size === 0 && e.timeStamp - me.t0 < 300) {
    selectSoundStation(e.clientY - canvas.getBoundingClientRect().top);
    if (lastTap && e.timeStamp - lastTap.t < 350 && Math.hypot(me.x - lastTap.x, me.y - lastTap.y) < 30) {
      resetView(); lastTap = null;
    } else lastTap = { t: e.timeStamp, x: me.x, y: me.y };
  }
  startGesture();
}
canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const x = localX(e), w = canvas.clientWidth, ev = META.events[S.idx];
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return setView(S.v0 + e.deltaX / w * S.span, S.span);
  const anchor = S.v0 + x / w * S.span, span = S.span * Math.exp(e.deltaY * 0.002);
  const next = Math.min(ev.n, Math.max(MIN_SPAN_SECONDS * ev.rate, span));
  setView(anchor - x / w * next, next);
}, { passive: false });
canvas.addEventListener("touchmove", e => e.preventDefault(), { passive: false });
for (const type of ["gesturestart", "gesturechange"]) canvas.addEventListener(type, e => e.preventDefault(), { passive: false });

// --- クイズ ---
function buildFilterButtons() {
  const box = $("filters");
  for (const f of [{ key: "", name: "元" }, ...META.filters.map(f => ({ key: f.key, name: f.key }))]) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = f.name; b.dataset.key = f.key;
    b.setAttribute("aria-label", f.key ? `フィルタ ${f.key}` : "フィルタなし");
    b.addEventListener("click", () => setFilter(f.key));
    if (META.filters.some(x => x.key === f.key && x.extra)) b.dataset.extra = "1";
    box.append(b);
  }
}
function setFilter(key) {
  S.filter = key;
  for (const b of $("filters").children) b.setAttribute("aria-pressed", b.dataset.key === key);
  updateListenButtons();
  requestDraw();
}
function setCommon(v) {
  S.common = v;
  $("ampCommon").setAttribute("aria-pressed", v); $("ampEach").setAttribute("aria-pressed", !v);
  requestDraw();
}
function buildChoices() {
  META.choices.forEach((c, i) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = c.name;
    b.addEventListener("click", () => answer(i));
    $("choices").append(b);
  });
}
function answer(i) {
  const ev = META.events[S.idx];
  stopAudio();
  answers[ev.no] = { pick: i, hint: !!S.hintUsed };
  saveAnswers(answers);
  S.audioStation = ev.audio.station;       // 答えたら、その揺れがいちばんよく聞こえる拠点の音にする
  setFilter(ev.best);   // 答えたら、その揺れがいちばん見やすいフィルタにする(ほかにも切り替えられる)
  showResult();
  requestDraw();
}
function showResult() {
  const ev = META.events[S.idx], a = answers[ev.no];
  const done = a !== undefined;
  $("askBox").hidden = done; $("result").hidden = !done;
  $("listen").parentElement.hidden = soundHidden();
  $("hiddenWave").hidden = !waveHidden();
  $("amp").hidden = waveHidden();
  [...$("choices").children].forEach((b, i) => { b.disabled = (ev.disabled || []).includes(i); });
  // L は答えたあとだけ、どの問題でも出す(海外の地震の問題だけに出すと、出たことで答えがわかってしまうため)
  for (const b of $("filters").children) if (b.dataset.extra) b.hidden = !done;
  $("ask").textContent = ev.focus ? "印を付けた「この揺れ」は何の揺れでしょう?" : "何の揺れでしょう?";
  $("heading").textContent = `問題 ${ev.no}` + (done ? `  ${META.choices[ev.kind].name}` : `(${MODES[S.mode]})`);
  if (!done) return;
  const ok = a.pick === ev.kind, c = META.choices[ev.kind];
  const best = ev.best, bestF = META.filters.find(f => f.key === best);
  const rows = META.filters.map(f => `<tr class="${f.key === best ? "best" : ""}"><td>${f.key}</td><td>${f.low}〜${f.high} Hz</td>`
    + `<td>${f.sound}</td><td>${f.key === best ? "← この揺れが見やすい" : f.extra ? "← 答えのあとだけ使える" : ""}</td></tr>`).join("");
  $("result").innerHTML = `<h2 class="${ok ? "ok" : "ng"}">${ok ? "正解!" : "ざんねん"} 答えは「${c.name}」</h2>`
    + (ok ? "" : `<p>あなたの答え: ${META.choices[a.pick].name}</p>`)
    + `<p>${escapeHtml(ev.time)}  ${escapeHtml(ev.label)}</p>`
    + `<div class="listenRow"><button class="listen" id="listenRaw" type="button"></button>`
    + `<button class="listen alt" id="listenBest" type="button"></button>`
    + (ev.low && best !== LOW_KEY ? `<button class="listen alt" id="listenLow" type="button"></button>` : "")
    + (ev.audio.speed !== META.audio.common
       ? `<div class="seg" id="speedSeg" role="group" aria-label="速さ"><button type="button" data-speed="${ev.audio.speed}">×${ev.audio.speed}</button>`
         + `<button type="button" data-speed="${META.audio.common}">×${META.audio.common}</button></div>` : "")
    + `</div><p class="note">フィルタなしの音と、この揺れが見やすいフィルタ ${best}(${escapeHtml(bestF.sound)})の音を聴き比べられます。`
    + `×${ev.audio.speed} は ${ev.audio.speed} 倍の速さで、揺れの周波数も ${ev.audio.speed} 倍になります`
    + `(たとえば 5 Hz の揺れが ${5 * ev.audio.speed} Hz の音に)。${ev.audio.speed !== META.audio.common ? `×${META.audio.common} は答える前と同じ速さです。` : ""}`
    + `音は「♪」の付いた拠点の上下動です(波形をタップすると、その拠点の音に変えられます)。大きさは最大でそろえています。</p>`
    + `<p>${escapeHtml(c.text)}</p>`
    + (ev.note ? `<p><b>この問題の注意:</b> ${escapeHtml(ev.note)}</p>` : "")
    + `<table><tr><th>フィルタ</th><th>帯域</th><th>音でいうと</th><th></th></tr>${rows}</table>`
    + `<p class="note">波形は、いちばん見やすいフィルタ ${best} に切り替えました。上のボタンでほかのフィルタやフィルタなし(元)にも戻せます。`
    + `時間の目盛りは「${escapeHtml(ev.ref)}」を 0 秒にしました(紫の破線)。`
    + (Object.keys(ev.phases || {}).length ? "青の P・赤の S の破線は、震源から計算した到着の見込みです(実際とは 1〜2 秒ずれることがあります)。" : "")
    + `${a.hint ? "ヒントを使いました。" : ""}</p>`
    + `<button id="nextQ" type="button">${S.idx < META.events.length - 1 ? "次の問題へ" : "一覧に戻る"}</button>`;
  $("nextQ").addEventListener("click", () => S.idx < META.events.length - 1 ? step(1) : (location.hash = ""));
  S.ansSpeed = ev.audio.speed;
  $("listenRaw").addEventListener("click", () => playAudio(S.ansSpeed, ""));
  $("listenBest").addEventListener("click", () => playAudio(S.ansSpeed, best));
  if ($("listenLow")) $("listenLow").addEventListener("click", () => playAudio(S.ansSpeed, LOW_KEY));
  for (const b of document.querySelectorAll("#speedSeg button")) {
    b.addEventListener("click", () => { stopAudio(); S.ansSpeed = Number(b.dataset.speed); updateListenButtons(); });
  }
  updateListenButtons();
}
function stationLabel(id) { const st = META.stations.find(s => s.id === id); return st ? st.label : id; }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]); }
$("hint").addEventListener("click", () => {
  const ev = META.events[S.idx];
  S.hintUsed = true;
  setFilter(META.choices[ev.kind].best);
});

// --- 画面の切り替え ---
async function openQuestion(idx) {
  stopAudio();
  const ev = META.events[idx], token = ++S.token;
  S.audioStation = answers[ev.no] ? ev.audio.station : firstSoundStation(ev);
  if ($("view").hidden) S.listY = window.scrollY;
  S.idx = idx; S.data = null; S.hintUsed = false;
  $("list").hidden = true; $("view").hidden = false;
  $("prev").disabled = idx === 0;
  $("next").disabled = idx === META.events.length - 1;
  setFilter(answers[ev.no] ? ev.best : "");
  showResult();
  $("bottom").scrollTop = 0;
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
  stopAudio();
  S.token++; S.idx = -1; S.data = null;
  $("view").hidden = true; $("list").hidden = false;
  buildList();
  window.scrollTo(0, S.listY);
}
function route() {
  const idx = META.events.findIndex(e => String(e.no) === location.hash.slice(1));
  if (idx >= 0 && typeof DecompressionStream !== "undefined") openQuestion(idx); else showList();
}
function buildList() {
  const list = $("qs");
  list.textContent = "";
  let done = 0, ok = 0;
  for (const ev of META.events) {
    const a = answers[ev.no], li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `${ev.no}<small>${a === undefined ? "" : a.pick === ev.kind ? "正解" : "×"}</small>`;
    if (a !== undefined) { done++; if (a.pick === ev.kind) ok++; b.className = a.pick === ev.kind ? "ok" : "ng"; }
    b.addEventListener("click", () => { location.hash = String(ev.no); });
    li.append(b); list.append(li);
  }
  $("score").textContent = `${META.events.length} 問中 ${done} 問に答えました` + (done ? `(正解 ${ok} 問)` : "");
}
$("back").addEventListener("click", () => { location.hash = ""; });
function step(delta) {
  const ev = META.events[S.idx + delta];
  if (ev) location.replace("#" + ev.no);
}
$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("ampEach").addEventListener("click", () => setCommon(false));
$("ampCommon").addEventListener("click", () => setCommon(true));
$("reset").addEventListener("click", () => {
  if (!confirm(`「${MODES[S.mode]}」の答えをすべて消しますか?`)) return;
  answers = {}; saveAnswers(answers); buildList();
});
window.addEventListener("hashchange", route);
new ResizeObserver(resize).observe(canvas);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); requestDraw(); });
if (typeof DecompressionStream === "undefined") $("unsupported").hidden = false;
$("choiceNote").textContent = META.choiceNote;
readColors(); buildFilterButtons(); buildChoices(); setCommon(false); setMode(S.mode); route();
</script>
</body>
</html>
"""


def main():
  from hypocenter import load_stations
  parser = argparse.ArgumentParser(description="「この揺れは何?」クイズの HTML を書き出す(問題は etc/shake_quiz.csv)")
  parser.add_argument("-o", "--output", help=f"出力する HTML(省略すると batch_plots/quiz/quiz.html)")
  parser.add_argument("--quiz", help="問題の一覧の CSV(省略すると etc/shake_quiz.csv)")
  args = parser.parse_args()
  steps = count_steps(app_paths.ETC_DIR)
  stations = {nm: c for nm, c in load_stations(app_paths.ETC_DIR).items() if nm in STATIONS}
  events = []
  for q in read_quiz(args.quiz):
    before, after = q["show"]
    start = q["t"] + timedelta(seconds=before)
    st = load_stream(start, start + timedelta(seconds=after - before))
    info, waves = event_data(q, st, steps, stations)
    events.append((info, waves))
    missing = [station_names.full(nm) for nm in STATIONS if nm not in waves]
    print(f"  問題{q['no']:2d} {q['kind']} {q['t']:%Y-%m-%d %H:%M:%S} {info['rate']} Hz  音: {station_names.full(info['audio']['station'])}"
          + (f"  記録なし: {', '.join(missing)}" if missing else ""))
  out = Path(args.output) if args.output else OUTPUT_DIR / "quiz.html"
  size = write_page(out, events)
  answers = out.with_name(out.stem + "_answers.csv")
  write_answers(answers, events)
  print(f"書き出しました({len(events)} 問、{size / 1e6:.1f} MB): {out}")
  print(f"答えの一覧: {answers}")

if __name__ == "__main__":
  main()
