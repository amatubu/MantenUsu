# quake_locate.py
# 地震の候補の一覧(CSV)から、候補ごとに震源を求める(UsuMantenViewer.py のバッチ処理から使う)
#   1. 候補の前後の波形で P波を検出し(quake_features の短い STA/LTA)、AIC 法で決め直す
#   2. P波のあとの水平動から S波を検出する(AIC 法)
#      震源リストと対応した候補は、その地震の S-P時間の見込みに合わせて S波を探す範囲を広げる
#   3. P波・S波の到着時刻から、近い地震として震源を求める(震源推定の画面と同じ方法・既定値。hypocenter.py)
#      位置が決まらないとき(範囲外・残差が大きい)は、遠い地震として到来方向と S-P時間から震央を推定する
#   4. 一覧(CSV)と、震央の地図・東西と南北の断面図、候補ごとの確認用の画像(P波・S波の位置)を出力する
#   自動の結果なので、あやしいものは震源推定の画面で到着時刻を手で直して確かめる
import csv
from datetime import datetime, timedelta

import numpy as np
import obspy
from obspy.geodetics import gps2dist_azimuth, kilometers2degrees
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

import app_paths
import gsi_tiles
import jma_match
import quake_detect
from quake_features import USU_SUMMIT
from hypocenter import (LocalProjection, fit_plane_wave, locate_grid_ps, pick_filtered, refine_p, pick_s,
                        estimate_far, LOCAL_VP, LOCAL_VP_VS, LOCAL_S_SEARCH, OMORI_K, S_EXCLUDED_DEFAULT,
                        MIN_OBSERVATIONS, MAX_LOCAL_RMS, FAR_MIN_SP)
from plot_common import NM_TO_CM

# 候補の前後、波形を使う範囲 [秒](揺れの始まりの前・終わりの後)
WINDOW_BEFORE = 30.0
WINDOW_AFTER = 60.0
# 震源リストと対応した遠い地震は、S-P時間の見込みにこの秒数を足した範囲で S波を探す
S_SEARCH_MARGIN = 10.0
# 震源リストの見込みで S-P時間がこの秒数より長い地震だけ、S波を探す範囲を広げる
#   (近い地震は既定の範囲のままにする。広げると、P波の揺れの中の別の変化を拾いやすくなる)
FAR_SP_SECONDS = 3.0
# 地図・断面図で、残差がこの秒数未満の震源を塗りつぶし、それ以外は白抜きにする
GOOD_RMS = 0.1
# 地図の最小の範囲 [km](拠点の中心から東西・南北)
MAP_HALF = (6.5, 4.5)
# 確認用の画像に描く範囲: 最初の P波の1秒前から、最も遅い S波のこの秒数後まで(最短 CHECK_MIN 秒、最長 CHECK_MAX 秒)
CHECK_AFTER_S = 3.0
CHECK_MIN, CHECK_MAX = 5.0, 90.0
# 確認用の画像のフィルター [Hz](検出と同じ帯域)
CHECK_BAND = (2.0, 30.0)

# 判定(一覧・画像に書く)
KIND_LOCAL = "近い地震"
KIND_LOCAL_POOR = "近い地震(残差大)"   # S-P時間は短いが残差が大きい(到着時刻の検出がずれている。手で確かめる)
KIND_FAR = "遠い地震"
KIND_NONE = "決まらない"

# 出力先
OUTPUT_DIR = app_paths.BATCH_PLOTS_DIR / "quakes" / "locate"


# 日時の文字列を読む(この処理の一覧の形式と、Excel で保存し直した形式のどちらでも読めるようにする)
def parse_datetime(text):
  text = (text or "").strip()
  for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S",
              "%Y/%m/%d %H:%M"):
    try:
      return datetime.strptime(text, fmt)
    except ValueError:
      pass
  return None

# 数値の文字列を読む(空や数値でなければ None)
def parse_float(text):
  try:
    return float(text)
  except (TypeError, ValueError):
    return None

