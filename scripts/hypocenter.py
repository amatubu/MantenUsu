# hypocenter.py
# 4拠点のP波到着時刻から、震源の位置と地震の発生時刻を推定する(画面は hypocenter_window.py)
#   - 近い地震: 均質な速度構造を仮定し、震源の位置(x, y, 深さ)と発生時刻をグリッドサーチで求める
#   - 遠い地震: P波が平面波として届くとみなし、到来方向と見かけの速度を求める
#     (拠点の間隔が数kmしかないため、遠い地震は P波だけでは距離が決まらない)
import numpy as np
from obspy.signal.trigger import recursive_sta_lta

import sp_distance

# 拠点の緯度・経度・高度が入っているチャンネル表(etc フォルダ)
STATION_TABLE = "manten_ch.tbl"

# 緯度・経度1度あたりの距離 [km](拠点付近の狭い範囲なので平面で近似する)
KM_PER_DEG_LAT = 110.57
KM_PER_DEG_LON_EQ = 111.32

# グリッドサーチの範囲: 拠点の中心から水平 ±HORIZONTAL_RANGE km、深さ 0〜DEPTH_RANGE km
HORIZONTAL_RANGE = 30.0
DEPTH_RANGE = 30.0

# チャンネル表から拠点ごとの (緯度, 経度, 高度[m]) を読み込む
def load_stations(etc_dir):
  stations = {}
  with open(etc_dir / STATION_TABLE, encoding="utf-8") as f:
    for line in f:
      fields = line.split()
      if not fields or fields[0].startswith("#") or len(fields) < 16:
        continue
      # 4番目が拠点名、14〜16番目が緯度・経度・高度
      stations[fields[3]] = (float(fields[13]), float(fields[14]), float(fields[15]))
  return stations

# 緯度・経度と、拠点の中心を原点とする平面座標 [km](x: 東、y: 北)を変換する
class LocalProjection:
  def __init__(self, coords):
    self.lat0 = float(np.mean([c[0] for c in coords]))
    self.lon0 = float(np.mean([c[1] for c in coords]))
    self.km_per_lon = KM_PER_DEG_LON_EQ * np.cos(np.radians(self.lat0))

  def to_xy(self, lat, lon):
    return (lon - self.lon0) * self.km_per_lon, (lat - self.lat0) * KM_PER_DEG_LAT

  def to_latlon(self, x, y):
    return self.lat0 + y / KM_PER_DEG_LAT, self.lon0 + x / self.km_per_lon

# CFT が閾値を下から上に超えたインデックスの一覧
def rising_edges(cft, thr):
  above = cft > thr
  return np.where(above & ~np.r_[False, above[:-1]])[0]

# 各拠点の上下動(U)から P波の到着時刻を検出する
#   traces ... {拠点名: 上下動の Trace}
#   window ... 全拠点の到着がこの幅 [秒] に収まる組み合わせを探す
# 戻り値: (picks, cfts)
#   picks ... {拠点名: 到着時刻(基準時刻からの秒) または None}
#   cfts  ... {拠点名: CFT の配列}
# 拠点ごとに最初に閾値を超えた時刻を使うと、雑音で外れた拠点が出るため、
# 閾値を超えた時刻が window 秒以内にそろう拠点が最も多い、最も早い組み合わせを選ぶ
def pick_p_arrivals(traces, ref_time, p_sta, p_lta, p_thr, window):
  cfts, edges = {}, {}
  for name, tr in traces.items():
    df = tr.stats.sampling_rate
    cft = recursive_sta_lta(tr.data, max(1, int(p_sta * df)), max(2, int(p_lta * df)))
    cfts[name] = cft
    # 基準時刻からの秒に直す(拠点ごとに記録開始時刻が違っても比べられるように)
    offset = tr.stats.starttime - ref_time
    edges[name] = offset + rising_edges(cft, p_thr) / df

  best_count, best_picks = 0, {name: None for name in traces}
  for t in sorted(np.concatenate(list(edges.values()))):
    picks = {}
    for name, times in edges.items():
      hits = times[(times >= t) & (times <= t + window)]
      picks[name] = float(hits[0]) if len(hits) else None
    count = sum(p is not None for p in picks.values())
    if count > best_count:
      best_count, best_picks = count, picks
      if count == len(traces):
        break
  return best_picks, cfts

