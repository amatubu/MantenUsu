# sp_distance.py(S-P時間から震央距離を求める走時表)のテスト
import numpy as np
import pytest

import sp_distance


def test_table_is_increasing():
  # 表から逆に引く(S-P時間 → 距離)ので、距離・S-P時間とも増えていく必要がある
  dist = [d for d, _ in sp_distance.TABLE]
  sp = [s for _, s in sp_distance.TABLE]
  assert all(b > a for a, b in zip(dist, dist[1:]))
  assert all(b > a for a, b in zip(sp, sp[1:]))


@pytest.mark.parametrize("dist, sp", sp_distance.TABLE[::7])
def test_table_points_round_trip(dist, sp):
  assert sp_distance.epicentral(sp) == pytest.approx(dist, abs=0.01)


@pytest.mark.parametrize("sp, expected, tol", [
  (12.6, 100, 5),     # iasp91・深さ 10 km で 100 km の S-P時間(深さ 30 km の表でもほぼ同じ距離)
  (23.7, 213, 5),
  (73.5, 718, 5),     # 2026/8/23 02:00 の茨城県南部の地震(手で指定した S-P時間。震源リストの震央距離は 730 km)
  (75.0, 734, 5),
])
def test_known_distances(sp, expected, tol):
  assert sp_distance.epicentral(sp) == pytest.approx(expected, abs=tol)


def test_short_sp_is_zero():
  # 仮定した深さ(30 km)の真上でも S-P時間は 3.6 秒あるので、それより短いと 0 km
  assert sp_distance.epicentral(1.0) == 0.0


def test_extrapolates_beyond_table():
  last_dist, last_sp = sp_distance.TABLE[-1]
  assert sp_distance.epicentral(last_sp + 10) > last_dist


def test_hypocentral_includes_depth():
  assert sp_distance.hypocentral(1.0) == pytest.approx(sp_distance.ASSUMED_DEPTH)
  epi = sp_distance.epicentral(50.0)
  assert sp_distance.hypocentral(50.0) == pytest.approx(np.hypot(epi, sp_distance.ASSUMED_DEPTH))
