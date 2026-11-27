# 揺れの強さを iPhone の振動のファイル(AHAP)と音に書き出す haptic_export.py のテスト
import json
import wave

import numpy as np
import pytest

import haptic_export as hx
from haptic_page import HAPTIC
from quake_wave_view import encode_samples


def test_step_events_follow_levels():
  # 0.04 秒ごとの区間の平均が強さになり、弱い区間(0.02 未満)には振動を置かない
  times = np.arange(0.01, 1.0, 0.02)
  vals = np.where(times < 0.4, 0.0, np.where(times < 0.8, 0.5, 1.0))
  events = hx.step_events(times, vals, 1.0, 0.04, 0.3)
  starts = [e["Event"]["Time"] for e in events]
  assert starts[0] == pytest.approx(0.4) and starts[-1] == pytest.approx(0.96)
  assert len(events) == 15
  inten = {round(e["Event"]["Time"], 2): e["Event"]["EventParameters"][0]["ParameterValue"] for e in events}
  assert inten[0.4] == 0.5 and inten[0.96] == 1.0
  first = events[0]["Event"]
  assert first["EventType"] == "HapticContinuous" and first["EventDuration"] == 0.04
  assert first["EventParameters"][1] == {"ParameterID": "HapticSharpness", "ParameterValue": 0.3}


def test_window_levels_cuts_and_fades():
  # 発生の -10 秒から 20 ms ごと。start=0 から 2 秒分を切り出し、終わりの 0.5 秒で 0 に向かって弱める
  levels = np.ones(1500)
  times, vals = hx.window_levels(levels, -10.0, 0.0, 2.0)
  assert times.min() >= 0 and times.max() <= 2.0
  assert vals[times < 1.4].min() == 1.0
  assert vals[-1] < 0.05


def test_file_stem():
  assert hx.file_stem({"time": "2026-05-20 23:51:57.7", "m": "1.2"}, "Mimatsu") == "20260520_235157_M1.2_Mimatsu"
  assert hx.file_stem({"time": "2026-01-29 12:23:26.4", "m": ""}, "KWMNM") == "20260129_122326_KWMNM"


# 地震 1 つ・拠点 1 つの小さな波形のページ(発生の -10 秒から 30 秒。P は 2 秒、2〜6 秒に 10 Hz の揺れ)
def fake_page(path):
  rate, t0 = 250, -10.0
  n = 30 * rate
  t = t0 + np.arange(n) / rate
  rng = np.random.default_rng(0)
  quake = np.where((t >= 2) & (t < 6), 2000 * np.sin(2 * np.pi * 10 * t), 0)
  comps = {c: {"d": encode_samples(np.round(rng.normal(0, 5, n) + quake)), "s": 1.0, "g": 0} for c in "UNE"}
  meta = {"rate": rate, "haptic": HAPTIC, "stations": [{"id": "Mimatsu", "label": "Mimatsu"}],
          "events": [{"no": 3, "time": "2026-05-20 23:51:57.7", "m": "1.2", "t0": t0, "n": n,
                      "picks": {"Mimatsu": {"p": 2.0, "s": 2.7}}, "peaks": {"Mimatsu": [100.0, 1.0]}}]}
  path.write_text(f'<script type="application/json" id="meta">{json.dumps(meta)}</script>\n'
                  f'<script type="application/json" id="w3">{json.dumps({"Mimatsu": comps})}</script>', encoding="utf-8")


def test_export_writes_ahap_and_wav(tmp_path):
  page = tmp_path / "page.html"
  fake_page(page)
  hx.main(["--page", str(page), "--wav", "--length", "8", "-o", str(tmp_path / "out")])
  stem = tmp_path / "out" / "20260520_235157_M1.2_Mimatsu"
  soft = json.loads(stem.with_name(stem.name + "_yawaraka.ahap").read_text(encoding="utf-8"))
  sharp = json.loads(stem.with_name(stem.name + "_kukkiri.ahap").read_text(encoding="utf-8"))
  ev = [e["Event"] for e in soft["Pattern"]]
  starts = np.array([e["Time"] for e in ev])
  inten = np.array([e["EventParameters"][0]["ParameterValue"] for e in ev])
  # 揺れは P の 2 秒前から数えて 2〜6 秒。その前後(フィルタと 0.1 秒の平均でにじむ分)だけ振動し、いちばん強い所は 1 に近い
  assert 1.4 <= starts.min() < 2.0 and 6.0 <= starts.max() < 6.6
  assert inten.max() > 0.95
  assert sharp["Pattern"][0]["Event"]["EventParameters"][1]["ParameterValue"] == HAPTIC["sharpness"][1]
  assert "S は 2.7 秒" in soft["Metadata"]["Description"]
  with wave.open(str(stem.with_name(stem.name + ".wav"))) as w:
    assert w.getframerate() == hx.WAV_RATE and w.getnframes() == 8 * hx.WAV_RATE


def test_unknown_event_stops(tmp_path):
  page = tmp_path / "page.html"
  fake_page(page)
  with pytest.raises(SystemExit):
    hx.main(["--page", str(page), "--events", "99", "-o", str(tmp_path / "out")])


def test_index_page_links_files(tmp_path):
  page = tmp_path / "page.html"
  fake_page(page)
  hx.main(["--page", str(page), "--wav", "--length", "8", "-o", str(tmp_path / "out")])
  text = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
  for name in ("20260520_235157_M1.2_Mimatsu_kukkiri.ahap", "20260520_235157_M1.2_Mimatsu_yawaraka.ahap", "20260520_235157_M1.2_Mimatsu.wav"):
    assert f'href="{name}" download' in text
    assert (tmp_path / "out" / name).exists()
  assert "S−P 0.7 秒" in text and "No.3" in text and hx.HAPTICSYNC_URL in text
