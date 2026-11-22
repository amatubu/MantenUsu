# template_match.py
# 有珠山リストの地震の波形をひな形(テンプレート)にして、記録全体から似た波形の地震を探す(TODO 31・37)
#   1. 有珠山リストの地震ごとに、満点の拠点の P波の到着時刻の 0.5秒前から 4.5秒間を、3成分ともひな形にする
#      (到着時刻は quake_locate と同じ。震源推定の画面で保存したものがあればそれを使う)
#   2. 1日ずつ、全拠点・全成分の波形とひな形の相関係数(正規化した相互相関)を計算し、
#      拠点ごとの到着時刻の差を保ったまま平均する
#   3. 平均した相関係数が、その日の中央値から MAD(中央値からの差の中央値)の THRESHOLD_MAD 倍を超えたところを検出とする
#   波形は帯域通過フィルター(BAND)のあと、SAMPLING_RATE に間引いて使う
#   使い方: python3 template_match.py -s 20251210 -e 20251217 [-j 3]
#   --set 名前 で、今の有珠山リスト(地震リストの usu)からひな形を作り、ひな形と日ごとの結果を別の名前で保存する
#   (例: --set v2_25。決まったファイル usu_quakes_24.csv から作った v1 のひな形と結果は、そのまま残る)
#   --set app は、アプリのメンテナンス画面の「似た波形の地震さがし」と同じ処理(run)。日ごとの結果を、ひな形ごとに保存して使い回し、
#   有珠山リストに地震が増えたときなどは、変わったひな形の分だけ計算する(ひな形の中身が変われば別のものとして扱う)。
#   よく似たひな形はグループにまとめて、一覧に書く(ひな形は減らさない。似ていても拾う地震が違うことがあるため)
import argparse
import hashlib
import csv
import json
import os
import sys
import tempfile
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks, oaconvolve

import app_paths
import station_names
import win_pipeline

warnings.filterwarnings("ignore", message="Sample spacing read from SAC file")

BAND = (2.0, 20.0)
SAMPLING_RATE = 50.0
DECIMATE = 5                 # 250 Hz → 50 Hz
STATIONS = ("Bamba", "Hokudai", "Mimatsu", "Nizaemn")
COMPONENTS = ("U", "N", "E")
BEFORE_P = 0.5               # ひな形の始まり(P波の何秒前)
TEMPLATE_LENGTH = 4.5        # ひな形の長さ [秒]
MIN_TEMPLATE_STATIONS = 3    # P波の到着時刻がこれより少ない地震はひな形にしない
MIN_CHANNELS = 6             # 相関を平均するのに要るデータのある成分の数
THRESHOLD_MAD = 9.0          # 検出の基準(MAD の倍数)
MIN_CC = 0.25                # 平均した相関係数の下限(MAD が小さすぎる日のため)
FEW_CHANNELS = 6             # 成分がこの数以下(2拠点)の検出は、相関が高いものだけ残す
FEW_CHANNELS_MIN_CC = 0.35   # 2拠点の検出を残す相関係数の下限(既知のものは常に残す)
# ひな形にしない地震(有珠山リストにあるが、地震ではない可能性が高いもの)。{ひな形の名前: 理由}
#   ひな形を作るときに除き、すでに作ったひな形で見つかったものも一覧から除く
EXCLUDED_TEMPLATES = {
  "20260623_125847": "採石場の発破の可能性(平日の昼に、似た揺れが山頂の南西〜西南西 約2.5 km で繰り返す)",
  "20260708_112027": "採石場の発破の可能性(20260623_125847 と同じ場所)",
}
DEDUP_SECONDS = 4.0          # この秒数より近い検出は1つにまとめる(相関係数が高い方)
KNOWN_MARGIN = 10.0          # 地震の候補の開始・終了からこの秒数以内なら「既知」

QUAKES_DIR = app_paths.BATCH_PLOTS_DIR / "quakes"
USU_CSV = QUAKES_DIR / "usu_quakes_24.csv"
ALL_CSV = QUAKES_DIR / "quake_candidates_20250708_20260826.csv"
WORK_DIR = app_paths.OUTPUT_DIR / "template_match"
OUT_DIR = QUAKES_DIR / "template_match"
# ひな形の組の名前(--set)。並列で動かす子のプロセスにも同じ組を使わせるため、環境変数で渡す
SET_ENV = "USU_TEMPLATE_SET"
DEFAULT_SET = "v1"
APP_SET = "app"             # アプリから使う組(今の有珠山リストから作り、日ごと・ひな形ごとに結果を保存する)
GROUP_MIN_CC = 0.55         # ひな形どうしの相関係数がこれ以上なら同じグループにする(平均の距離でまとめる)

# ひな形の組ごとのファイル: v1 は決まったファイル(USU_CSV)から、それ以外は今の有珠山リストから作る
def configure(name):
  global TEMPLATE_SET, TEMPLATES_FILE, DAYS_DIR, TEMPLATE_SOURCE_CSV
  TEMPLATE_SET = name
  TEMPLATES_FILE = WORK_DIR / f"templates_{name}.npz"
  DAYS_DIR = WORK_DIR / f"days_{name}"
  TEMPLATE_SOURCE_CSV = USU_CSV if name == DEFAULT_SET else WORK_DIR / f"usu_list_{name}.csv"

configure(os.environ.get(SET_ENV, DEFAULT_SET))


# --- 波形の読み込み ---

