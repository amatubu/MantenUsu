# 振動の部品(haptic_page.py)の JavaScript のテスト。macOS の JavaScriptCore(jsc)で動かす
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

import haptic_page

JSC = Path("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current/Helpers/jsc")


def run_js(tmp_path, expr):
  if not JSC.exists():
    pytest.skip("jsc がありません")
  code = re.search(r"// HAPTIC_CURVE_BEGIN\n(.*?)// HAPTIC_CURVE_END", haptic_page.HAPTIC_JS, re.S).group(1)
  script = tmp_path / "t.js"
  script.write_text(code + f"\nprint(JSON.stringify({expr}));", encoding="utf-8")
  return json.loads(subprocess.run([str(JSC), str(script)], capture_output=True, text=True, check=True).stdout)


@pytest.mark.parametrize("level, on_ratio", [(1.0, 1.0), (0.5, 0.5), (0.0, 0.0), (0.03, 0.0), (0.1, 0.25), (0.9, 1.0)])
def test_vibration_pattern_uses_on_time_for_strength(tmp_path, level, on_ratio):
  # 2 秒、同じ強さ。40 ms ごとのオンの時間の割合が強さになる(短すぎるオンは 10 ms、短すぎる止まりは振動のまま)
  pattern = run_js(tmp_path, f"hapticPattern(new Array(100).fill({level}), 0.02)")
  assert sum(pattern) == 2000
  on = sum(pattern[0::2])
  assert on == pytest.approx(2000 * on_ratio, abs=1)
  assert all(ms >= 0 for ms in pattern)


def test_vibration_pattern_follows_changes(tmp_path):
  # 0.5 秒静か → 0.5 秒強い → 0.5 秒静か: 振動から始まる並びなので先頭は 0 ms の振動。40 ms の区切りが境目をまたぐ所は半分だけ振動する
  pattern = run_js(tmp_path, "hapticPattern([...new Array(25).fill(0), ...new Array(25).fill(1), ...new Array(25).fill(0)], 0.02)")
  assert pattern == [0, 480, 20, 20, 480, 500]
  assert sum(pattern) == 1500 and sum(pattern[0::2]) == 500


def test_displacement_of_sine_velocity(tmp_path):
  # 2 Hz の速度 1000 nm/s の変位は 1000 / (2π·2) ≒ 79.6 nm(0.5〜5 Hz の中なのでほぼそのまま)
  rate, f = 125, 2.0
  t = np.arange(20 * rate) / rate
  v = (1000 * np.sin(2 * np.pi * f * t)).tolist()
  d = np.array(run_js(tmp_path, f"Array.from(hapticDisplacement({json.dumps(v)}, {rate}))"))
  middle = d[5 * rate:15 * rate]
  assert np.abs(middle).max() == pytest.approx(1000 / (2 * np.pi * f), rel=0.05)
  # 0.1 Hz のゆっくりした揺れはほとんど残らない
  slow = (1000 * np.sin(2 * np.pi * 0.1 * t)).tolist()
  ds = np.array(run_js(tmp_path, f"Array.from(hapticDisplacement({json.dumps(slow)}, {rate}))"))
  assert np.abs(ds[5 * rate:15 * rate]).max() < 0.1 * 1000 / (2 * np.pi * 0.1)
