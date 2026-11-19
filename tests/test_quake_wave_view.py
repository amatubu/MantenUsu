# スマートフォンで波形を見る HTML(quake_wave_view.py)のテスト。波形のデータは作り物を使う
from datetime import datetime
import json
import re

import numpy as np
import obspy
import pytest

import quake_wave_view as wv

T0 = datetime(2026, 5, 20, 23, 51, 47)


def make_stream(data, station="Bamba", channel="U", start=T0, rate=250.0):
  return obspy.Stream([obspy.Trace(data=data, header={"station": station, "channel": channel, "sampling_rate": rate,
                                                      "starttime": obspy.UTCDateTime(start)})])


@pytest.mark.parametrize("values", [
  [],
  [0],
  [5, 4, 4, 9, -3],
  [2**30, -2**30 + 1, 2**30],
])
def test_encode_round_trip(values):
  assert wv.decode_samples(wv.encode_samples(values)).tolist() == values


def test_encode_round_trip_random_walk():
  values = np.cumsum(np.random.default_rng(1).integers(-5000, 5000, 15000))
  assert np.array_equal(wv.decode_samples(wv.encode_samples(values)), values)


def test_encode_is_repeatable_and_rejects_too_large_steps():
  assert wv.encode_samples([1, 2, 3]) == wv.encode_samples([1, 2, 3])   # 時刻などが入らない
  with pytest.raises(ValueError):
    wv.encode_samples([-2**31, 2**31])


def test_trace_samples_restores_counts_and_removes_mean():
  counts = np.array([10, 12, 14, 16, 18, 20], dtype=float)
  item = wv.trace_samples(make_stream(counts * 5.96), "Bamba", "U", T0, 6, 5.96)
  assert wv.decode_samples(item["d"]).tolist() == [-5, -3, -1, 1, 3, 5]
  assert item["s"] == 5.96 and item["g"] == []


def test_trace_samples_aligns_start_and_reports_missing_ranges():
  # 記録が 2 点目から始まり、途中に欠測があり、4 点で終わる
  data = np.ma.array([1.0, 3.0, 5.0, 7.0], mask=[False, True, False, False])
  start = obspy.UTCDateTime(T0) + 2 / 250
  item = wv.trace_samples(make_stream(data, start=start.datetime), "Bamba", "U", T0, 8, 1.0)
  assert wv.decode_samples(item["d"]).tolist() == [0, 0, -3, 0, 1, 3, 0, 0]
  assert item["g"] == [[0, 2], [3, 4], [6, 8]]


def test_trace_samples_without_usable_data():
  st = make_stream(np.ones(10))
  assert wv.trace_samples(st, "Bamba", "U", T0, 10, 1.0) is None          # 値が変わらない
  assert wv.trace_samples(st, "Bamba", "N", T0, 10, 1.0) is None          # 成分が無い
  assert wv.trace_samples(st, "Hokudai", "U", T0, 10, 1.0) is None        # 拠点が無い
  assert wv.trace_samples(make_stream(np.arange(10.0), rate=100.0), "Bamba", "U", T0, 10, 1.0) is None   # 割り切れない


def test_trace_samples_averages_when_rate_is_lower():
  item = wv.trace_samples(make_stream(np.arange(8.0)), "Bamba", "U", T0, 4, 1.0, rate=125)
  assert wv.decode_samples(item["d"]).tolist() == [-3, -1, 1, 3]   # 2点ずつの平均 0.5 2.5 4.5 6.5 を丸めて平均を引く


def test_count_steps_match_channel_table():
  from conftest import ETC_DIR
  steps = wv.count_steps(ETC_DIR)
  assert steps[("Hokudai", "U")] == pytest.approx(5.96)     # 5.96e-7 V / 100 V/(m/s) = 5.96 nm/s
  assert steps[("Bamba", "U")] == pytest.approx(5.96e-7 / 88 * 1e9)
  assert len(steps) == 12


def test_window_start_is_on_a_sample():
  t = wv.window_start(datetime(2026, 5, 20, 23, 51, 57, 851754))
  assert t == datetime(2026, 5, 20, 23, 51, 47, 848000)


def test_filter_sections_match_pick_filter():
  from hypocenter import pick_filtered
  from scipy.signal import sosfilt
  x = np.cumsum(np.random.default_rng(2).normal(size=3000))
  x -= x.mean()
  sos = np.array([[b0, b1, b2, 1.0, a1, a2] for b0, b1, b2, a1, a2 in wv.filter_sections()])
  y = sosfilt(sos, sosfilt(sos, x)[::-1])[::-1]     # ブラウザの filterSamples と同じ、往復のフィルタ
  assert np.allclose(y, pick_filtered(obspy.Trace(x, {"sampling_rate": 250.0})))


def test_pick_lines_mark_manual_and_automatic_times():
  origin = datetime(2026, 5, 20, 23, 51, 57)
  r = {"ref": datetime(2026, 5, 20, 23, 51, 27), "picks": {"Bamba": 31.5, "Hokudai": 31.0, "KBBY": 30.5},
       "s_picks": {"Bamba": 32.25, "KBBY": 31.0}}
  lines = wv.pick_lines(r, {"Bamba": (), "Hokudai": ()}, origin, {"Hokudai"}, {"Bamba"})
  assert lines == {"Bamba": {"p": 1.5, "pm": False, "s": 2.25, "sm": True},
                   "Hokudai": {"p": 1.0, "pm": True}}       # 外部の観測点(KBBY)は入れない


def test_written_page_is_self_contained(tmp_path):
  item = {"d": wv.encode_samples([1, 2, 3]), "s": 5.96, "g": []}
  info = {"no": 1, "key": "20260520_235145", "time": "2026-05-20 23:51:57.8", "type": "", "m": "1.2", "mSource": "気象庁", "depth": "0.5 km",
          "t0": -10.0, "n": 3, "picks": {"Bamba": {"p": 1.0, "s": 2.0}}}
  path = tmp_path / "waves.html"
  size = wv.write_wave_view(path, [(info, {"Bamba": {"U": item}})], ["Bamba", "Hokudai"], "地震</script>")
  text = path.read_text(encoding="utf-8")
  assert size == path.stat().st_size
  assert "__META__" not in text and "__WAVES__" not in text and "__TITLE__" not in text
  assert not re.search(r"""(?:src|href)=["']?https?:""", text)      # 外部のファイルを読まない
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', text, re.S).group(1))
  assert [s["id"] for s in meta["stations"]] == ["Bamba", "Hokudai"] and meta["title"] == "地震</script>"
  assert meta["filter"]["sections"] == wv.filter_sections()
  waves = json.loads(re.search(r'id="w1">(.*?)</script>', text, re.S).group(1))
  assert wv.decode_samples(waves["Bamba"]["U"]["d"]).tolist() == [1, 2, 3]


def test_station_labels_follow_the_setting(tmp_path, monkeypatch):
  import station_names
  monkeypatch.setattr(station_names, "_enabled", True)
  monkeypatch.setattr(station_names, "_table", {"Hokudai": ("Kawaminami", "KWMNM")})
  path = tmp_path / "waves.html"
  wv.write_wave_view(path, [], ["Hokudai"], "地震")
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', path.read_text(encoding="utf-8"), re.S).group(1))
  assert meta["stations"] == [{"id": "Hokudai", "label": "KWMNM"}]
