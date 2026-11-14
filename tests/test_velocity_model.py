# velocity_model.py(深さで速度が変わる構造の走時)と、その構造での震源の計算(hypocenter.locate_event)のテスト
import numpy as np
import pytest

import hypocenter as h
import velocity_model as vm


def test_uniform_table_matches_straight_line():
  # 一様な構造では、最短経路法の走時は直線の走時とほぼ同じ(誤差 1% 以内)
  f = vm.travel_table(lambda z: np.full_like(np.asarray(z, float), 3.0), -0.2)
  for r, z in [(0.5, 0.3), (2.0, 1.0), (5.0, 2.0), (8.0, 0.5), (3.0, 5.0)]:
    exact = np.hypot(r, z + 0.2) / 3.0
    assert f([[z, r]])[0] == pytest.approx(exact, rel=0.01)


def test_gradient_model_values():
  m = vm.get(vm.GRADIENT)
  assert m.vp(np.array([-0.5, 1.0, 2.0, 3.5]))[:] == pytest.approx([2.2, 3.5, 4.7, 5.7])
  # vp/vs は 1.74 一定と Vp = 1.45 Vs + 0.9 を 3:1 で混ぜたもの(浅いほど大きい。海面下 0.75 km で約 1.80)
  ratio = m.vp_vs(np.array([0.0, 3.0]))
  assert ratio[0] > ratio[1] > 1.74
  assert m.vp_vs(np.array([0.75]))[0] == pytest.approx(1.80, abs=0.01)
  assert vm.get(vm.UNIFORM) is None


def test_locate_event_with_gradient_model(stations):
  # 勾配の構造で作った到着時刻から、元の震源(山頂の西、海面下 0.7 km)が求め直せる
  proj = h.LocalProjection(list(stations.values()))
  xyz = {nm: np.array([*proj.to_xy(*stations[nm][:2]), stations[nm][2] / 1000]) for nm in stations}
  model = vm.get(vm.GRADIENT)
  src = (-1.5, 0.2, 0.7)
  names = sorted(xyz)
  pos = np.array([xyz[nm] for nm in names])
  t_p = vm.travel_function(model, pos, ["P"] * len(names))(*src)
  t_s = vm.travel_function(model, pos, ["S"] * len(names))(*src)
  picks = {nm: 100.0 + t for nm, t in zip(names, t_p)}
  s_picks = {nm: 100.0 + t for nm, t in zip(names, t_s)}
  res = h.locate_event(xyz, picks, s_picks, proj, model=model)
  loc = res["loc"]
  assert res["kind"] == h.KIND_LOCAL
  assert (loc["x"], loc["y"], loc["depth"]) == pytest.approx(src, abs=0.05)
  assert loc["origin"] == pytest.approx(100.0, abs=0.01)
  assert loc["depth_min"] <= 0.7 <= loc["depth_max"]