# 平面波として、到来方向と見かけの速度を求める(3拠点以上)
#   xy ... 拠点の座標 [km] の配列 (n, 2)、t ... 到着時刻 [秒] の配列 (n,)
# 戻り値: 到来方向(震源のある方位。北から時計回り [度])、見かけの速度 [km/s]、残差 [秒]
def fit_plane_wave(xy, t):
  # t = t0 + sx * x + sy * y (s: スローネス。波の進む向きのベクトル)
  a = np.c_[np.ones(len(t)), xy]
  sol, *_ = np.linalg.lstsq(a, t, rcond=None)
  s = sol[1:]
  rms = float(np.sqrt(np.mean((a @ sol - t) ** 2)))
  norm = np.linalg.norm(s)
  if norm == 0:
    return None, None, rms
  # 波は震源から遠ざかる向きに進むので、震源の方位はスローネスの逆向き
  azimuth = float((np.degrees(np.arctan2(-s[0], -s[1])) + 360) % 360)
  return azimuth, float(1 / norm), rms

# 指定した点の集まりそれぞれについて、走時の残差と発生時刻を求める
#   gx, gy, gz ... 震源の候補の座標 [km](同じ形の配列)
def _grid_residuals(xy, t, vp, gx, gy, gz):
  dist = np.sqrt((gx[..., None] - xy[:, 0]) ** 2 + (gy[..., None] - xy[:, 1]) ** 2
                 + gz[..., None] ** 2)
  # 発生時刻は、到着時刻 - 走時 の平均(これで残差が最小になる)
  origin = (t - dist / vp).mean(axis=-1)
  rms = np.sqrt(((t - dist / vp - origin[..., None]) ** 2).mean(axis=-1))
  return rms, origin

# 粗いグリッドで探してから、最良の点のまわりを細かいグリッドで探す
#   xy ... 拠点の座標 [km] の配列 (n, 2)、t ... 到着時刻 [秒]、vp ... P波の速度 [km/s]
# 戻り値: dict(x, y, depth [km], origin [秒], rms [秒], at_edge)
#   at_edge ... 探索範囲の端で最良になった(範囲の外の遠い地震で、位置が決まらない)
def locate_grid(xy, t, vp, horizontal=HORIZONTAL_RANGE, depth=DEPTH_RANGE):
  center = (0.0, 0.0, depth / 2)
  half = (horizontal, horizontal, depth / 2)
  for step in (1.0, 0.1, 0.01):
    axes = [np.arange(max(c - h, lo), min(c + h, hi) + step / 2, step)
            for c, h, lo, hi in zip(center, half,
                                    (-horizontal, -horizontal, 0.0),
                                    (horizontal, horizontal, depth))]
    gx, gy, gz = np.meshgrid(*axes, indexing="ij")
    rms, origin = _grid_residuals(xy, t, vp, gx, gy, gz)
    k = np.unravel_index(np.argmin(rms), rms.shape)
    center = (gx[k], gy[k], gz[k])
    half = (step * 2,) * 3
    best_rms, best_origin = float(rms[k]), float(origin[k])
  x, y, z = (float(v) for v in center)
  at_edge = (max(abs(x), abs(y)) >= horizontal - 0.5) or z >= depth - 0.5
  return dict(x=x, y=y, depth=z, origin=best_origin, rms=best_rms, at_edge=at_edge)

# --- P波の到着時刻の決め直しと S波の検出(有珠山周辺の近い地震向け) ---
#   STA/LTA の検出は窓の長さの分だけ時刻がずれることがあるので、その前後を AIC 法で決め直す
#   AIC 法: 区間の各点で波形を前と後に分け、前後それぞれのばらつきから AIC を計算し、
#   最小になる点(性質が最もはっきり変わる点)を到着時刻とする
#   (有珠山直下の地震の候補16件では、2〜30 Hz で決め直した P波の到着時刻は、拡大した波形の立ち上がりとよく合った)
PICK_BAND = (2.0, 30.0)
# P波: STA/LTA で検出した時刻の AIC_BEFORE 秒前〜AIC_AFTER 秒後で決め直す
AIC_BEFORE = 1.0
AIC_AFTER = 0.5
# S波: P波の S_MIN_GAP 秒後〜(P波 + 探す範囲)で、水平動のエネルギー(南北² + 東西²)に AIC 法を使う
#   (候補16件では、S-P時間は Hokudai・Mimatsu 0.5〜1.3秒、Nizaemn 1.3〜1.6秒だった)
S_MIN_GAP = 0.1

