# template_match.py(試作)
# 有珠山リストの地震の波形をひな形(テンプレート)にして、記録全体から似た波形の地震を探す
#   1. 有珠山リストの地震ごとに、満点の拠点の P波の到着時刻の 0.5秒前から 4.5秒間を、3成分ともひな形にする
#      (到着時刻は quake_locate と同じ。震源推定の画面で保存したものがあればそれを使う)
#   2. 1日ずつ、全拠点・全成分の波形とひな形の相関係数(正規化した相互相関)を計算し、
#      拠点ごとの到着時刻の差を保ったまま平均する
#   3. 平均した相関係数が、その日の中央値から MAD(中央値からの差の中央値)の THRESHOLD_MAD 倍を超えたところを検出とする
#   波形は帯域通過フィルター(BAND)のあと、SAMPLING_RATE に間引いて使う
#   使い方: python3 template_match.py -s 20251210 -e 20251217 [-j 3]
import argparse
import csv
import json
import sys
import tempfile
import warnings
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks, oaconvolve

import app_paths
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
TEMPLATES_FILE = WORK_DIR / "templates_v1.npz"
DAYS_DIR = WORK_DIR / "days_v1"
OUT_DIR = QUAKES_DIR / "template_match"


# --- 波形の読み込み ---

# 期間 [t0, t1) の波形を ZIP から取り出して SAC に変換し、フィルターと間引きをする
#   戻り値 ... ({(拠点, 成分): float32 の配列}, 始まりの日時)(データの無いところは 0)
def load_filtered(t0, t1):
  from obspy import Stream, UTCDateTime, read
  with tempfile.TemporaryDirectory(prefix="UsuMantenViewer_tm_") as work:
    work = Path(work)
    zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(t0, t1))
    # WIN ファイルは1分ずつなので、t0 を含む分のファイルから取り出す
    if win_pipeline.extract_wins_from_zips(zips, t0.replace(second=0, microsecond=0), t1, work) == 0:
      return {}, t0
    win_pipeline.convert_wins_to_sacs(work, app_paths.TOOLS_DIR, app_paths.ETC_DIR)
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

def build_templates():
  import quake_locate
  from hypocenter import LocalProjection, load_stations
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  cands = quake_locate.read_candidates(USU_CSV)

  def load_hour(hour):
    from obspy import Stream, read
    with tempfile.TemporaryDirectory(prefix="UsuMantenViewer_tm_") as work:
      work = Path(work)
      zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(hour, hour + timedelta(hours=1)))
      if win_pipeline.extract_wins_from_zips(zips, hour, hour + timedelta(hours=1), work) == 0:
        return None
      win_pipeline.convert_wins_to_sacs(work, app_paths.TOOLS_DIR, app_paths.ETC_DIR)
      st = Stream()
      for p in win_pipeline.find_sacs(work):
        st += read(str(p))
      return st

  n = int(round(TEMPLATE_LENGTH * SAMPLING_RATE))
  names, data, offsets, info = [], [], [], []
  for cand, r in quake_locate.locate_all(cands, load_hour, stations, proj,
                                         on_result=lambda i, m, c, r, e: print(f"  ひな形 {i}/{m} {c['start']}", flush=True)):
    p = {nm: r["ref"] + timedelta(seconds=t) for nm, t in r["picks"].items() if nm in STATIONS}
    name = f"{cand['start']:%Y%m%d_%H%M%S}"
    if name in EXCLUDED_TEMPLATES:
      print(f"  除くひな形なので使いません: {cand['start']}({EXCLUDED_TEMPLATES[name]})")
      continue
    if len(p) < MIN_TEMPLATE_STATIONS:
      print(f"  P波が {len(p)} 拠点しかないので使いません: {cand['start']}")
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

# 1日分の検出
def scan_day(day):
  path = DAYS_DIR / f"{day:%Y%m%d}.json"
  if path.exists():
    return json.loads(path.read_text(encoding="utf-8"))
  tdata, toffs, tinfo = load_templates()
  wave, t0 = load_filtered(day, day + timedelta(days=1))
  result = {"day": f"{day:%Y-%m-%d}", "detections": [], "stats": {}}
  if wave:
    n = tdata.shape[-1]
    npts = len(next(iter(wave.values())))
    norms = {}
    for key, d in wave.items():
      nm = moving_norm(d, n)
      # 欠測(0 が続くところ)は相関を計算しない
      norms[key] = np.where(nm > 1e-6 * (np.median(nm[nm > 0]) if np.any(nm > 0) else 1), nm, np.inf)
    L = npts - n + 1
    for ti, info in enumerate(tinfo):
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
        continue
      med = float(np.median(used))
      mad = float(np.median(np.abs(used - med)))
      thr = max(med + THRESHOLD_MAD * mad, MIN_CC)
      result["stats"][info["name"]] = {"median": med, "mad": mad, "threshold": thr}
      peaks, _ = find_peaks(mean, height=thr, distance=int(DEDUP_SECONDS * SAMPLING_RATE))
      first = datetime.fromisoformat(info["first"])
      shift = (datetime.fromisoformat(info["origin"]) - first).total_seconds() if info["origin"] else BEFORE_P
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
        result["detections"].append({
          "time": (t_first + timedelta(seconds=shift)).isoformat(timespec="milliseconds"),
          "first": t_first.isoformat(timespec="milliseconds"),
          "template": info["name"], "cc": round(float(mean[k]), 3), "mad_ratio": round((mean[k] - med) / mad, 1) if mad else None,
          "channels": int(count[k]), "amp_ratio": round(float(np.median(ratios)), 3) if ratios else None})
  # テンプレートをまたいで、近い検出は相関係数が最も高いものだけ残す
  dets = sorted(result["detections"], key=lambda x: -x["cc"])
  kept = []
  for d in dets:
    t = datetime.fromisoformat(d["time"])
    if all(abs((t - datetime.fromisoformat(k["time"])).total_seconds()) > DEDUP_SECONDS for k in kept):
      d["n_templates"] = sum(1 for x in dets if abs((t - datetime.fromisoformat(x["time"])).total_seconds()) <= DEDUP_SECONDS)
      kept.append(d)
  result["detections"] = sorted(kept, key=lambda x: x["time"])
  DAYS_DIR.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
  return result


