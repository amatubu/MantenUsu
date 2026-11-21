# 揺れを並べて比べるページ(shake_compare.py)のテスト。波形のデータは作り物を使う(テーマの一覧は etc/shake_compare.csv)
from datetime import datetime, timedelta
import csv
import json
import re

import numpy as np
import obspy
import pytest

import quake_wave_view as wv
import shake_compare as sc
import shake_quiz as sq

T0 = datetime(2026, 5, 20, 23, 51, 57)
HEADER = ["テーマ", "題", "説明", "見どころ", "基準", "表示の始まり[秒]", "表示の終わり[秒]", "P波でそろえる", "はじめの拠点", "振幅",
          "フィルタ", "音の速さ", "紙のフィルタ", "見方", "周波数の範囲[秒]", "目印の帯域", "振幅の基準", "0秒の線の名前", "目印の線", "段の名前", "種類", "基準の時刻", "拠点", "2つめの地震",
          "グループ"]


def write_themes(path, rows):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    w.writerows(rows)
  return path


def theme_row(no, title="題", **kw):
  r = dict.fromkeys(HEADER, "")
  r.update({"テーマ": no, "題": title, "基準": "発生時刻", "表示の始まり[秒]": -5, "表示の終わり[秒]": 20, "基準の時刻": "2026-05-20 23:51:57"})
  r.update(kw)
  return [r[c] for c in HEADER]


def row(no, **kw):
  r = dict.fromkeys(HEADER, "")
  r.update({"テーマ": no, "基準の時刻": "2026-05-21 12:09:25"})
  r.update(kw)
  return [r[c] for c in HEADER]


def make_trace(data, station, start, rate=250.0):
  return obspy.Trace(data=np.asarray(data, dtype=float),
                     header={"station": station, "channel": "U", "sampling_rate": rate, "starttime": obspy.UTCDateTime(start)})


def test_theme_file_is_valid():
  from conftest import ETC_DIR
  themes = sc.read_themes(ETC_DIR / sc.THEME_FILE)
  assert sorted(th["no"] for th in themes) == list(range(1, len(themes) + 1))
  views = [th["view"] for th in themes]
  assert views == sorted(views, key=lambda v: v != "wave")                 # 波形のテーマのあとに周波数のテーマ
  for th in themes:
    assert th["title"] and th["text"] and th["look"] and th["rows"]
    assert th["show"][0] < 0 < th["show"][1]
    assert th["filter"] in ("", "A", "B", "C", "D", "L") and th["speeds"]
    assert not th["pick"] or th["first"] in sq.STATIONS


def test_read_themes_inherits_theme_settings(tmp_path):
  path = write_themes(tmp_path / "t.csv", [
    theme_row(2, "拠点で比べる", **{"段の名前": "", "拠点": "Hokudai", "振幅": "共通", "フィルタ": "D", "音の速さ": "5 10"}),
    row(2, **{"拠点": "Bamba", "基準の時刻": "2026-05-20 23:51:57"}),
    theme_row(1, "地震と発破", **{"段の名前": "地震", "種類": "有珠山の地震", "はじめの拠点": "Mimatsu", "P波でそろえる": "○",
                                  "紙のフィルタ": "元 A"}),
    row(1, **{"段の名前": "発破", "種類": "発破"}),
  ])
  themes = sc.read_themes(path)
  assert [th["no"] for th in themes] == [1, 2]
  one, two = themes
  assert one["pick"] and one["first"] == "Mimatsu" and one["align_p"] and not one["common"] and one["speeds"] == [sq.COMMON_SPEED]
  assert one["paper"] == ["", "A"]
  assert [r["label"] for r in one["rows"]] == ["地震", "発破"] and one["rows"][1]["t"] == datetime(2026, 5, 21, 12, 9, 25)
  assert not two["pick"] and two["common"] and two["filter"] == "D" and two["speeds"] == [5, 10] and two["paper"] == ["D"]
  assert [r["station"] for r in two["rows"]] == ["Hokudai", "Bamba"]


@pytest.mark.parametrize("rows, message", [
  ([theme_row(1), row(1, **{"題": "2つめの題"})], "最初の行にだけ"),
  ([theme_row(1, **{"拠点": "Hokudai"}), row(1)], "全部の段に書くか"),
  ([theme_row(1, **{"フィルタ": "E"})], "フィルタ"),
  ([theme_row(1, **{"フィルタ": "X"})], "A〜D・L"),
  ([theme_row(1, **{"紙のフィルタ": "元 X"})], "紙のフィルタ"),
  ([theme_row(1, **{"種類": "雷"})], "種類"),
  ([theme_row(1, **{"拠点": "KBBY"})], "満点の拠点"),
  ([theme_row(1, **{"振幅": "大きい順"})], "振幅"),
  ([theme_row(1, **{"表示の始まり[秒]": 30})], "表示の始まり"),
  ([row(1)], "題がありません"),
  ([theme_row(1, **{"見方": "地図"})], "見方"),
  ([theme_row(1, **{"見方": "周波数", "周波数の範囲[秒]": "0 99"})], "周波数の範囲"),
  ([theme_row(1, **{"見方": "周波数", "目印の帯域": "4-2"})], "目印の帯域"),
])
def test_read_themes_rejects_bad_rows(tmp_path, rows, message):
  with pytest.raises(ValueError, match=message):
    sc.read_themes(write_themes(tmp_path / "t.csv", rows))


