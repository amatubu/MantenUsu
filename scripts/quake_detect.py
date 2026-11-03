# quake_detect.py
# 記録全体から地震らしい揺れ(地震の候補)を探す(UsuMantenViewer.py のバッチ処理から使う)
#   1. 日ごとに全拠点の上下動を帯域通過フィルター(QUAKE_BAND)に通し、10秒ごとの RMS にする
#      (RMS は output/quake_rms に日ごとに保存し、2回目からはそれを使う)
#   2. RMS が周りの雑音(前後30分の中央値)の何倍かを拠点ごとに求め、
#      QUAKE_MIN_STATIONS 拠点以上で同じころに QUAKE_RATIO 倍を超えたところを地震の候補とする
#      (データのある拠点が QUAKE_MIN_STATIONS より少ない時間は調べない)
#   3. 候補の前後を、拠点の倍率の中央値が ONSET_RATIO 倍を下回るところまで広げ、揺れの始まりと終わりとする
#   判定の基準は train_day_plot.py(列車の揺れから地震を除く処理)で決めたものと同じ
import csv
import json
from datetime import datetime, timedelta
import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

import app_paths
import jma_match
import quake_features
from hypocenter import LocalProjection
from matplotlib.figure import Figure
import obspy

from plot_common import NM_TO_CM
from log_common import log_message

# 地震の見分け方: 列車や車の揺れは近くの拠点にしか出ないが、地震はどの拠点でも大きく揺れる
#   QUAKE_BAND で全拠点の揺れを調べ、同じころに QUAKE_MIN_STATIONS 拠点以上で
#   雑音の QUAKE_RATIO 倍以上揺れていたら地震の候補とする
#   多少外しても広く候補を拾うため、基準は低めにしている
#   (2026/8/20〜24 の2〜20 Hz では、気象庁の震源リストにある地震5件(8/20 00:50 浦河沖 M4.0、
#    00:55 浦河沖 M3.5、8/23 02:00 茨城県南部 M5.9、22:45 浦河沖、8/24 01:36)は3〜4拠点で20倍以上。
#    列車や日中の揺れで20倍を超えたのは、同時には多くて2拠点だった)
QUAKE_BAND = (2.0, 20.0)
QUAKE_RATIO = 20.0
QUAKE_MIN_STATIONS = 3
# 有珠山の直下の小さな地震を拾うための例外: 山頂に近い Hokudai がとくに大きく揺れたとき
#   HOKUDAI 拠点が雑音の HOKUDAI_RATIO 倍以上で、ほかの HOKUDAI_OTHERS 拠点以上が HOKUDAI_OTHERS_RATIO 倍以上なら、
#   QUAKE_MIN_STATIONS 拠点が QUAKE_RATIO 倍を超えていなくても候補にする
#   (気象庁の震源リストで有珠山の地震とされた 2025/7/8 21:30 M0.3 は Hokudai 219倍・Bamba 18倍・Mimatsu 14倍、
#    7/13 20:28 M-0.1 は Hokudai 49倍・Bamba 7倍・Mimatsu 4倍で、3拠点が20倍を超えず見逃していた。
#    この例外で、2025/7〜2026/8 の候補が113件増える。Hokudai で毎晩21時ごろに記録される
#    洞爺湖の花火の音(4/28〜10/31)は、ほかの拠点では小さいため含まれなかった)
HOKUDAI = "Hokudai"
HOKUDAI_RATIO = 40.0
HOKUDAI_OTHERS_RATIO = 4.0
HOKUDAI_OTHERS = 2
# 候補を見つけた基準(一覧・履歴に書く)
RULE_STATIONS = f"{QUAKE_MIN_STATIONS}拠点"
RULE_HOKUDAI = "Hokudai"
# 拠点ごとに、この秒数のずれは同じころとみなす(地震の揺れが拠点に届く時間の差など)
QUAKE_MARGIN = 30.0
# RMS を計算する窓の長さ [秒]
RMS_WINDOW = 10.0
# 雑音の基準(RMS の移動中央値)を取る窓の長さ [秒]
NOISE_WINDOW = 1800.0
# 雑音の基準を求めるのに必要な、データのある窓の数(これより少なければ求めない)
NOISE_MIN_WINDOWS = 30
# この秒数より近い揺れは1つにまとめる
MERGE_GAP = 30.0
# 揺れの始まりと終わり: 拠点の倍率の中央値がこの倍率を下回るところまで広げる
ONSET_RATIO = 3.0
# 前・後に広げる上限 [秒](雑音の多い時間に、どこまでも広がらないように)
ONSET_MAX_BEFORE = 300.0
ONSET_MAX_AFTER = 1800.0
# この長さ [秒] より短い連続したデータは使わない(フィルターの端の影響が大きいため)
MIN_SEGMENT = 60.0

