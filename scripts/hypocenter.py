# hypocenter.py
# 4拠点のP波到着時刻から、震源の位置と地震の発生時刻を推定する(画面は hypocenter_window.py)
#   - 近い地震: 均質な速度構造を仮定し、震源の位置(x, y, 深さ)と発生時刻をグリッドサーチで求める
#   - 遠い地震: P波が平面波として届くとみなし、到来方向と見かけの速度を求める
#     (拠点の間隔が数kmしかないため、遠い地震は P波だけでは距離が決まらない)
import numpy as np
from obspy.signal.trigger import recursive_sta_lta

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
