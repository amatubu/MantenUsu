# hypocenter.py(震源の計算と判定)のテスト
#   拠点の座標は Git で管理している etc/manten_ch.tbl のものを使い、到着時刻は計算で作る
import numpy as np
import pytest

import hypocenter as h
import sp_distance

ORIGIN = 100.0   # 発生時刻(基準からの秒)


@pytest.fixture(scope="module")
def setup(stations):
  proj = h.LocalProjection(list(stations.values()))
  xyz = {nm: np.array([*proj.to_xy(*stations[nm][:2]), stations[nm][2] / 1000]) for nm in stations}
  return proj, xyz


# 震源 (x, y, 深さ) から各拠点への到着時刻(均質な速度構造。locate_grid_ps と同じ考え方)
def arrivals(xyz, src, vp=h.LOCAL_VP, vp_vs=h.LOCAL_VP_VS):
  x, y, z = src
  picks, s_picks = {}, {}
  for nm, (sx, sy, sz) in xyz.items():
    dist = np.sqrt((x - sx) ** 2 + (y - sy) ** 2 + (z + sz) ** 2)
    picks[nm] = ORIGIN + dist / vp
    s_picks[nm] = ORIGIN + dist / (vp / vp_vs)
  return picks, s_picks


def test_projection_round_trip(setup):
  proj, _xyz = setup
  lat, lon = proj.to_latlon(3.0, -2.0)
  assert proj.to_xy(lat, lon) == pytest.approx((3.0, -2.0))


def test_destination_one_degree_north():
  lat, lon = h.destination(42.0, 140.0, 0.0, h.EARTH_RADIUS_KM * np.radians(1.0))
  assert (lat, lon) == pytest.approx((43.0, 140.0))


@pytest.mark.parametrize("azimuth", [30.0, 186.0, 300.0])
def test_fit_plane_wave(setup, azimuth):
  _proj, xyz = setup
  names = sorted(xyz)
  xy = np.array([xyz[nm][:2] for nm in names])
  # 方位 azimuth の遠くから、見かけの速度 6 km/s で届く平面波
  direction = np.array([np.sin(np.radians(azimuth)), np.cos(np.radians(azimuth))])
  t = ORIGIN - xy @ direction / 6.0
  az, app_v, rms = h.fit_plane_wave(xy, t)
  assert az == pytest.approx(azimuth, abs=0.01)
  assert app_v == pytest.approx(6.0)
  assert rms == pytest.approx(0.0, abs=1e-9)


# --- 近い地震 ---

def test_local_quake_is_located(setup):
  proj, xyz = setup
  src = (1.0, -0.5, 2.5)
  picks, s_picks = arrivals(xyz, src)
  s_picks.pop("Bamba")   # Bamba の S波は最初は使わない(S_EXCLUDED_DEFAULT)
  res = h.locate_event(xyz, picks, s_picks, proj, confirmed=False)
  assert res["kind"] == h.KIND_LOCAL
  loc = res["loc"]
  assert (loc["x"], loc["y"], loc["depth"]) == pytest.approx(src, abs=0.05)
  assert loc["origin"] == pytest.approx(ORIGIN, abs=0.02)
  assert loc["rms"] < 0.01
  assert (res["n_p"], res["n_s"]) == (4, 3)
  assert (res["lat"], res["lon"]) == pytest.approx(proj.to_latlon(*src[:2]), abs=1e-3)
  assert h.depth_short(loc) == "2.5"


def test_p_only_needs_five_arrivals(setup):
  proj, xyz = setup
  picks, _s = arrivals(xyz, (0.5, 0.5, 1.0))
  # P波だけ(4つ)では到着時刻が足りない(5つ以上必要)。到来方向は求まる
  res = h.locate_event(xyz, picks, {}, proj)
  assert res["kind"] == h.KIND_NONE and res["loc"] is None and res["azimuth"] is not None


# 深さの表示: 浅い方の端(-1 km)まで残差がほぼ同じときは「◯ km より浅い」
@pytest.mark.parametrize("loc, text, short, plot", [
  (dict(depth=1.7, at_top=False, depth_min=1.3, depth_max=2.1), "1.7 km(1.3〜2.1 km)", "1.7", 1.7),
  (dict(depth=-0.5, at_top=False, depth_min=-1.0, depth_max=0.5), "浅い(0.5 km より浅い)", "<0.5", 0.0),
  (dict(depth=-1.0, at_top=True, depth_min=-1.0, depth_max=-0.3), "浅い(-0.3 km より浅い)", "<-0.3", -0.3),
  (dict(depth=-1.0, at_top=True, depth_min=None, depth_max=None), "浅い(深さは決まらない)", "浅い", 0.0),
  (dict(depth=2.0, at_top=False, depth_min=None, depth_max=None), "2.0 km", "2.0", 2.0),
])
def test_depth_text(loc, text, short, plot):
  assert h.depth_text(loc) == text
  assert h.depth_short(loc) == short
  assert h.depth_for_plot(loc) == pytest.approx(plot)