# 地震の候補の一覧(quake_detect.write_candidates_csv の形式)を読む
#   「候補」の列があれば、その列に何か書いてある行だけを使う(Excel で候補に印を付けて CSV に保存したもの)
#   UTF-8(BOM つき)とふつうの CSV(Shift_JIS)のどちらでも読める
#   戻り値 ... [{"start", "end", "jma"(震源リストの地震 または None), "row"(元の行)}, ...]
def read_candidates(path):
  try:
    with open(path, encoding="utf-8-sig") as f:
      rows = list(csv.DictReader(f))
  except UnicodeDecodeError:
    with open(path, encoding="cp932") as f:
      rows = list(csv.DictReader(f))
  if rows and "候補" in rows[0]:
    rows = [r for r in rows if (r.get("候補") or "").strip()]
  candidates = []
  for r in rows:
    start = parse_datetime(r.get("開始"))
    if start is None:
      continue
    # 終了は時刻だけなので、開始の日付を付ける(日付をまたいだら翌日)
    end = start
    try:
      t = datetime.strptime((r.get("終了") or "").strip(), "%H:%M:%S").time()
      end = datetime.combine(start.date(), t)
      if end < start:
        end += timedelta(days=1)
    except ValueError:
      pass
    jma = None
    origin = parse_datetime(r.get("気象庁: 発震時刻"))
    lat, lon, depth = (parse_float(r.get(k)) for k in ("緯度", "経度", "深さ[km]"))
    if origin and None not in (lat, lon, depth):
      jma = {"日時": origin, "緯度": lat, "経度": lon, "深さ[km]": depth, "M": parse_float(r.get("M")),
             "震央地名": r.get("震央地名", "")}
    candidates.append({"start": start, "end": end, "jma": jma, "row": r})
  return candidates

# 震源リストの地震に、拠点の中心までの距離と、P波・S波の到達見込みを加える
def add_arrivals(q, center):
  dist_m, azimuth, _ = gps2dist_azimuth(center[0], center[1], q["緯度"], q["経度"])
  p, s = jma_match.travel_times(round(kilometers2degrees(dist_m / 1000), 2), round(q["深さ[km]"]))
  q.update(dist_km=dist_m / 1000, azimuth=azimuth)
  q["p_time"] = q["日時"] + timedelta(seconds=p) if p is not None else None
  q["s_time"] = q["日時"] + timedelta(seconds=s) if s is not None else None

# 候補の前後の、波形を使う範囲 (開始, 終了)
def candidate_window(cand):
  t0 = cand["start"] - timedelta(seconds=WINDOW_BEFORE)
  t1 = max(cand["end"], cand["start"]) + timedelta(seconds=WINDOW_AFTER)
  q = cand["jma"]
  if q and q.get("s_time"):
    t1 = max(t1, q["s_time"] + timedelta(seconds=WINDOW_AFTER / 2))
  return t0, t1

