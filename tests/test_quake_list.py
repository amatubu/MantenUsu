# quake_list.py(地震リストの保存・CSV の取り込み・一覧への加え方)のテスト
#   本物の地震リストを書き換えないよう、user_files でユーザーの設定を一時フォルダに差し替える
import csv
import json

import pytest

import quake_list
from quake_list import KNOWN, USU


def entry(start, end=None, **fields):
  return {"start": start, "end": end or start, "time": start, "image": "", "fields": dict(fields)}


def empty_lists():
  return {KNOWN: [], USU: [], "removed": {KNOWN: [], USU: []}}


def write_csv(path, header, rows):
  with open(path, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=header)
    w.writeheader()
    for r in rows:
      w.writerow({k: r.get(k, "") for k in header})
  return path


# --- 波形を表示する範囲 ---

@pytest.mark.parametrize("start, end, expected", [
  # 開始の30秒前から終了の30秒後までを、分にそろえる
  ("2026-08-23 02:02:10", "2026-08-23 02:03:40", ("202608230201", "202608230204")),
  ("2026-08-23 02:02:40", "2026-08-23 02:02:50", ("202608230202", "202608230203")),
  # 日をまたぐ
  ("2026-08-23 23:59:50", "2026-08-24 00:00:20", ("202608232359", "202608240000")),
])
def test_display_range(start, end, expected):
  assert quake_list.display_range(entry(start, end)) == expected


def test_display_range_is_at_least_one_minute():
  t0, t1 = quake_list.display_range(entry("2026-08-23 02:02:00", "2026-08-23 02:02:00"))
  assert (t0, t1) == ("202608230201", "202608230202")


def test_title_of():
  known = entry("2026-08-23 02:02:10", 震央地名="茨城県南部", M="5.9")
  known["time"] = "2026-08-23 02:00:40.3"
  assert quake_list.title_of(KNOWN, known) == "2026-08-23 02:00:40 茨城県南部 M5.9(既知の地震)"
  usu = entry("2026-03-04 22:58:30", **{"深さ[km]": "<0.4"})
  assert "有珠山付近" in quake_list.title_of(USU, usu) and "<0.4" in quake_list.title_of(USU, usu)


# --- 一覧に加える・置き換える・外す ---

def test_merge_adds_and_replaces_keeping_blank_fields():
  lists = empty_lists()
  old = entry("2026-01-01 00:00:00", 深さ="2.0", メモ="手で書いたメモ")
  old["image"] = "batch_plots/quakes/a.png"
  lists[USU] = [old]
  new = entry("2026-01-01 00:00:00", 深さ="2.5", メモ="")   # メモ・画像が空の行で取り込み直す
  added = entry("2026-01-02 00:00:00", 深さ="1.0")
  result = quake_list.merge(lists, {KNOWN: [], USU: [added, new]})
  assert result[USU] == (1, 1)
  first, second = lists[USU]
  assert [e["start"] for e in lists[USU]] == ["2026-01-01 00:00:00", "2026-01-02 00:00:00"]
  # 新しい値で置き換え、空の値は前の値を残す
  assert first["fields"] == {"深さ": "2.5", "メモ": "手で書いたメモ"}
  assert first["image"] == "batch_plots/quakes/a.png"


def test_merge_keep_existing_known():
  lists = empty_lists()
  lists[KNOWN] = [entry("2026-01-01 00:00:00", 震央地名="前の値")]
  quake_list.merge(lists, {KNOWN: [entry("2026-01-01 00:00:00", 震央地名="新しい値")], USU: []},
                   keep_existing_known=True)
  assert lists[KNOWN][0]["fields"]["震央地名"] == "前の値"