# 1日の窓の数
WINDOWS_PER_DAY = int(24 * 3600 / RMS_WINDOW)

# 日ごとの RMS の保存先
RMS_CACHE_DIR = app_paths.OUTPUT_DIR / "quake_rms"
# 候補の一覧・確認用の画像の出力先
OUTPUT_DIR = app_paths.BATCH_PLOTS_DIR / "quakes"


# 保存した RMS のファイル(帯域・窓の長さが変わったら別のファイルにする)
def rms_cache_path(day):
  return RMS_CACHE_DIR / f"{day:%Y%m%d}_{QUAKE_BAND[0]:g}-{QUAKE_BAND[1]:g}Hz_{RMS_WINDOW:g}s.npz"

def load_rms_cache(day):
  path = rms_cache_path(day)
  if not path.exists():
    return None
  try:
    with np.load(path) as z:
      return {str(name): z[f"rms_{i}"] for i, name in enumerate(z["stations"])}
  except Exception as e:
    log_message(f"  保存した RMS を読めませんでした({path.name}): {e}")
    return None

# RMS を保存済みの日(今の帯域・窓の長さのもの)
def rms_cache_days():
  suffix = rms_cache_path(datetime(2000, 1, 1)).name[8:]
  days = []
  if RMS_CACHE_DIR.exists():
    for path in RMS_CACHE_DIR.glob(f"*{suffix}"):
      try:
        days.append(datetime.strptime(path.name[:8], "%Y%m%d"))
      except ValueError:
        pass
  return sorted(days)

def save_rms_cache(day, rms):
  RMS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
  names = sorted(rms)
  np.savez_compressed(rms_cache_path(day), stations=np.array(names),
                      **{f"rms_{i}": rms[name] for i, name in enumerate(names)})

# 1時間ずつ読み込んだ波形をつなぐ
#   hours ... [(その時間の開始日時, Stream), ...](None ならその時間はデータなし)
#   戻り値 ... Stream(欠測のところは masked array)
def merge_hours(hours):
  st = obspy.Stream()
  for start, hour_st in hours:
    if not hour_st:
      continue
    # 1時間ごとのフォルダには次の時間の最初の1分も入っているので、その時間の分だけにする
    t0 = obspy.UTCDateTime(start)
    for tr in hour_st:
      st += tr.slice(t0, t0 + 3600 - tr.stats.delta)
  st.merge(method=1, fill_value=None)
  return st

# 上下動を拠点ごとに取り出す。戻り値 ... {拠点名: Trace}
def vertical_traces(st):
  return {tr.stats.station: tr for tr in st if tr.stats.channel.endswith("U")}

# 候補の前後の波形から、近い地震かどうかを見分けるための値を計算する(quake_features.analyze)
#   st ... 候補の前後を含む Stream(全成分)
#   戻り値 ... (analyze の結果, {拠点名: P波の到着時刻(datetime)}) または (None, {})
def analyze_candidate(st, t0, t1, stations):
  w = st.slice(obspy.UTCDateTime(t0), obspy.UTCDateTime(t1)).copy()
  for tr in w:
    if np.ma.isMaskedArray(tr.data):
      tr.data = tr.data.filled(0)
  w = obspy.Stream([tr for tr in w if tr.stats.npts > 0 and np.any(tr.data) and tr.stats.station in stations])
  names = sorted({tr.stats.station for tr in w if tr.stats.channel.endswith("U")})
  if len(names) < 2:
    return None, {}
  w = obspy.Stream([tr for tr in w if tr.stats.station in names])
  proj = LocalProjection([stations[nm] for nm in names])
  res, info = quake_features.analyze(w, stations, proj)
  picks = {nm: info["ref_time"].datetime + timedelta(seconds=t) for nm, t in info["picks"].items() if t is not None}
  return res, picks