# 候補1件の震源を求める
#   st ... 候補の前後を含む Stream(全成分)、stations ... {拠点名: (緯度, 経度, 高度)}
#   proj ... 震源の計算と地図に使う平面座標(全拠点の中心が原点)
#   戻り値 ... dict(kind, picks・s_picks(t0 からの秒), s_used(S波を計算に使った拠点), ref(t0), loc, far,
#                  azimuth, app_v, plane_rms, n_p, n_s, lat, lon, traces)
def locate_candidate(st, cand, stations, proj):
  t0, t1 = candidate_window(cand)
  ref = obspy.UTCDateTime(t0)
  w = st.slice(ref, obspy.UTCDateTime(t1)).copy()
  for tr in w:
    if np.ma.isMaskedArray(tr.data):
      tr.data = tr.data.filled(0)
  w = obspy.Stream([tr for tr in w if tr.stats.npts > 0 and np.any(tr.data) and tr.stats.station in stations])
  result = dict(kind=KIND_NONE, picks={}, s_picks={}, s_used=[], ref=t0, loc=None, far=None, azimuth=None,
                app_v=None, plane_rms=None, n_p=0, n_s=0, lat=None, lon=None, traces=w)

  # P波(短い STA/LTA で検出し、AIC 法で決め直す)
  _feat, coarse = quake_detect.analyze_candidate(w, t0, t1, stations)
  q = cand["jma"]
  s_search = LOCAL_S_SEARCH
  if q and q.get("p_time") and q.get("s_time"):
    sp = (q["s_time"] - q["p_time"]).total_seconds()
    if sp > FAR_SP_SECONDS:
      s_search = max(s_search, sp + S_SEARCH_MARGIN)
  result["s_search"] = s_search
  for nm in sorted(coarse):
    u = w.select(station=nm, channel="*U*")
    if not u:
      continue
    u = u[0]
    df, offset = u.stats.sampling_rate, u.stats.starttime - ref
    tp = refine_p(pick_filtered(u), df, offset, (coarse[nm] - t0).total_seconds())
    result["picks"][nm] = tp
    # S波(水平動のエネルギーに AIC 法)
    hor = [w.select(station=nm, channel=f"*{c}*") for c in "NE"]
    if all(hor):
      n_data, e_data = pick_filtered(hor[0][0]), pick_filtered(hor[1][0])
      m = min(len(n_data), len(e_data))
      ts = pick_s(n_data[:m] ** 2 + e_data[:m] ** 2, df, hor[0][0].stats.starttime - ref, tp, s_search)
      if ts is not None:
        result["s_picks"][nm] = ts

  picks, s_picks = result["picks"], result["s_picks"]
  used = sorted(picks)
  used_s = [nm for nm in used if nm in s_picks and nm not in S_EXCLUDED_DEFAULT]
  result.update(s_used=used_s, n_p=len(used), n_s=len(used_s))
  xyz = {nm: np.array([*proj.to_xy(*stations[nm][:2]), stations[nm][2] / 1000]) for nm in used}
  if len(used) >= 3:
    result["azimuth"], result["app_v"], result["plane_rms"] = fit_plane_wave(
      np.array([xyz[nm][:2] for nm in used]), np.array([picks[nm] for nm in used]))
  vs = LOCAL_VP / LOCAL_VP_VS
  obs = [(xyz[nm], picks[nm], LOCAL_VP) for nm in used] + [(xyz[nm], s_picks[nm], vs) for nm in used_s]
  if len(used) >= 3 and len(obs) >= MIN_OBSERVATIONS:
    loc = locate_grid_ps(np.array([o[0] for o in obs]), np.array([o[1] for o in obs]),
                         np.array([o[2] for o in obs]))
    result["loc"] = loc
    sp_times = [s_picks[nm] - picks[nm] for nm in used_s]
    if not loc["at_edge"] and (loc["rms"] <= MAX_LOCAL_RMS or np.mean(sp_times or [0]) < FAR_MIN_SP):
      result["kind"] = KIND_LOCAL if loc["rms"] <= MAX_LOCAL_RMS else KIND_LOCAL_POOR
      result["lat"], result["lon"] = proj.to_latlon(loc["x"], loc["y"])
  if result["kind"] == KIND_NONE:
    far = estimate_far((proj.lat0, proj.lon0), result["azimuth"], [s_picks[nm] - picks[nm] for nm in used_s],
                       OMORI_K)
    if far is not None:
      result.update(kind=KIND_FAR, far=far, lat=far["lat"], lon=far["lon"])
  return result

# 推定した震央と、震源リストの震央・有珠山の山頂との距離 [km]
def epicenter_distance(lat, lon, lat2, lon2):
  if None in (lat, lon, lat2, lon2):
    return None
  return gps2dist_azimuth(lat, lon, lat2, lon2)[0] / 1000

def fmt(v, spec):
  return "" if v is None else format(v, spec)

# 結果の一覧(CSV)
def write_csv(path, items, names):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["開始", "判定"]
                    + [f"{nm} P[秒]" for nm in names] + [f"{nm} S-P[秒]" for nm in names]
                    + ["P波の数", "S波の数", "緯度", "経度", "深さ[km](海面下)", "残差[秒]", "発生時刻",
                       "到来方向[度]", "見かけの速度[km/s]", "遠い地震の距離[km]", "S-P時間の平均[秒]",
                       "気象庁: 発震時刻", "震央地名", "M", "気象庁: 緯度", "気象庁: 経度", "気象庁: 深さ[km]",
                       "気象庁: 震央距離[km]", "気象庁の震央との距離[km]", "山頂からの距離[km]", "画像"])
    for cand, r in items:
      picks, s_picks = r["picks"], r["s_picks"]
      first = min(picks.values(), default=None)
      loc, far, q = r["loc"], r["far"], cand["jma"]
      local = r["kind"] in (KIND_LOCAL, KIND_LOCAL_POOR)
      row = [cand["start"].strftime("%Y-%m-%d %H:%M:%S"), r["kind"]]
      row += [fmt(picks[nm] - first, ".3f") if nm in picks else "" for nm in names]
      row += [(fmt(s_picks[nm] - picks[nm], ".2f") + ("" if nm in r["s_used"] else "(不使用)"))
              if nm in s_picks and nm in picks else "" for nm in names]
      row += [r["n_p"], r["n_s"], fmt(r["lat"], ".4f"), fmt(r["lon"], ".4f"),
              fmt(loc["depth"], ".1f") if local and not loc["at_top"] else ("不定" if local else ""),
              fmt(loc["rms"], ".3f") if loc else "",
              (r["ref"] + timedelta(seconds=loc["origin"])).strftime("%H:%M:%S.%f")[:-4] if local else "",
              fmt(r["azimuth"], ".0f"), fmt(r["app_v"], ".1f"),
              fmt(far["distance"], ".0f") if far else "", fmt(far["sp"], ".2f") if far else ""]
      if q:
        row += [q["日時"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-5], q["震央地名"], fmt(q["M"], "g"),
                f"{q['緯度']:.4f}", f"{q['経度']:.4f}", f"{q['深さ[km]']:g}", fmt(q.get("dist_km"), ".0f"),
                fmt(epicenter_distance(r["lat"], r["lon"], q["緯度"], q["経度"]), ".1f")]
      else:
        row += [""] * 8
      row += [fmt(epicenter_distance(r["lat"], r["lon"], *USU_SUMMIT[:2]), ".1f"), r.get("image", "")]
      writer.writerow(row)

