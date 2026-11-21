# cf_catalog.py(CF カードの台帳)のテスト
import zipfile
from datetime import datetime
from types import SimpleNamespace

import cf_catalog as cc


def info(station="1070432003", index=1, cards=2, has_data=True):
  return SimpleNamespace(station=station, index=index, cards=cards, has_data=has_data)


RANGE = (datetime(2026, 8, 26, 8, 17, 30), datetime(2026, 10, 5, 2, 41, 0), True)


def test_label_order():
  assert sorted(["CF-10", "CF-2", "CF-1", "A"], key=cc.label_key) == ["A", "CF-1", "CF-2", "CF-10"]


def test_set_content_and_round_trip(tmp_path):
  r = cc.set_content(cc.new_row("CF-2"), info(), "Bamba", RANGE)
  assert (r["状態"], r["枚目"], r["記録の始め"], r["記録の終わり"], r["いっぱい"]) == \
    (cc.RECORDED, "1", "2026-08-26 08:17", "2026-10-05 02:41", "はい")
  b = cc.set_content(cc.new_row("CF-10"), info(has_data=False, index=0), "Bamba", None)
  assert b["状態"] == cc.BLANK and b["記録の始め"] == "" and b["枚目"] == ""
  assert cc.set_content(cc.new_row("X"), info(station=""), "", None)["状態"] == cc.FOREIGN
  path, log = cc.catalog_paths(tmp_path)
  cc.save(path, {"CF-10": b, "CF-2": r})
  assert path.read_bytes().startswith(b"\xef\xbb\xbf")       # Excel で開けるように BOM 付き
  assert list(cc.load(path)) == ["CF-2", "CF-10"] and cc.load(path)["CF-2"] == r
  cc.append_log(log, "CF-2", "登録", "x")
  cc.append_log(log, "CF-2", "初期化", "y")
  lines = log.read_text(encoding="utf-8-sig").splitlines()
  assert lines[0] == "日時,ラベル,したこと,内容" and len(lines) == 3


def test_guess_label():
  r = cc.set_content(cc.new_row("CF-2"), info(), "Bamba", RANGE)
  rows = {"CF-2": r, "CF-3": cc.set_content(cc.new_row("CF-3"), info(index=2), "Bamba", RANGE)}
  assert cc.guess_label(rows, cc.set_content(cc.new_row(""), info(), "Bamba", RANGE)) == "CF-2"
  assert cc.guess_label(rows, cc.set_content(cc.new_row(""), info(has_data=False), "Bamba", None)) is None


def test_win_state_and_reusable(tmp_path):
  r = cc.set_content(cc.new_row("CF-2"), info(), "Bamba", RANGE)
  assert cc.win_state(r, tmp_path) == cc.WIN_NONE
  st = "1070432003"
  with zipfile.ZipFile(tmp_path / "260826.zip", "w") as z:
    z.writestr(f"{st}/260826/{st}_2608260818.win", b"x")
  assert cc.win_state(r, tmp_path) == cc.WIN_PART
  with zipfile.ZipFile(tmp_path / "261005.zip", "w") as z:
    z.writestr(f"{st}/261005/{st}_2610050240.win", b"x")
  r["WIN"] = cc.win_state(r, tmp_path)
  assert r["WIN"] == cc.WIN_OK and cc.reusable(r)
  # 並べ方: 使い回せるもの、初期化済み、まだ WIN が無い記録
  b = cc.set_content(cc.new_row("CF-1"), info(has_data=False), "Bamba", None)
  n = dict(r, ラベル="CF-3", WIN=cc.WIN_NONE)
  assert [x["ラベル"] for x in sorted([n, b, r], key=cc.list_key)] == ["CF-2", "CF-1", "CF-3"]
  assert "初期化してよい 1 枚" in cc.summary({"CF-1": b, "CF-2": r, "CF-3": n})


def test_next_number():
  assert cc.next_number("満点10-009") == "満点10-010"
  assert cc.next_number("満点10-99") == "満点10-100"
  assert cc.next_number("満点10-") == "満点10-"