# 候補ごとの波形の解析結果(analyze_candidate の戻り値)を保存するファイル
#   候補の始まり・終わりが同じなら解析結果も同じなので、次に候補を探すときは波形を読まずにこれを使う
#   (全期間で200件余りの候補の解析に、SAC が手元にあっても1分近く、ZIP から取り出すときはもっとかかるため)
#   quake_features.analyze や analyze_candidate の中身を変えたら FEATURES_VERSION を上げて、作り直させる
FEATURES_VERSION = 1
FEATURES_CACHE_FILE = app_paths.OUTPUT_DIR / "quake_features" / f"features_v{FEATURES_VERSION}.json"

# 解析結果を保存するときの、候補の見分け方(始まりと終わり)
def features_key(cand):
  return f"{cand['start']:%Y%m%d%H%M%S}_{cand['end']:%Y%m%d%H%M%S}"

# 保存した解析結果。戻り値 ... {features_key: {"features": ..., "picks": ..., "utc": 時刻の項目}}
def load_features_cache():
  try:
    return json.loads(FEATURES_CACHE_FILE.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return {}

def save_features_cache(cache):
  FEATURES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
  tmp = FEATURES_CACHE_FILE.with_suffix(".tmp")
  tmp.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
  tmp.replace(FEATURES_CACHE_FILE)

# 解析結果を保存できる形にする(時刻は文字列にし、どの項目が時刻だったかを覚えておく)
def encode_features(features, picks):
  utc = [k for k, v in (features or {}).items() if isinstance(v, obspy.UTCDateTime)]
  f = None if features is None else {k: str(v) if k in utc else v for k, v in features.items()}
  return {"features": f, "picks": {nm: t.isoformat() for nm, t in picks.items()}, "utc": utc}

# encode_features の逆。戻り値 ... (features, picks)
def decode_features(entry):
  f = entry["features"]
  if f is not None:
    f = {k: obspy.UTCDateTime(v) if k in entry["utc"] else v for k, v in f.items()}
  return f, {nm: datetime.fromisoformat(t) for nm, t in entry["picks"].items()}

# 1日分の波形から、拠点ごとに QUAKE_BAND の 10秒ごとの RMS [cm/s] を求める
#   戻り値 ... {拠点名: 長さ WINDOWS_PER_DAY の配列}(データの無い窓は NaN)
#   欠測をはさむところでフィルターが乱れないよう、連続した区間ごとにフィルターをかける
def day_rms(day, traces):
  result = {}
  for name, tr in traces.items():
    rms = np.full(WINDOWS_PER_DAY, np.nan)
    for seg in tr.split():
      if seg.stats.endtime - seg.stats.starttime < MIN_SEGMENT:
        continue
      seg = seg.copy()
      seg.data = seg.data.astype(float)
      seg.detrend("demean")
      seg.filter("bandpass", freqmin=QUAKE_BAND[0], freqmax=QUAKE_BAND[1], corners=4, zerophase=True)
      df = seg.stats.sampling_rate
      n = int(round(RMS_WINDOW * df))
      # 区間の中に完全に入る窓だけを使う
      offset = (seg.stats.starttime.datetime - day).total_seconds()
      k0 = max(0, int(np.ceil(offset / RMS_WINDOW - 1e-6)))
      i0 = int(round((k0 * RMS_WINDOW - offset) * df))
      count = min((len(seg.data) - i0) // n, WINDOWS_PER_DAY - k0)
      if count <= 0:
        continue
      blocks = seg.data[i0:i0 + count * n].reshape(count, n) * NM_TO_CM
      values = np.sqrt(np.mean(blocks ** 2, axis=1))
      # 記録が止まって0が続いている窓はデータなしとする
      rms[k0:k0 + count] = np.where(values > 0, values, np.nan)
    result[name] = rms
  return result

# 雑音の基準(RMS の移動中央値)。データの無い窓(NaN)は除いて計算する
#   計算を速くするため、中央値は NOISE_STEP 個の窓ごとに求め、その間は同じ値にする
#   長い期間をまとめて計算するとメモリを使うので、1日分ずつ計算する
NOISE_STEP = 6
def noise_level(rms):
  half = int(NOISE_WINDOW / RMS_WINDOW / 2)
  padded = np.concatenate([np.full(half, np.nan), rms, np.full(half, np.nan)])
  noise = np.full(len(rms), np.nan)
  for i in range(0, len(rms), WINDOWS_PER_DAY):
    j = min(i + WINDOWS_PER_DAY, len(rms))
    view = sliding_window_view(padded[i:j + 2 * half], 2 * half + 1)[::NOISE_STEP]
    # NaN は並べ替えると後ろに集まるので、データのある窓の数から中央の位置を決める
    ordered = np.sort(view, axis=1)
    count = np.isfinite(view).sum(axis=1)
    rows = np.arange(len(count))
    med = (ordered[rows, np.maximum((count - 1) // 2, 0)] + ordered[rows, np.maximum(count // 2, 0)]) / 2
    med = np.where(count >= NOISE_MIN_WINDOWS, med, np.nan)
    noise[i:j] = np.repeat(med, NOISE_STEP)[:j - i]
  return noise

# 前後 margin 個の窓での最大(NaN は除く。すべて NaN なら NaN)
def moving_max(values, margin):
  padded = np.concatenate([np.full(margin, np.nan), values, np.full(margin, np.nan)])
  return np.fmax.reduce(sliding_window_view(padded, 2 * margin + 1), axis=1)

# 期間全体の RMS から、地震の候補を探す
#   start_day ... 期間の最初の日(0時)
#   rms       ... {拠点名: 期間全体の RMS の配列}(長さは日数 × WINDOWS_PER_DAY)
#   戻り値 ... (候補のリスト, 期間の情報)
#     候補 ... {"start", "end", "peak"(datetime), "ratios"({拠点名: 区間での最大の倍率}),
#              "over"(QUAKE_RATIO 倍を超えた拠点数), "available"(データのある拠点数), "max_ratio"}
#     期間の情報 ... {"names", "ratio"(拠点 × 窓の倍率), "available"(窓ごとのデータのある拠点数),
#                    "start_day"}(見逃した地震の確認に使う)
def find_candidates(start_day, rms):
  names = sorted(rms)
  ratio = np.array([rms[nm] / noise_level(rms[nm]) for nm in names])
  half = int(QUAKE_MARGIN / RMS_WINDOW)
  peaks = np.array([moving_max(r, half) for r in ratio])
  available = np.isfinite(peaks).sum(axis=0)
  over = (np.nan_to_num(peaks, nan=0) >= QUAKE_RATIO).sum(axis=0)
  by_stations = (available >= QUAKE_MIN_STATIONS) & (over >= QUAKE_MIN_STATIONS)
  by_hokudai = np.zeros_like(by_stations)
  if HOKUDAI in names:
    i = names.index(HOKUDAI)
    others = np.delete(np.nan_to_num(peaks, nan=0), i, axis=0)
    by_hokudai = ((available >= QUAKE_MIN_STATIONS) & (np.nan_to_num(peaks[i], nan=0) >= HOKUDAI_RATIO)
                  & ((others >= HOKUDAI_OTHERS_RATIO).sum(axis=0) >= HOKUDAI_OTHERS))
  flagged = by_stations | by_hokudai

  # 続いている窓をまとめる
  runs = []
  gap = int(MERGE_GAP / RMS_WINDOW)
  for k in np.flatnonzero(flagged):
    if runs and k - runs[-1][1] <= gap + 1:
      runs[-1][1] = k
    else:
      runs.append([k, k])

  with warnings.catch_warnings():
    warnings.simplefilter("ignore", RuntimeWarning)   # すべて NaN の窓
    median = np.nanmedian(ratio, axis=0)
  median = np.nan_to_num(median, nan=0)
  score = np.nansum(np.log10(np.clip(np.nan_to_num(ratio, nan=1), 1, None)), axis=0)
  raw_count = (np.nan_to_num(ratio, nan=0) >= QUAKE_RATIO).sum(axis=0)

  spans = []
  for k0, k1 in runs:
    # まとめた区間は前後 QUAKE_MARGIN 秒の最大で広がっているので、実際に超えた窓に縮める
    #   1拠点だけの雑音(Hokudai の近くの物音など)で始まりが早くならないよう、2拠点以上で超えた窓にする
    #   (2026/8/24 01:36 の日高地方東部の地震は、P波の30秒前から Hokudai だけが雑音の20〜45倍だった)
    #   2拠点以上で超えた窓が無ければ(拠点に届く時刻のずれが大きいときなど)、1拠点でも超えた窓にする
    core = np.flatnonzero(raw_count[k0:k1 + 1] >= 2)
    if len(core) == 0:
      core = np.flatnonzero(raw_count[k0:k1 + 1] >= 1)
    if len(core):
      k0, k1 = k0 + core[0], k0 + core[-1]
    # 倍率の中央値が ONSET_RATIO を下回るところまで広げる
    a, b = k0, k1
    while a > 0 and k0 - a < ONSET_MAX_BEFORE / RMS_WINDOW and median[a - 1] >= ONSET_RATIO:
      a -= 1
    while b < len(median) - 1 and b - k1 < ONSET_MAX_AFTER / RMS_WINDOW and median[b + 1] >= ONSET_RATIO:
      b += 1
    # 広げた結果、前の候補と重なったら1つにする
    if spans and a <= spans[-1][1] + gap:
      spans[-1][1] = max(spans[-1][1], b)
    else:
      spans.append([a, b])

  candidates = []
  for a, b in spans:
    p = a + int(np.argmax(score[a:b + 1]))
    # 拠点ごとの最大の倍率は、拠点に届く時刻のずれを見込んで前後 QUAKE_MARGIN 秒も含めて求める
    seg = ratio[:, max(0, a - half):b + half + 1]
    ratios = {nm: float(np.nanmax(seg[i])) for i, nm in enumerate(names) if np.isfinite(seg[i]).any()}
    candidates.append({
      "start": start_day + timedelta(seconds=a * RMS_WINDOW),
      "end": start_day + timedelta(seconds=(b + 1) * RMS_WINDOW),
      "peak": start_day + timedelta(seconds=(p + 0.5) * RMS_WINDOW),
      "ratios": ratios,
      "over": sum(v >= QUAKE_RATIO for v in ratios.values()),
      "available": int(available[a:b + 1].max()),
      "max_ratio": max(ratios.values()) if ratios else 0.0,
      # 3拠点の基準で見つかったか、Hokudai の例外だけで見つかったか
      "rule": RULE_STATIONS if by_stations[max(0, a - half):b + half + 1].any() else RULE_HOKUDAI,
    })
  info = {"names": names, "ratio": ratio, "available": available, "start_day": start_day}
  return candidates, info

# 期間の情報から、指定した時間の窓のインデックス
def window_index(info, t):
  return int((t - info["start_day"]).total_seconds() // RMS_WINDOW)

# 指定した時間の範囲での、データのある拠点数と、拠点ごとの最大の倍率
def ratios_between(info, t0, t1):
  k0, k1 = max(0, window_index(info, t0)), min(info["ratio"].shape[1], window_index(info, t1) + 1)
  if k1 <= k0:
    return 0, {}
  seg = info["ratio"][:, k0:k1]
  ratios = {nm: float(np.nanmax(seg[i])) for i, nm in enumerate(info["names"]) if np.isfinite(seg[i]).any()}
  return len(ratios), ratios

# 時刻の表示(秒まで)
def hhmmss(t):
  return t.strftime("%H:%M:%S")

# 拠点ごとの倍率の表示。例: Bamba 35 / Hokudai 120
def format_ratios(ratios):
  return " / ".join(f"{nm} {v:.0f}" for nm, v in ratios.items())

# 確認用の画像: 全拠点の上下動(QUAKE_BAND のフィルター後)を、候補の前後で並べる
#   traces ... {拠点名: Trace}(候補の前後を含む上下動)
#   match  ... 気象庁の震源リストと対応した地震(jma_match.match_candidates の結果)または None
#              あれば P波・S波の到達見込みの時刻に線を引く
#   cand["picks"] があれば、自動で検出した P波の到着時刻に短い線を引く
#   missed ... 見逃した地震の画像(揺れの区間・最大の線を引かない)
def save_candidate_image(path, cand, traces, match, title, missed=False):
  t0 = cand["start"] - timedelta(seconds=30)
  t1 = min(cand["end"] + timedelta(seconds=30), t0 + timedelta(minutes=10))
  names = sorted(traces)
  # pyplot を使わずに描く(メンテナンス画面から別スレッドで呼ぶため)
  fig = Figure(figsize=(12, 1.8 * len(names) + 0.6))
  axes = fig.subplots(len(names), 1, sharex=True, squeeze=False)
  axes = axes[:, 0]
  for ax, nm in zip(axes, names):
    # 欠測をはさむときは、連続した区間ごとにフィルターをかける
    for tr in traces[nm].slice(obspy.UTCDateTime(t0) - 10, obspy.UTCDateTime(t1) + 10).split():
      if tr.stats.npts < tr.stats.sampling_rate:
        continue
      tr.data = tr.data.astype(float)
      tr.detrend("demean")
      tr.filter("bandpass", freqmin=QUAKE_BAND[0], freqmax=QUAKE_BAND[1], corners=4, zerophase=True)
      t = tr.times() + (tr.stats.starttime.datetime - t0).total_seconds()
      ax.plot(t, tr.data * NM_TO_CM, lw=0.4, color="#1f77b4")
    if not missed:
      ax.axvspan((cand["start"] - t0).total_seconds(), (cand["end"] - t0).total_seconds(),
                 color="gray", alpha=0.12, lw=0)
      ax.axvline((cand["peak"] - t0).total_seconds(), color="gray", ls=":", lw=0.8)
    pick = cand.get("picks", {}).get(nm)
    if pick:
      ax.axvline((pick - t0).total_seconds(), ymin=0.25, ymax=0.75, color="#ff7f0e", lw=2)
    if match:
      for key, color, label in (("p_time", "#d62728", "P"), ("s_time", "#2ca02c", "S")):
        x = (match[key] - t0).total_seconds()
        if 0 <= x <= (t1 - t0).total_seconds():
          ax.axvline(x, color=color, ls="--", lw=1)
          ax.text(x, 0.95, f" {label}", color=color, transform=ax.get_xaxis_transform(), va="top", fontsize=8)
    ratio = cand["ratios"].get(nm)
    ax.text(0.005, 0.92, f"{nm}" + (f"  ×{ratio:.0f}" if ratio is not None else "  (データなし)"),
            transform=ax.transAxes, va="top", weight="bold", fontsize=9,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=1))
    ax.set_ylabel("cm/s")
    ax.grid(True, alpha=0.3)
  axes[0].set_xlim(0, (t1 - t0).total_seconds())
  axes[0].set_title(title, fontsize=9, loc="left")
  note = f"時刻 [秒]  {t0:%Y-%m-%d %H:%M:%S} から  上下動 {QUAKE_BAND[0]:g}-{QUAKE_BAND[1]:g} Hz  "
  note += "×: 雑音の何倍か" if missed else "灰色: 揺れの区間  点線: 揺れの最大  ×: 雑音の何倍か"
  if cand.get("picks"):
    note += "  橙: 自動で検出した P波"
  if match:
    note += "  破線: 気象庁の震源からの到達見込み(赤 P波・緑 S波)"
  axes[-1].set_xlabel(note, fontsize=8)
  fig.tight_layout()
  fig.savefig(path, dpi=90)

# 候補の短い説明(画像のタイトル・履歴に使う)
#   例: 地震候補 08/23 22:45:40 浦河沖 M4.0(4拠点 最大×350)
LABEL_PREFIX = "地震候補 "
def candidate_label(cand):
  text = f"{LABEL_PREFIX}{cand['start']:%m/%d %H:%M:%S} "
  q = cand.get("match")
  if q:
    text += f"{q['震央地名']} M{q['M']:.1f}" + ("?" if cand.get("doubtful") else "")
  else:
    text += "震源リストに該当なし"
  rule = f" {RULE_HOKUDAI}" if cand.get("rule") == RULE_HOKUDAI else ""
  return text + f"({cand['over']}拠点{rule} 最大×{cand['max_ratio']:.0f})"

# 画像のタイトル(2行)
def candidate_title(cand):
  line1 = (f"地震候補 {cand['start']:%Y-%m-%d %H:%M:%S}〜{hhmmss(cand['end'])}"
           f"(長さ {(cand['end'] - cand['start']).total_seconds():.0f}秒)  "
           f"{cand['available']}拠点中 {cand['over']}拠点で雑音の{QUAKE_RATIO:g}倍超  最大×{cand['max_ratio']:.0f}"
           + (f"  [Hokudai の例外({HOKUDAI_RATIO:g}倍以上・ほか{HOKUDAI_OTHERS}拠点が{HOKUDAI_OTHERS_RATIO:g}倍以上)]"
              if cand.get("rule") == RULE_HOKUDAI else ""))
  q = cand.get("match")
  if q:
    line2 = (f"気象庁: {q['日時']:%H:%M:%S.%f}"[:-5] + f" {q['震央地名']} M{q['M']:.1f} 深さ{q['深さ[km]']:g}km  "
             f"震央距離 {q['dist_km']:.0f}km  方位 {q['azimuth']:.0f}度({jma_match.direction_name(q['azimuth'])})  "
             f"S-P {(q['s_time'] - q['p_time']).total_seconds():.1f}秒  "
             f"揺れの始まり−P見込み {(cand['start'] - q['p_time']).total_seconds():+.0f}秒"
             + (f"  (ほかに揺れの間に届く地震 {cand['others']}件)" if cand.get("others") else "")
             + ("  ※見込みの振幅が小さく、対応は疑わしい" if cand.get("doubtful") else "")
             + ("  有珠山の地震" if cand.get("usu") else ""))
  else:
    line2 = f"気象庁の震源リストに該当する地震なし  有珠山の地震らしさ: {cand.get('usu') or '-'}"
  f = cand.get("features")
  if f:
    line2 += (f"\n波形の解析: P検出 {f['picked']}拠点  P→最大 {fmt(f['p_to_peak'], '.1f')}秒  卓越 {fmt(f['freq'], '.1f')}Hz  "
              f"見かけの速度 {fmt(f['app_v'], '.1f')}km/s  到来方向 {fmt(f['azimuth'], '.0f')}度"
              + (f"  震源(近い地震として) {f['location']}" if f["location"] else ""))
  return line1 + "\n" + line2

def fmt(v, spec):
  return "-" if v is None else format(v, spec)

# 見逃した地震の画像のタイトル
def missed_title(q, available, ratios):
  return (f"見逃し? 気象庁: {q['日時']:%Y-%m-%d %H:%M:%S} {q['震央地名']} M{q['M']:.1f} 深さ{q['深さ[km]']:g}km  "
          f"震央距離 {q['dist_km']:.0f}km  方位 {q['azimuth']:.0f}度({jma_match.direction_name(q['azimuth'])})  "
          f"見込みの振幅 {q['log_amp']:.2f}\n"
          f"データのある拠点 {available}  P波〜S波の60秒後の最大の倍率: {format_ratios(ratios)}"
          f"(地震の候補とする基準: {QUAKE_MIN_STATIONS}拠点以上で{QUAKE_RATIO:g}倍超)")

# 画像(PNG)をまとめて1つのPDFにする
def save_pdf(path, image_paths):
  from PIL import Image
  pages = [Image.open(p).convert("RGB") for p in image_paths if p.exists()]
  if not pages:
    return False
  pages[0].save(path, save_all=True, append_images=pages[1:], resolution=90)
  return True

# 候補の一覧(CSV)
def write_candidates_csv(path, candidates, names):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["開始", "最大", "終了", "長さ[秒]", "検出の基準", "超えた拠点数", "データのある拠点数", "最大の倍率"]
                    + [f"{nm}の倍率" for nm in names]
                    + ["P波を検出した拠点数", "P→最大振幅[秒]", "卓越周波数[Hz]", "見かけの速度[km/s]",
                       "到来方向[度]", "震源(近い地震として)", "有珠山の地震らしさ"]
                    + ["震源リストとの対応", "気象庁: 発震時刻", "震央地名", "緯度", "経度", "M", "深さ[km]", "震央距離[km]", "震源の方位[度]",
                       "P波の到達見込み", "S波の到達見込み", "S-P[秒]", "揺れの始まり−P見込み[秒]",
                       "見込みの振幅(log A)", "ほかに揺れの間に届く地震の数", "画像"])
    for cand in candidates:
      row = [cand["start"].strftime("%Y-%m-%d %H:%M:%S"), hhmmss(cand["peak"]), hhmmss(cand["end"]),
             f"{(cand['end'] - cand['start']).total_seconds():.0f}", cand.get("rule", ""), cand["over"], cand["available"],
             f"{cand['max_ratio']:.1f}"]
      row += [f"{cand['ratios'][nm]:.1f}" if nm in cand["ratios"] else "" for nm in names]
      f = cand.get("features")
      if f:
        row += [f["picked"], fmt(f["p_to_peak"], ".1f"), fmt(f["freq"], ".1f"), fmt(f["app_v"], ".1f"),
                fmt(f["azimuth"], ".0f"), f["location"]]
      else:
        row += [""] * 6
      row.append(cand.get("usu", ""))
      q = cand.get("match")
      if q:
        row += ["対応?(見込みの振幅が小さい)" if cand.get("doubtful") else "対応",
                q["日時"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-5], q["震央地名"], f"{q['緯度']:.4f}", f"{q['経度']:.4f}",
                f"{q['M']:g}", f"{q['深さ[km]']:g}",
                f"{q['dist_km']:.0f}", f"{q['azimuth']:.0f}", q["p_time"].strftime("%H:%M:%S.%f")[:-5],
                q["s_time"].strftime("%H:%M:%S.%f")[:-5], f"{(q['s_time'] - q['p_time']).total_seconds():.1f}",
                f"{(cand['start'] - q['p_time']).total_seconds():+.0f}", f"{q['log_amp']:.2f}", cand["others"]]
      else:
        row += ["該当なし"] + [""] * 14
      row.append(cand.get("image", ""))
      writer.writerow(row)

# 見逃した地震の一覧(CSV)
#   missed ... jma_match.find_missed の結果
def write_missed_csv(path, missed, names):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["発震時刻", "震央地名", "緯度", "経度", "M", "深さ[km]", "震央距離[km]", "震源の方位[度]",
                     "P波の到達見込み", "S波の到達見込み", "見込みの振幅(log A)", "データのある拠点数"]
                    + [f"{nm}の倍率" for nm in names] + ["画像"])
    for q, available, ratios in missed:
      writer.writerow([q["日時"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-5], q["震央地名"],
                       f"{q['緯度']:.4f}", f"{q['経度']:.4f}", f"{q['M']:g}",
                       f"{q['深さ[km]']:g}", f"{q['dist_km']:.0f}", f"{q['azimuth']:.0f}",
                       q["p_time"].strftime("%H:%M:%S"), q["s_time"].strftime("%H:%M:%S"),
                       f"{q['log_amp']:.2f}", available]
                      + [f"{ratios[nm]:.1f}" if nm in ratios else "" for nm in names] + [q.get("image", "")])
