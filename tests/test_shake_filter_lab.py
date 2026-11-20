# フィルタの帯域をスライダーで試すページ(shake_filter_lab.py)のテスト。波形のデータは作り物を使う
from datetime import datetime, timedelta
import csv
import json
import re

import numpy as np
import obspy
import pytest

import quake_wave_view as wv
import shake_filter_lab as fl
import shake_quiz as sq

T0 = datetime(2026, 5, 21, 12, 9, 25)
HEADER = ["番号", "名前", "種類", "基準の時刻", "基準", "表示の始まり[秒]", "表示の終わり[秒]", "拠点", "音の速さ", "見やすい帯域", "説明", "試してみよう"]


def write_items(path, rows):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    w.writerows(rows)
  return path


def make_trace(data, station, start, rate=250.0):
  return obspy.Trace(data=np.asarray(data, dtype=float),
                     header={"station": station, "channel": "U", "sampling_rate": rate, "starttime": obspy.UTCDateTime(start)})


def test_item_file_is_valid():
  from conftest import ETC_DIR
  items = fl.read_items(ETC_DIR / fl.ITEM_FILE)
  assert [it["no"] for it in items] == list(range(1, len(items) + 1))
  for it in items:
    assert it["name"] and it["try"] and it["speeds"] and it["show"][0] < it["show"][1]


def test_read_items(tmp_path):
  items = fl.read_items(write_items(tmp_path / "i.csv", [
    [2, "静か", "", "2026-05-21 02:08:30", "02:08:30", 0, 120, "Hokudai", "", "", "説明", "試す"],
    [1, "発破", "発破", "2026-05-21 12:09:25", "揺れの始まり", -5, 40, "Hokudai", "20 40", "1.8-3.9", "説明", "試す"],
  ]))
  assert [it["no"] for it in items] == [1, 2]
  assert items[0]["speeds"] == [20, 40] and items[1]["speeds"] == [sq.COMMON_SPEED] and items[1]["kind"] == ""
  assert items[0]["best"] == [1.8, 3.9] and items[1]["best"] is None


@pytest.mark.parametrize("row, message", [
  ([1, "x", "雷", "2026-05-21 12:09:25", "x", -5, 40, "Hokudai", "", "", "", ""], "種類"),
  ([1, "x", "", "2026-05-21 12:09:25", "x", -5, 40, "KBBY", "", "", "", ""], "満点の拠点"),
  ([1, "x", "", "2026-05-21 12:09:25", "x", 40, -5, "Hokudai", "", "", "", ""], "表示の始まり"),
  ([1, "x", "", "2026-05-21 12:09:25", "x", -5, 40, "Hokudai", "", "29-22", "", ""], "見やすい帯域"),
  ([1, "x", "", "2026-05-21 12:09:25", "x", -5, 40, "Hokudai", "", "22〜29", "", ""], "見やすい帯域"),
])
def test_read_items_rejects_bad_rows(tmp_path, row, message):
  with pytest.raises(ValueError, match=message):
    fl.read_items(write_items(tmp_path / "i.csv", [row]))


def test_spectrum_finds_the_peak():
  rate = 250
  t = np.arange(60 * rate) / rate
  f, a = fl.spectrum(np.sin(2 * np.pi * 5 * t) + 0.1 * np.sin(2 * np.pi * 0.2 * t), rate)
  assert len(f) == fl.SPEC_BINS and f[0] > fl.SPEC_MIN and f[-1] < rate / 2
  assert f[int(np.argmax(a))] == pytest.approx(5, rel=0.1) and max(a) == 1.0
  assert all(np.isfinite(a))                                 # 線の無い低い帯も埋まる


def test_item_data_and_page(tmp_path, stations):
  rng = np.random.default_rng(4)
  st = obspy.Stream([make_trace(rng.normal(size=60 * 250) * 50, "Hokudai", T0 - timedelta(seconds=10))])
  it = {"no": 2, "name": "発破", "kind": "発破", "t": T0, "ref": "Hokudai の揺れの始まり", "show": (-5.0, 40.0),
        "station": "Hokudai", "speeds": [20, 40], "best": [1.8, 3.9], "text": "Hokudai の説明", "try": "A にしてみよう"}
  info, wave = fl.item_data(it, lambda a, b: st, {("Hokudai", "U"): 5.96}, stations)
  assert info["rate"] == 250 and info["n"] == 45 * 250 and info["have"] and info["phases"] == {} and info["best"] == [1.8, 3.9]
  assert len(info["spec"]["f"]) == fl.SPEC_BINS and wv.decode_samples(wave["d"]).size == info["n"]
  missing, none = fl.item_data(dict(it, no=3, station="Bamba"), lambda a, b: st, {}, stations)
  assert none is None and not missing["have"] and missing["spec"] is None

  path = tmp_path / "lab.html"
  size = fl.write_page(path, [(info, wave), (missing, none)])
  text = path.read_text(encoding="utf-8")
  assert size == path.stat().st_size
  assert not re.search(r"__[A-Z_]+__", text)                            # 置き換え忘れが無い
  assert not re.search(r"""(?:src|href)=["']?https?:""", text)          # 外部のファイルを読まない
  assert "function resampleForSound(" in text and "function bandSections(" in text and "function rememberMine(" in text
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', text, re.S).group(1))
  assert [p["key"] for p in meta["presets"]] == ["A", "B", "C", "D", "L"]
  assert len(meta["notch"]["250"]) == 2                                 # 50・100 Hz
  assert 'id="w2"' in text and 'id="w3"' not in text                     # 記録の無いものは波形を入れない
