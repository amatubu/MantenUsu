# 紙の教材(shake_print.py)のテスト。波形のデータは作り物を使う
from datetime import datetime, timedelta
import io
import wave

import numpy as np
import obspy
import pytest

import shake_compare as sc
import shake_print as sp
import shake_quiz as sq

T0 = datetime(2026, 5, 21, 12, 9, 25)


def make_trace(data, station, start, rate=250.0):
  return obspy.Trace(data=np.asarray(data, dtype=float),
                     header={"station": station, "channel": "U", "sampling_rate": rate, "starttime": obspy.UTCDateTime(start)})


def test_read_settings(tmp_path):
  assert sp.read_settings(tmp_path / "none.txt") == {}
  path = tmp_path / "s.txt"
  path.write_text("# 注\n音の置き場所 = https://example.jp/listen   # 注\n空 =\n", encoding="utf-8")
  assert sp.read_settings(path) == {"音の置き場所": "https://example.jp/listen", "空": ""}
  assert sp.page_url("https://example.jp/listen/", "q3.html") == "https://example.jp/listen/q3.html"
  assert sp.page_url("", "q3.html") == ""


def test_settings_file_is_valid():
  from conftest import ETC_DIR
  assert sp.URL_KEY in sp.read_settings(ETC_DIR / sp.SETTINGS_FILE)


def test_wrap_counts_full_width_and_keeps_words():
  lines = sp.wrap("青の P と赤の S の破線は、震源から計算した到着の見込みです。S-P 時間が長くなります。", 12).split("\n")
  assert all(sum(1 if ord(c) > 0x2000 else 0.5 for c in line) <= 13 for line in lines)
  assert not any(line.startswith(("、", "。")) for line in lines)              # 行頭に句読点を置かない
  assert any("S-P" in line for line in lines)                                  # 半角の語は切らない


def test_samples_restore_values_and_gaps():
  from quake_wave_view import trace_samples
  data = np.arange(1000) % 9 * 5.96
  data[100:200] = np.nan
  item = trace_samples(obspy.Stream([make_trace(data, "Hokudai", T0)]), "Hokudai", "U", T0, 1000, 5.96)
  v = sp.samples(item)
  assert np.isnan(v[100:200]).all() and np.isfinite(v[:100]).all()
  assert np.allclose(np.diff(v[:100]), np.diff(data[:100]))