# 期間 [t0, t1) の波形を ZIP から取り出して SAC に変換し、フィルターと間引きをする
#   戻り値 ... ({(拠点, 成分): float32 の配列}, 始まりの日時)(データの無いところは 0)
def load_filtered(t0, t1):
  from obspy import Stream, UTCDateTime, read
  with tempfile.TemporaryDirectory(prefix="UsuMantenViewer_tm_") as work:
    work = Path(work)
    zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(t0, t1))
    # WIN ファイルは1分ずつなので、t0 を含む分のファイルから変換する
    if win_pipeline.zips_to_sacs(zips, t0.replace(second=0, microsecond=0), t1, work,
                                 app_paths.TOOLS_DIR, app_paths.ETC_DIR) == 0:
      return {}, t0
    out = {}
    npts = int(round((t1 - t0).total_seconds() * SAMPLING_RATE))
    for path in win_pipeline.find_sacs(work):
      st = Stream([read(str(path))[0]])
      tr0 = st[0]
      key = (tr0.stats.station, tr0.stats.channel[-1])
      if key[0] not in STATIONS or key[1] not in COMPONENTS:
        continue
      st.merge(method=1, fill_value=0)
      tr = st[0]
      tr.trim(UTCDateTime(t0), UTCDateTime(t1) - tr.stats.delta, pad=True, fill_value=0)
      data = tr.data.astype(np.float64)
      gap = data == 0
      data[~gap] -= np.mean(data[~gap]) if np.any(~gap) else 0
      tr.data = data
      tr.filter("bandpass", freqmin=BAND[0], freqmax=BAND[1], corners=4, zerophase=True)
      d = tr.data[::DECIMATE].astype(np.float32)
      d[gap[::DECIMATE]] = 0
      out[key] = np.pad(d, (0, max(0, npts - len(d))))[:npts]
    return out, t0


# --- ひな形 ---

#   log ... 経過を受け取る関数 / use_cache ... 震源の計算結果のキャッシュ(画面と同じもの)を使う(アプリから)
def build_templates(log=print, use_cache=False):
  import quake_list
  import quake_locate
  from hypocenter import LocalProjection, load_stations
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  if TEMPLATE_SET != DEFAULT_SET:
    n_usu = quake_list.write_usu_candidates(TEMPLATE_SOURCE_CSV)
    log(f"  今の有珠山リスト {n_usu} 件からひな形を作ります: {TEMPLATE_SOURCE_CSV}")
  cands = quake_locate.read_candidates(TEMPLATE_SOURCE_CSV)
  # 有珠山リストで地震以外(発破・未確定)にしたものは、ひな形にしない
  skipped = [c for c in cands if quake_locate.event_type(c) != quake_list.TYPE_QUAKE]
  for c in skipped:
    log(f"  地震ではない種別({quake_locate.event_type(c)})なので使いません: {c['start']}")
  cands = [c for c in cands if quake_locate.event_type(c) == quake_list.TYPE_QUAKE]
  if use_cache:
    locate = lambda *a, **k: quake_locate.locate_all_cached(*a, **k)[0]
  else:
    locate = quake_locate.locate_all

  def load_hour(hour):
    from obspy import Stream, read
    with tempfile.TemporaryDirectory(prefix="UsuMantenViewer_tm_") as work:
      work = Path(work)
      zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(hour, hour + timedelta(hours=1)))
      if win_pipeline.zips_to_sacs(zips, hour, hour + timedelta(hours=1), work,
                                   app_paths.TOOLS_DIR, app_paths.ETC_DIR) == 0:
        return None
      st = Stream()
      for p in win_pipeline.find_sacs(work):
        st += read(str(p))
      return st

  n = int(round(TEMPLATE_LENGTH * SAMPLING_RATE))
  names, data, offsets, info = [], [], [], []
  for cand, r in locate(cands, load_hour, stations, proj,
                        on_result=lambda i, m, c, r, e: log(f"  ひな形 {i}/{m} {c['start']}")):
    p = {nm: r["ref"] + timedelta(seconds=t) for nm, t in r["picks"].items() if nm in STATIONS}
    name = f"{cand['start']:%Y%m%d_%H%M%S}"
    if name in EXCLUDED_TEMPLATES:
      log(f"  除くひな形なので使いません: {cand['start']}({EXCLUDED_TEMPLATES[name]})")
      continue
    if len(p) < MIN_TEMPLATE_STATIONS:
      log(f"  P波が {len(p)} 拠点しかないので使いません: {cand['start']}")
      continue
    first = min(p.values()) - timedelta(seconds=BEFORE_P)
    t0 = first.replace(microsecond=0) - timedelta(seconds=60)
    wave, _ = load_filtered(t0, t0 + timedelta(seconds=180))
    arr = np.zeros((len(STATIONS), len(COMPONENTS), n), np.float32)
    off = np.full(len(STATIONS), -1, np.int64)
    k_first = int(round((first - t0).total_seconds() * SAMPLING_RATE))
    for i, nm in enumerate(STATIONS):
      if nm not in p:
        continue
      start = p[nm] - timedelta(seconds=BEFORE_P)
      k0 = int(round((start - t0).total_seconds() * SAMPLING_RATE))
      ok = True
      for j, c in enumerate(COMPONENTS):
        d = wave.get((nm, c))
        if d is None or not np.any(d[k0:k0 + n]):
          ok = False
          break
        arr[i, j] = d[k0:k0 + n]
      if ok:
        off[i] = k0 - k_first   # 切り出した位置の差そのもの(丸めのずれが出ないように)
    origin = r["ref"] + timedelta(seconds=r["loc"]["origin"]) if r.get("loc") else None
    m = cand["jma"]["M"] if cand["jma"] else None
    names.append(f"{cand['start']:%Y%m%d_%H%M%S}")
    data.append(arr)
    offsets.append(off)
    info.append({"name": names[-1], "first": first.isoformat(), "origin": origin.isoformat() if origin else None,
                 "kind": r["kind"], "jma_m": m, "lat": r["lat"], "lon": r["lon"],
                 "depth": r["loc"]["depth"] if r.get("loc") else None})
  WORK_DIR.mkdir(parents=True, exist_ok=True)
  np.savez_compressed(TEMPLATES_FILE, data=np.array(data), offsets=np.array(offsets),
                      info=json.dumps(info, ensure_ascii=False))

