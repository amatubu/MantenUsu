# 平面直角座標系の GeoTIFF を緯度・経度で読む(plane_dem.py)のテスト。GeoTIFF は小さな作り物を使う
import numpy as np
import pytest
from PIL import Image, TiffImagePlugin, TiffTags

import plane_dem

XI = plane_dem.ZONES[6679]


def test_origin_is_zero_and_axes_point_north_and_east():
  north, east = plane_dem.to_plane(44.0, 140.25, XI)
  assert north == pytest.approx(0, abs=1e-6) and east == pytest.approx(0, abs=1e-6)
  n1, _ = plane_dem.to_plane(44.01, 140.25, XI)
  _, e1 = plane_dem.to_plane(44.0, 140.26, XI)
  assert n1 == pytest.approx(1111.8, abs=1.0)          # 緯度 0.01 度は約 1.11 km(縮尺 0.9999)
  assert e1 == pytest.approx(800.0, abs=2.0)           # 北緯 44 度で経度 0.01 度は約 0.80 km


def write_tiff(path, values, left, top, step=10.0, epsg=6679):
  ifd = TiffImagePlugin.ImageFileDirectory_v2()
  ifd[33550] = (step, step, 0.0)
  ifd.tagtype[33550] = TiffTags.DOUBLE
  ifd[33922] = (0.0, 0.0, 0.0, left, top, 0.0)
  ifd.tagtype[33922] = TiffTags.DOUBLE
  ifd[34735] = (1, 1, 0, 2, 1024, 0, 1, 1, 3072, 0, 1, epsg)
  ifd.tagtype[34735] = TiffTags.SHORT
  Image.fromarray(np.asarray(values, dtype=np.float32), mode="F").save(path, tiffinfo=ifd)


def test_reads_values_at_lat_lon(tmp_path):
  lat, lon = 42.6, 140.8
  north, east = plane_dem.to_plane(lat, lon, XI)
  # 左上の角を、この点が (行 1.5, 列 2.5) の画素の中心の間に来るように置く
  left, top = float(east) - 30.0, float(north) + 20.0
  values = np.arange(20, dtype=float).reshape(4, 5) * 10      # 右へ 10、下へ 50 ずつ増える
  write_tiff(tmp_path / "dem.tif", values, left, top)
  dem = plane_dem.PlaneDem(tmp_path / "dem.tif")
  # (行 1.5, 列 2.5) は 4 点 (1,2)=70 (1,3)=80 (2,2)=120 (2,3)=130 の真ん中
  assert dem.elevations(np.array([lat]), np.array([lon]))[0] == pytest.approx(100.0, abs=0.5)
  assert np.isnan(dem.elevations(np.array([lat + 0.01]), np.array([lon]))[0])   # 範囲の外


def test_rejects_other_coordinate_systems(tmp_path):
  write_tiff(tmp_path / "dem.tif", np.zeros((2, 2)), 0.0, 0.0, epsg=6677)
  with pytest.raises(ValueError):
    plane_dem.PlaneDem(tmp_path / "dem.tif")
