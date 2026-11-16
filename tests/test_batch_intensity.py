# batch_intensity.py(地震のリストの計測震度をまとめて求める)のテスト
from datetime import datetime

import batch_intensity as bi


def test_read_events_default_and_given_range(tmp_path):
  path = tmp_path / "events.csv"
  path.write_text(
    "発生日時,震央地名,M,深さ,比べる震度,開始,終了\n"
    "2025-12-08 23:15,青森県東方沖,7.5,54 km,4,,\n"
    "2026/03/10 00:45:30,檜山地方,5.8,145 km,2(洞爺町),2026-03-10 00:44,2026-03-10 00:50\n"
    "日時なし,,,,,,\n",
    encoding="utf-8")
  events = bi.read_events(path)
  assert len(events) == 2
  assert events[0]["start"] == datetime(2025, 12, 8, 23, 13)
  assert events[0]["end"] == datetime(2025, 12, 8, 23, 24)
  assert events[1]["origin"] == datetime(2026, 3, 10, 0, 45, 30)
  assert (events[1]["start"], events[1]["end"]) == (datetime(2026, 3, 10, 0, 44), datetime(2026, 3, 10, 0, 50))


def test_scale_diff_and_judge():
  assert bi.judge(bi.scale_diff("3", "4")) == "1つ下"
  assert bi.judge(bi.scale_diff("2", "2(洞爺町)")) == "一致"
  assert bi.judge(bi.scale_diff("5強", "4")) == "2つ上"
  assert bi.judge(bi.scale_diff("2", "")) == ""