def load_templates():
  with np.load(TEMPLATES_FILE) as z:
    return z["data"], z["offsets"], json.loads(str(z["info"]))


# --- 相関 ---

# 長さ n の窓ごとの、データの (平均を引いた) 二乗和の平方根
def moving_norm(d, n):
  c1 = np.concatenate([[0.0], np.cumsum(d, dtype=np.float64)])
  c2 = np.concatenate([[0.0], np.cumsum(d.astype(np.float64) ** 2)])
  s1 = c1[n:] - c1[:-n]
  s2 = c2[n:] - c2[:-n]
  var = np.maximum(s2 - s1 * s1 / n, 0.0)
  return np.sqrt(var)

# 1日分の波形の、ひな形の長さ n の窓ごとの大きさ(欠測のところは inf にして相関を計算しない)
def day_norms(wave, n):
  norms = {}
  for key, d in wave.items():
    nm = moving_norm(d, n)
    norms[key] = np.where(nm > 1e-6 * (np.median(nm[nm > 0]) if np.any(nm > 0) else 1), nm, np.inf)
  return norms

# 1つのひな形で1日分を探す。戻り値 ... (その日の基準の統計 または None, [検出])
#   検出はひな形ごとのもの(ほかのひな形と近いものをまとめる前)
def template_peaks(wave, t0, norms, tdata, toffs, ti, info):
  n = tdata.shape[-1]
  npts = len(next(iter(wave.values())))
  L = npts - n + 1
  total = np.zeros(L, np.float64)
  count = np.zeros(L, np.int16)
  for si, nm in enumerate(STATIONS):
    o = int(toffs[ti, si])
    if o < 0:
      continue
    for ci, c in enumerate(COMPONENTS):
      d = wave.get((nm, c))
      if d is None:
        continue
      t = tdata[ti, si, ci].astype(np.float64)
      t = t - t.mean()
      tn = np.linalg.norm(t)
      if tn == 0:
        continue
      cc = oaconvolve(d.astype(np.float64), t[::-1] / tn, mode="valid") / norms[(nm, c)]
      # ひな形の始まり(最初の拠点)の位置 k に対して、この成分は k + o から始まる
      seg = cc[o:]
      valid = np.isfinite(seg) & (seg != 0)
      total[:len(seg)] += np.where(valid, seg, 0)
      count[:len(seg)] += valid
  mean = np.where(count >= MIN_CHANNELS, total / np.maximum(count, 1), 0.0)
  used = mean[count >= MIN_CHANNELS]
  if len(used) == 0:
    return None, []
  med = float(np.median(used))
  mad = float(np.median(np.abs(used - med)))
  thr = max(med + THRESHOLD_MAD * mad, MIN_CC)
  stats = {"median": med, "mad": mad, "threshold": thr}
  peaks, _ = find_peaks(mean, height=thr, distance=int(DEDUP_SECONDS * SAMPLING_RATE))
  first = datetime.fromisoformat(info["first"])
  shift = (datetime.fromisoformat(info["origin"]) - first).total_seconds() if info["origin"] else BEFORE_P
  dets = []
  for k in peaks:
    # 振幅の比(ひな形に対する、データの成分ごとの大きさの比の中央値)
    ratios = []
    for si, nm in enumerate(STATIONS):
      o = int(toffs[ti, si])
      if o < 0:
        continue
      for ci, c in enumerate(COMPONENTS):
        d = wave.get((nm, c))
        if d is None:
          continue
        a = np.std(d[k + o:k + o + n])
        b = np.std(tdata[ti, si, ci])
        if a > 0 and b > 0:
          ratios.append(a / b)
    t_first = t0 + timedelta(seconds=k / SAMPLING_RATE)
    dets.append({
      "time": (t_first + timedelta(seconds=shift)).isoformat(timespec="milliseconds"),
      "first": t_first.isoformat(timespec="milliseconds"),
      "template": info["name"], "cc": round(float(mean[k]), 3), "mad_ratio": round((mean[k] - med) / mad, 1) if mad else None,
      "channels": int(count[k]), "amp_ratio": round(float(np.median(ratios)), 3) if ratios else None})
  return stats, dets

# ひな形をまたいで、近い検出は相関係数が最も高いものだけ残す(似ていたひな形の数を n_templates に書く)
def merge_detections(dets):
  dets = sorted(dets, key=lambda x: -x["cc"])
  kept = []
  for d in dets:
    t = datetime.fromisoformat(d["time"])
    if all(abs((t - datetime.fromisoformat(k["time"])).total_seconds()) > DEDUP_SECONDS for k in kept):
      d = dict(d)
      d["n_templates"] = sum(1 for x in dets if abs((t - datetime.fromisoformat(x["time"])).total_seconds()) <= DEDUP_SECONDS)
      kept.append(d)
  return sorted(kept, key=lambda x: x["time"])

