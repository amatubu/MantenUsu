# quake_web_map.py(地震の候補の震源を、ブラウザで見る地図の HTML に書き出す)のテスト
import json
import re
from datetime import datetime

import quake_web_map


def result(kind, lat, lon, depth, rms, manual=""):
  loc = {"x": 0, "y": 0, "depth": depth, "rms": rms, "origin": 2.0, "at_top": False, "at_edge": False,
         "depth_min": depth - 0.5, "depth_max": depth + 0.5}
  return {"kind": kind, "lat": lat, "lon": lon, "loc": loc, "ref": datetime(2026, 6, 23, 12, 58, 40), "manual": manual}


def test_write_web_map(stations, tmp_path):
  jma = {"日時": datetime(2026, 6, 23, 12, 58, 41, 500000), "緯度": 42.55, "経度": 140.84, "深さ[km]": 2.0,
         "M": 0.5, "震央地名": "胆振地方西部"}
  items = [
    ({"start": datetime(2026, 6, 23, 12, 58, 40), "jma": jma, "row": {"メモ": "発破の可能性 <確認>"}},
     result("近い地震", 42.53, 140.81, 1.5, 0.05, manual="x.json")),
    ({"start": datetime(2026, 6, 24, 1, 0, 0), "jma": None, "row": {}}, result("近い地震(残差大)", 42.54, 140.85, 3.0, 0.2)),
    ({"start": datetime(2026, 6, 25, 1, 0, 0), "jma": None, "row": {}},
     {"kind": "遠い地震", "lat": 43.5, "lon": 142.0, "loc": None}),
  ]
  path = tmp_path / "map" / "test_map.html"
  assert quake_web_map.write_web_map(path, items, stations, "テスト <一覧>") == 2   # 遠い地震は描かない
  page = path.read_text(encoding="utf-8")
  assert "<title>テスト &lt;一覧&gt;</title>" in page
  data = json.loads(re.search(r"const DATA = (.*);\n", page).group(1))
  q1, q2 = data["quakes"]
  assert (q1["no"], q1["time"], q1["good"], q1["manual"]) == (1, "2026-06-23 12:58:42.0", True, True)
  assert q1["memo"] == "発破の可能性 <確認>" and q1["jma"]["name"] == "胆振地方西部"
  assert q2["good"] is False and q2["jma"] is None
  assert q1["color"] != q2["color"]   # 深さで色が変わる
  assert data["others"] == 1 and len(data["stations"]) == len(stations)