def test_sound_wave_speeds_up_and_removes_hum():
  rate = 250
  t = np.arange(20 * rate) / rate
  v = np.sin(2 * np.pi * 5 * t) + np.sin(2 * np.pi * 50 * t)
  y, out = sp.sound_wave(v, rate, 20, False, "")
  assert out == 8000 and len(y) == pytest.approx(len(v) / 20 * out / rate, abs=2)   # 20 倍の速さ = 1 秒
  spec = np.abs(np.fft.rfft(y[out // 4:-out // 4]))
  f = np.fft.rfftfreq(len(y[out // 4:-out // 4]), 1 / out)
  assert f[np.argmax(spec)] == pytest.approx(100, abs=3)                    # 5 Hz → 100 Hz の音
  assert spec[np.argmin(np.abs(f - 1000))] < spec.max() * 0.01             # 50 Hz の雑音(→ 1000 Hz)は消える


def test_write_sounds_real_ratio(tmp_path):
  rate = 250
  t = np.arange(10 * rate) / rate
  big, small = np.sin(2 * np.pi * 5 * t) * 10, np.sin(2 * np.pi * 5 * t)
  def peak(name):
    with wave.open(io.BytesIO((tmp_path / name).read_bytes())) as w:
      return np.abs(np.frombuffer(w.readframes(w.getnframes()), "<i2")).max() / 32767
  sp.write_sounds(tmp_path, [("a.wav", big, rate, 10, False, ""), ("b.wav", small, rate, 10, False, "")], real=True)
  assert 0.75 < peak("a.wav") <= 0.9 and peak("b.wav") / peak("a.wav") == pytest.approx(0.1, rel=0.02)   # 大きさの比のまま
  sp.write_sounds(tmp_path, [("a.wav", big, rate, 10, False, ""), ("b.wav", small, rate, 10, False, "")])
  assert peak("b.wav") == pytest.approx(peak("a.wav"), rel=0.02)          # それぞれ最大でそろえる


def quiz_event(stations):
  rng = np.random.default_rng(1)
  start = T0 - timedelta(seconds=10)
  st = obspy.Stream([make_trace(rng.normal(size=60 * 250) * 50, nm, start) for nm in ("Hokudai", "Bamba")])
  q = {"no": 3, "kind": "発破", "t": T0, "ref": "揺れの始まり", "show": (-10.0, 50.0), "label": "発破", "focus": "印",
       "note": "注意", "second": "", "audio_station": "Hokudai"}
  info, waves = sq.event_data(q, st, {}, stations)
  return q, info, {nm: sp.samples(w["U"]) for nm, w in waves.items()}


def test_quiz_pages(tmp_path, stations):
  from plot_common import setup_japanese_font
  setup_japanese_font()
  events = [quiz_event(stations)]
  listen = tmp_path / "listen"
  pages = sp.quiz_listen(listen, events)
  assert pages == {3: ("q3.html", "a3.html")}
  assert {p.name for p in listen.glob("*.wav")} == {"q3.wav", "a3_x20_raw.wav", "a3_x20_A.wav", "a3_x40_raw.wav", "a3_x40_A.wav"}
  assert "答え" not in (listen / "q3.html").read_text(encoding="utf-8")       # 問題の聴くページに答えを出さない
  assert "発破" in (listen / "a3.html").read_text(encoding="utf-8")
  sp.write_index(listen)
  index = (listen / "index.html").read_text(encoding="utf-8")
  assert 'href="q3.html"' in index and 'href="a3.html"' in index
  for base in ("", "https://example.jp/listen"):
    sp.quiz_pdf(tmp_path / "quiz.pdf", events, pages, base)
    sp.answers_pdf(tmp_path / "answers.pdf", events, pages, base)
    assert (tmp_path / "quiz.pdf").read_bytes()[:4] == b"%PDF" and (tmp_path / "answers.pdf").stat().st_size > 1000


def test_compare_pages(tmp_path, stations):
  from plot_common import setup_japanese_font
  setup_japanese_font()
  rng = np.random.default_rng(2)
  st = obspy.Stream([make_trace(rng.normal(size=40 * 250) * 50, nm, T0 - timedelta(seconds=10)) for nm in sq.STATIONS])
  th = {"no": 4, "title": "冬と夏", "text": "説明", "look": "見どころ", "ref": "最大", "show": (-5.0, 20.0), "align_p": False,
        "first": "Hokudai", "common": True, "filter": "", "speeds": [10, 20], "paper": ["", "C"], "pick": True,
        "rows": [{"label": f"段{k}", "kind": "鉄道", "t": T0, "station": "", "second": ""} for k in range(2)]}
  info, waves = sc.theme_data(th, lambda a, b: st, {}, stations)
  themes = [(info, [{nm: sp.samples(w["U"]) for nm, w in data.items()} for data in waves])]
  listen = tmp_path / "listen"
  pages = sp.compare_listen(listen, themes)
  assert pages == {4: "t4.html"}
  assert len(list(listen.glob("t4_*.wav"))) == 2 * 2 * 2                     # 段 × 紙のフィルタ × 速さ
  assert "本当の揺れの大きさの比" in (listen / "t4.html").read_text(encoding="utf-8")
  sp.compare_pdf(tmp_path / "compare.pdf", themes, pages, "https://example.jp/listen")
  assert (tmp_path / "compare.pdf").read_bytes()[:4] == b"%PDF"