# --- 既知の候補との照合と出力 ---

def known_intervals():
  import quake_locate
  out = []
  for path, label in ((ALL_CSV, "地震の候補"), (USU_CSV, "有珠山リスト")):
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

def write_csv(path, rows, tinfo):
  m_of = {i["name"]: i["jma_m"] for i in tinfo}
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["時刻(推定の発生時刻)", "相関係数", "MAD の倍数", "成分の数", "似ているひな形", "ひな形の数",
                "振幅の比", "M の目安", "既知", "既知の候補の開始"])
    for d in rows:
      m = m_of.get(d["template"])
      mag = f"{m + np.log10(d['amp_ratio']):.1f}" if m is not None and d["amp_ratio"] else ""
      w.writerow([d["time"].replace("T", " "), d["cc"], d["mad_ratio"], d["channels"], d["template"],
                  d["n_templates"], d["amp_ratio"], mag, d["known"], d["known_start"] or ""])

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
      ax.set_ylabel(f"{nm}\n{c}", fontsize=7, rotation=0, ha="right", va="center")
      ax.set_yticks([])
  axes[-1].set_xlabel("ひな形の始まり(最初の拠点の P波の0.5秒前)からの秒")
  axes[-1].set_xlim(-10, 20)
  fig.suptitle(f"{det['time'].replace('T', ' ')} 相関 {det['cc']}(MAD×{det['mad_ratio']}) "
               f"ひな形 {det['template']} 振幅比 {det['amp_ratio']}  {det['known'] or '新しい'}", fontsize=9)
  fig.savefig(path, dpi=100)


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("-s", "--start", required=True)
  ap.add_argument("-e", "--end", required=True)
  ap.add_argument("-j", "--jobs", type=int, default=3)
  ap.add_argument("--rebuild-templates", action="store_true")
  ap.add_argument("--images", action="store_true", help="新しく見つかったものの画像を作る(images フォルダ)")
  args = ap.parse_args()
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
  intervals = known_intervals()
  for d in rows:
    d["known"], start = match_known(datetime.fromisoformat(d["time"]), intervals)
    d["known_start"] = f"{start:%Y-%m-%d %H:%M:%S}" if start else None
  # 2拠点だけの検出は雑音が多いので、相関が高いものと既知のものだけ残す
  rows = [d for d in rows if d["known"] or d["channels"] > FEW_CHANNELS or d["cc"] >= FEW_CHANNELS_MIN_CC]
  # 除くひな形に一番似ているものは除く(発破などの可能性が高い)
  excluded = [d for d in rows if d["template"] in EXCLUDED_TEMPLATES]
  rows = [d for d in rows if d["template"] not in EXCLUDED_TEMPLATES]
  if excluded:
    print(f"除くひな形に似ているので除いた検出: {len(excluded)} 件")
  OUT_DIR.mkdir(parents=True, exist_ok=True)
  out = OUT_DIR / f"detections_{args.start}_{args.end}.csv"
  write_csv(out, rows, tinfo)
  new = [d for d in rows if not d["known"]]
  print(f"検出 {len(rows)} 件(有珠山リスト {sum(d['known'] == '有珠山リスト' for d in rows)}・"
        f"地震の候補 {sum(d['known'] == '地震の候補' for d in rows)}・新しい {len(new)}): {out}")
  if args.images:
    img_dir = OUT_DIR / "images"
    img_dir.mkdir(exist_ok=True)
    # 名前は相関係数(3桁)と時刻。名前順が、一覧を相関係数・時刻で並べた順と同じになる
    for d in new:
      t = datetime.fromisoformat(d["time"])
      save_image(img_dir / f"cc{d['cc']:.3f}_{t:%Y%m%d_%H%M%S}.png", d, tdata, toffs, tinfo)


if __name__ == "__main__":
  main()