# 設定値が書かれないまま記録したカード(拠点ID 0000000000)を Bamba と読み替える: WIN は読み替えた拠点で探す
def test_as_station(tmp_path):
  r = cc.set_content(cc.new_row("CF-9"), info(station="0000000000"), "Bamba", RANGE, as_station="1070432003")
  assert r["拠点ID"] == "0000000000" and r["読み替え"] == "1070432003" and cc.station_of(r) == "1070432003"
  assert "0000000000 を 1070432003 と読み替え" in cc.describe(r)
  st = "1070432003"
  for day, minute in (("260826", "2608260818"), ("261005", "2610050240")):
    with zipfile.ZipFile(tmp_path / f"{day}.zip", "w") as z:
      z.writestr(f"{st}/{day}/{st}_{minute}.win", b"x")
  assert cc.win_state(r, tmp_path) == cc.WIN_OK
  # 読み替えなければ 0000000000 で探すので無い
  r2 = cc.set_content(cc.new_row("CF-8"), info(station="0000000000"), "0000000000", RANGE)
  assert r2["読み替え"] == "" and cc.win_state(r2, tmp_path) == cc.WIN_NONE


# 先頭が全部 0xFF のカードは「何も書かれていない」(ロガーのカードではない、とは分ける)
def test_empty_card():
  e = SimpleNamespace(station="", index=0, cards=0, has_data=False, empty=True)
  f = SimpleNamespace(station="", index=0, cards=0, has_data=False, empty=False)
  assert cc.set_content(cc.new_row("A"), e, "", None)["状態"] == cc.EMPTY
  assert cc.set_content(cc.new_row("B"), f, "", None)["状態"] == cc.FOREIGN
  rows = {"A": cc.set_content(cc.new_row("A"), e, "", None)}
  assert "何も書かれていない 1 枚" in cc.summary(rows)


# 並べ方: 同じ回収のカード(1 枚目の終わり = 2 枚目の始め)は続けて並べる。記録の無いものは後ろ
def test_sorted_rows():
  def rec(label, name, idx, a, b):
    return cc.new_row(label) | {"状態": cc.RECORDED, "拠点ID": name, "拠点名": name, "枚目": str(idx),
                                "記録の始め": a, "記録の終わり": b}
  rows = {r["ラベル"]: r for r in [
    rec("X-5", "Bamba", 2, "2026-07-15 07:37", "2026-08-24 16:06"),
    rec("X-1", "Bamba", 1, "2026-06-04 11:11", "2026-07-15 07:37"),
    rec("X-3", "Mimatsu", 1, "2026-06-04 10:40", "2026-07-20 00:00"),
    rec("X-2", "Hokudai", 1, "2026-07-01 00:00", "2026-07-10 00:00"),
    cc.new_row("X-9") | {"状態": cc.EMPTY},
    cc.new_row("X-0") | {"状態": cc.BLANK, "拠点名": "Bamba"}]}
  assert cc.collection_start(rows, rows["X-5"]) == "2026-06-04 11:11"
  assert [r["ラベル"] for r in cc.sorted_rows(rows)] == ["X-1", "X-5", "X-2", "X-3", "X-0", "X-9"]
  # 記録の期間: 回収の始めの順で、Bamba の 2 枚目は Hokudai(7/1 の回収)より前に続ける
  assert [r["ラベル"] for r in cc.sorted_rows(rows, "記録の期間")] == ["X-3", "X-1", "X-5", "X-2", "X-0", "X-9"]
  assert [r["ラベル"] for r in cc.sorted_rows(rows, "ラベル")] == ["X-0", "X-1", "X-2", "X-3", "X-5", "X-9"]


# まだ設置していない機体(チャンネル表に無い拠点)は、拠点・期間の並べ方で拠点の最後
def test_sorted_rows_uninstalled_last():
  def rec(label, sid, name):
    return cc.new_row(label) | {"状態": cc.RECORDED, "拠点ID": sid, "拠点名": name, "枚目": "1",
                                "記録の始め": "2025-07-06 17:44", "記録の終わり": "2025-07-07 10:29"}
  rows = {r["ラベル"]: r for r in [rec("A", "1472580096", "1472580096"), rec("B", "1070432002", "Nizaemn"),
                                   rec("C", "1070432003", "Bamba")]}
  assert [r["ラベル"] for r in cc.sorted_rows(rows, installed={"1070432002", "1070432003"})] == ["C", "B", "A"]


def test_pc_format_card():
  p = SimpleNamespace(station="", index=0, cards=0, has_data=False, empty=False, pc_format=True)
  assert cc.set_content(cc.new_row("A"), p, "", None)["状態"] == cc.PC_FORMAT