# 検出に使う帯域通過フィルターをかけたデータ
def pick_filtered(tr):
  tr = tr.copy()
  tr.data = tr.data.astype(float)
  tr.detrend("demean")
  tr.filter("bandpass", freqmin=PICK_BAND[0], freqmax=PICK_BAND[1], corners=4, zerophase=True)
  return tr.data

# data[i0:i1] で AIC が最小になるインデックス(区間が短すぎれば None)
def aic_onset(data, i0, i1):
  from obspy.signal.trigger import aic_simple
  i0, i1 = max(0, i0), min(len(data), i1)
  if i1 - i0 < 20:
    return None
  aic = aic_simple(data[i0:i1])
  return i0 + 5 + int(np.argmin(aic[5:-5]))

# 時刻(基準時刻からの秒)の t0〜t1 で、AIC が最小になる時刻
#   data ... pick_filtered の結果(または水平動のエネルギー)、offset ... 記録開始の基準時刻からの秒
def aic_time(data, df, offset, t0, t1):
  i = aic_onset(data, int((t0 - offset) * df), int((t1 - offset) * df))
  return None if i is None else offset + i / df

# P波の到着時刻を AIC 法で決め直す。決め直せなければ元の時刻
def refine_p(data_u, df, offset, t_p):
  t = aic_time(data_u, df, offset, t_p - AIC_BEFORE, t_p + AIC_AFTER)
  return t_p if t is None else t

# S波の到着時刻(水平動のエネルギー energy に AIC 法を使う)
def pick_s(energy, df, offset, t_p, search):
  return aic_time(energy, df, offset, t_p + S_MIN_GAP, t_p + search)

# 有珠山周辺の近い地震の震源を求めるときの既定値(震源推定の画面と、地震の候補をまとめて計算する処理で共通)
#   速度: 有珠山直下の地震の候補16件で、気象庁の震央に最も近くなったのは P波 3.0 km/s、vp/vs 1.73〜1.9
#   (P波 5.5 km/s では平均 5.5 km 離れた)
#   S波を探す範囲: P波からこの秒数後まで(候補16件の S-P時間は 0.5〜1.6秒)
LOCAL_VP = 3.0
LOCAL_VP_VS = 1.73
LOCAL_S_SEARCH = 3.0
# 遠い地震の距離を大森公式(S-P時間 × k)で求めるときの大森係数 [km/s](P波・S波検出の画面と同じ)
#   ふだんは走時表(sp_distance.py)で求める。k は距離で変わるため(150 km までは 8.0、730 km で 9.6 くらい)
OMORI_K = 8.0
# S波を最初から計算に使わない拠点
#   Bamba は雑音が多く、S波の検出がずれやすいため(候補16件で S-P時間が 0.5〜2.0秒とばらついた)
S_EXCLUDED_DEFAULT = {"Bamba"}
# 震源を求めるのに必要な到着時刻の数(P波・S波を合わせて。未知数は位置3つと発生時刻の4つ)
MIN_OBSERVATIONS = 5
# 自動の検出で「近い地震」とするのに必要な到着時刻の数
#   ちょうど MIN_OBSERVATIONS(5つ)だと未知数に対して余裕がなく、雑音を拾った到着時刻でも残差が小さく出て、
#   位置が当てにならないことがある(2026/8/3 00:51 は残差 0.083秒、2026/7/2 03:59 は 0.132秒で「近い地震」になったが、
#   Mimatsu 以外は雑音を拾っていた)。これより少ないときは「近い地震(到着時刻少)」として、手で確かめる
#   震源推定の画面で保存した(手で確かめた)到着時刻は、5つでも「近い地震」とする
CONFIRM_OBSERVATIONS = 6
# 震源の残差がこの秒数を超えたら、近い地震としての震源は当てにならないとみなす
#   S波は P波のあと「S波を探す範囲」の中だけで探すので、遠い地震では P波の揺れの中の別の変化を拾ってしまう
#   (有珠山直下の地震の候補で残差は 0.01〜0.08秒、2026/8/23 02:00 の茨城県南部の地震(730 km)では 0.6秒だった)
MAX_LOCAL_RMS = 0.15
# 遠い地震として震央を推定するのは、S-P時間(平均)がこの秒数以上のときだけ
#   これより短い(震源距離が約25 km より近い)のに残差が大きいときは、遠い地震ではなく、
#   近い地震で到着時刻の検出がずれているとみなす
#   (有珠山直下の地震の候補で、検出がずれて残差が大きくなったものも S-P時間は 0.8〜1.2秒だった)
FAR_MIN_SP = 3.0

