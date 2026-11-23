# 「この揺れは何?」クイズ(shake_quiz.py)のテスト。波形のデータは作り物を使う(問題の一覧は etc/shake_quiz.csv)
from datetime import datetime, timedelta
import csv
import json
import re

import numpy as np
import obspy
import pytest
from scipy import signal

import quake_wave_view as wv
import shake_quiz as sq

T0 = datetime(2026, 5, 20, 23, 51, 57)
HEADER = ["問題", "種類", "基準の時刻", "基準", "表示の始まり[秒]", "表示の終わり[秒]", "内容", "この揺れの印", "注意",
          "2つめの地震", "答えの音の拠点", "答えのフィルタ"]


def write_quiz(path, rows):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    w.writerows(rows)
  return path


def make_trace(data, station, start, rate=250.0):
  return obspy.Trace(data=np.asarray(data, dtype=float),
                     header={"station": station, "channel": "U", "sampling_rate": rate, "starttime": obspy.UTCDateTime(start)})


def test_quiz_file_is_valid():
  from conftest import ETC_DIR
  quiz = sq.read_quiz(ETC_DIR / sq.QUIZ_FILE)
  assert [q["no"] for q in quiz] == list(range(1, len(quiz) + 1))
  assert {q["kind"] for q in quiz} == set(sq.KINDS)            # どの種類も 1 問はある
  for q in quiz:
    assert q["show"][0] < 0 < q["show"][1]                     # 基準の時刻(0 秒)が表示の中にある
    assert q["label"] and q["ref"]


def test_read_quiz_sorts_and_reads_settings(tmp_path):
  path = write_quiz(tmp_path / "q.csv", [
    [2, "花火", "2026-06-27 21:04:46", "Hokudai に音が届いた時刻", -60, 40, "花火", "", "", "", "", ""],
    [1, "少し遠い地震", "2026-01-09 04:30:03", "発生時刻(気象庁)", -10, 110, "渡島", "印", "注意の文", "2026-01-09 04:30:42.6", "Hokudai", ""],
    [3, "さらに遠い地震(海外)", "2026-07-11 19:46:43", "PKIKP", -60, 600, "南", "", "", "", "", "L"],
  ])
  quiz = sq.read_quiz(path)
  assert [q["no"] for q in quiz] == [1, 2, 3]
  assert quiz[0]["focus"] == "印" and quiz[0]["note"] == "注意の文" and quiz[0]["second"] == "2026-01-09 04:30:42.6"
  assert quiz[0]["audio_station"] == "Hokudai" and quiz[0]["show"] == (-10.0, 110.0)
  assert quiz[1]["focus"] == "" and quiz[1]["audio_station"] == "" and quiz[1]["answer_filter"] == ""
  assert quiz[2]["answer_filter"] == "L"


@pytest.mark.parametrize("rows, message", [
  ([[1, "雷", "2026-06-27 21:04:46", "x", -1, 1, "x", "", "", "", ""]], "種類"),
  ([[1, "花火", "2026-06-27 21:04:46", "x", -1, 1, "x", "", "", "", "", "L"]], "答えのフィルタ"),   # L は海外の地震だけ
  ([[1, "花火", "2026-06-27 21:04:46", "x", -1, 1, "x", "", "", "", "KBBY"]], "満点の拠点"),
  ([[1, "花火", "2026-06-27 21:04:46", "x", -1, 1, "x", "", "", "", ""],
    [1, "発破", "2026-05-21 12:09:25", "x", -1, 1, "x", "", "", "", ""]], "重なって"),
])
def test_read_quiz_rejects_bad_rows(tmp_path, rows, message):
  with pytest.raises(ValueError, match=message):
    sq.read_quiz(write_quiz(tmp_path / "q.csv", rows))


def test_display_names_use_the_name_table(monkeypatch):
  import station_names
  monkeypatch.setattr(station_names, "_enabled", True)
  assert sq.display_names("Hokudai に音が届いた時刻") == f"{station_names.full('Hokudai')} に音が届いた時刻"
  assert sq.display_names("Hokudai2 と Bamba") == f"Hokudai2 と {station_names.full('Bamba')}"   # 名前の一部は変えない


@pytest.mark.parametrize("rate, far, count", [(250, False, 3), (250, True, 5), (125, False, 2), (125, True, 4)])
def test_audio_sections_remove_mains_hum(rate, far, count):
  sections = sq.audio_sections(rate, far)
  assert len(sections) == count                     # 高域通過 + 50・100 Hz のノッチ(ナイキストより下だけ) + 低域通過(遠くの地震)
  sos = np.array([[b0, b1, b2, 1.0, a1, a2] for b0, b1, b2, a1, a2 in sections])
  t = np.arange(int(20 * rate)) / rate
  def gain(f):
    y = signal.sosfiltfilt(sos, np.sin(2 * np.pi * f * t))
    return np.std(y[rate * 5:-rate * 5]) / np.std(np.sin(2 * np.pi * f * t))
  assert gain(5.0) == pytest.approx(1.0, abs=0.02)   # 地震の揺れの帯域は通す
  assert gain(50.0) < 0.01                           # 電気の雑音は消す
  if far:
    assert gain(23.0) < 0.05                         # 遠くの地震は、すぐ上の細い雑音の線も消す