# 確認用の画像: 拠点ごとに上下動と水平動を並べ、P波・S波の位置と震源リストの到達見込みを示す
def save_check_image(path, cand, r, title):
  picks, s_picks = r["picks"], r["s_picks"]
  if not picks:
    return False
  first = min(picks.values())
  last_s = max([s_picks[nm] for nm in s_picks] + [first])
  span = min(max(last_s - first + CHECK_AFTER_S, CHECK_MIN), CHECK_MAX)
  ref = obspy.UTCDateTime(r["ref"])
  names = sorted({tr.stats.station for tr in r["traces"]})
  fig, axes = plt.subplots(2 * len(names), 1, figsize=(11, 1.25 * 2 * len(names) + 0.8), sharex=True, squeeze=False)
  axes = axes[:, 0]
  q = cand["jma"]
  for k, nm in enumerate(names):
    for j, (comps, colors) in enumerate(((["U"], ["#1f77b4"]), (["N", "E"], ["#ff7f0e", "#2ca02c"]))):
      ax = axes[2 * k + j]
      for c, color in zip(comps, colors):
        sel = r["traces"].select(station=nm, channel=f"*{c}*")
        if not sel:
          continue
        tr = sel[0].copy()
        tr.data = tr.data.astype(float)
        tr.detrend("demean")
        tr.filter("bandpass", freqmin=CHECK_BAND[0], freqmax=CHECK_BAND[1], corners=4, zerophase=True)
        t = tr.times() + (tr.stats.starttime - ref) - first
        inside = (t >= -1) & (t <= span)
        ax.plot(t[inside], tr.data[inside] * NM_TO_CM, lw=0.6, color=color, label=c)
      if nm in picks:
        ax.axvline(picks[nm] - first, color="red", lw=1)
      if nm in s_picks:
        ax.axvline(s_picks[nm] - first, color="magenta", lw=1.2, ls="--", alpha=1.0 if nm in r["s_used"] else 0.35)
      if q and q.get("p_time"):
        for key in ("p_time", "s_time"):
          x = (q[key] - r["ref"]).total_seconds() - first
          if -1 <= x <= span:
            ax.axvline(x, color="teal", lw=1.2, ls=":")
      ax.text(0.005, 0.88, f"{nm} {'上下動' if j == 0 else '水平動(N/E)'}", transform=ax.transAxes, fontsize=8,
              weight="bold", bbox=dict(facecolor="white", edgecolor="none", alpha=0.7, pad=1))
      ax.set_ylabel("cm/s", fontsize=7)
      ax.tick_params(labelsize=7)
      ax.grid(alpha=0.3)
  axes[0].set_xlim(-1, span)
  axes[0].set_title(title, fontsize=8, loc="left")
  t_first = r["ref"] + timedelta(seconds=first)
  axes[-1].set_xlabel(f"最初の P波からの秒({t_first:%H:%M:%S.%f}"[:-4] + f")  {CHECK_BAND[0]:g}-{CHECK_BAND[1]:g} Hz  "
                      "赤: P波  マゼンタの破線: S波(薄い線は計算に使わない)  青緑の点線: 気象庁の震源からの到達見込み",
                      fontsize=8)
  fig.tight_layout()
  fig.savefig(path, dpi=80)
  plt.close(fig)
  return True