# 1日分の検出(ひな形の組ごとに、1日分をまとめて保存する。--set v1 など)
def scan_day(day):
  path = DAYS_DIR / f"{day:%Y%m%d}.json"
  if path.exists():
    return json.loads(path.read_text(encoding="utf-8"))
  tdata, toffs, tinfo = load_templates()
  wave, t0 = load_filtered(day, day + timedelta(days=1))
  result = {"day": f"{day:%Y-%m-%d}", "detections": [], "stats": {}}
  if wave:
    norms = day_norms(wave, tdata.shape[-1])
    dets = []
    for ti, info in enumerate(tinfo):
      stats, found = template_peaks(wave, t0, norms, tdata, toffs, ti, info)
      if stats is not None:
        result["stats"][info["name"]] = stats
      dets += found
    result["detections"] = merge_detections(dets)
  DAYS_DIR.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
  return result

# ひな形を見分ける名前: 「ひな形の名前_中身の短いハッシュ」。到着時刻を直すなどで中身が変われば、別のひな形として計算し直す
def template_keys(tdata, toffs, tinfo):
  return [f"{info['name']}_{hashlib.md5(tdata[i].tobytes() + toffs[i].tobytes()).hexdigest()[:8]}"
          for i, info in enumerate(tinfo)]

# 1日分の検出(日ごと・ひな形ごとに保存し、まだ計算していないひな形の分だけ計算する。--set app)
#   戻り値 ... {"day", "detections"(ひな形をまたいでまとめたもの), "computed"(この日に計算したひな形の数), "no_data"}
#   波形の無い日は保存しない(あとで ZIP を取ってきたときに計算できるように)
def scan_day_cached(day):
  path = DAYS_DIR / f"{day:%Y%m%d}.json"
  tdata, toffs, tinfo = load_templates()
  keys = template_keys(tdata, toffs, tinfo)
  cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"day": f"{day:%Y-%m-%d}", "templates": {}}
  missing = [i for i, k in enumerate(keys) if k not in cache["templates"]]
  if missing:
    wave, t0 = load_filtered(day, day + timedelta(days=1))
    if not wave:
      return {"day": f"{day:%Y-%m-%d}", "detections": [], "computed": 0, "no_data": True}
    norms = day_norms(wave, tdata.shape[-1])
    for ti in missing:
      stats, found = template_peaks(wave, t0, norms, tdata, toffs, ti, tinfo[ti])
      cache["templates"][keys[ti]] = {"stats": stats, "detections": found}
    # 今のひな形に無いもの(リストから外したもの・中身が変わる前のもの)は消す
    cache["templates"] = {k: v for k, v in cache["templates"].items() if k in keys}
    DAYS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
  dets = [d for k in keys for d in cache["templates"][k]["detections"]]
  return {"day": cache["day"], "detections": merge_detections(dets), "computed": len(missing), "no_data": False}


# アプリの「似た波形の地震さがし」の状況。戻り値 ... {"templates": 前回のひな形の数(無ければ None), "groups", "days": [保存した日]}
#   グループの数は、前回の一覧のファイル(groups_app.json)から読む(ひな形の似かたは計算し直さない)
def app_summary():
  templates_file = WORK_DIR / f"templates_{APP_SET}.npz"
  groups_file = WORK_DIR / f"groups_{APP_SET}.json"
  days_dir = WORK_DIR / f"days_{APP_SET}"
  n = None
  if templates_file.exists():
    try:
      with np.load(templates_file) as z:
        n = len(json.loads(str(z["info"])))
    except Exception:
      n = None
  groups = 0
  if groups_file.exists():
    try:
      groups = len({g for g in json.loads(groups_file.read_text(encoding="utf-8")).values() if g})
    except Exception:
      groups = 0
  days = sorted(datetime.strptime(p.stem, "%Y%m%d") for p in days_dir.glob("*.json")) if days_dir.exists() else []
  return {"templates": n, "groups": groups, "days": days}

# --- ひな形のグループ ---

# ひな形どうしの似かた: ひな形 i を、ひな形 j の波形(拠点の時刻差を保って並べたもの)に当てたときの、平均した相関係数の最大
#   探すときと同じ計算。戻り値 ... (N, N) の行列(i と j を入れ替えた大きいほう。自分どうしは 1)
def template_similarity(tdata, toffs):
  N, n = len(tdata), tdata.shape[-1]
  pad = int(2 * SAMPLING_RATE)
  S = np.eye(N)
  for j in range(N):
    L = int(toffs[j][toffs[j] >= 0].max()) + n + 2 * pad
    wave = {}
    for si, nm in enumerate(STATIONS):
      if toffs[j, si] < 0:
        continue
      for ci, c in enumerate(COMPONENTS):
        a = np.zeros(L, np.float32)
        a[pad + toffs[j, si]:pad + toffs[j, si] + n] = tdata[j, si, ci]
        wave[(nm, c)] = a
    norms = {k: np.where(v > 1e-9, v, np.inf) for k, v in ((k, moving_norm(d, n)) for k, d in wave.items())}
    for i in range(N):
      if i == j:
        continue
      total = count = None
      for si, nm in enumerate(STATIONS):
        o = int(toffs[i, si])
        if o < 0:
          continue
        for ci, c in enumerate(COMPONENTS):
          d = wave.get((nm, c))
          t = tdata[i, si, ci].astype(np.float64)
          t = t - t.mean()
          tn = np.linalg.norm(t)
          if d is None or tn == 0:
            continue
          cc = oaconvolve(d.astype(np.float64), t[::-1] / tn, mode="valid") / norms[(nm, c)]
          seg = cc[o:]
          if total is None:
            total, count = np.zeros(len(cc)), np.zeros(len(cc))
          valid = np.isfinite(seg) & (seg != 0)
          total[:len(seg)] += np.where(valid, seg, 0)
          count[:len(seg)] += valid
      if total is not None:
        mean = np.where(count >= MIN_CHANNELS, total / np.maximum(count, 1), 0.0)
        S[i, j] = max(S[i, j], float(mean.max()))
  return np.maximum(S, S.T)

