# velocity_model.py
# 有珠山周辺の近い地震の震源を求めるための、深さで速度が変わる構造(1次元)と走時(TODO 32)
#   走時は、拠点の位置を始点にした最短経路法で求める((水平距離 r, 深さ z) の2次元の格子。scipy の dijkstra)
#   1次元の構造では「拠点 → 点」と「点(震源) → 拠点」の走時が同じなので、拠点の標高ごとに1回計算すればよい
#   走時表は、同じ構造・同じ標高なら作り直さずに使い回す(9拠点で、最初の1回だけ1〜2秒かかる)
#   深さ z は海面からの深さ [km](下が正)。拠点は z = -標高
#
# 既定の構造(GRADIENT): 有珠山の人工地震探査(2001年、Onizawa et al. 2007)の3次元 P波速度構造を
#   Aoyama et al. (2009) の断面図から読んだ目安。地表付近(海面下 -0.5 km)2.2 km/s、海面下 1 km 3.5 km/s、
#   2 km 4.7 km/s、3.5 km 5.7 km/s、5 km より下 6.0 km/s(間は直線でつなぐ)
#   vp/vs は「1.74 一定と Vp = 1.45 Vs + 0.9(Onizawa et al. 2007)を 3:1 で混ぜたもの」(S波の遅さ 1/Vs を
#   VP_VS_WEIGHT で重み付けして平均。Aoyama et al. (2009) は山頂の浅い地震で、この2つの中間(1:1)がよいとした)。
#   1:1 では有珠山付近の地震24件で、S波が多くの拠点で計算より 0.1 秒ほど早く届いたため(海面下 0.5〜1 km で vp/vs 1.85〜1.9)、
#   1.74(和達ダイアグラムの値)の側に寄せた。3:1 で残差が最も小さく(中央値 0.100 → 0.089 秒)、
#   P波・S波のずれの偏りもほぼ 0 になった。震源は少し浅くなる(深さの中央値 0.81 → 0.71 km)
#   (検証は batch_plots/velocity/scripts/vpvs_test.py)
#   満点の地震15件では、一様な 3.0 km/s より山頂の地震の深さがそろい(海面下 0.3〜1.0 km)、
#   気象庁の火山活動解説資料の浅い地震の帯(0.5〜1 km)と合った。残差はほぼ同じ(0.11 秒ほど)
#   (検証は batch_plots/velocity/scripts/compare_models.py・tune_gradient.py)
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.interpolate import RegularGridInterpolator

# 走時表の範囲と刻み [km]。震源を探す範囲(LAYERED_HORIZONTAL・LAYERED_DEPTH)に、拠点の広がりを足した広さ
TABLE_R_MAX = 20.0
TABLE_Z_TOP = -1.0
TABLE_Z_BOTTOM = 10.0
TABLE_STEP = 0.05
# 格子のつなぎ方(となりだけでなく少し離れた点にもつなぎ、斜めの経路の誤差を小さくする。一様な構造で誤差 0.7% 以内)
NEIGHBORS = [(dr, dz) for dr in range(-4, 5) for dz in range(-4, 5)
             if (dr, dz) != (0, 0) and np.gcd(abs(dr), abs(dz)) == 1]
# 深さで速度が変わる構造で震源を探す範囲 [km](拠点の中心から水平 ±、深さの下限)
#   これより外で最良になったら、範囲の外(遠い地震)とみなす(一様な構造の HORIZONTAL_RANGE・DEPTH_RANGE と同じ扱い)
LAYERED_HORIZONTAL = 12.0
LAYERED_DEPTH = 9.0

# 構造の名前(保存する到着時刻のパラメータ "velocity" の値)
UNIFORM = "uniform"
GRADIENT = "gradient"
NAMES = {UNIFORM: "一様", GRADIENT: "勾配(先行研究)"}
DEFAULT = GRADIENT


class Model:
  # vp, vp_vs ... 深さ z [km](配列)から P波の速度 [km/s]・vp/vs を返す関数
  def __init__(self, key, vp, vp_vs):
    self.key, self.vp, self.vp_vs = key, vp, vp_vs
  def vs(self, z):
    return self.vp(z) / self.vp_vs(z)


def piecewise(points):
  zs = np.array([p[0] for p in points], dtype=float)
  vs = np.array([p[1] for p in points], dtype=float)
  return lambda z: np.interp(z, zs, vs)

# vp/vs 一定(ratio)の重み(残りが Vp = 1.45 Vs + 0.9 の重み)。0.5 で2つの中間、1 で ratio 一定
VP_VS_WEIGHT = 0.75

def middle_vp_vs(vp, ratio=1.74, weight=VP_VS_WEIGHT):
  # S波の遅さ(1/Vs)を、vp/vs 一定(ratio)のときと Vp = 1.45 Vs + 0.9 のときで重み付けして平均する
  return lambda z: vp(z) * (weight * ratio / vp(z) + (1 - weight) * 1.45 / (vp(z) - 0.9))

GRADIENT_VP = piecewise([(-1.0, 2.2), (-0.5, 2.2), (1.0, 3.5), (2.0, 4.7), (3.5, 5.7), (5.0, 6.0), (10.0, 6.1)])
MODELS = {GRADIENT: Model(GRADIENT, GRADIENT_VP, middle_vp_vs(GRADIENT_VP))}


