# cache_manager.py(キャッシュの集計と削除)のテスト
#   外付けドライブ(exFAT など)では、ファイルを消すと macOS が対の ._ ファイルも消す。それを再現して確かめる
import os
from pathlib import Path

import pytest

import cache_manager


def make_cache(root):
  hour = root / "stack_20260823_02"
  hour.mkdir(parents=True)
  for name in ("1070432002_2608230200.win", "combined_1070432002.win", "Bamba.U.1070432003_sac", "manten_ch.tbl"):
    (hour / name).write_bytes(b"x" * 10)
    (hour / f"._{name}").write_bytes(b"m" * 4)   # Mac の付随ファイル
  (root / "picks").mkdir()
  (root / "picks" / "20260823_020216.json").write_text("{}")
  return hour


# ファイルを消すと、対の ._ ファイルも消えるようにする(外付けドライブの macOS の動き)
@pytest.fixture
def external_drive(monkeypatch):
  real_unlink = os.unlink

  def unlink(path, *, dir_fd=None):
    real_unlink(path, dir_fd=dir_fd)
    name = os.path.basename(path)
    if not name.startswith("._"):
      partner = os.path.join(os.path.dirname(path), "._" + name)
      try:
        real_unlink(partner, dir_fd=dir_fd)
      except FileNotFoundError:
        pass

  monkeypatch.setattr(os, "unlink", unlink)
  monkeypatch.setattr(os, "remove", unlink)


def test_classify():
  assert cache_manager.classify("1070432002_2608230200.win") == "raw_win"
  assert cache_manager.classify("combined_1070432002.win") == "merged_win"
  assert cache_manager.classify("corrected_0970355225.win") == "merged_win"
  assert cache_manager.classify("Bamba.U.1070432003_sac") == "sac"
  # Mac の付随ファイルは WIN・SAC として数えない
  assert cache_manager.classify("._1070432002_2608230200.win") == "other"
  assert cache_manager.classify("._Bamba.U.1070432003_sac") == "other"


def test_scan_output(tmp_path):
  make_cache(tmp_path)
  stats = cache_manager.scan_output(tmp_path)
  assert stats["raw_win"] == [1, 10]
  assert stats["merged_win"] == [1, 10]
  assert stats["sac"] == [1, 10]


def test_delete_wins_on_external_drive(tmp_path, external_drive):
  hour = make_cache(tmp_path)
  deleted = cache_manager.delete_output_kinds(tmp_path, {"raw_win", "merged_win"})
  assert deleted == 2
  assert sorted(p.name for p in hour.iterdir()) == ["._Bamba.U.1070432003_sac", "._manten_ch.tbl",
                                                    "Bamba.U.1070432003_sac", "manten_ch.tbl"]
  assert (tmp_path / "picks" / "20260823_020216.json").exists()


def test_delete_all_removes_folder_on_external_drive(tmp_path, external_drive):
  hour = make_cache(tmp_path)
  # WIN も SAC も残らないフォルダは、残りのファイルごと消す(._ ファイルが先に消えてもエラーにしない)
  cache_manager.delete_output_kinds(tmp_path, {"raw_win", "merged_win", "sac"})
  assert not hour.exists()
  assert (tmp_path / "picks").exists()


def test_zip_files_on_external_drive(tmp_path, external_drive):
  for name in ("260823.zip", "260824.zip"):
    (tmp_path / name).write_bytes(b"z")
    (tmp_path / f"._{name}").write_bytes(b"m")
  files = cache_manager.collect_files(tmp_path, "*.zip")
  assert sorted(p.name for p in files) == ["260823.zip", "260824.zip"]
  cache_manager.delete_files(files)
  assert list(tmp_path.iterdir()) == []


def test_remove_file_and_tree_ignore_missing(tmp_path):
  assert cache_manager.remove_file(tmp_path / "none") is False
  d = tmp_path / "d"
  (d / "sub").mkdir(parents=True)
  (d / "sub" / "a").write_text("a")
  cache_manager.remove_tree(d)
  assert not d.exists()
  cache_manager.remove_tree(tmp_path / "none")   # 無いフォルダでもエラーにしない
