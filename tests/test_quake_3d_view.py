# 3Dの図(quake_3d_view.py)と、その部品(標高タイルの読み方・波の通り道)のテスト。インターネットには接続しない
from datetime import datetime
import json
import re

import numpy as np
import pytest

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
  assert meta["slowdowns"] == [1, 5, 10, 20]
  # P波・S波の印は同じ形(記号は端末の文字で描かれ、◆ は端末によって大きさが変わるため)
  marks = [fig["data"][i]["marker"] for i in meta["waves"]]
  assert marks[0].get("symbol") == marks[1].get("symbol") and marks[0]["size"] == marks[1]["size"]
  assert meta["quakes"][0]["image"] == "images/w.png"
  assert meta["quakes"][0]["key"] == "20250903_232300" and meta["wave_page"] == ""   # 有珠山の一覧でないので波形のページは無い


def test_wave_page_link_only_when_page_exists(tmp_path, monkeypatch):
  monkeypatch.setattr(gsi_tiles, "elevations", lambda lats, lons, z=None: np.full(np.shape(lats), 100.0))
  monkeypatch.setattr(quake_3d_view, "waveform_image", lambda cand, r, usu=None: None)
  monkeypatch.setattr(quake_3d_view, "usu_list_images", lambda: {})
  stations = {"Mimatsu": (42.54075, 140.85705, 189.8), "Bamba": (42.53533, 140.78266, 17.1)}
  path = tmp_path / "usu_quakes_3d.html"
  def wave_page():
    quake_3d_view.write_3d_view(path, fake_items(), stations, quake_3d_view.USU_LIST_TITLE)
    return json.loads(path.read_text(encoding="utf-8").split("const meta = ", 1)[1].split(";\nconst gd", 1)[0])["wave_page"]
  assert wave_page() == ""
  (tmp_path / quake_3d_view.WAVE_PAGE_FILE).write_text("", encoding="utf-8")
  assert wave_page() == "usu_quakes_waves.html"


def test_html_file_name_is_ascii():
  name = quake_3d_view.html_file_name
  assert name("有珠山付近の地震") == "usu_quakes_3d.html"
  assert name("地震リスト_有珠山付近の地震") == "usu_quakes_3d.html"
  assert name("usu_candidates_17") == "usu_candidates_17_3d.html"
  # 日本語などを含む題名は英数字だけにし、題名が違えば別の名前になる
  a, b = name("S波の読み直し候補"), name("S波の見直し候補")
  assert a != b and re.fullmatch(r"[A-Za-z0-9_-]+\.html", a) and re.fullmatch(r"[A-Za-z0-9_-]+\.html", name("発破"))


def test_notes_have_speeds_and_acknowledgement(tmp_path, monkeypatch):
  monkeypatch.setattr(gsi_tiles, "elevations", lambda lats, lons, z=None: np.full(np.shape(lats), 100.0))
  monkeypatch.setattr(quake_3d_view, "waveform_image", lambda cand, r, usu=None: None)
  monkeypatch.setattr(quake_3d_view, "usu_list_images", lambda: {})
  stations = {"Mimatsu": (42.54075, 140.85705, 189.8), "Bamba": (42.53533, 140.78266, 17.1)}
  fig, meta, _n = quake_3d_view.figure(fake_items(), stations, "テスト")
  note = fig["layout"]["annotations"][0]["text"]
  model = velocity_model.MODELS[velocity_model.DEFAULT]
  assert f"S波 {float(model.vs(0.0)):.1f} / " in note and f"vp/vs {float(model.vp_vs(0.0)):.2f} / " in note
  assert quake_3d_view.ACKNOWLEDGEMENT in note and "V-net" in quake_3d_view.ACKNOWLEDGEMENT
  assert quake_3d_view.ACKNOWLEDGEMENT_SHORT in meta["attribution"]   # 幅の狭い画面で出す短い説明にも入れる


def test_pick_filter_keeps_list_numbers(tmp_path, monkeypatch):
  monkeypatch.setattr(gsi_tiles, "elevations", lambda lats, lons, z=None: np.full(np.shape(lats), 100.0))
  monkeypatch.setattr(quake_3d_view, "waveform_image", lambda cand, r, usu=None: None)
  monkeypatch.setattr(quake_3d_view, "usu_list_images", lambda: {})
  stations = {"Mimatsu": (42.54075, 140.85705, 189.8), "Bamba": (42.53533, 140.78266, 17.1)}
  (cand, r), _far = fake_items()
  few = dict(r, picks={"Mimatsu": 33.4})
  items = [({**cand, "start": datetime(2025, 9, 1)}, few), (cand, r)]
  keep, title, suffix = quake_3d_view.pick_filter(stations, min_picks=2)
  assert quake_3d_view.USU_LIST_TITLE in title and suffix == "_p2"
  _fig, meta, n = quake_3d_view.figure(items, stations, title, keep=keep)
  assert n == 1 and meta["quakes"][0]["no"] == 2 and meta["quakes"][0]["short"].startswith("No.2 ")   # 一覧の番号のまま
  keep, _title, suffix = quake_3d_view.pick_filter(stations, min_manten=2)
  assert suffix == "_m2" and not keep(2, cand, r)   # 満点の拠点は Mimatsu だけ
  assert quake_3d_view.pick_filter(stations) == (None, quake_3d_view.USU_LIST_TITLE, "")
  keep, title, suffix = quake_3d_view.pick_filter(stations, only={1})
  assert suffix == "_1" and "1件" in title
  _fig, meta, n = quake_3d_view.figure(items, stations, title, keep=keep)
  assert n == 1 and meta["quakes"][0]["no"] == 1


def test_bottom_depth_follows_deepest_point():
  q = {"depth": 1.0, "err_down": 1.4, "waves": {"S": [[[0, 0, -2.15, 0.0]]]}}
  assert quake_3d_view.bottom_depth([(q, [[(0, 0, -2.1)]])]) == 2.5          # 2.4 km + 余白 → 0.5 km 刻みで 2.5 km
  deep = dict(q, waves={"S": [[[0, 0, -2.45, 0.0]]]})
  assert quake_3d_view.bottom_depth([(deep, [])]) == 3.0                     # 通り道が深ければ、その下まで
  assert quake_3d_view.bottom_depth([]) == quake_3d_view.BOTTOM_MIN_KM


def test_lake_floor_uses_bed_only_inside_the_lake():
  class FakeDem:   # どこでも標高 -50 m
    def elevations(self, lat, lon):
      return np.full(np.shape(lat), -50.0)
  stations = {"Mimatsu": (42.54075, 140.85705, 189.8), "Bamba": (42.53533, 140.78266, 17.1)}
  proj = quake_3d_view.LocalProjection(list(stations.values()))
  x, y = np.arange(3.0), np.arange(2.0)
  h = np.array([[0.0, 0.084, 0.3], [0.0, 0.084, 0.084]])
  water = np.array([[True, True, False], [True, True, True]])      # 左の列は海(0 m)、0.084 km は湖
  floor, lake, level, depth = quake_3d_view.lake_floor(x, y, h, water, proj, FakeDem())
  assert level == pytest.approx(0.084) and lake.tolist() == [[False, True, False], [False, True, True]]
  assert floor[0, 1] == pytest.approx(-0.05) and floor[0, 0] == 0.0 and floor[0, 2] == 0.3
  assert depth[1, 2] == pytest.approx(0.134) and depth[0, 0] == 0.0
  # 湖底の形が無ければ、そのまま
  same, _lake, level, depth = quake_3d_view.lake_floor(x, y, h, water, proj, None)
  assert level is None and np.array_equal(same, h) and not depth.any()