def _graph(slowness, nr, nz):
  rows, cols, weights = [], [], []
  idx = np.arange(nr * nz).reshape(nz, nr)
  for dr, dz in NEIGHBORS:
    r0, r1 = max(0, -dr), nr - max(0, dr)
    z0, z1 = max(0, -dz), nz - max(0, dz)
    a = idx[z0:z1, r0:r1]
    b = idx[z0 + dz:z1 + dz, r0 + dr:r1 + dr]
    s = 0.5 * (slowness[z0:z1, r0:r1] + slowness[z0 + dz:z1 + dz, r0 + dr:r1 + dr])
    rows.append(a.ravel())
    cols.append(b.ravel())
    weights.append((s * TABLE_STEP * np.hypot(dr, dz)).ravel())
  n = nr * nz
  return coo_matrix((np.concatenate(weights), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()


# 深さ z_rcv の拠点から格子の各点までの走時の表 → 補間の関数 f([[z, r], ...])
def travel_table(velocity, z_rcv):
  r = np.arange(0, TABLE_R_MAX + TABLE_STEP / 2, TABLE_STEP)
  z = np.arange(TABLE_Z_TOP, TABLE_Z_BOTTOM + TABLE_STEP / 2, TABLE_STEP)
  nr, nz = len(r), len(z)
  slowness = np.repeat((1.0 / velocity(z))[:, None], nr, axis=1)
  k = int(round((z_rcv - TABLE_Z_TOP) / TABLE_STEP))
  k = min(max(k, 0), nz - 1)
  t = dijkstra(_graph(slowness, nr, nz), indices=k * nr).reshape(nz, nr)
  # 拠点を格子の点にのせたずれ(標高と格子の深さの差)の分を足す
  t = t + abs(z[k] - z_rcv) / float(velocity(np.array([z_rcv]))[0])
  return RegularGridInterpolator((z, r), t, bounds_error=False, fill_value=None)


_tables = {}

# 構造・波(P/S)・拠点の標高ごとの走時表(作り直さずに使い回す)
def table(model, phase, elevation_km):
  key = (model.key, phase, round(float(elevation_km), 3))
  if key not in _tables:
    _tables[key] = travel_table(model.vp if phase == "P" else model.vs, -float(elevation_km))
  return _tables[key]


# --- 波の通り道(3D表示で描くため) ---
#   走時表と同じ格子・同じ最短経路法で、拠点から格子の各点への最短経路の「1つ前の点」を覚えておき、
#   震源の点からたどって拠点までの通り道にする(1次元の構造なので、上から見ると拠点と震央を結ぶ直線の上)
_predecessors = {}

def ray_path(model, phase, elevation_km, r_src, z_src):
  """震源(拠点からの水平距離 r_src [km]、海面からの深さ z_src [km])から拠点までの通り道
  戻り値 ... [(水平距離 r, 深さ z), ...](震源が最初、拠点が最後。格子の刻み TABLE_STEP の折れ線)"""
  key = (model.key, phase, round(float(elevation_km), 3))
  if key not in _predecessors:
    velocity = model.vp if phase == "P" else model.vs
    r = np.arange(0, TABLE_R_MAX + TABLE_STEP / 2, TABLE_STEP)
    z = np.arange(TABLE_Z_TOP, TABLE_Z_BOTTOM + TABLE_STEP / 2, TABLE_STEP)
    nr, nz = len(r), len(z)
    slowness = np.repeat((1.0 / velocity(z))[:, None], nr, axis=1)
    k = min(max(int(round((-float(elevation_km) - TABLE_Z_TOP) / TABLE_STEP)), 0), nz - 1)
    _dist, pred = dijkstra(_graph(slowness, nr, nz), indices=k * nr, return_predecessors=True)
    _predecessors[key] = (pred, nr, nz)
  pred, nr, nz = _predecessors[key]
  i = min(max(int(round((z_src - TABLE_Z_TOP) / TABLE_STEP)), 0), nz - 1)
  j = min(max(int(round(r_src / TABLE_STEP)), 0), nr - 1)
  node, path = i * nr + j, []
  while node >= 0:
    path.append((node % nr * TABLE_STEP, TABLE_Z_TOP + node // nr * TABLE_STEP))
    node = pred[node]
  # 格子の点にのせたずれをなくすため、両端は震源・拠点の位置そのものにする
  path[0] = (float(r_src), float(z_src))
  path[-1] = (0.0, -float(elevation_km))
  return path


# 震源の候補の点ごとの走時を返す関数を作る(hypocenter.locate_grid_ps の travel に渡す)
#   pos ... 観測した拠点の座標 [km] の配列 (n, 3)。3列目は高さ(海面からの高度。上が正)
#   phases ... 観測ごとの "P"・"S"
#   戻り値の関数 travel(gx, gy, gz) は、形 gx.shape + (n,) の走時 [秒] を返す
def travel_function(model, pos, phases):
  tabs = [table(model, ph, p[2]) for p, ph in zip(pos, phases)]
  def travel(gx, gy, gz):
    gx, gy, gz = np.broadcast_arrays(np.asarray(gx, float), np.asarray(gy, float), np.asarray(gz, float))
    out = np.empty(gx.shape + (len(tabs),))
    for i, f in enumerate(tabs):
      r = np.hypot(gx - pos[i, 0], gy - pos[i, 1])
      out[..., i] = f(np.stack([gz.ravel(), r.ravel()], axis=-1)).reshape(gx.shape)
    return out
  return travel


def get(key):
  # 一様な構造(UNIFORM)は None(hypocenter はこれまでどおり直線の走時で求める)
  return MODELS.get(key)
