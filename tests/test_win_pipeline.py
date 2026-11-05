# win_pipeline.py の、外部ツールを使わない部分(日付の扱い・ファイルの整理)のテスト
#   変換そのもの(win2sac_32・wchch_32)は test_realdata.py で、手元のデータがあるときだけ確かめる
import sys
from datetime import datetime
from pathlib import Path

import pytest

import win_pipeline


@pytest.mark.parametrize("start, end, expected", [
  (datetime(2026, 8, 23, 2, 0), datetime(2026, 8, 23, 2, 10), {"260823"}),
  # 終了がちょうど翌日の0時なら、翌日の ZIP は要らない
  (datetime(2026, 8, 23, 23, 0), datetime(2026, 8, 24, 0, 0), {"260823"}),
  (datetime(2026, 8, 23, 23, 0), datetime(2026, 8, 24, 0, 1), {"260823", "260824"}),
  (datetime(2026, 2, 28, 12, 0), datetime(2026, 3, 1, 12, 0), {"260228", "260301"}),
])
def test_target_dates(start, end, expected):
  assert win_pipeline.target_dates(start, end) == expected


@pytest.mark.parametrize("name, expected", [
  ("1070432002/260826/1070432002_2608260858.win", datetime(2026, 8, 26, 8, 58)),
  ("1070432002_2608260858.win", datetime(2026, 8, 26, 8, 58)),
  ("1070432002/260826/readme.txt", None),
  ("combined_1070432002.win", None),
  ("1070432002_26082608.win", None),
  ("1070432002_2613320858.win", None),   # 13月
])
def test_win_datetime(name, expected):
  assert win_pipeline.win_datetime(name) == expected


def touch(path, data=b"x"):
  path.write_bytes(data)
  return path


def test_raw_wins_grouping_and_removal(tmp_path):
  touch(tmp_path / "1070432002_2608260859.win", b"b")
  touch(tmp_path / "1070432002_2608260858.win", b"a")
  touch(tmp_path / "0970355225_2608260858.win")
  touch(tmp_path / "combined_1070432002.win")
  touch(tmp_path / "corrected_0970355225.win")
  touch(tmp_path / "._1070432002_2608260858.win")   # Mac の付随ファイル
  touch(tmp_path / "Bamba.U.1070432003_sac")
  groups = win_pipeline.group_raw_wins(tmp_path)
  assert sorted(groups) == ["0970355225", "1070432002"]
  assert [p.name for p in groups["1070432002"]] == ["1070432002_2608260858.win", "1070432002_2608260859.win"]

  combined = tmp_path / "combined_test.win"
  win_pipeline.combine_wins(groups["1070432002"], combined)
  assert combined.read_bytes() == b"ab"

  # SAC ファイルを作ったあとは WIN ファイルを消す(SAC は残す)
  removed = win_pipeline.remove_wins(tmp_path)
  assert removed == 7   # 結合前3・結合後2・読み替え後1・付随ファイル1(どれも .win)
  assert win_pipeline.find_wins(tmp_path) == []
  assert [p.name for p in win_pipeline.find_sacs(tmp_path)] == ["Bamba.U.1070432003_sac"]


def test_find_current_sacs_checks_channel_table(tmp_path):
  etc = tmp_path / "etc"
  dest = tmp_path / "dest"
  etc.mkdir()
  dest.mkdir()
  (etc / win_pipeline.CHANNEL_TABLE).write_text("table v1")
  touch(dest / "Bamba.U.1070432003_sac")
  touch(dest / "1070432003_2608260858.win")
  touch(dest / "combined_1070432003.win")
  # 変換したときのチャンネル表のコピーが無ければ、作り直す(SAC と途中のファイルを消す。結合前の WIN は残す)
  assert win_pipeline.find_current_sacs(dest, etc) == []
  assert win_pipeline.find_sacs(dest) == []
  assert [p.name for p in win_pipeline.find_wins(dest)] == ["1070432003_2608260858.win"]
  # チャンネル表が同じなら、そのまま使う
  touch(dest / "Bamba.U.1070432003_sac")
  (dest / win_pipeline.CHANNEL_TABLE).write_text("table v1")
  assert len(win_pipeline.find_current_sacs(dest, etc)) == 1
  # チャンネル表が変われば、作り直す
  (etc / win_pipeline.CHANNEL_TABLE).write_text("table v2")
  assert win_pipeline.find_current_sacs(dest, etc) == []


def test_tool_path():
  p = win_pipeline.tool_path(Path("tools"), "win2sac_32")
  assert p.name == ("win2sac_32.exe" if sys.platform.startswith("win") else "win2sac_32")
