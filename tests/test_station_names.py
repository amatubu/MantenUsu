# station_names.py(満点地震計の拠点名を、表示のときだけ読み替える)のテスト
import numpy as np
import pytest
from obspy import Stream, Trace, UTCDateTime

import station_names
from conftest import ETC_DIR

SAMPLE = """\
# 内部名  正式名  略称
Bamba    Irie          Irie     # 入江
Nizaemn  Takinomachi   Takino
Hokudai  SobetsuOnsen  Sobetsu
"""


# 読み替えの表を一時ファイルに差し替える(テストのあとは、アプリに付けたファイルの表に戻す)
@pytest.fixture
def aliases(tmp_path):
  def use(text):
    path = tmp_path / "station_names.txt"
    path.write_text(text, encoding="utf-8")
    return station_names.reload(path, enabled=True)
  yield use
  station_names.reload()


def test_bundled_file_and_switch(tmp_path, monkeypatch):
  import app_paths
  monkeypatch.setattr(app_paths, "SETTINGS_FILE", tmp_path / "settings.json")
  monkeypatch.setattr(app_paths, "USER_DIR", tmp_path)
  # アプリに付けるファイルには発表用の名前が書いてある
  table = station_names.reload(ETC_DIR / "station_names.txt")
  assert table["Bamba"] == ("Irie", "Irie")
  assert "Mimatsu" not in table
  assert station_names.load(ETC_DIR / "no_such_file.txt") == {}
  try:
    # 切り替えがオフ(既定)なら内部名のまま
    assert not station_names.is_enabled()
    assert station_names.full("Bamba") == "Bamba"
    assert station_names.internal("Irie") == "Irie"
    # 入れると読み替え、settings.json に覚えておく(次に起動したときも使う)
    station_names.set_enabled(True)
    assert station_names.full("Bamba") == "Irie"
    assert app_paths.load_settings()["station_names"] is True
    monkeypatch.setattr(station_names, "_enabled", None)   # 起動し直したのと同じ
    assert station_names.is_enabled()
    station_names.set_enabled(False)
    assert station_names.short("Nizaemn") == "Nizaemn"
    assert app_paths.load_settings()["station_names"] is False
  finally:
    station_names.reload()


def test_full_short_and_back(aliases):
  table = aliases(SAMPLE)
  assert table == {"Bamba": ("Irie", "Irie"), "Nizaemn": ("Takinomachi", "Takino"),
                   "Hokudai": ("SobetsuOnsen", "Sobetsu")}
  assert station_names.full("Nizaemn") == "Takinomachi"
  assert station_names.short("Nizaemn") == "Takino"
  # 表に無い拠点(Mimatsu、ほかの機関の観測点)はそのまま
  assert station_names.full("Mimatsu") == "Mimatsu"
  assert station_names.short("USUA") == "USUA"
  # 「拠点名.成分」
  assert station_names.channel("Hokudai.U") == "SobetsuOnsen.U"
  assert station_names.channel("Hokudai.U", short_name=True) == "Sobetsu.U"
  # 表示の名前(正式名・略称・内部名)から内部名に戻す
  assert station_names.internal("Takinomachi") == "Nizaemn"
  assert station_names.internal("Takino") == "Nizaemn"
  assert station_names.internal("Nizaemn") == "Nizaemn"
  assert station_names.internal_channel("SobetsuOnsen.E") == "Hokudai.E"
  assert station_names.internal_channel("Mimatsu.N") == "Mimatsu.N"


def test_bad_lines_are_skipped(aliases):
  table = aliases("Bamba\n"                       # 名前が無い
                  "Bamba  Iri.e\n"                # 「.」は使えない
                  "Bamba  A  B  C\n"              # 項目が多い
                  "Nizaemn  Takinomachi\n"        # 略称を省くと正式名
                  "Hokudai  Takinomachi\n"        # ほかの拠点の名前とぶつかる
                  "Nizaemn  Other\n"              # 同じ拠点の2行目
                  "Mimatsu  Bamba\n")             # ほかの拠点の内部名とぶつかる
  assert table == {"Nizaemn": ("Takinomachi", "Takinomachi")}


def test_swapped_internal_names_are_rejected(aliases):
  # 内部名どうしを入れ替えると、どの拠点か分からなくなるので使わない
  assert aliases("Bamba  Hokudai\nHokudai  Bamba\n") == {}


def stream():
  st = Stream()
  for nm in ("Bamba", "Hokudai", "Mimatsu", "Nizaemn"):
    for c in "UNE":
      st += Trace(np.zeros(500), header={"station": nm, "channel": c, "sampling_rate": 100,
                                         "starttime": UTCDateTime(2026, 7, 8, 21, 30)})
  return st


def test_waveform_figures_use_display_names(aliases):
  import matplotlib.pyplot as plt
  import waveform_window
  aliases(SAMPLE)
  ordered = waveform_window.sort_stream(stream())
  fig = waveform_window.create_waveform_figure(ordered)
  try:
    # 2x2 は各枠の見出しに正式名
    waveform_window.draw_station_grid(fig, ordered, "")
    titles = {ax.get_title() for ax in fig.axes if ax.get_title()}
    assert titles == {"Irie", "SobetsuOnsen", "Mimatsu", "Takinomachi"}
    # 縦1列は縦軸のラベル(狭い所)に略称
    waveform_window.draw_vertical(fig, ordered, "")
    labels = {ax.get_ylabel().split("\n")[0] for ax in fig.axes if ax.get_ylabel()}
    assert labels == {"Irie", "Sobetsu", "Mimatsu", "Takino"}
  finally:
    plt.close(fig)
  # 波形そのものの拠点名(内部名)は変わらない
  assert {tr.stats.station for tr in ordered} == {"Bamba", "Hokudai", "Mimatsu", "Nizaemn"}


def test_stack_title_uses_full_name(aliases):
  import matplotlib.pyplot as plt
  from datetime import datetime
  from stack_plot import StackFigure
  aliases(SAMPLE)
  tr = stream().select(station="Nizaemn", channel="U")[0]
  stack = StackFigure(for_screen=False)
  try:
    stack.render(tr, datetime(2026, 7, 8, 21), "Nizaemn", "U")
    assert "Takinomachi.U" in stack.fig._suptitle.get_text()
  finally:
    plt.close(stack.fig)


def test_web_map_uses_full_name(aliases, stations):
  import quake_web_map
  aliases(SAMPLE)
  names = [s["name"] for s in quake_web_map.map_data([], stations)["stations"]]
  assert sorted(names) == ["Irie", "Mimatsu", "SobetsuOnsen", "Takinomachi"]
