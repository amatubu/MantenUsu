# テストの共通の準備
#   scripts フォルダのモジュールを読めるようにし、Matplotlib は画面を出さない(Agg)にする
#   ユーザーの設定(地震リストなど)を書き換えるテストは、user_files で一時フォルダに差し替える
import os
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
ETC_DIR = PROJECT_DIR / "etc"
sys.path.insert(0, str(SCRIPTS_DIR))
os.environ.setdefault("MPLBACKEND", "Agg")

import app_paths  # noqa: E402


# 拠点の座標(Git で管理している etc/manten_ch.tbl から)
@pytest.fixture(scope="session")
def stations():
  from hypocenter import load_stations
  return load_stations(ETC_DIR)


# ユーザーの設定とデータフォルダを一時フォルダに差し替える(本物の地震リストなどを書き換えないため)
@pytest.fixture
def user_files(tmp_path, monkeypatch):
  user_dir = tmp_path / "user"
  data_dir = tmp_path / "data"
  user_dir.mkdir()
  data_dir.mkdir()
  monkeypatch.setattr(app_paths, "QUAKE_LISTS_FILE", user_dir / "quake_lists.json")
  monkeypatch.setattr(app_paths, "DATA_DIR", data_dir)
  return user_dir, data_dir


# 実データのテストの基準(tests/baseline/*.json)を、今の結果で作り直すオプション
#   python3 -m pytest -m realdata --update-baseline
#   計算の方法を意図して変えたときだけ使う(作り直したら、git diff で変わった値を確かめる)
def pytest_addoption(parser):
  parser.addoption("--update-baseline", action="store_true", help="実データのテストの基準を今の結果で作り直す")


@pytest.fixture
def update_baseline(request):
  return request.config.getoption("--update-baseline")
