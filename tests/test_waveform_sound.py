# 波形を音で聴く(waveform_sound.py、TODO 48)のテスト。波形は作り物を使う
import wave

import numpy as np
import obspy
import pytest

import waveform_sound as ws

RATE = 250.0


def sine(freq, seconds, rate=RATE, amp=1000.0):
  t = np.arange(int(seconds * rate)) / rate
  return amp * np.sin(2 * np.pi * freq * t)


def test_filter_choices_match_quiz():
  names = [name for name, _band in ws.FILTER_CHOICES]
  assert names[0] == "なし"
  bands = dict(ws.FILTER_CHOICES)
  assert bands["A 0.5〜2 Hz"] == (0.5, 2.0)
  assert bands["D 20〜40 Hz"] == (20.0, 40.0)
  assert bands["L 0.1〜0.5 Hz"] == (0.1, 0.5)
  assert ws.DEFAULT_SPEED in ws.SPEEDS


def test_filter_samples_keeps_band_and_removes_outside():
  x = sine(1.0, 60) + sine(30.0, 60)
  y = ws.filter_samples(x, RATE, (0.5, 2.0))
  mid = slice(int(10 * RATE), int(50 * RATE))
  # 1 Hz は残り、30 Hz はほぼ消える
  low = np.sqrt(np.mean(y[mid] ** 2))
  assert 600 < low < 800
  y2 = ws.filter_samples(sine(30.0, 60), RATE, (0.5, 2.0))
  assert np.max(np.abs(y2[mid])) < 5


def test_filter_samples_none_only_demeans_and_fills_gaps():
  x = np.ma.masked_array(np.full(100, 5.0), mask=[False] * 50 + [True] * 50)
  y = ws.filter_samples(x, RATE, None)
  assert np.allclose(y, 0.0)
  assert len(y) == 100


def test_filtered_stream_copies():
  tr = obspy.Trace(data=sine(1.0, 30) + 300.0, header={"sampling_rate": RATE, "station": "Hokudai", "channel": "U"})
  st = obspy.Stream([tr])
  assert ws.filtered_stream(st, None) is st
  out = ws.filtered_stream(st, (0.5, 2.0))
  assert out is not st
  assert abs(np.mean(out[0].data)) < 20      # 直流は消える
  assert np.mean(st[0].data) > 250           # 元の波形は変わらない


def test_sound_samples_length_rate_and_level():
  x = sine(2.0, 120) + sine(50.0, 120, amp=3000.0)   # 50 Hz は電気の雑音として消える
  i0, i1 = int(20 * RATE), int(80 * RATE)
  y, rate = ws.sound_samples(x, RATE, i0, i1, None, 20)
  assert rate == ws.SOUND_RATE
  # 60 秒 × 1/20 = 3 秒の音
  assert abs(len(y) / rate - 3.0) < 0.01
  assert np.max(np.abs(y)) == pytest.approx(ws.PEAK_LEVEL, rel=0.02)
  assert y[0] == 0.0 and y[-1] == 0.0          # 始めと終わりは絞る
  # 残っているのは 2 Hz × 20 = 40 Hz の音(50 Hz × 20 = 1000 Hz は消えている)
  spec = np.abs(np.fft.rfft(y))
  freqs = np.fft.rfftfreq(len(y), 1 / rate)
  assert abs(freqs[np.argmax(spec)] - 40.0) < 2.0
  near_1000 = spec[(freqs > 950) & (freqs < 1050)].max()
  assert near_1000 < spec.max() * 0.05


def test_sound_samples_too_short():
  y, rate = ws.sound_samples(sine(1.0, 10), RATE, 100, 101, None, 20)
  assert len(y) == 0


def test_write_wav(tmp_path):
  y = np.sin(np.linspace(0, 20, 1600))
  path = tmp_path / "a.wav"
  ws.write_wav(path, y, ws.SOUND_RATE)
  with wave.open(str(path)) as w:
    assert w.getnchannels() == 1
    assert w.getsampwidth() == 2
    assert w.getframerate() == ws.SOUND_RATE
    assert w.getnframes() == 1600
