# repackage_to_daily_zip.py(元の ZIP を日ごとの ZIP にまとめ直す)のテスト
import sys
import zipfile
from datetime import datetime

import repackage_to_daily_zip as rp


def make_source(path, names):
  with zipfile.ZipFile(path, "w") as z:
    for n in names:
      z.writestr(n, b"x")


def test_stale_caches(tmp_path):
  (tmp_path / "quake_rms").mkdir()
  for name in ("20260826_2-20Hz_10s.npz", "20260827_2-20Hz_10s.npz"):
    (tmp_path / "quake_rms" / name).write_bytes(b"")
  for name in ("stack_20260826_08", "stack_20260826_09", "stack_20260826_10", "stack_20260827_00",
               "202608260900_202608260910", "202608260750_202608261010", "202608260800_202608260810"):
    (tmp_path / name).mkdir()
  hours = {datetime(2026, 8, 26, 9), datetime(2026, 8, 26, 10)}
  got = [p.name for p in rp.stale_caches(hours, tmp_path)]
  assert got == ["20260826_2-20Hz_10s.npz", "202608260750_202608261010", "202608260900_202608260910",
                 "stack_20260826_09", "stack_20260826_10"]
  assert rp.stale_caches(hours, tmp_path / "none") == []


def test_win_hours():
  assert rp.win_hours("0970355243_2608260905.win", "260826") == {datetime(2026, 8, 26, 9)}
  assert len(rp.win_hours("odd.win", "260826")) == 24


def test_reports_only_appended_days(tmp_path, monkeypatch, capsys):
  src, out, cache = tmp_path / "src", tmp_path / "out", tmp_path / "cache"
  src.mkdir(); out.mkdir(); (cache / "stack_20260826_09").mkdir(parents=True); (cache / "stack_20260826_08").mkdir()
  # 8/26 の ZIP は前からある(09:04 まで)。新しい元の ZIP には 8/26 の続きと 8/27 がある
  with zipfile.ZipFile(out / "260826.zip", "w") as z:
    z.writestr("0970355243/260826/0970355243_2608260904.win", b"x")
  make_source(src / "a.zip", ["card/260826/0970355243_2608260904.win", "card/260826/0970355243_2608260905.win",
                              "card/260827/0970355243_2608270000.win"])
  monkeypatch.setattr(rp.app_paths, "OUTPUT_DIR", cache)
  monkeypatch.setattr(sys, "argv", ["rp", "--source", str(src), "--output", str(out)])
  rp.main()
  text = capsys.readouterr().out
  assert "書き足しました: 260826" in text and "260827" not in text.split("書き足しました")[1].splitlines()[0]
  assert "stack_20260826_09" in text and "stack_20260826_08" not in text
  assert len(zipfile.ZipFile(out / "260826.zip").namelist()) == 2


def test_no_message_without_append(tmp_path, monkeypatch, capsys):
  src, out = tmp_path / "src", tmp_path / "out"
  src.mkdir()
  make_source(src / "a.zip", ["card/260901/0970355243_2609010000.win"])
  monkeypatch.setattr(sys, "argv", ["rp", "--source", str(src), "--output", str(out)])
  rp.main()
  assert "書き足しました" not in capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
  src, out, cache = tmp_path / "src", tmp_path / "out", tmp_path / "cache"
  src.mkdir(); out.mkdir(); (cache / "stack_20260826_09").mkdir(parents=True)
  with zipfile.ZipFile(out / "260826.zip", "w") as z:
    z.writestr("0970355243/260826/0970355243_2608260904.win", b"x")
  before = (out / "260826.zip").read_bytes()
  make_source(src / "a.zip", ["card/260826/0970355243_2608260904.win", "card/260826/0970355243_2608260905.win",
                              "card/260827/0970355243_2608270000.win", "card/260827/1070432002_2608270000.win"])
  monkeypatch.setattr(rp.app_paths, "OUTPUT_DIR", cache)
  monkeypatch.setattr(sys, "argv", ["rp", "--source", str(src), "--output", str(out), "--dry-run"])
  rp.main()
  text = capsys.readouterr().out
  assert sorted(p.name for p in out.iterdir()) == ["260826.zip"]
  assert (out / "260826.zip").read_bytes() == before
  assert "合計 3 個" in text
  assert "260827.zip  2 個" in text and "260826.zip  1 個" in text
  assert "書き足すことになります: 260826" in text and "stack_20260826_09" in text