# よく似たひな形をグループにまとめる(平均の距離。相関係数 GROUP_MIN_CC 以上が目安)
#   戻り値 ... {ひな形の名前: "G1" など}。2件以上のグループを件数の多い順(同じなら古い順)に G1, G2, ... とし、1件だけのものは ""
def template_groups(tdata, toffs, tinfo, min_cc=GROUP_MIN_CC):
  from scipy.cluster.hierarchy import fcluster, linkage
  from scipy.spatial.distance import squareform
  names = [i["name"] for i in tinfo]
  if len(names) < 2:
    return {nm: "" for nm in names}
  S = template_similarity(tdata, toffs)
  D = np.clip(1.0 - S, 0, None)
  np.fill_diagonal(D, 0)
  labels = fcluster(linkage(squareform(D, checks=False), "average"), t=1.0 - min_cc, criterion="distance")
  members = {}
  for nm, lb in zip(names, labels):
    members.setdefault(lb, []).append(nm)
  multi = sorted((m for m in members.values() if len(m) > 1), key=lambda m: (-len(m), min(m)))
  out = {nm: "" for nm in names}
  for g, m in enumerate(multi, 1):
    for nm in m:
      out[nm] = f"G{g}"
  return out


# --- 既知の候補との照合と出力 ---

def known_intervals():
  import quake_locate
  out = []
  # 地震の候補は、全期間の一覧と、その後に検出した期間の一覧(quake_candidates_*.csv)のすべて
  sources = [(ALL_CSV, "地震の候補")] + [(p, "地震の候補") for p in sorted(QUAKES_DIR.glob("quake_candidates_*.csv"))
                                          if p != ALL_CSV]
  for path, label in sources + [(TEMPLATE_SOURCE_CSV, "有珠山リスト")]:
    if path.exists():
      for c in quake_locate.read_candidates(path):
        out.append((c["start"] - timedelta(seconds=KNOWN_MARGIN), c["end"] + timedelta(seconds=KNOWN_MARGIN), label,
                    c["start"]))
  return out

def match_known(t, intervals):
  labels = [(label, start) for a, b, label, start in intervals if a <= t <= b]
  if any(l == "有珠山リスト" for l, _ in labels):
    return "有珠山リスト", min(s for l, s in labels if l == "有珠山リスト")
  if labels:
    return labels[0]
  return "", None

# M の目安(ひな形の M + log10(振幅の比))。ひな形の M が無ければ ""
def magnitude_text(d, m_of):
  m = m_of.get(d["template"])
  return f"{m + np.log10(d['amp_ratio']):.1f}" if m is not None and d["amp_ratio"] else ""

#   groups ... {ひな形の名前: グループ}(None なら「グループ」の列を書かない。--set v1 などの今までの形)
def write_csv(path, rows, tinfo, groups=None):
  m_of = {i["name"]: i["jma_m"] for i in tinfo}
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["時刻(推定の発生時刻)", "相関係数", "MAD の倍数", "成分の数", "似ているひな形"]
               + (["グループ"] if groups is not None else [])
               + ["ひな形の数", "振幅の比", "M の目安", "既知", "既知の候補の開始"])
    for d in rows:
      w.writerow([d["time"].replace("T", " "), d["cc"], d["mad_ratio"], d["channels"], d["template"]]
                 + ([groups.get(d["template"], "")] if groups is not None else [])
                 + [d["n_templates"], d["amp_ratio"], magnitude_text(d, m_of), d["known"], d["known_start"] or ""])

# 地震の候補の震源の画面(⌘O)で開ける一覧(quake_locate.read_candidates が読む形)を書く
#   有珠山リストにすでにあるものは入れない。開始は最初の拠点の P波の CANDIDATE_BEFORE 秒前、終了は CANDIDATE_AFTER 秒後
#   その画面で確かめて、⌘U で有珠山リストに加える
CANDIDATE_BEFORE = 5.0
CANDIDATE_AFTER = 20.0
CANDIDATE_FIELDS = ["開始", "終了", "メモ", "相関係数", "似ているひな形", "グループ", "M の目安", "既知"]
def write_candidates(path, rows, tinfo, groups):
  m_of = {i["name"]: i["jma_m"] for i in tinfo}
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=CANDIDATE_FIELDS)
    w.writeheader()
    for d in rows:
      if d["known"] == "有珠山リスト":
        continue
      first = datetime.fromisoformat(d["first"])
      start, end = first - timedelta(seconds=CANDIDATE_BEFORE), first + timedelta(seconds=CANDIDATE_AFTER)
      group = groups.get(d["template"], "")
      w.writerow({"開始": f"{start:%Y-%m-%d %H:%M:%S}", "終了": f"{end:%H:%M:%S}",
                  "メモ": f"似た波形: {d['template']}{'(' + group + ')' if group else ''} 相関 {d['cc']:.2f}",
                  "相関係数": d["cc"], "似ているひな形": d["template"], "グループ": group,
                  "M の目安": magnitude_text(d, m_of), "既知": d["known"]})

# 確かめ用の一覧(xlsx)。震源の画面で開ける一覧と同じ列に、判定を書く列と、確認用の画像・気象庁の観測点の
# 1時間の連続波形画像(防災科研のページ)へのリンクを足す。前の期間の一覧(detections_20250708_20260826.xlsx)と同じ形
REVIEW_LINK_STATIONS = [("V.KBBY", "KBBY 北屏風山"), ("V.USSW", "USSW 昭和新山南麓"), ("V.USUA", "USUA 有珠南山麓")]
REVIEW_FIELDS = CANDIDATE_FIELDS + ["判定", "その他", "画像"] + [label for _code, label in REVIEW_LINK_STATIONS]

