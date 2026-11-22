# 震源リストを、記録のあった日(とその前の日)だけ読む(jma_match.load_catalog の days)のテスト
from datetime import datetime
from pathlib import Path

import jma_hypocenter
import jma_match


def test_load_catalog_reads_only_given_days_and_day_before(monkeypatch):
  read = []
  monkeypatch.setattr(jma_hypocenter, "csv_path", lambda day: Path("/nonexistent"))
  monkeypatch.setattr(jma_hypocenter, "load_day", lambda day: read.append(day) or [])
  monkeypatch.setattr(jma_match, "save_travel_times", lambda: None)
  start, end = datetime(2026, 11, 1), datetime(2026, 11, 10)
  _quakes, missing = jma_match.load_catalog(start, end, (42.5, 140.8), days=[datetime(2026, 11, 5)])
  assert read == [datetime(2026, 11, 4), datetime(2026, 11, 5)]
  assert missing == read
  read.clear()
  jma_match.load_catalog(start, end, (42.5, 140.8))
  assert len(read) == 11   # 省略すると、前の日から期間の終わりまで