def test_removed_quake_is_skipped_by_auto_add_but_readded_by_import():
  lists = empty_lists()
  e = entry("2026-01-01 00:00:00")
  lists[KNOWN] = [e]
  quake_list.remove(lists, KNOWN, e)
  assert lists[KNOWN] == [] and lists["removed"][KNOWN] == ["2026-01-01 00:00:00"]
  # 地震の候補を探したあとの自動の追加では戻さない
  quake_list.merge(lists, {KNOWN: [entry("2026-01-01 00:00:00")], USU: []}, skip_removed=True)
  assert lists[KNOWN] == []
  # 自分で CSV を取り込んだときは戻し、外した印を消す
  quake_list.merge(lists, {KNOWN: [entry("2026-01-01 00:00:00")], USU: []})
  assert len(lists[KNOWN]) == 1 and lists["removed"][KNOWN] == []


# --- 保存と読み込み ---

def test_save_and_load(user_files):
  _user_dir, data_dir = user_files
  lists = empty_lists()
  e = entry("2026-01-01 00:00:00")
  e["image"] = str(data_dir / "batch_plots" / "x.png")   # 前の版はフルパスで持っていた
  lists[USU] = [e]
  quake_list.save(lists)
  loaded = quake_list.load()
  # データフォルダの中の画像は、データフォルダからの相対パスにする
  assert loaded[USU][0]["image"] == "batch_plots/x.png"
  assert loaded["removed"] == {KNOWN: [], USU: []}


def test_load_without_file(user_files):
  assert quake_list.load() == empty_lists()


def test_add_from_detection(user_files, tmp_path):
  path = write_csv(tmp_path / "quake_candidates_test.csv",
                   ["開始", "終了", "震源リストとの対応", "気象庁: 発震時刻", "震央地名", "M", "深さ[km]", "震央距離[km]", "最大の倍率"],
                   [{"開始": "2026-08-23 02:02:10", "終了": "02:04:00", "震源リストとの対応": "対応(P波の見込み +2秒)",
                     "気象庁: 発震時刻": "2026-08-23 02:00:40.3", "震央地名": "茨城県南部", "M": "5.9"},
                    {"開始": "2026-08-24 01:00:00", "終了": "01:00:30", "震源リストとの対応": "なし"}])
  assert quake_list.add_from_detection(path) == (1, 0)
  saved = json.loads(user_files[0].joinpath("quake_lists.json").read_text(encoding="utf-8"))
  assert [e["start"] for e in saved[KNOWN]] == ["2026-08-23 02:02:10"]
  assert saved[KNOWN][0]["end"] == "2026-08-23 02:04:00"
  assert saved[KNOWN][0]["fields"]["震央地名"] == "茨城県南部"


# --- CSV の読み込み ---

def test_read_candidates_csv_end_time_next_day(tmp_path):
  path = write_csv(tmp_path / "c.csv", ["開始", "終了", "震源リストとの対応", "気象庁: 発震時刻"],
                   [{"開始": "2026-08-23 23:59:40", "終了": "00:00:20", "震源リストとの対応": "対応",
                     "気象庁: 発震時刻": "2026-08-23 23:59:00.0"}])
  out = quake_list.read_csv(path)
  assert out[USU] == []
  assert out[KNOWN][0]["end"] == "2026-08-24 00:00:20"   # 終了が時刻だけで、日をまたいでいる


