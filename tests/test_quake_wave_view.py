# スマートフォンで波形を見る HTML(quake_wave_view.py)のテスト。波形のデータは作り物を使う
from datetime import datetime
import json
from pathlib import Path
import re

import numpy as np
import obspy
import pytest

import haptic_page
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
  assert "__COUNT__" not in text and "__SIZE__" not in text
  # 読み込み中の表示: 波形の前に出し、地震を1つ読むごとに数を進める
  assert text.index('id="loading"') < text.index('id="w1"') < text.index("<script>loadingStep(1)</script>")
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


# --- 振動の強さの曲線(TODO 58) ---

def quake_like_traces(n=2500, t0=-2.0, rate=250):
  # 雑音の中に、0.5 秒で P(小さい)、1.5 秒で S(大きい)が来る作り物
  rng = np.random.default_rng(3)
  t = t0 + np.arange(n) / rate
  amp = 1 + 20 * ((t > 0.5) & (t < 1.5)) + 200 * np.exp(-np.clip(t - 1.5, 0, None)) * (t >= 1.5)
  return [(np.round(rng.normal(size=n) * amp * k).astype(np.int64), step) for k, step in ((3, 5.96), (2, 6.8), (2, 6.8))]


def test_haptic_curve_rises_from_p_to_s():
  traces, rate, t0 = quake_like_traces(), 250, -2.0
  curve = wv.haptic_curve(traces, 2500, t0)
  frame_time = t0 + (np.arange(curve.size) + 0.5) * wv.HAPTIC["frame"] / rate
  before = curve[frame_time < 0.3]
  p = curve[(frame_time > 0.7) & (frame_time < 1.3)]
  s = curve[(frame_time > 1.6) & (frame_time < 1.9)]
  assert curve.size == 500 and curve.min() >= 0 and curve.max() == pytest.approx(1)
  assert before.mean() < 0.05            # 雑音の間はほとんど振動しない
  assert 0.08 < p.mean() < 0.5 < 0.7 < s.mean()  # P は中くらい、S は強い


def test_haptic_curve_is_zero_without_signal():
  assert not wv.haptic_curve([(np.zeros(500, dtype=np.int64), 1.0)], 500, -2.0).any()
  assert wv.haptic_curve([], 3, -2.0).size == 0


def run_browser_curve(tmp_path, traces, n, t0, accel, ref):
  # macOS に入っている JavaScriptCore の jsc で、ページの hapticCurve を動かす
  import subprocess
  jsc = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")
  if not jsc.exists():
    pytest.skip("jsc がありません")
  code = re.search(r"// HAPTIC_CURVE_BEGIN\n(.*?)// HAPTIC_CURVE_END", haptic_page.HAPTIC_JS, re.S).group(1)
  arg = json.dumps([{"v": v.tolist(), "step": s} for v, s in traces])
  script = tmp_path / "curve.js"
  script.write_text(code + f"\nprint(JSON.stringify(Array.from(hapticCurve({arg}, {n}, {t0}, 250, {json.dumps(wv.HAPTIC)}, "
                    f"{json.dumps(accel)}, {json.dumps(ref)}))));", encoding="utf-8")
  return json.loads(subprocess.run([str(jsc), str(script)], capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize("accel, ref", [(False, None), (True, None), (False, 5e4), (True, 2e7)])
def test_haptic_curve_matches_browser(tmp_path, accel, ref):
  traces = quake_like_traces()
  expected = wv.haptic_curve(traces, 2500, -2.0, accel=accel, ref=ref)
  assert expected.max() > 0
  assert np.allclose(run_browser_curve(tmp_path, traces, 2500, -2.0, accel, ref), expected, atol=1e-9)


def test_haptic_acceleration_is_derivative_of_velocity():
  t = np.arange(2500) / 250
  f = 5.0
  traces = [(np.round(1000 * np.sin(2 * np.pi * f * t)), 1.0)]
  vel = wv.haptic_envelope(traces, 2500)
  acc = wv.haptic_envelope(traces, 2500, accel=True)
  assert np.median(acc / vel) == pytest.approx(2 * np.pi * f, rel=0.01)   # 中央の差分なので sin(ωΔt)/Δt ≒ ω


def test_haptic_common_reference_makes_small_quakes_weaker():
  traces = quake_like_traces()
  env = wv.haptic_envelope(traces, 2500)
  own = wv.haptic_curve(traces, 2500, -2.0)
  common = wv.haptic_curve(traces, 2500, -2.0, ref=10 * env.max())   # 10 倍大きい地震を基準にする
  assert own.max() == pytest.approx(1) and 0.1 < common.max() < 0.25   # (1/10 くらい) の 0.7 乗


def test_haptic_peaks_follow_browser_filter():
  item = {"d": wv.encode_samples(quake_like_traces()[0][0]), "s": 5.96}
  peaks = wv.haptic_peaks({"Bamba": {"U": item}}, 2500)
  vel_env = wv.haptic_envelope([(wv.browser_filtered(wv.decode_samples(item["d"])), 5.96)], 2500)
  assert peaks["Bamba"][0] == pytest.approx(vel_env.max(), abs=1e-3) and peaks["Bamba"][1] > peaks["Bamba"][0]


def test_page_has_haptic_bridge(tmp_path):
  path = tmp_path / "waves.html"
  wv.write_wave_view(path, [], ["Bamba"], "地震")
  text = path.read_text(encoding="utf-8")
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', text, re.S).group(1))
  assert meta["haptic"] == wv.HAPTIC
  assert "messageHandlers.usuHaptics" in text and 'cmd: "play"' in text and 'cmd: "stop"' in text
