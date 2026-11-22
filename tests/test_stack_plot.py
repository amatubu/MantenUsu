# 1時間スタック波形(stack_plot.py)の縦の大きさ(TODO 52)のテスト。波形は作り物を使う
from datetime import datetime

import numpy as np
import obspy
import pytest

import stack_plot as sp

T0 = datetime(2026, 10, 24, 2, 0)


def hour_trace(data, rate=100.0):
  return obspy.Trace(data=np.asarray(data, dtype=float),
                     header={"station": "Hokudai", "channel": "U", "sampling_rate": rate,
                             "starttime": obspy.UTCDateTime(T0)})


def minutes_of(data):
  return sp.slice_into_minutes(hour_trace(data), T0)


def test_typical_level_ignores_offset():
  rng = np.random.default_rng(0)
  x = rng.normal(0, 10, 360000) + 5000.0   # 直流のずれがあっても、ふだんの揺れは雑音の大きさ
  level = sp.typical_level(minutes_of(x))
  assert level == pytest.approx(10 * 0.6745, rel=0.05)   # 正規分布の |x| の中央値は 0.6745σ


def test_scale_height_max_mode_is_one_fifth_of_max():
  x = np.zeros(360000)
  x[1000] = 500.0
  half, gmax, floored = sp.scale_height(minutes_of(x), floor=False)
  assert gmax == 500.0
  assert half == pytest.approx(100.0)
  assert not floored


def test_scale_height_floor_on_quiet_hour():
  rng = np.random.default_rng(1)
  x = rng.normal(0, 10, 360000)            # 雑音だけの静かな1時間
  minutes = minutes_of(x)
  half_max, gmax, _ = sp.scale_height(minutes, floor=False)
  half_floor, _, floored = sp.scale_height(minutes, floor=True)
  assert floored
  assert half_floor == pytest.approx(sp.typical_level(minutes) * sp.FLOOR_GAIN)
  assert half_floor > half_max                # 下限があると雑音が小さく描かれる


def test_scale_height_floor_not_used_when_big_quake():
  rng = np.random.default_rng(2)
  x = rng.normal(0, 10, 360000)
  x[200000:200500] += 20000.0 * np.sin(np.arange(500) / 3)   # 大きな揺れ
  half, _, floored = sp.scale_height(minutes_of(x), floor=True)
  assert not floored
  assert half == pytest.approx(sp.calc_global_max(minutes_of(x)) / sp.MAX_DIVISOR)


def test_render_shows_floor_note():
  rng = np.random.default_rng(3)
  tr = hour_trace(rng.normal(0, 10, 360000))
  stack = sp.StackFigure(for_screen=False)
  stack.render(tr, T0, "Hokudai", "U", floor=True)
  assert "下限" in stack.max_amp_text.get_text()
  stack.render(tr, T0, "Hokudai", "U", floor=False)
  assert "下限" not in stack.max_amp_text.get_text()