# 確認用の画像のタイトル(2行)
def check_title(cand, r):
  picks, s_picks = r["picks"], r["s_picks"]
  sp = "  ".join(f"{nm} {s_picks[nm] - picks[nm]:.2f}" for nm in sorted(s_picks) if nm in picks)
  line1 = f"{cand['start']:%Y-%m-%d %H:%M:%S}  {r['kind']}  P波 {r['n_p']}・S波 {r['n_s']}  S-P[秒]: {sp or '-'}"
  loc, far = r["loc"], r["far"]
  if r["kind"] in (KIND_LOCAL, KIND_LOCAL_POOR):
    depth = "深さ不定" if loc["at_top"] else f"深さ {loc['depth']:.1f} km"
    line2 = f"震源 北緯 {r['lat']:.4f}° 東経 {r['lon']:.4f}° {depth}  残差 {loc['rms']:.3f} 秒"
  elif r["kind"] == KIND_FAR:
    line2 = (f"遠い地震として: 到来方向 {r['azimuth']:.0f}°  距離 {far['distance']:.0f} km"
             f"(S-P {far['sp']:.1f} 秒 × k {OMORI_K:g})  震央 北緯 {far['lat']:.2f}° 東経 {far['lon']:.2f}°")
  else:
    line2 = "震源は決まりませんでした(P波・S波の到着時刻が足りない)"
  q = cand["jma"]
  if q:
    d = epicenter_distance(r["lat"], r["lon"], q["緯度"], q["経度"])
    line2 += (f"\n気象庁: {q['日時']:%H:%M:%S} {q['震央地名']} M{fmt(q['M'], 'g')} 深さ {q['深さ[km]']:g} km"
              + (f"  気象庁の震央との距離 {d:.1f} km" if d is not None else ""))
  return line1 + "\n" + line2