def test_read_locations_csv(tmp_path):
  header = ["開始", "判定", "緯度", "経度", "深さ[km](海面下)", "残差[秒]", "発生時刻", "気象庁: 発震時刻",
            "震央地名", "M", "山頂からの距離[km]", "画像"]
  path = write_csv(tmp_path / "x_locations.csv", header, [
    {"開始": "2026-03-04 22:58:30", "判定": "近い地震", "緯度": "42.5390", "経度": "140.8380",
     "深さ[km](海面下)": "<0.4", "残差[秒]": "0.040", "発生時刻": "22:58:37.12",
     "気象庁: 発震時刻": "2026-03-04 22:58:37.0", "震央地名": "胆振地方西部", "M": "0.7", "山頂からの距離[km]": "0.5"},
    {"開始": "2026-03-05 23:59:50", "判定": "近い地震", "緯度": "42.5400", "経度": "140.8400",
     "深さ[km](海面下)": "2.0", "残差[秒]": "0.020", "発生時刻": "00:00:01.50", "山頂からの距離[km]": "0.7"},
  ])
  out = quake_list.read_csv(path)
  assert len(out[USU]) == 2 and len(out[KNOWN]) == 1
  first, second = out[USU]
  assert first["time"] == "2026-03-04 22:58:37.12"
  assert first["end"] == "2026-03-04 22:59:00"           # 終了の列が無いときは開始の30秒後
  assert first["fields"]["深さ[km]"] == "<0.4"
  assert first["fields"]["山頂から"].startswith("0.5 km ")   # 方位は緯度・経度から求める
  assert second["time"] == "2026-03-06 00:00:01.50"      # 発生時刻が日をまたいでいれば翌日にする
  assert out[KNOWN][0]["fields"]["震央地名"] == "胆振地方西部"


def test_read_unknown_csv(tmp_path):
  assert quake_list.read_csv(write_csv(tmp_path / "u.csv", ["開始", "何か"], [{"開始": "2026-01-01 00:00:00"}])) is None
  assert quake_list.read_csv(write_csv(tmp_path / "v.csv", ["列"], [{"列": "1"}])) is None


# --- 有珠山付近の地震を、地震の候補の CSV に書き出す(地震リストの「地図にプロット」) ---

def test_write_usu_candidates_reads_back_with_jma(user_files, monkeypatch, tmp_path):
  import app_paths
  import quake_locate
  jma_dir = tmp_path / "jma"
  jma_dir.mkdir()
  monkeypatch.setattr(app_paths, "JMA_HYPOCENTER_DIR", jma_dir)
  write_csv(jma_dir / "20250708.csv", ["日時", "緯度", "経度", "深さ[km]", "M", "震央地名"],
            [{"日時": "2025-07-08 21:30:04.70", "緯度": "42.55", "経度": "140.85", "深さ[km]": "2", "M": "0.3",
              "震央地名": "胆振地方西部"}])
  lists = empty_lists()
  lists[USU] = [entry("2025-07-22 08:49:30", "2025-07-22 08:50:00", メモ="山頂付近", らしさ="低"),
                entry("2025-07-08 21:30:00", "2025-07-08 21:30:40")]
  # 既知の地震に同じ開始があれば、その発震時刻で震源リストから引く
  lists[KNOWN] = [dict(entry("2025-07-08 21:30:00", "2025-07-08 21:30:40"), time="2025-07-08 21:30:04.7")]
  out = tmp_path / "out" / "usu.csv"
  assert quake_list.write_usu_candidates(out, lists) == 2
  cands = quake_locate.read_candidates(out)
  assert [f"{c['start']:%H:%M:%S}" for c in cands] == ["21:30:00", "08:49:30"]   # 開始の順
  assert f"{cands[0]['end']:%H:%M:%S}" == "21:30:40"
  assert cands[0]["jma"]["震央地名"] == "胆振地方西部" and cands[0]["jma"]["M"] == 0.3
  assert cands[1]["jma"] is None
  assert cands[1]["row"]["メモ"] == "山頂付近" and cands[1]["row"]["有珠山の地震らしさ"] == "低"


# --- メモの編集(地震リストの「メモを編集」) ---

def test_set_memo_saves_and_clears(user_files):
  lists = empty_lists()
  e = entry("2026-06-23 12:58:40", メモ="")
  lists[USU] = [e]
  quake_list.set_memo(e, "  採石場の発破の可能性 ")
  quake_list.save(lists)
  assert quake_list.load()[USU][0]["fields"]["メモ"] == "採石場の発破の可能性"
  quake_list.set_memo(e, "")
  quake_list.save(lists)
  assert quake_list.load()[USU][0]["fields"]["メモ"] == ""
