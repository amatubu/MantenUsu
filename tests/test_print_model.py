# 3Dプリントの模型(print_model.py)の、柱の積み重ねと閉じた形の作り方のテスト
from collections import Counter

import numpy as np
import pytest

import print_model


def edge_counts(tris):
  # 閉じた形なら、どの辺も向きを逆にして2回ずつ使われる
  c = Counter()
  for t in np.round(tris, 4):
    for a, b in ((0, 1), (1, 2), (2, 0)):
      c[(tuple(t[a]), tuple(t[b]))] += 1
  return c


def test_paint_splits_segments():
  cols = print_model.Columns(1, 1)
  cols.stacks[0][0] = [[0, 10, "layer3"], [10, 20, "mountain"]]
  cols.paint(0, 0, 8, 12, "quake")
  assert cols.stacks[0][0] == [[0, 8, "layer3"], [8, 12, "quake"], [12, 20, "mountain"]]
  cols.paint(0, 0, 18, 30, "water")   # 柱の上にはみ出す分は塗らない
  assert cols.stacks[0][0][-1] == [18, 20, "water"]


def test_mesh_is_closed():
  cols = print_model.Columns(3, 2)
  for j in range(2):
    for i in range(3):
      cols.stacks[j][i] = [[0, 5, "layer1"], [5, 5 + i + j, "mountain"]] if i + j else [[0, 5, "layer1"]]
  cols.paint(1, 0, 2, 3, "quake")   # 中に埋め込んだ色
  for mat in ("layer1", "mountain", "quake"):
    tris = print_model.mesh(cols, mat, 0.0, 0.0, 1.0)
    c = edge_counts(tris)
    # 辺 (a→b) の数と (b→a) の数が同じ(穴が無く、面の向きがそろっている)
    assert all(c[(a, b)] == c[(b, a)] for a, b in c), mat


def test_write_stl(tmp_path):
  tris = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]]], dtype=np.float32)
  path = tmp_path / "t.stl"
  print_model.write_stl(path, tris, "t")
  data = path.read_bytes()
  assert len(data) == 84 + 50 and int.from_bytes(data[80:84], "little") == 1


def signed_volume(tris):
  t = tris.astype(np.float64)
  return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)


def test_mesh_volume_and_outward_faces():
  # 面が外を向いていれば、体積が正で、柱の区間の長さの合計と同じになる
  cols = print_model.Columns(2, 2)
  cols.stacks = [[[[0, 3, "layer1"]], [[0, 5, "layer1"]]], [[[0, 4, "layer1"]], [[0, 2, "layer1"]]]]
  cols.paint(1, 0, 1, 2, "quake")
  assert abs(signed_volume(print_model.mesh(cols, "layer1", 0.0, 0.0, 2.0)) - (3 + 4 + 4 + 2) * 4) < 1e-6
  assert abs(signed_volume(print_model.mesh(cols, "quake", 0.0, 0.0, 2.0)) - 1 * 4) < 1e-6


def test_heightfield_solid_and_cut():
  # なめらかな地形の閉じた形(manifold3d が無ければ飛ばす)。体積は高さの格子の台形の和と同じ
  import pytest
  manifold3d = pytest.importorskip("manifold3d")
  top = np.array([[2.0, 3.0, 2.0], [2.0, 4.0, 2.0]])
  m = print_model.heightfield_solid(top)
  g = print_model.GRID_MM
  expected = sum((top[j, i] + top[j, i + 1] + top[j + 1, i + 1] + top[j + 1, i]) / 4 * g * g
                 for j in range(1) for i in range(2))
  # 四角を2つの三角形に分けるので、山の形では台形の和から少しずれる
  assert abs(m.volume() - expected) < 0.1 * g * g
  # 切り口で2つに分けると、体積の和は元と同じ
  a = m.trim_by_plane((1, 0, 0), g * 0.7)
  b = m.trim_by_plane((-1, 0, 0), -g * 0.7)
  assert abs(a.volume() + b.volume() - m.volume()) < 1e-6