# 震央の地図と、東西・南北の断面図(近い地震として震源が決まったものだけ)
#   items ... [(候補, 結果), ...]、stations ... 拠点の位置、proj ... 平面座標
def save_location_map(path, items, stations, proj, title):
  local = [(cand, r) for cand, r in items if r["kind"] in (KIND_LOCAL, KIND_LOCAL_POOR)]
  points = [(*proj.to_xy(r["lat"], r["lon"]), r["loc"]["depth"]) for _cand, r in local]
  half_x = max([MAP_HALF[0]] + [abs(p[0]) + 1 for p in points])
  half_y = max([MAP_HALF[1]] + [abs(p[1]) + 1 for p in points])
  # 地図の縦横比を保つ
  half_x, half_y = max(half_x, half_y * MAP_HALF[0] / MAP_HALF[1]), max(half_y, half_x * MAP_HALF[1] / MAP_HALF[0])
  zmax = max([5.0] + [p[2] + 1 for p in points])

  fig = plt.figure(figsize=(13, 10.5))
  width = 0.62
  height = width * 13 / 10.5 * half_y / half_x
  axm = fig.add_axes([0.07, 0.40, width, height])
  axe = fig.add_axes([0.07, 0.105, width, 0.22], sharex=axm)
  axn = fig.add_axes([0.07 + width + 0.03, 0.40, 0.25, height], sharey=axm)
  lat_s, lon_w = proj.to_latlon(-half_x, -half_y)
  lat_n, lon_e = proj.to_latlon(half_x, half_y)
  try:
    for img, (west, east, south, north) in gsi_tiles.tiles_for_bounds("std", lat_s, lat_n, lon_w, lon_e):
      x0, y0 = proj.to_xy(south, west)
      x1, y1 = proj.to_xy(north, east)
      axm.imshow(img, extent=(x0, x1, y0, y1), zorder=0, interpolation="bilinear", alpha=0.8)
    axm.text(0.01, 0.01, gsi_tiles.ATTRIBUTION, transform=axm.transAxes, fontsize=7,
             bbox=dict(facecolor="white", edgecolor="none", alpha=0.7))
  except Exception:
    pass   # 地図のタイルが取得できなくても、震源は描く

  # 3つの図に同じものを描く(fx, fy で東・北・深さのどれを横軸・縦軸にするかを決める)
  def plot_all(ax, fx, fy):
    for nm, (la, lo, el) in stations.items():
      x, y = proj.to_xy(la, lo)
      z = -el / 1000
      ax.plot(fx(x, y, z), fy(x, y, z), "^", color="black", ms=9, markeredgecolor="white", zorder=5)
      ax.annotate(nm, (fx(x, y, z), fy(x, y, z)), textcoords="offset points", xytext=(5, 4), fontsize=8, zorder=5)
    x, y = proj.to_xy(*USU_SUMMIT[:2])
    z = -0.733   # 大有珠の標高 733 m
    ax.plot(fx(x, y, z), fy(x, y, z), "P", color="saddlebrown", ms=10, markeredgecolor="white", zorder=5)
    for (cand, r), (x, y, z) in zip(local, points):
      good = r["loc"]["rms"] < GOOD_RMS
      ax.plot(fx(x, y, z), fy(x, y, z), "o", color="red" if good else "none", markeredgecolor="red", ms=7, zorder=6)
      q = cand["jma"]
      if q:
        jx, jy = proj.to_xy(q["緯度"], q["経度"])
        jz = q["深さ[km]"]
        ax.plot(fx(jx, jy, jz), fy(jx, jy, jz), "x", color="teal", ms=8, mew=2, zorder=6)
        ax.plot([fx(x, y, z), fx(jx, jy, jz)], [fy(x, y, z), fy(jx, jy, jz)], color="teal", lw=0.8, ls=":", zorder=5)
  plot_all(axm, lambda x, y, z: x, lambda x, y, z: y)
  plot_all(axe, lambda x, y, z: x, lambda x, y, z: z)
  plot_all(axn, lambda x, y, z: z, lambda x, y, z: y)
  for i, (x, y, _z) in enumerate(points, 1):
    axm.annotate(str(i), (x, y), textcoords="offset points", xytext=(4, 3), fontsize=7.5, color="darkred",
                 weight="bold", zorder=7)
  axm.set_xlim(-half_x, half_x)
  axm.set_ylim(-half_y, half_y)
  axm.set_aspect("equal")
  axm.set_ylabel("北 [km]")
  plt.setp(axm.get_xticklabels(), visible=False)
  axe.set_ylim(zmax, -1)
  axe.set_xlabel("東 [km]")
  axe.set_ylabel("深さ [km](海面下)")
  axe.set_title("東西断面", fontsize=9, loc="left")
  axe.axhline(0, color="gray", lw=0.6)
  axn.set_xlim(-1, zmax)
  axn.set_xlabel("深さ [km](海面下)")
  axn.set_title("南北断面", fontsize=9, loc="left")
  axn.axvline(0, color="gray", lw=0.6)
  plt.setp(axn.get_yticklabels(), visible=False)
  for ax in (axm, axe, axn):
    ax.grid(alpha=0.3)

  handles = [Line2D([], [], marker="o", color="red", ls="", label=f"推定した震源(残差 {GOOD_RMS:g}秒未満)"),
             Line2D([], [], marker="o", color="none", markeredgecolor="red", ls="",
                    label=f"推定した震源(残差 {GOOD_RMS:g}秒以上)"),
             Line2D([], [], marker="x", color="teal", mew=2, ls="", label="気象庁の震源"),
             Line2D([], [], marker="^", color="black", ls="", label="拠点"),
             Line2D([], [], marker="P", color="saddlebrown", ls="", label="有珠山 山頂(大有珠)")]
  fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.07 + width + 0.03, 0.325), fontsize=8)
  # 番号の一覧(多いときは途中まで)
  listing = [f"{i:2d}. {cand['start']:%y/%m/%d %H:%M}  深さ"
             + ("不定" if r["loc"]["at_top"] else f"{r['loc']['depth']:.1f}km") + f"  残差{r['loc']['rms']:.2f}秒"
             + (" ×" if cand["jma"] else "") for i, (cand, r) in enumerate(local, 1)]
  if len(listing) > 24:
    listing = listing[:23] + [f"  ほか {len(listing) - 23}件(一覧の CSV を参照)"]
  fig.text(0.07 + width + 0.03, 0.225, "\n".join(listing), fontsize=7, va="top", linespacing=1.15)
  others = len(items) - len(local)
  fig.text(0.07, 0.005,
           f"P波・S波の到着時刻から推定(自動)。均質な速度構造(P波 {LOCAL_VP:g} km/s、vp/vs {LOCAL_VP_VS:g})、"
           f"S波は {'・'.join(sorted(S_EXCLUDED_DEFAULT))} を除く。深さは海面からの深さ(拠点の高度を考慮)。\n"
           f"番号は右下の一覧(× は気象庁の震源リストにあるもの)。近い地震として震源が決まった {len(local)}件を表示"
           + f"(残差が {MAX_LOCAL_RMS:g}秒を超えるものを含む)"
           + (f"。遠い地震・決まらないもの {others}件は一覧の CSV を参照" if others else ""),
           fontsize=8, va="bottom")
  fig.suptitle(title, fontsize=12, x=0.07, ha="left")
  fig.savefig(path, dpi=110)
  plt.close(fig)
