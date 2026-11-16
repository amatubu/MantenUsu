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


# 期間のキャッシュを消す(daily_zips に書き足した日を作り直すとき)
def test_clear_period_caches(tmp_path, monkeypatch):
  from datetime import datetime
  import app_paths
  monkeypatch.setattr(app_paths, "OUTPUT_DIR", tmp_path)
  monkeypatch.setattr(quake_detect, "FEATURES_CACHE_FILE", tmp_path / "quake_features" / "f.json")
  for name in ("stack_20250724_15", "stack_20250723_23", "202507280119_202507280121", "202507200000_202507200010"):
    (tmp_path / name).mkdir()
  (tmp_path / "quake_rms").mkdir()
  for day in ("20250724", "20250723"):
    (tmp_path / "quake_rms" / f"{day}_2-20Hz_10s.npz").write_bytes(b"x")
  quake_detect.save_features_cache({"20250724100000_20250724100100": {}, "20250723100000_20250723100100": {}})
  a, b = datetime(2025, 7, 24), datetime(2025, 9, 3)
  paths, n = quake_detect.clear_period_caches(a, b, dry_run=True)
  assert sorted(p.name for p in paths) == ["20250724_2-20Hz_10s.npz", "202507280119_202507280121", "stack_20250724_15"]
  assert n == 1 and (tmp_path / "stack_20250724_15").exists()
  quake_detect.clear_period_caches(a, b)
  assert not (tmp_path / "stack_20250724_15").exists() and (tmp_path / "stack_20250723_23").exists()
  assert not (tmp_path / "quake_rms" / "20250724_2-20Hz_10s.npz").exists()
  assert list(quake_detect.load_features_cache()) == ["20250723100000_20250723100100"]