def quiz_item(kind, **kw):
  q = {"no": 1, "kind": kind, "t": T0, "ref": "x", "show": (-10.0, 50.0), "label": "x", "focus": "", "note": "",
       "second": "", "audio_station": "", "answer_filter": ""}
  q.update(kw)
  return q


def test_audio_station_rules():
  assert sq.audio_station(quiz_item("鉄道"), obspy.Stream()) == "Bamba"
  assert sq.audio_station(quiz_item("花火"), obspy.Stream()) == "Hokudai"
  assert sq.audio_station(quiz_item("発破", audio_station="Mimatsu"), obspy.Stream()) == "Mimatsu"


def test_audio_station_picks_the_clearest_record():
  rng = np.random.default_rng(3)
  start = T0 - timedelta(seconds=10)
  t = np.arange(60 * 250) / 250 - 10
  quake = np.where((t > 0) & (t < 10), np.sin(2 * np.pi * 5 * t) * 50, 0.0)
  st = obspy.Stream([make_trace(rng.normal(size=t.size) + quake, "Mimatsu", start),          # はっきり見える
                     make_trace(rng.normal(size=t.size) * 20 + quake, "Nizaemn", start),     # 雑音が大きい
                     make_trace(rng.normal(size=t.size) + quake * 5, "Bamba", start)])       # いちばん大きいが、印の問題では選ばない
  assert sq.audio_station(quiz_item("有珠山の地震"), st) == "Bamba"
  assert sq.audio_station(quiz_item("有珠山の地震", focus="列車が入っている"), st) == "Mimatsu"


def test_event_data_and_written_page(tmp_path, stations):
  start = T0 - timedelta(seconds=10)
  st = obspy.Stream([make_trace(np.arange(60 * 250) % 7 * 5.96, "Hokudai", start)])
  q = quiz_item("発破", label="Hokudai の近く", focus="印", note="注意")
  info, waves = sq.event_data(q, st, {("Hokudai", "U"): 5.96}, stations)
  assert list(waves) == ["Hokudai"] and info["have"] == ["Hokudai"]
  assert info["rate"] == 250 and info["n"] == 60 * 250 and info["t0"] == -10.0
  assert info["kind"] == sq.CHOICES.index("発破") and info["best"] == "A" and info["focus"] is True
  assert info["phases"] == {} and info["low"] is False and info["audio"] == {"station": "Hokudai", "speed": 20, "far": False, "speeds": [20, 40, 80], "lowNote": sq.low_speed_note([20, 40, 80])}

  path = tmp_path / "quiz.html"
  size = sq.write_page(path, [(info, waves)])
  text = path.read_text(encoding="utf-8")
  assert size == path.stat().st_size
  assert not re.search(r"__[A-Z]+__", text)                            # 置き換え忘れが無い
  assert not re.search(r"""(?:src|href)=["']?https?:""", text)          # 外部のファイルを読まない
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', text, re.S).group(1))
  assert len(meta["choices"]) == len(sq.KINDS) and [f["key"] for f in meta["filters"]] == ["A", "B", "C", "D", "L"]
  assert [f["extra"] for f in meta["filters"]] == [False] * 4 + [True]          # L は答えのあとだけ
  assert meta["filters"][0]["sections"]["250"] == wv.filter_sections((0.5, 2.0), 250)
  assert meta["audio"]["common"] == sq.COMMON_SPEED and meta["audio"]["first"] == sq.FIRST_SOUND
  data = json.loads(re.search(r'id="w1">(.*?)</script>', text, re.S).group(1))
  assert wv.decode_samples(data["Hokudai"]["U"]["d"]).size == info["n"]

  sq.write_answers(tmp_path / "answers.csv", [(info, waves)])
  rows = list(csv.DictReader(open(tmp_path / "answers.csv", encoding="utf-8-sig")))
  assert rows[0]["答え"] == "発破(爆破)" and rows[0]["答えの音の速さ"] == "×20"


def test_long_windows_are_thinned(stations, monkeypatch):
  monkeypatch.setattr(sq, "world_phases", lambda ref, stations: {})     # USGS の一覧は読まない
  st = obspy.Stream([make_trace(np.arange(700 * 250) % 9, "Hokudai", T0 - timedelta(seconds=60))])
  info, _ = sq.event_data(quiz_item("さらに遠い地震(海外)", show=(-60.0, 600.0)), st, {}, stations)
  assert info["rate"] == 125 and info["n"] == 660 * 125


def test_low_speed_note():
  import shake_quiz as sq
  assert sq.low_speed_note([20, 40]) == ""
  blast = sq.low_speed_note(sq.ANSWER_SPEEDS["発破"])
  assert "×80" in blast and "L" not in blast
  world = sq.low_speed_note(sq.ANSWER_SPEEDS["さらに遠い地震(海外)"])
  assert "×80" in world and "×320 で 32〜160 Hz" in world