def fake_loader(streams):
  calls = []
  def load(t0, t1):
    calls.append((t0, t1))
    return streams
  return load, calls


def test_theme_data_with_fixed_stations(stations):
  st = obspy.Stream([make_trace(np.arange(40 * 250) % 7 * 5.96, nm, T0 - timedelta(seconds=10)) for nm in ("Hokudai", "Bamba")])
  th = {"no": 5, "title": "花火", "text": "Hokudai から", "look": "見て", "ref": "Hokudai に届いた時刻", "show": (-5.0, 20.0),
        "align_p": False, "first": "", "common": True, "filter": "D", "speeds": [5, 10], "paper": ["D"], "pick": False,
        "rows": [{"label": "", "kind": "花火", "t": T0, "station": nm, "second": ""} for nm in ("Hokudai", "Mimatsu", "Bamba")]}
  load, calls = fake_loader(st)
  info, waves = sc.theme_data(th, load, {("Hokudai", "U"): 5.96}, stations)
  assert info["rate"] == 250 and info["n"] == 25 * 250 and info["t0"] == -5.0
  assert [list(w) for w in waves] == [["Hokudai"], [], ["Bamba"]]              # Mimatsu は記録なし
  assert [r["have"] for r in info["rows"]] == [["Hokudai"], [], ["Bamba"]]
  assert [r["label"] for r in info["rows"]] == ["Hokudai", "Mimatsu", "Bamba"]   # 段の名前が空なら拠点の名前
  assert info["rows"][0]["phases"] == {} and not info["rows"][0]["far"]
  assert calls[0] == (T0 - timedelta(seconds=5), T0 + timedelta(seconds=20))
  assert wv.decode_samples(waves[0]["Hokudai"]["U"]["d"]).size == info["n"]


def test_theme_data_aligns_each_station_at_p(stations, monkeypatch):
  # 拠点ごとに P波の到着の見込みを 0 秒にする(拠点によって切り出す範囲がずれる)
  p = {"Hokudai": 20.0, "Mimatsu": 21.0, "Nizaemn": 22.0, "Bamba": 23.0}
  monkeypatch.setattr(sq, "phase_lines", lambda q, st: {nm: {"p": p[nm], "s": p[nm] + 15} for nm in st})
  monkeypatch.setattr(sq, "second_phases", lambda q, st: {})
  start = T0 - timedelta(seconds=10)
  t = np.arange(80 * 250) / 250 - 10
  st = obspy.Stream([make_trace(np.where(t >= p[nm], 1000.0, 0.0) + np.arange(t.size) % 3, nm, start) for nm in sq.STATIONS])
  th = {"no": 2, "title": "遠さ", "text": "", "look": "", "ref": "P波", "show": (-5.0, 30.0), "align_p": True, "first": "Hokudai",
        "common": False, "filter": "", "speeds": [40], "paper": [""], "pick": True,
        "rows": [{"label": "十勝", "kind": "少し遠い地震", "t": T0, "station": "", "second": ""}]}
  load, calls = fake_loader(st)
  info, waves = sc.theme_data(th, load, {}, stations)
  r = info["rows"][0]
  assert r["have"] == sq.STATIONS and r["far"] and r["aligned"]
  assert calls[0] == (T0 + timedelta(seconds=15), T0 + timedelta(seconds=53))   # 全部の拠点の範囲をまとめて読む
  for nm in sq.STATIONS:
    assert r["phases"][nm] == {"p": 0.0, "s": 15.0}
    v = wv.decode_samples(waves[0][nm]["U"]["d"])
    step_at = int(np.argmax(np.diff(v) > 100)) + 1
    assert step_at == 5 * 250                                                   # どの拠点も 0 秒(表示の 5 秒目)で段差


