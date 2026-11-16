# quake_detect.py の小さな部分のテスト
import pytest

import quake_detect


@pytest.mark.parametrize("seconds, expected", [
  (8.25, "8.2秒"),
  (59.94, "59.9秒"),
  (60, "1分00秒"),
  (754, "12分34秒"),
  (3725, "1時間02分05秒"),
])
def test_format_duration(seconds, expected):
  assert quake_detect.format_duration(seconds) == expected