# 確認用の画像の名前。名前順が、一覧を相関係数・時刻で並べた順と同じになる
def image_name(d):
  return f"cc{d['cc']:.3f}_{datetime.fromisoformat(d['time']):%Y%m%d_%H%M%S}.png"

# 判定を書き込んだ一覧(xlsx。1枚目のシートの1列目が時刻)の時刻
def judged_times(path):
  import openpyxl
  wb = openpyxl.load_workbook(path, read_only=True)
  times = []
  for r in wb.worksheets[0].iter_rows(min_row=2, values_only=True):
    t = r[0] if r else None
    if isinstance(t, str):
      try:
        t = datetime.fromisoformat(t)
      except ValueError:
        t = None
    if isinstance(t, datetime):
      times.append(t)
  wb.close()
  return times

# 確かめ用の一覧に入れる検出(有珠山リストにあるもの、判定済みの一覧に時刻が DEDUP_SECONDS 秒より近いものがあるものは除く)
def review_rows(rows, judged=()):
  judged = sorted(judged)
  def is_judged(t):
    return any(abs((j - t).total_seconds()) < DEDUP_SECONDS for j in judged)
  return [d for d in rows if d["known"] != "有珠山リスト" and not is_judged(datetime.fromisoformat(d["time"]))]

# 確かめ用の一覧を書く。すでにあるファイルは上書きしない(判定を書いているかもしれないので、_2 などの名前にする)
#   img_dir ... 確認用の画像のフォルダ(一覧からの相対パスでリンクする)。戻り値 ... 書いたファイルのパス
def write_review_xlsx(path, rows, tinfo, groups, img_dir):
  import openpyxl
  from openpyxl.styles import Font
  from external_stations import image_url
  path = Path(path)
  base, n = path, 2
  while path.exists():
    path = base.with_name(f"{base.stem}_{n}{base.suffix}")
    n += 1
  m_of = {i["name"]: i["jma_m"] for i in tinfo}
  link_font = Font(color="0563C1", underline="single")
  wb = openpyxl.Workbook()
  ws = wb.active
  ws.title = path.stem[:31]
  ws.append(REVIEW_FIELDS)
  for d in rows:
    first = datetime.fromisoformat(d["first"])
    t = datetime.fromisoformat(d["time"])
    group = groups.get(d["template"], "")
    m = magnitude_text(d, m_of)
    ws.append([(first - timedelta(seconds=CANDIDATE_BEFORE)).replace(microsecond=0),
               (first + timedelta(seconds=CANDIDATE_AFTER)).replace(microsecond=0).time(),
               f"似た波形: {d['template']}{'(' + group + ')' if group else ''} 相関 {d['cc']:.2f}",
               d["cc"], d["template"], group or None, float(m) if m else None, d["known"] or None, None, None])
    r = ws.max_row
    img = Path(img_dir) / image_name(d)
    if img.exists():
      c = ws.cell(r, len(CANDIDATE_FIELDS) + 3, image_name(d))
      c.hyperlink, c.font = f"{Path(img_dir).name}/{image_name(d)}", link_font
    for k, (code, _label) in enumerate(REVIEW_LINK_STATIONS):
      c = ws.cell(r, len(CANDIDATE_FIELDS) + 4 + k, f"{t:%m/%d %H時 %M分の段}")
      c.hyperlink, c.font = image_url(code, t), link_font
  ws.column_dimensions["A"].width = 20
  ws.column_dimensions["C"].width = 36
  ws.column_dimensions["E"].width = 17
  for col in "KLMN":
    ws.column_dimensions[col].width = 26 if col == "K" else 20
  for row in ws.iter_rows(min_row=2, max_col=1):
    row[0].number_format = "yyyy-mm-dd hh:mm:ss"
  ws.freeze_panes = "B2"
  ws.auto_filter.ref = ws.dimensions
  wb.save(path)
  return path

# 新しく見つかったものの確認用の画像(上下動の波形と、似ているひな形を重ねる)
def save_image(path, det, tdata, toffs, tinfo):
  from matplotlib.figure import Figure
  ti = next(i for i, x in enumerate(tinfo) if x["name"] == det["template"])
  first = datetime.fromisoformat(det["first"])
  t0 = first.replace(microsecond=0) - timedelta(seconds=20)
  wave, _ = load_filtered(t0, t0 + timedelta(seconds=40))
  n = tdata.shape[-1]
  fig = Figure(figsize=(9, 7))
  axes = fig.subplots(len(STATIONS) * 2, 1, sharex=True)
  tt = np.arange(int(40 * SAMPLING_RATE)) / SAMPLING_RATE - 20 + (first - first.replace(microsecond=0)).total_seconds() * 0
  shift0 = (first - t0).total_seconds()
  row = 0
  for si, nm in enumerate(STATIONS):
    for c in ("U", "N"):
      ax = axes[row]
      row += 1
      d = wave.get((nm, c))
      if d is not None:
        ax.plot(np.arange(len(d)) / SAMPLING_RATE - shift0, d, color="k", lw=0.5)
        o = int(toffs[ti, si])
        if o >= 0:
          tpl = tdata[ti, si, COMPONENTS.index(c)]
          seg = d[int(round(shift0 * SAMPLING_RATE)) + o:][:n]
          scale = np.std(seg) / np.std(tpl) if np.std(tpl) > 0 and len(seg) == n else 1
          ax.plot(o / SAMPLING_RATE + np.arange(n) / SAMPLING_RATE, tpl * scale, color="tab:red", lw=0.7)
      ax.set_ylabel(f"{station_names.short(nm)}\n{c}", fontsize=7, rotation=0, ha="right", va="center")
      ax.set_yticks([])
  axes[-1].set_xlabel("ひな形の始まり(最初の拠点の P波の0.5秒前)からの秒")
  axes[-1].set_xlim(-10, 20)
  fig.suptitle(f"{det['time'].replace('T', ' ')} 相関 {det['cc']}(MAD×{det['mad_ratio']}) "
               f"ひな形 {det['template']} 振幅比 {det['amp_ratio']}  {det['known'] or '新しい'}", fontsize=9)
  fig.savefig(path, dpi=100)


