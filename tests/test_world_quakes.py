# world_quakes.py(世界の大きな地震の取得・保存・期間で探す)のテスト(ネットワークは使わない)
from datetime import datetime

import pytest

import app_paths
import world_quakes as wq

USGS_CSV = """time,latitude,longitude,depth,mag,magType,nst,gap,dmin,rms,net,id,updated,place,type
2026-02-22T16:57:46.738Z,6.8541,116.2942,629,7.1,mww,,,,,us,us7000abcd,2026-03-01T00:00:00.000Z,"57 km NNW of Kota Belud, Malaysia",earthquake
2026-02-28T15:30:00.000Z,-20.0,-70.0,30,7.0,mww,,,,,us,us7000efgh,2026-03-01T00:00:00.000Z,"Chile",earthquake
"""


@pytest.fixture
def world_dir(tmp_path, monkeypatch):
  monkeypatch.setattr(app_paths, "WORLD_QUAKES_DIR", tmp_path / "world_quakes")
  return tmp_path / "world_quakes"


def test_parse_usgs_converts_to_jst():
  quakes = wq.parse_usgs(USGS_CSV)
  assert len(quakes) == 2
  q = quakes[0]
  assert q["日時"] == datetime(2026, 2, 23, 1, 57, 46, 738000)   # 世界時 + 9時間
  assert (q["緯度"], q["経度"], q["深さ[km]"], q["M"]) == (6.8541, 116.2942, 629.0, 7.1)
  assert q["震央地名"] == "57 km NNW of Kota Belud, Malaysia" and q["USGS の ID"] == "us7000abcd"
  # 世界時の 2/28 15:30 は、日本時間では 3/1 00:30
  assert quakes[1]["日時"] == datetime(2026, 3, 1, 0, 30)


def test_save_load_and_quakes_between(world_dir):
  quakes = wq.parse_usgs(USGS_CSV)
  wq.save_csv(wq.csv_path(datetime(2026, 2, 1)), quakes[:1])
  wq.save_csv(wq.csv_path(datetime(2026, 3, 1)), quakes[1:])
  assert wq.saved_months() == [datetime(2026, 2, 1), datetime(2026, 3, 1)]
  back = wq.load_month(datetime(2026, 2, 1))
  assert back[0]["日時"] == datetime(2026, 2, 23, 1, 57, 46, 730000)   # 0.01 秒単位で保存
  assert back[0]["M"] == 7.1
  # 月をまたぐ期間でも探せる
  found = wq.quakes_between(datetime(2026, 2, 23), datetime(2026, 3, 1, 1, 0))
  assert [q["USGS の ID"] for q in found] == ["us7000abcd", "us7000efgh"]
  assert wq.quakes_between(datetime(2026, 2, 24), datetime(2026, 2, 28)) == []


def test_plan_months_refetches_last_and_this_month(world_dir, monkeypatch):
  monkeypatch.setattr(wq, "this_month", lambda: datetime(2026, 4, 1))
  wq.save_csv(wq.csv_path(datetime(2026, 1, 1)), [])
  wq.save_csv(wq.csv_path(datetime(2026, 2, 1)), [])
  months, todo = wq.plan_months(datetime(2026, 1, 1), datetime(2026, 4, 30))
  assert months == [datetime(2026, m, 1) for m in (1, 2, 3, 4)]
  # 取得済みの最後の月(2月)・取得していない月(3月)・今月(4月)
  assert todo == [datetime(2026, 2, 1), datetime(2026, 3, 1), datetime(2026, 4, 1)]
  # 終わりが今月より先でも、今月までにする
  months, _todo = wq.plan_months(datetime(2026, 3, 1), datetime(2026, 12, 1))
  assert months[-1] == datetime(2026, 4, 1)