# P波・S波の到着時刻から震源を求める(グリッドサーチ)
#   pos ... 観測した拠点の座標 [km] の配列 (n, 3)。3列目は高さ(海面からの高度。上が正)
#   t   ... 到着時刻 [秒] (n,)、v ... その波の速度 [km/s] (n,)(P波なら vp、S波なら vs)
#   深さは海面からの深さ [km]。山の上の拠点の高度を考えるため、海面より上(top まで)も探す
# 戻り値: dict(x, y, depth, origin, rms, at_edge, at_top, depth_min, depth_max)
#   at_edge ... 水平方向・深い方の端で最良になった(範囲外)、at_top ... 浅い方の端(深さが決まらない)
#   depth_min・depth_max ... 残差がほぼ同じ(最小の残差から DEPTH_RMS_TOL まで)になる深さの範囲 [km]
#                           深さごとに震央を探し直して残差を比べる(深さがどこまで確かかの目安)
def locate_grid_ps(pos, t, v, horizontal=HORIZONTAL_RANGE, depth=DEPTH_RANGE, top=-1.0):
  center = (0.0, 0.0, (top + depth) / 2)
  half = (horizontal, horizontal, (depth - top) / 2)
  lows, highs = (-horizontal, -horizontal, top), (horizontal, horizontal, depth)
  for step in (1.0, 0.1, 0.01):
    axes = [np.arange(max(c - h, lo), min(c + h, hi) + step / 2, step)
            for c, h, lo, hi in zip(center, half, lows, highs)]
    gx, gy, gz = np.meshgrid(*axes, indexing="ij")
    dist = np.sqrt((gx[..., None] - pos[:, 0]) ** 2 + (gy[..., None] - pos[:, 1]) ** 2
                   + (gz[..., None] + pos[:, 2]) ** 2)
    travel = dist / v
    origin = (t - travel).mean(axis=-1)
    rms = np.sqrt(((t - travel - origin[..., None]) ** 2).mean(axis=-1))
    k = np.unravel_index(np.argmin(rms), rms.shape)
    center = (gx[k], gy[k], gz[k])
    half = (step * 2,) * 3
    best_rms, best_origin = float(rms[k]), float(origin[k])
  x, y, z = (float(c) for c in center)
  depth_min, depth_max = _depth_range(pos, t, v, x, y, best_rms, top, depth)
  return dict(x=x, y=y, depth=z, origin=best_origin, rms=best_rms,
              at_edge=(max(abs(x), abs(y)) >= horizontal - 0.5) or z >= depth - 0.5,
              at_top=z <= top + 0.05, depth_min=depth_min, depth_max=depth_max)

# 残差がほぼ同じとみなす幅: 最小の残差の DEPTH_RMS_TOL 倍(小さすぎるときは DEPTH_RMS_MIN 秒)まで
#   DEPTH_RMS_MIN は到着時刻の読み取りの精度(約0.01秒)程度
#   (2026/3/4 22:58 を6拠点の P波だけで求めると、残差は深さ -1 km で 0.041秒、0 km で 0.050秒、
#    0.5 km で 0.052秒、1 km で 0.062秒。この基準では「0.5 km より浅い」になる。
#    S波も加えると 1.5 km で 0.104秒が最小で、1.0〜2.4 km になる)
DEPTH_RMS_TOL = 0.30
DEPTH_RMS_MIN = 0.01
# 深さの範囲を調べるときに、深さごとに震央を探し直す範囲 [km] と刻み [km]
DEPTH_SCAN_HORIZONTAL = 3.0
DEPTH_SCAN_STEP = 0.1

# 深さを固定して(震央は best の近くで探し直して)残差を求め、残差がほぼ同じになる深さの範囲を返す
def _depth_range(pos, t, v, bx, by, best_rms, top, bottom):
  limit = best_rms + max(best_rms * DEPTH_RMS_TOL, DEPTH_RMS_MIN)
  hx = np.arange(bx - DEPTH_SCAN_HORIZONTAL, bx + DEPTH_SCAN_HORIZONTAL + 1e-9, DEPTH_SCAN_STEP)
  hy = np.arange(by - DEPTH_SCAN_HORIZONTAL, by + DEPTH_SCAN_HORIZONTAL + 1e-9, DEPTH_SCAN_STEP)
  gx, gy = np.meshgrid(hx, hy, indexing="ij")
  ok = []
  for z in np.arange(top, bottom + 1e-9, DEPTH_SCAN_STEP):
    dist = np.sqrt((gx[..., None] - pos[:, 0]) ** 2 + (gy[..., None] - pos[:, 1]) ** 2 + (z + pos[:, 2]) ** 2)
    travel = dist / v
    origin = (t - travel).mean(axis=-1)
    rms = np.sqrt(((t - travel - origin[..., None]) ** 2).mean(axis=-1)).min()
    if rms <= limit:
      ok.append(float(z))
    elif ok and z > max(ok) + 1.0:
      break   # 範囲を過ぎて残差が大きくなった(深い方は調べなくてよい)
  if not ok:
    return None, None
  return round(min(ok), 1), round(max(ok), 1)

