# 3Dの図(quake_3d_view.py)と、その部品(標高タイルの読み方・波の通り道)のテスト。インターネットには接続しない
from datetime import datetime
import json

import numpy as np

import gsi_tiles
import quake_3d_view
import velocity_model


def test_decode_dem_png():
  # 標高PNG: x = 2^16 R + 2^8 G + B。2^23 未満は x × 0.01 m、2^23 より大きければ (x − 2^24) × 0.01 m、2^23 はデータなし
  rgb = np.array([[0, 0, 0], [0, 0x20, 0xD3], [0x80, 0, 0], [0xFF, 0xFF, 0xFF]])
  h = gsi_tiles.decode_dem_png(rgb)
  assert h[0] == 0 and abs(h[1] - 84.03) < 1e-9 and np.isnan(h[2]) and abs(h[3] + 0.01) < 1e-9


def test_lake_mask_ignores_sea():
  h = np.full((10, 10), 0.3)
  h[:5, :] = 0.084        # 湖(同じ高さの平らな面)
  sea = np.zeros(h.shape, bool)
  sea[8:, :] = True       # 海(データなし。0 にしたもの)
  h[sea] = 0.0
  lake = quake_3d_view.lake_mask(h, sea)
  assert lake[:5].all() and not lake[5:].any()


def test_ray_path_ends_at_source_and_station():
  model = velocity_model.MODELS[velocity_model.DEFAULT]
  path = velocity_model.ray_path(model, "P", 0.14, 4.0, 1.0)
  assert path[0] == (4.0, 1.0) and path[-1] == (0.0, -0.14)
  rs = [p[0] for p in path]
  assert all(a >= b for a, b in zip(rs, rs[1:]))   # 拠点に向かって近づく
  # 深いほど速い構造なので、震源と拠点を結ぶ直線より深い所を通る
  assert max(p[1] for p in path) > 1.0


def fake_items():
  cand = {"start": datetime(2025, 9, 3, 23, 23), "end": datetime(2025, 9, 3, 23, 24), "jma": None, "row": {}}
  r = {"kind": "近い地震", "lat": 42.545, "lon": 140.86, "ref": datetime(2025, 9, 3, 23, 22, 30),
       "loc": {"depth": 0.8, "depth_min": 0.5, "depth_max": 1.2, "rms": 0.05, "origin": 33.0, "at_top": False},
       "picks": {"Mimatsu": 33.4, "USUA": 33.5, "Nowhere": 34.0}, "s_picks": {},
       "external": {"USUA": (42.5283, 140.8362, 246.0)}}
  far = {"kind": "遠い地震", "lat": None, "lon": None}
  return [(cand, r), (cand, far)]


def test_write_3d_view(tmp_path, monkeypatch):
  # 標高タイルの代わりに、山頂付近が高い形を使う
  def fake_elevations(lats, lons, z=None):
    return 700.0 * np.exp(-(((lats - 42.5442) / 0.01) ** 2 + ((lons - 140.8392) / 0.013) ** 2)) + 50.0
  monkeypatch.setattr(gsi_tiles, "elevations", fake_elevations)
  # 波形の画像は、HTML と同じフォルダの下の images/ にあるものとする(HTML からの相対パスになる)
  monkeypatch.setattr(quake_3d_view, "waveform_image", lambda cand, r, usu=None: tmp_path / "images" / "w.png")
  monkeypatch.setattr(quake_3d_view, "usu_list_images", lambda: {})
  stations = {"Mimatsu": (42.54075, 140.85705, 189.8), "Bamba": (42.53533, 140.78266, 17.1)}
  path = tmp_path / "test_3d.html"
  n = quake_3d_view.write_3d_view(path, fake_items(), stations, "テスト")
  assert n == 1   # 遠い地震は描かない
  page = path.read_text(encoding="utf-8")
  assert quake_3d_view.PLOTLY_URL in page and gsi_tiles.DEM_ATTRIBUTION in page
  fig = json.loads(page.split("const fig = ", 1)[1].split(";\nconst meta", 1)[0])
  meta = json.loads(page.split("const meta = ", 1)[1].split(";\nconst gd", 1)[0])
  names = [t["name"] for t in fig["data"]]
  assert names[0] == "地形" and "震源" in names and "外部の観測点" in names
  quake = fig["data"][names.index("震源")]
  assert quake["z"] == [-0.8] and quake["error_z"]["array"] == [0.3] and quake["error_z"]["arrayminus"] == [0.4]
  # 通り道は、位置の分かる拠点(Mimatsu と外部の USUA)の2本。はじめは隠す
  ray = fig["data"][meta["rays"][0]]
  assert ray["name"].startswith("P波の通り道") and ray["visible"] is False and ray["x"].count(None) == 2
  # 時刻: 震源の発生時刻(23:23:03.00)と、2つの拠点の走時(到着時刻は一覧に出す)
  label = fig["data"][meta["labels"][0]]
  assert label["visible"] is False and label["text"][0] == "発生 23:23:03.00"
  assert sorted(label["text"][1:]) == ["P +0.40秒", "P +0.50秒"]
  assert meta["quakes"][0]["lines"][0].startswith("発生 23:23:03.00") and len(meta["quakes"][0]["lines"]) == 3
  assert "P 23:23:03.40(走時 0.40 秒)" in meta["quakes"][0]["lines"][1]
  # 切り替えに使う情報: 側面は地形の次の4つ、縦の倍率に 1.2 倍・1.5 倍もある
  assert meta["sides"] == [1, 2, 3, 4]
  ex = [(e["ex"], e["above"]) for e in meta["exaggerations"]]
  assert (1.2, 1) in ex and (1.5, 1) in ex and (1, 1.5) in ex   # 海面より上だけ 1.5 倍もある
  # 震源の番号(地図で見て選べるように)。地表と側面は押せない(後ろの震源を押せるように)
  assert fig["data"][meta["number_trace"]]["text"] == ["1"]
  assert all(fig["data"][i]["hoverinfo"] == "skip" for i in [meta["terrain"]] + meta["sides"])
  # 波の動き: P波は位置の分かる2拠点、S波は S波を読んだ拠点だけ(この例では無い)。時刻は震源で 0、拠点に向かって増える
  waves = meta["quakes"][0]["waves"]
  assert len(waves["P"]) == 2 and waves["S"] == []
  for path in waves["P"]:
    ts = [p[3] for p in path]
    assert ts[0] == 0 and all(a <= b for a, b in zip(ts, ts[1:])) and 0.2 < ts[-1] < 1.5
    assert path[0][2] == -0.8   # 震源の深さから出る
  assert len(meta["waves"]) == 2 and all(fig["data"][i]["visible"] is False for i in meta["waves"])
  assert meta["slowdowns"] == [5, 10, 20]
  assert meta["quakes"][0]["image"] == "images/w.png"