def test_tube_is_closed():
  import pytest
  pytest.importorskip("manifold3d")
  t = print_model.tube([(0, 0, 0), (5, 0, 0), (5, 5, 2)], 0.5)
  assert t.volume() > 0 and t.genus() == 0


def test_river_path_follows_connected_lines():
  # 2本の線(タイルの境目で切れた川)と、離れた別の線。始まりから終わりまで、つながった線だけをたどる
  lines = [[(140.90, 42.55), (140.89, 42.54), (140.88, 42.53)],
           [(140.88, 42.53), (140.87, 42.52)],
           [(140.80, 42.50), (140.79, 42.49)]]
  path = print_model.river_path(lines, (140.905, 42.551), (140.869, 42.519))
  assert path[0] == (140.90, 42.55) and path[-1] == (140.87, 42.52)
  assert (140.80, 42.50) not in path


def test_resample():
  pts = print_model.resample([(0, 0), (3, 0), (3, 4)], 1.0)
  assert len(pts) == 8 and np.allclose(pts[-1], (3, 4)) and np.allclose(pts[3], (3, 0))


def test_fold_ray_continues_on_east_west_face():
  # 切り口は p0 から南西へ。拠点は p0 の東にあるので、線は p0 を通って東西の切り口(y = p0 の y)を拠点の東西の位置まで進む
  p0 = np.array([100.0, 50.0])
  d = np.array([-0.8, -0.6])
  xy = [(60.0, 20.0), (100.0, 50.0 - 1e-9), (120.0, 65.0), (140.0, 80.0)]
  pts = print_model.fold_ray(xy, [10.0, 20.0, 30.0, 40.0], p0, d)
  assert pts[0] == pytest.approx((60.0, 20.0, 10.0))
  assert pts[-1] == pytest.approx((140.0, 50.0, 40.0))     # 拠点の東西の位置、高さはそのまま
  assert all(p[1] == pytest.approx(50.0) for p in pts[2:])  # p0 より先は東西の切り口の上
  assert any(p[:2] == pytest.approx((100.0, 50.0)) for p in pts)
  # 拠点が切り口の側なら、折り返さない
  on = print_model.fold_ray([(60.0, 20.0), (80.0, 35.0)], [1.0, 2.0], p0, d)
  assert on == [pytest.approx((60.0, 20.0, 1.0)), pytest.approx((80.0, 35.0, 2.0))]


def test_split_runs_cuts_below_height():
  pts = [(0, 0, 5.0), (1, 0, 1.0), (2, 0, -1.0), (3, 0, 1.0), (4, 0, 5.0)]
  runs = print_model.split_runs(pts, 0.5)
  assert len(runs) == 2
  assert runs[0][0] == (0, 0, 5.0) and runs[0][-1][2] == pytest.approx(0.5) and runs[0][-1][0] == pytest.approx(1.25)
  assert runs[1][0][2] == pytest.approx(0.5) and runs[1][-1] == (4, 0, 5.0)


def test_remove_small_regions():
  m = np.zeros((10, 10), bool)
  m[0:4, 0:4] = True      # 16 個
  m[8, 8] = True          # 1 個(消える)
  r = print_model.remove_small_regions(m, 4)
  assert r[0:4, 0:4].all() and not r[8, 8]


def test_split_runs_stops_below_ceiling():
  # 上の限界(ここでは高さ 3)を越える所で線を止める
  pts = [(0, 0, 1.0), (1, 0, 2.0), (2, 0, 4.0), (3, 0, 5.0)]
  runs = print_model.split_runs(pts, 0.5, lambda p: 3.0)
  assert len(runs) == 1 and runs[0][-1][2] == pytest.approx(3.0) and runs[0][-1][0] == pytest.approx(1.5)
