# 「S-P 時間で震源をさがす」教材(sp_locate.py)のテスト。波形と地図のタイルは作り物を使う
from datetime import datetime, timedelta
import base64
import json
import re

import numpy as np
import obspy
import pytest

import pick_store
import sp_locate as sp
from hypocenter import LocalProjection

from conftest import ETC_DIR

ORIGIN = datetime(2026, 5, 20, 23, 51, 47, 123456)


@pytest.fixture
def proj(stations):
  return LocalProjection(list(stations.values()))


def test_read_problems(tmp_path):
  path = tmp_path / "p.csv"
  path.write_text("問題,地震,説明,正解の場所,正解の名前\n"
                  "1,20260708_112027,発破,42.5274 140.8093,採石場\n"
                  "2,20260520_235130,地震,,\n"
                  ",,,,\n", encoding="utf-8")
  probs = sp.read_problems(path)
  assert [p["no"] for p in probs] == [1, 2]
  assert probs[0]["site"] == (42.5274, 140.8093) and probs[0]["site_name"] == "採石場"
  assert probs[1]["site"] is None and probs[1]["site_name"] == "正解の場所"


def test_problem_file_in_etc():
  probs = sp.read_problems(ETC_DIR / sp.PROBLEM_FILE)
  assert 5 <= len(probs) <= 8                                  # 方針: 5〜8件
  assert len({p["key"] for p in probs}) == len(probs)
  assert all(re.fullmatch(r"\d{8}_\d{6}", p["key"]) for p in probs)
  assert probs[0]["site"] is not None                          # 最初の問題は場所がわかっている発破


def test_omori_k():
  assert sp.omori_k(3.0, 1.73) == pytest.approx(4.1, abs=0.05)
  assert sp.OMORI_K_RANGE[0] <= sp.OMORI_K <= sp.OMORI_K_RANGE[1]


def test_map_bounds_contains_stations(stations, proj):
  south, north, west, east = sp.map_bounds(stations, proj, [sp.SUMMIT])
  for nm in sp.STATIONS:
    lat, lon = stations[nm][:2]
    assert south < lat < north and west < lon < east


def fake_tile(z, x, y):
  return np.full((256, 256, 3), (x * 7 + y) % 256, dtype=np.uint8)


def test_stitch_tiles_crops_to_bounds():
  img, (south, north, west, east) = sp.stitch_tiles(42.51, 42.58, 140.75, 140.92, z=14, tile=fake_tile)
  assert img.ndim == 3 and img.shape[2] == 3
  # 切り抜いた範囲は、頼んだ範囲とほぼ同じ(1画素は 10 m ほど)
  assert south == pytest.approx(42.51, abs=2e-4) and north == pytest.approx(42.58, abs=2e-4)
  assert west == pytest.approx(140.75, abs=2e-4) and east == pytest.approx(140.92, abs=2e-4)
  # 画素の数と範囲の比が合う(東西の 1 度あたりの画素)
  assert img.shape[1] / (east - west) == pytest.approx(256 * 2 ** 14 / 360, rel=0.01)


def test_stitch_tiles_without_tiles():
  assert sp.stitch_tiles(42.51, 42.58, 140.75, 140.92, z=14, tile=lambda z, x, y: None) is None


def test_map_data(stations, proj):
  bounds = sp.map_bounds(stations, proj)
  item = sp.map_data(bounds, proj, stitch=lambda *b: sp.stitch_tiles(*b, z=13, tile=fake_tile))
  assert item["src"].startswith("data:image/jpeg;base64,")
  assert base64.b64decode(item["src"].split(",", 1)[1])[:2] == b"\xff\xd8"
  assert item["x0"] < 0 < item["x1"] and item["y0"] < 0 < item["y1"]
  assert item["credit"].startswith("出典: 地理院タイル")
  empty = sp.map_data(bounds, proj, stitch=lambda *b: None)
  assert empty["src"] == "" and empty["x0"] < empty["x1"]