# --- 深さの表示(震源推定の画面・地震の候補の震源で共通) ---
#   浅い方の端まで残差がほぼ同じときは、深さを決めずに「◯ km より浅い」とする
#   (探す範囲は海面より上(-1 km)まであるので、浅い方の端を深さとして出すと、震源が宙に浮いて見える)
def is_shallow(loc):
  return loc["at_top"] or (loc.get("depth_min") is not None and loc["depth_min"] <= -0.95)

# 長い説明。例: 「深さ 1.7 km(1.3〜2.1 km)」「浅い(0.5 km より浅い)」
def depth_text(loc):
  lo, hi = loc.get("depth_min"), loc.get("depth_max")
  if is_shallow(loc):
    return f"浅い({hi:.1f} km より浅い)" if hi is not None else "浅い(深さは決まらない)"
  if lo is None:
    return f"{loc['depth']:.1f} km"
  return f"{loc['depth']:.1f} km({lo:.1f}〜{hi:.1f} km)"

# 表などの短い表示。例: 「1.7」「<0.5」
def depth_short(loc):
  if is_shallow(loc):
    return f"<{loc['depth_max']:.1f}" if loc.get("depth_max") is not None else "浅い"
  return f"{loc['depth']:.1f}"

# 断面図に描く深さ: 浅いときは、海面(0 km)と範囲の深い方の端の、浅い方
def depth_for_plot(loc):
  if is_shallow(loc):
    return min(0.0, loc["depth_max"]) if loc.get("depth_max") is not None else 0.0
  return loc["depth"]

# --- 遠い地震: 到来方向と S-P時間から震央を推定する ---
#   拠点の間隔が数kmしかないため、遠い地震は P波・S波の到着時刻でも位置が決まらない
#   そこで、P波から求めた到来方向に、S-P時間から求めた震央距離だけ進んだ点を震央とする
#   距離は、ふだんは走時表(sp_distance.py。深さ 30 km を仮定)で求める。
#   大森公式(距離 = k × S-P時間)を選んだときは、その距離(震源距離)を震央距離ともみなす(区別しない)
EARTH_RADIUS_KM = 6371.0

# (lat, lon) から方位 azimuth [度] に dist_km 進んだ点の (緯度, 経度)(球面で計算する)
def destination(lat, lon, azimuth, dist_km):
  phi1, lam1, theta = np.radians(lat), np.radians(lon), np.radians(azimuth)
  delta = dist_km / EARTH_RADIUS_KM
  phi2 = np.arcsin(np.sin(phi1) * np.cos(delta) + np.cos(phi1) * np.sin(delta) * np.cos(theta))
  lam2 = lam1 + np.arctan2(np.sin(theta) * np.sin(delta) * np.cos(phi1),
                           np.cos(delta) - np.sin(phi1) * np.sin(phi2))
  return float(np.degrees(phi2)), float(np.degrees(lam2))

# 到来方向と S-P時間(拠点ごとの値の平均)から震央を推定する
#   center ... 拠点の中心 (緯度, 経度)、sp_times ... S-P時間 [秒] のリスト
#   k ... 大森係数 [km/s]。None なら走時表で求める
# 戻り値: dict(sp(平均の S-P時間), distance(震央距離 [km]), hypo(震源距離 [km]。推定マグニチュードに使う),
#              k(大森係数。走時表なら None), lat, lon) または None
def estimate_far(center, azimuth, sp_times, k=None):
  if azimuth is None or not sp_times:
    return None
  sp = float(np.mean(sp_times))
  if k is None:
    distance, hypo = sp_distance.epicentral(sp), sp_distance.hypocentral(sp)
  else:
    distance = hypo = k * sp
  lat, lon = destination(center[0], center[1], azimuth, distance)
  return dict(sp=sp, distance=distance, hypo=hypo, k=k, lat=lat, lon=lon)