def test_written_page(tmp_path, stations):
  st = obspy.Stream([make_trace(np.arange(40 * 250) % 7 * 5.96, "Hokudai", T0 - timedelta(seconds=10))])
  th = {"no": 1, "title": "比べる", "text": "Hokudai の説明", "look": "見どころ", "ref": "発生時刻", "show": (-5.0, 20.0),
        "align_p": False, "first": "Hokudai", "common": False, "filter": "", "speeds": [20, 40], "paper": ["", "A"], "pick": True,
        "rows": [{"label": "地震", "kind": "有珠山の地震", "t": T0, "station": "", "second": ""},
                 {"label": "発破", "kind": "発破", "t": T0, "station": "", "second": ""}]}
  info, waves = sc.theme_data(th, fake_loader(st)[0], {}, stations)
  path = tmp_path / "compare.html"
  size = sc.write_page(path, [(info, waves)])
  text = path.read_text(encoding="utf-8")
  assert size == path.stat().st_size
  assert not re.search(r"__[A-Z_]+__", text)                            # 置き換え忘れが無い
  assert not re.search(r"""(?:src|href)=["']?https?:""", text)          # 外部のファイルを読まない
  assert "function resampleForSound(" in text and "function drawTrace(" in text   # クイズと同じ部品
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', text, re.S).group(1))
  assert [f["key"] for f in meta["filters"]] == ["A", "B", "C", "D", "L"] and list(meta["audio"]["sections"]) == ["250"]
  t = meta["themes"][0]
  assert t["speeds"] == [20, 40] and t["pick"] and [r["label"] for r in t["rows"]] == ["地震", "発破"]
  for k in range(2):
    data = json.loads(re.search(rf'id="w1_{k}">(.*?)</script>', text, re.S).group(1))
    assert list(data) == ["Hokudai"] and wv.decode_samples(data["Hokudai"]["U"]["d"]).size == t["n"]


def test_quiz_page_still_has_the_common_parts(tmp_path):
  path = tmp_path / "quiz.html"
  sq.write_page(path, [])
  text = path.read_text(encoding="utf-8")
  assert "__COMMON_JS__" not in text and "function resampleForSound(" in text and "function finishSound(" in text


def test_frequency_theme(tmp_path, stations):
  # 周波数のテーマ: 段ごとに、範囲の振幅スペクトル(いちばん大きいところを 1)を入れる
  path = write_themes(tmp_path / "t.csv", [
    theme_row(13, "周波数", **{"見方": "周波数", "周波数の範囲[秒]": "0 15", "目印の帯域": "1.8-3.9 4-10", "段の名前": "低い",
                              "グループ": "発破"}),
    row(13, **{"段の名前": "高い", "基準の時刻": "2026-05-20 23:51:57"}),
  ])
  th = sc.read_themes(path)[0]
  assert th["view"] == "freq" and th["spec_window"] == (0.0, 15.0) and th["marks"] == [[1.8, 3.9], [4.0, 10.0]]
  rate = 250
  t = np.arange(40 * rate) / rate - 10
  low = make_trace(np.sin(2 * np.pi * 3 * t) * 100, "Hokudai", T0 - timedelta(seconds=10))
  high = make_trace(np.sin(2 * np.pi * 8 * t) * 100, "Hokudai", T0 - timedelta(seconds=10))
  streams = iter([obspy.Stream([low]), obspy.Stream([high])])
  info, _ = sc.theme_data(th, lambda a, b: next(streams), {}, stations)
  f = np.array(info["spec_f"])
  peaks = [f[int(np.argmax(r["spec"]["Hokudai"]))] for r in info["rows"]]
  assert peaks[0] == pytest.approx(3, rel=0.1) and peaks[1] == pytest.approx(8, rel=0.1)
  assert [r["group"] for r in info["rows"]] == ["発破", "高い"]                 # グループが空なら種類(ここでは無い)の代わりに段の名前
  assert max(info["rows"][0]["spec"]["Hokudai"]) == 1.0


def test_amplitude_spectrum_handles_gaps():
  v = np.sin(2 * np.pi * 5 * np.arange(30 * 250) / 250)
  v[1000:2000] = np.nan
  f, a = sc.amplitude_spectrum(v, 250)
  assert len(f) == sc.SPEC_BINS and all(np.isfinite(a)) and f[int(np.argmax(a))] == pytest.approx(5, rel=0.1)


def test_paper_none_means_screen_only(tmp_path):
  th = sc.read_themes(write_themes(tmp_path / "t.csv", [theme_row(1, **{"紙のフィルタ": "なし", "段の名前": "x"})]))[0]
  assert th["paper"] == []


def test_amp_window(tmp_path):
  th = sc.read_themes(write_themes(tmp_path / "t.csv", [theme_row(1, **{"振幅の基準": "-5 10 前の揺れ", "段の名前": "x"})]))[0]
  assert th["amp_window"] == [-5.0, 10.0, "前の揺れ"]
  with pytest.raises(ValueError, match="振幅の基準"):
    sc.read_themes(write_themes(tmp_path / "u.csv", [theme_row(1, **{"振幅の基準": "-5 99 x", "段の名前": "x"})]))


def test_extra_lines(tmp_path):
  th = sc.read_themes(write_themes(tmp_path / "t.csv", [theme_row(1, **{"目印の線": "5 PP(見込み); 12 SS", "段の名前": "x"})]))[0]
  assert th["lines"] == [[5.0, "PP(見込み)"], [12.0, "SS"]]
  with pytest.raises(ValueError, match="目印の線"):
    sc.read_themes(write_themes(tmp_path / "u.csv", [theme_row(1, **{"目印の線": "99 x", "段の名前": "x"})]))