# 一覧に残す検出をえらぶ(既知の候補と照合し、2拠点だけの弱いものと、除くひな形に似ているものを外す)
#   戻り値 ... (残す検出, 除くひな形に似ていたので除いた検出)
def select_rows(rows):
  intervals = known_intervals()
  for d in rows:
    d["known"], start = match_known(datetime.fromisoformat(d["time"]), intervals)
    d["known_start"] = f"{start:%Y-%m-%d %H:%M:%S}" if start else None
  # 2拠点だけの検出は雑音が多いので、相関が高いものと既知のものだけ残す
  rows = [d for d in rows if d["known"] or d["channels"] > FEW_CHANNELS or d["cc"] >= FEW_CHANNELS_MIN_CC]
  # 除くひな形に一番似ているものは除く(発破などの可能性が高い)
  excluded = [d for d in rows if d["template"] in EXCLUDED_TEMPLATES]
  return [d for d in rows if d["template"] not in EXCLUDED_TEMPLATES], excluded

# アプリのメンテナンス画面の「似た波形の地震さがし」(TODO 37)。--set app と同じ
#   start, end ... 期間(datetime の日) / jobs ... 並列で計算するプロセスの数 / images ... 新しく見つかったものの画像を作る
#   log ... 経過を受け取る関数 / cancelled ... 中止するなら True を返す関数 / progress ... (済んだ数, 全体の数, 段階) を受け取る関数
#   review ... 確かめ用の一覧(review_<期間>_app.xlsx。画像と連続波形画像へのリンク付き)も作る。その分の画像は無ければ作る
#   judged ... 判定を書き込んだ一覧(xlsx)。確かめ用の一覧から、そこにあるものを除く
#   戻り値 ... {"rows", "detections_csv", "candidates_csv", "review_xlsx", "groups", "templates", "new"}、中止したら None
#   判定を書き込んだ前の期間の一覧(detections_20250708_20260826.xlsx)には触らない(ファイル名の最後に _app を付ける)
def run(start, end, jobs=3, images=False, log=print, cancelled=lambda: False, progress=None, review=False, judged=None):
  os.environ[SET_ENV] = APP_SET   # 並列で動かす子のプロセスも同じ組を使う
  configure(APP_SET)
  progress = progress or (lambda *a: None)
  progress(0, 0, "今の有珠山リストからひな形を作っています")
  log("=== 似た波形の地震さがし(テンプレートマッチング)を開始します ===")
  build_templates(log=log, use_cache=True)
  if cancelled():
    log("=== 中止しました ===")
    return None
  tdata, toffs, tinfo = load_templates()
  groups = template_groups(tdata, toffs, tinfo)
  (WORK_DIR / f"groups_{APP_SET}.json").write_text(json.dumps(groups, ensure_ascii=False, indent=1), encoding="utf-8")
  n_groups = len({g for g in groups.values() if g})
  log(f"  ひな形 {len(tinfo)} 件(よく似たもののグループ {n_groups} 組)")
  for g in sorted({g for g in groups.values() if g}, key=lambda x: int(x[1:])):
    log(f"    {g}: {'・'.join(nm for nm, x in groups.items() if x == g)}")
  days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
  log(f"  期間: {start:%Y-%m-%d}〜{end:%Y-%m-%d}({len(days)}日)、並列 {jobs}")
  rows, done, computed, no_data = [], 0, 0, 0
  ex = ProcessPoolExecutor(max_workers=jobs)
  try:
    futures = {ex.submit(scan_day_cached, d): d for d in days}
    progress(0, len(days), "日ごとに探しています(日)")
    for f in as_completed(futures):
      if cancelled():
        ex.shutdown(wait=False, cancel_futures=True)
        log("=== 中止しました(計算の済んだ日の結果は保存してあります) ===")
        return None
      res = f.result()
      done += 1
      rows += res["detections"]
      computed += bool(res["computed"])
      no_data += res["no_data"]
      if res["computed"]:
        log(f"  {res['day']}: {len(res['detections'])} 件(ひな形 {res['computed']} 件分を計算)")
      progress(done, len(days), "日ごとに探しています(日)")
  finally:
    ex.shutdown(wait=True, cancel_futures=True)
  log(f"  計算した日 {computed} 日・保存した結果を使った日 {len(days) - computed - no_data} 日・波形の無い日 {no_data} 日")
  rows, excluded = select_rows(sorted(rows, key=lambda d: d["time"]))
  if excluded:
    log(f"  除くひな形に似ているので除いた検出: {len(excluded)} 件")
  OUT_DIR.mkdir(parents=True, exist_ok=True)
  tag = f"{start:%Y%m%d}_{end:%Y%m%d}_{APP_SET}"
  det_csv = OUT_DIR / f"detections_{tag}.csv"
  cand_csv = OUT_DIR / f"candidates_{tag}.csv"
  write_csv(det_csv, rows, tinfo, groups)
  write_candidates(cand_csv, rows, tinfo, groups)
  new = [d for d in rows if not d["known"]]
  log(f"  検出 {len(rows)} 件(有珠山リスト {sum(d['known'] == '有珠山リスト' for d in rows)}・"
      f"地震の候補 {sum(d['known'] == '地震の候補' for d in rows)}・新しい {len(new)})")
  log(f"  一覧: {det_csv}")
  log(f"  地震の候補の震源の画面で開く一覧: {cand_csv}")
  img_dir = OUT_DIR / f"images_{APP_SET}"
  to_review = review_rows(rows, judged_times(judged) if judged else ()) if review else []
  if review and judged:
    log(f"  確かめ用の一覧: 判定済みの一覧({Path(judged).name})にあるものを除いて {len(to_review)} 件")
  # 画像を作るもの: images なら新しいものすべて、review なら確かめ用の一覧のうち画像の無いもの
  targets = new if images else [d for d in to_review if not (img_dir / image_name(d)).exists()]
  stopped = False
  if targets:
    img_dir.mkdir(exist_ok=True)
    for i, d in enumerate(targets, 1):
      if cancelled():
        log("=== 画像を作る途中で中止しました ===")
        stopped = True
        break
      progress(i, len(targets), "新しく見つかったものの画像を作っています(枚)")
      save_image(img_dir / image_name(d), d, tdata, toffs, tinfo)
    log(f"  画像: {img_dir}")
  review_xlsx = None
  if review and not stopped:
    review_xlsx = write_review_xlsx(OUT_DIR / f"review_{tag}.xlsx", to_review, tinfo, groups, img_dir)
    log(f"  確かめ用の一覧(画像と連続波形画像へのリンク付き): {review_xlsx}")
  log("=== 似た波形の地震さがしが終わりました ===")
  return {"rows": rows, "detections_csv": det_csv, "candidates_csv": cand_csv, "review_xlsx": review_xlsx, "groups": groups,
          "templates": len(tinfo), "new": len(new)}