# 遠い地震の距離の求め方の説明(結果の欄・確認用の画像に出す)
def far_method_text(far):
  if far.get("k") is None:
    return f"S-P {far['sp']:.1f} 秒、走時表・深さ {sp_distance.ASSUMED_DEPTH:.0f} km"
  return f"S-P {far['sp']:.1f} 秒 × k {far['k']:.1f}"

# --- P波・S波の到着時刻から震源を求めて判定する(震源推定の画面と、地震の候補をまとめて計算する処理で共通) ---
# 判定
KIND_LOCAL = "近い地震"
KIND_LOCAL_POOR = "近い地震(残差大)"   # S-P時間は短いが残差が大きい(到着時刻の検出がずれている。手で確かめる)
KIND_LOCAL_FEW = "近い地震(到着時刻少)"  # 残差は小さいが、自動の検出の到着時刻が CONFIRM_OBSERVATIONS より少ない(手で確かめる)
KIND_FAR = "遠い地震"
KIND_NONE = "決まらない"
# 近い地震として震源を出す判定(要確認のものを含む)
LOCAL_KINDS = (KIND_LOCAL, KIND_LOCAL_POOR, KIND_LOCAL_FEW)

# 震源を求めて判定する
#   xyz     ... {拠点名: 座標 [km](東, 北, 海面からの高度)}(proj の平面座標)
#   picks   ... {拠点名: P波の到着時刻 [秒]}(計算に使う拠点だけ)
#   s_picks ... {拠点名: S波の到着時刻 [秒]}(計算に使う拠点だけ。picks にある拠点に限る)
#   proj    ... 平面座標(緯度・経度に直すのと、遠い地震の推定の起点(拠点の中心)に使う)
#   vp, vp_vs, k ... P波の速度 [km/s]、vp/vs、遠い地震の距離に使う大森係数 [km/s](None なら走時表で求める)
#   近い地震として位置が決まらないとき(範囲外・残差が大きい)は、遠い地震として到来方向と S-P時間から震央を推定する
#   (S-P時間が短いのに残差が大きいときは、遠い地震ではなく検出のずれとみなし、近い地震(残差大)とする)
#   confirmed ... 到着時刻を手で確かめたか(震源推定の画面で保存したもの)。False で到着時刻が CONFIRM_OBSERVATIONS より
#                 少ないときは、残差が小さくても近い地震(到着時刻少)とする(残差が大きいときは近い地震(残差大)のまま)
# 戻り値: dict(kind, loc(locate_grid_ps の結果 または None), far(estimate_far の結果 または None),
#              azimuth, app_v, plane_rms, n_p, n_s, lat, lon)
def locate_event(xyz, picks, s_picks, proj, vp=LOCAL_VP, vp_vs=LOCAL_VP_VS, k=None, confirmed=True):
  used = sorted(picks)
  used_s = [nm for nm in used if nm in s_picks]
  res = dict(kind=KIND_NONE, loc=None, far=None, azimuth=None, app_v=None, plane_rms=None,
             n_p=len(used), n_s=len(used_s), lat=None, lon=None)
  if len(used) >= 3:
    res["azimuth"], res["app_v"], res["plane_rms"] = fit_plane_wave(
      np.array([xyz[nm][:2] for nm in used]), np.array([picks[nm] for nm in used]))
  obs = [(xyz[nm], picks[nm], vp) for nm in used] + [(xyz[nm], s_picks[nm], vp / vp_vs) for nm in used_s]
  loc = None
  if len(used) >= 3 and len(obs) >= MIN_OBSERVATIONS:
    loc = locate_grid_ps(np.array([o[0] for o in obs]), np.array([o[1] for o in obs]),
                         np.array([o[2] for o in obs]))
    res["loc"] = loc
  sp_times = [s_picks[nm] - picks[nm] for nm in used_s]
  if loc is not None and not loc["at_edge"] and (loc["rms"] <= MAX_LOCAL_RMS or np.mean(sp_times or [0]) < FAR_MIN_SP):
    if loc["rms"] > MAX_LOCAL_RMS:
      res["kind"] = KIND_LOCAL_POOR
    elif not confirmed and len(obs) < CONFIRM_OBSERVATIONS:
      res["kind"] = KIND_LOCAL_FEW
    else:
      res["kind"] = KIND_LOCAL
    res["lat"], res["lon"] = proj.to_latlon(loc["x"], loc["y"])
    return res
  far = estimate_far((proj.lat0, proj.lon0), res["azimuth"], sp_times, k)
  if far is not None:
    res.update(kind=KIND_FAR, far=far, lat=far["lat"], lon=far["lon"])
  return res
