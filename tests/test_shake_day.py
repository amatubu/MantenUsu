# 1日の記録の紙(shake_day.py)のテスト。波形のデータは作り物を使う
from datetime import datetime, timedelta
import csv

import numpy as np
import obspy
import pytest

import shake_day as sd

DAY = datetime(2026, 7, 8)


def fake_loader(rate=100.0):
  # 呼ばれた範囲の Hokudai の上下動(雑音 + 01:40:15 からの 5 Hz の揺れ)
  def load(t0, t1):
    n = int((t1 - t0).total_seconds() * rate)
    sec = np.arange(n) / rate + (t0 - DAY).total_seconds()
    rng = np.random.default_rng(int(sec[0]) % 1000)
    x = rng.normal(size=n) * 10 + np.where((sec >= 6015) & (sec < 6045), np.sin(2 * np.pi * 5 * sec) * 500, 0)
    return obspy.Stream([obspy.Trace(data=x, header={"station": "Hokudai", "channel": "U", "sampling_rate": rate,
                                                      "starttime": obspy.UTCDateTime(t0)})])
  return load


def test_day_waveforms_cover_the_day(monkeypatch):
  monkeypatch.setattr("quake_wave_view.count_steps", lambda etc: {("Hokudai", "U"): 2.0})
  wf = sd.day_waveforms(DAY, (0.5, 20.0), fake_loader())
  x = wf["Hokudai"]
  assert x.shape == (86400 * sd.RATE,) and np.isfinite(x).all()
  assert np.isnan(wf["Bamba"]).all()                                          # 記録の無い拠点は NaN
  quake = np.abs(x[6020 * sd.RATE:6040 * sd.RATE]).max()
  quiet = np.abs(x[7000 * sd.RATE:7100 * sd.RATE]).max()
  assert quake > 20 * quiet                                                   # 01:40:15 からの揺れが、その時刻にある
  assert quake == pytest.approx(1000, rel=0.2)                                 # カウント × 1 カウントの大きさ(2)


def test_hour_level():
  assert sd.hour_level(np.array([1.0, -2.0, np.nan, 3.0])) == 2.0
  assert sd.hour_level(np.array([np.nan])) == 0.0


def write_answers(path, rows):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["番号", "種類", "始まり", "終わり", "内容", "見え方", "説明"])
    w.writerows(rows)
  return path


def test_read_answers(tmp_path):
  path = write_answers(tmp_path / "a.csv", [
    [2, "発破", "11:20:36", "11:21:20", "発破", "はっきり", "説明"],
    [1, "遠くの地震", "01:40:15", "01:41:45", "青森県東方沖", "はっきり", ""],
  ])
  ev = sd.read_answers(DAY, path)
  assert [e["no"] for e in ev] == [1, 2] and ev[0]["start"] == 6015 and ev[1]["end"] == 11 * 3600 + 21 * 60 + 20
  assert sd.read_answers(DAY, tmp_path / "none.csv") == []
  with pytest.raises(ValueError, match="終わり"):
    sd.read_answers(DAY, write_answers(tmp_path / "b.csv", [[1, "発破", "11:20:36", "11:20:00", "x", "x", ""]]))


@pytest.mark.parametrize("day", ["20260708", "20251222", "20260823"])
def test_answer_files_are_valid(day):
  from conftest import ETC_DIR
  ev = sd.read_answers(datetime.strptime(day, "%Y%m%d"), ETC_DIR / f"shake_day_{day}.csv")
  assert len(ev) >= 5 and all(e["kind"] in sd.KIND_COLORS for e in ev)
  assert len({e["no"] for e in ev}) == len(ev)


def test_pdfs(tmp_path):
  from plot_common import setup_japanese_font
  setup_japanese_font()
  wave = np.random.default_rng(1).normal(size=86400 * sd.RATE).astype(np.float32)
  wave[:3600 * sd.RATE // 2] = np.nan                                          # 記録の無い時間
  sd.day_pdf(tmp_path / "day.pdf", DAY, "Hokudai", wave, (0.5, 20.0), hours=[0, 1])
  sd.day_pdf(tmp_path / "noclip.pdf", DAY, "Hokudai", wave, (0.5, 20.0), hours=[1], clip=0)
  ev = [{"no": 1, "kind": "遠くの地震", "start": 6015, "end": 6105, "text": "x", "vis": "はっきり", "note": ""}]
  sd.day_pdf(tmp_path / "answers.pdf", DAY, "Hokudai", wave, (0.5, 20.0), events=ev, png_dir=tmp_path)
  for name in ("day.pdf", "noclip.pdf", "answers.pdf"):
    assert (tmp_path / name).read_bytes()[:4] == b"%PDF"
  # 答えは、一覧のページと揺れのある時間(1 時)のページだけ
  assert sorted(p.name for p in tmp_path.glob("answers_*.png")) == ["answers_00list.png", "answers_01.png"]