def test_window_start_is_on_sample_ticks():
  t = sp.window_start(ORIGIN)
  assert t.microsecond % (1_000_000 // sp.SAMPLE_RATE) == 0
  assert timedelta(0) <= ORIGIN - timedelta(seconds=sp.BEFORE_SECONDS) - t < timedelta(seconds=1 / sp.SAMPLE_RATE)


def test_jma_hypocenter(proj):
  assert sp.jma_hypocenter({"jma": None}, proj) is None
  assert sp.jma_hypocenter({"jma": {"M": 0.3}}, proj) is None
  q = sp.jma_hypocenter({"jma": {"緯度": proj.lat0, "経度": proj.lon0, "深さ[km]": 3.0}}, proj)
  assert q == {"x": 0.0, "y": 0.0, "z": 3.0}


def make_stream(start, n=3000):
  rng = np.random.default_rng(2)
  traces = []
  for nm in ("Bamba", "Hokudai"):
    for comp in sp.COMPONENTS:
      traces.append(obspy.Trace(data=rng.normal(0, 50, n), header={
        "station": nm, "channel": comp, "sampling_rate": 250.0, "starttime": obspy.UTCDateTime(start)}))
  return obspy.Stream(traces)


def test_event_data(monkeypatch, stations, proj):
  monkeypatch.setattr(pick_store, "find_record", lambda t0, t1: (None, None))
  ref = ORIGIN.replace(microsecond=0) - timedelta(seconds=10)
  r = {"ref": ref, "lat": 42.544, "lon": 140.835,
       "loc": {"origin": (ORIGIN - ref).total_seconds(), "depth": 0.8, "at_top": False, "depth_min": 0.5, "depth_max": 1.1},
       "picks": {"Bamba": 11.5, "Hokudai": 11.0, "USUA": 10.8}, "s_picks": {"Bamba": 12.5}}
  cand = {"start": ref, "end": ref + timedelta(seconds=20), "jma": {"M": 1.2}, "type": "地震"}
  prob = {"no": 3, "key": "20260520_235137", "note": "説明", "site": (42.5274, 140.8093), "site_name": "採石場"}
  st = make_stream(ORIGIN - timedelta(seconds=3))
  info, waves = sp.event_data(prob, cand, r, st, stations, {}, proj)
  assert set(waves) == {"Bamba", "Hokudai"} and set(waves["Bamba"]) == set(sp.COMPONENTS)
  assert info["n"] == int((sp.BEFORE_SECONDS + sp.AFTER_SECONDS) * sp.SAMPLE_RATE)
  assert info["originAt"] == pytest.approx(sp.BEFORE_SECONDS, abs=0.005)
  # 読み取りは波形の最初からの秒。外部の観測点(USUA)は入れない
  assert set(info["picks"]) == {"Bamba", "Hokudai"}
  assert info["picks"]["Bamba"]["p"] == pytest.approx(info["originAt"] + 11.5 - (ORIGIN - ref).total_seconds(), abs=1e-3)
  assert info["picks"]["Bamba"]["s"] - info["picks"]["Bamba"]["p"] == pytest.approx(1.0, abs=1e-3)
  assert "s" not in info["picks"]["Hokudai"]
  assert info["app"]["z"] == 0.8 and info["m"] == "1.2" and info["jma"] is None
  assert info["site"]["name"] == "採石場"


def test_write_page(tmp_path, stations, proj):
  info = {"no": 1, "key": "k", "note": "n", "time": "2026-05-20 23:51", "type": "地震", "m": "", "n": 10, "originAt": 2.0,
          "picks": {}, "app": {"x": 0, "y": 0, "z": 1.0, "zText": "1.0"}, "jma": None, "site": None}
  waves = {"Bamba": {"U": {"d": "AAAA", "s": 1.0, "g": []}}}
  map_info = {"src": "", "x0": -5, "x1": 5, "y0": -3, "y1": 3, "credit": sp.MAP_ATTRIBUTION}
  path = tmp_path / "sp.html"
  size = sp.write_page(path, [(info, waves)], sp.station_items(stations, proj), map_info, {"x": 0, "y": 0, "name": "有珠山"})
  text = path.read_text(encoding="utf-8")
  assert size == path.stat().st_size
  assert "__META__" not in text and "__WAVES__" not in text and "__TITLE__" not in text
  meta = json.loads(re.search(r'<script type="application/json" id="meta">(.*?)</script>', text, re.S).group(1))
  assert meta["k"] == sp.OMORI_K and meta["example"]["k"] == 4.1
  assert [s["id"] for s in meta["stations"]] == sp.STATIONS
  assert json.loads(re.search(r'id="w1">(.*?)</script>', text, re.S).group(1)) == waves
  # 画面のボタン(大きく・S を読む助け)
  for button in ("mapMax", "waveMax", "commonAmp", "hintBtn", "predBtn"):
    assert f'id="{button}"' in text