def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("-s", "--start", required=True)
  ap.add_argument("-e", "--end", required=True)
  ap.add_argument("-j", "--jobs", type=int, default=3)
  ap.add_argument("--rebuild-templates", action="store_true")
  ap.add_argument("--images", action="store_true", help="新しく見つかったものの画像を作る(images フォルダ)")
  ap.add_argument("--review", action="store_true",
                  help="--set app のとき、確かめ用の一覧(review_<期間>_app.xlsx。画像と気象庁の観測点の1時間の連続波形画像へのリンク付き)も作る")
  ap.add_argument("--judged", help="--review の一覧から、この判定済みの一覧(xlsx)にあるものを除く")
  ap.add_argument("--set", default=DEFAULT_SET,
                  help="ひな形の組の名前(v1 以外は今の有珠山リストから作り、別のファイルに保存する。app はアプリと同じ処理)")
  args = ap.parse_args()
  if args.set == APP_SET:
    import matplotlib
    matplotlib.use("Agg")
    import plot_common
    plot_common.setup_japanese_font()
    run(datetime.strptime(args.start, "%Y%m%d"), datetime.strptime(args.end, "%Y%m%d"), jobs=args.jobs, images=args.images,
        review=args.review, judged=args.judged)
    return
  os.environ[SET_ENV] = args.set   # 並列で動かす子のプロセスも同じ組を使う
  configure(args.set)
  print(f"ひな形の組: {TEMPLATE_SET}({TEMPLATES_FILE.name}、日ごとの結果 {DAYS_DIR.name})")
  import matplotlib
  matplotlib.use("Agg")
  import plot_common
  plot_common.setup_japanese_font()
  if args.rebuild_templates or not TEMPLATES_FILE.exists():
    print("ひな形を作ります")
    build_templates()
  tdata, toffs, tinfo = load_templates()
  print(f"ひな形 {len(tinfo)} 件")
  s = datetime.strptime(args.start, "%Y%m%d")
  e = datetime.strptime(args.end, "%Y%m%d")
  days = [s + timedelta(days=i) for i in range((e - s).days + 1)]
  rows = []
  with ProcessPoolExecutor(max_workers=args.jobs) as ex:
    for res in ex.map(scan_day, days):
      print(f"  {res['day']}: {len(res['detections'])} 件", flush=True)
      rows += res["detections"]
  rows, excluded = select_rows(rows)
  if excluded:
    print(f"除くひな形に似ているので除いた検出: {len(excluded)} 件")
  OUT_DIR.mkdir(parents=True, exist_ok=True)
  suffix = "" if TEMPLATE_SET == DEFAULT_SET else f"_{TEMPLATE_SET}"
  out = OUT_DIR / f"detections_{args.start}_{args.end}{suffix}.csv"
  write_csv(out, rows, tinfo)
  new = [d for d in rows if not d["known"]]
  print(f"検出 {len(rows)} 件(有珠山リスト {sum(d['known'] == '有珠山リスト' for d in rows)}・"
        f"地震の候補 {sum(d['known'] == '地震の候補' for d in rows)}・新しい {len(new)}): {out}")
  if args.images:
    img_dir = OUT_DIR / f"images{suffix}"
    img_dir.mkdir(exist_ok=True)
    # 名前は相関係数(3桁)と時刻。名前順が、一覧を相関係数・時刻で並べた順と同じになる
    for d in new:
      t = datetime.fromisoformat(d["time"])
      save_image(img_dir / f"cc{d['cc']:.3f}_{t:%Y%m%d_%H%M%S}.png", d, tdata, toffs, tinfo)


if __name__ == "__main__":
  main()