# 到着時刻が5つ(計算に要る最低の数)のとき: 自動の検出なら要確認、手で確かめたものなら近い地震
@pytest.mark.parametrize("confirmed, expected", [(False, h.KIND_LOCAL_FEW), (True, h.KIND_LOCAL)])
def test_five_arrivals(setup, confirmed, expected):
  proj, xyz = setup
  picks, s_picks = arrivals(xyz, (0.0, 1.0, 3.0))
  picks.pop("Bamba")
  s_picks = {nm: s_picks[nm] for nm in ("Mimatsu", "Nizaemn")}
  res = h.locate_event(xyz, picks, s_picks, proj, confirmed=confirmed)
  assert res["n_p"] + res["n_s"] == h.MIN_OBSERVATIONS
  assert res["kind"] == expected
  assert res["kind"] in h.LOCAL_KINDS


def test_large_residual_with_short_sp(setup):
  proj, xyz = setup
  picks, s_picks = arrivals(xyz, (1.0, 0.0, 3.0))
  s_picks.pop("Bamba")
  picks["Hokudai"] += 0.6   # 1拠点の P波の検出がずれた
  for confirmed in (False, True):
    res = h.locate_event(xyz, picks, s_picks, proj, confirmed=confirmed)
    # S-P時間が短い(近い)のに残差が大きいときは、遠い地震ではなく「近い地震(残差大)」
    assert res["loc"]["rms"] > h.MAX_LOCAL_RMS
    assert res["kind"] == h.KIND_LOCAL_POOR


# --- 遠い地震 ---

def far_arrivals(xyz, azimuth, sp, app_v=7.0):
  direction = np.array([np.sin(np.radians(azimuth)), np.cos(np.radians(azimuth))])
  picks = {nm: ORIGIN - p[:2] @ direction / app_v for nm, p in xyz.items()}
  return picks, {nm: t + sp for nm, t in picks.items()}


def test_far_quake_uses_travel_time_table(setup):
  proj, xyz = setup
  picks, s_picks = far_arrivals(xyz, 186.0, 73.5)
  res = h.locate_event(xyz, picks, s_picks, proj)
  assert res["kind"] == h.KIND_FAR
  far = res["far"]
  assert res["azimuth"] == pytest.approx(186.0, abs=0.01)
  assert far["sp"] == pytest.approx(73.5)
  assert far["k"] is None
  assert far["distance"] == pytest.approx(sp_distance.epicentral(73.5))
  assert far["distance"] == pytest.approx(718, abs=5)   # 茨城県南部の地震(震源リストでは 730 km)
  assert far["hypo"] == pytest.approx(sp_distance.hypocentral(73.5))
  assert "走時表" in h.far_method_text(far)
  # 震央は拠点の中心から南(186°)へ震央距離だけ進んだ点
  lat, lon = h.destination(proj.lat0, proj.lon0, 186.0, far["distance"])
  assert (res["lat"], res["lon"]) == pytest.approx((lat, lon))


def test_far_quake_with_omori(setup):
  proj, xyz = setup
  picks, s_picks = far_arrivals(xyz, 186.0, 73.5)
  res = h.locate_event(xyz, picks, s_picks, proj, k=9.0)
  assert res["kind"] == h.KIND_FAR
  assert res["far"]["distance"] == pytest.approx(9.0 * 73.5)
  assert res["far"]["hypo"] == pytest.approx(9.0 * 73.5)
  assert h.far_method_text(res["far"]) == "S-P 73.5 秒 × k 9.0"


def test_far_quake_needs_s_waves(setup):
  proj, xyz = setup
  picks, _s = far_arrivals(xyz, 90.0, 30.0)
  res = h.locate_event(xyz, picks, {}, proj)
  assert res["kind"] == h.KIND_NONE
  assert res["azimuth"] == pytest.approx(90.0, abs=0.01)   # 到来方向は P波だけで求まる


def test_too_few_stations(setup):
  proj, xyz = setup
  picks, s_picks = arrivals(xyz, (0.0, 0.0, 2.0))
  picks = {nm: picks[nm] for nm in ("Hokudai", "Mimatsu")}
  res = h.locate_event(xyz, picks, {nm: s_picks[nm] for nm in picks}, proj)
  assert res["kind"] == h.KIND_NONE and res["azimuth"] is None
