# 似た波形の地震さがし(template_match.py、TODO 37)のテスト。波形とひな形は作り物を使う
from datetime import datetime, timedelta
import json

import numpy as np
import pytest

import quake_locate
import template_match as tm

DAY = datetime(2026, 11, 2)
N = int(tm.TEMPLATE_LENGTH * tm.SAMPLING_RATE)


def chirp(seed):
  rng = np.random.default_rng(seed)
  t = np.arange(N) / tm.SAMPLING_RATE
  return (np.sin(2 * np.pi * (3 + 3 * t) * t) * np.exp(-t) + 0.05 * rng.normal(size=N)).astype(np.float32)


def make_templates():
  # 0 と 1 はほとんど同じ波形、2 は別の波形。拠点ごとの時刻差は同じ
  data = np.zeros((3, 4, 3, N), np.float32)
  rng = np.random.default_rng(9)
  for si in range(4):
    for ci in range(3):
      base = chirp(si * 3 + ci)
      data[0, si, ci] = base
      data[1, si, ci] = base + 0.1 * rng.normal(size=N).astype(np.float32)
      data[2, si, ci] = rng.normal(size=N).astype(np.float32)
  offs = np.array([[0, 20, 40, 60]] * 3, np.int64)
  info = [{"name": f"2026010{i}_000000", "first": "2026-01-01T00:00:00", "origin": None, "kind": "",
           "jma_m": 1.0, "lat": None, "lon": None, "depth": None} for i in range(3)]
  return data, offs, info


def test_merge_detections_keeps_best_and_counts():
  dets = [{"time": "2026-11-02T01:00:00.000", "cc": 0.4, "template": "a"},
          {"time": "2026-11-02T01:00:01.000", "cc": 0.6, "template": "b"},
          {"time": "2026-11-02T03:00:00.000", "cc": 0.3, "template": "a"}]
  out = tm.merge_detections(dets)
  assert [d["template"] for d in out] == ["b", "a"]
  assert out[0]["n_templates"] == 2 and out[1]["n_templates"] == 1


def test_template_groups_puts_look_alikes_together():
  data, offs, info = make_templates()
  S = tm.template_similarity(data, offs)
  assert S[0, 1] > 0.9 and S[0, 2] < 0.3
  groups = tm.template_groups(data, offs, info)
  assert groups[info[0]["name"]] == groups[info[1]["name"]] == "G1"
  assert groups[info[2]["name"]] == ""


def test_template_keys_change_with_content():
  data, offs, info = make_templates()
  k1 = tm.template_keys(data, offs, info)
  data2 = data.copy()
  data2[2, 0, 0, 0] += 1
  k2 = tm.template_keys(data2, offs, info)
  assert k1[:2] == k2[:2] and k1[2] != k2[2]
  assert k1[0].startswith(info[0]["name"] + "_")


def test_scan_day_cached_reuses_and_only_computes_new(tmp_path, monkeypatch):
  data, offs, info = make_templates()
  # 1日分の波形: 雑音の中に、ひな形 0 と同じ揺れを 1 つ入れる
  npts = int(3600 * tm.SAMPLING_RATE)
  rng = np.random.default_rng(1)
  wave = {}
  k = 50000
  for si, nm in enumerate(tm.STATIONS):
    for ci, c in enumerate(tm.COMPONENTS):
      d = (0.2 * rng.normal(size=npts)).astype(np.float32)
      d[k + offs[0, si]:k + offs[0, si] + N] += 5 * data[0, si, ci]
      wave[(nm, c)] = d
  calls = []
  monkeypatch.setattr(tm, "load_filtered", lambda t0, t1: (calls.append(t0) or wave, t0))
  monkeypatch.setattr(tm, "DAYS_DIR", tmp_path)
  monkeypatch.setattr(tm, "load_templates", lambda: (data[:2], offs[:2], info[:2]))
  r1 = tm.scan_day_cached(DAY)
  assert r1["computed"] == 2 and len(calls) == 1
  assert len(r1["detections"]) == 1
  det = r1["detections"][0]
  assert abs((datetime.fromisoformat(det["first"]) - DAY).total_seconds() - k / tm.SAMPLING_RATE) < 0.05
  # 同じひな形なら波形を読まずに結果を使う
  r2 = tm.scan_day_cached(DAY)
  assert r2["computed"] == 0 and len(calls) == 1
  # ひな形が 1 件増えたら、その分だけ計算する
  monkeypatch.setattr(tm, "load_templates", lambda: (data, offs, info))
  r3 = tm.scan_day_cached(DAY)
  assert r3["computed"] == 1 and len(calls) == 2
  saved = json.loads((tmp_path / f"{DAY:%Y%m%d}.json").read_text(encoding="utf-8"))
  assert len(saved["templates"]) == 3


def test_scan_day_cached_does_not_save_days_without_data(tmp_path, monkeypatch):
  data, offs, info = make_templates()
  monkeypatch.setattr(tm, "load_filtered", lambda t0, t1: ({}, t0))
  monkeypatch.setattr(tm, "DAYS_DIR", tmp_path)
  monkeypatch.setattr(tm, "load_templates", lambda: (data, offs, info))
  r = tm.scan_day_cached(DAY)
  assert r["no_data"] and r["detections"] == []
  assert not list(tmp_path.glob("*.json"))


def test_write_candidates_is_readable_by_locate_screen(tmp_path):
  _data, _offs, info = make_templates()
  rows = [
    {"time": "2026-11-02T21:46:09.891", "first": "2026-11-02T21:46:09.000", "template": info[0]["name"], "cc": 0.35,
     "amp_ratio": 0.1, "known": "", "known_start": None},
    {"time": "2026-11-03T03:17:42.000", "first": "2026-11-03T03:17:41.000", "template": info[1]["name"], "cc": 1.0,
     "amp_ratio": 1.0, "known": "有珠山リスト", "known_start": "2026-11-03 03:17:40"},
  ]
  path = tmp_path / "c.csv"
  tm.write_candidates(path, rows, info, {info[0]["name"]: "G1"})
  cands = quake_locate.read_candidates(path)
  assert len(cands) == 1   # 有珠山リストにあるものは入れない
  c = cands[0]
  assert c["start"] == datetime(2026, 11, 2, 21, 46, 4)
  assert c["end"] == datetime(2026, 11, 2, 21, 46, 29)
  assert c["row"]["グループ"] == "G1"
  assert c["row"]["M の目安"] == "0.0"


def test_heavy_jobs_limit_is_half_of_cores():
  import maintenance_window as mw
  assert mw.heavy_jobs_limit(10) == 5
  assert mw.heavy_jobs_limit(16) == 8
  assert mw.heavy_jobs_limit(2) == 2


def test_review_xlsx_has_links_and_skips_judged(tmp_path):
  import openpyxl
  _data, _offs, info = make_templates()
  rows = [
    {"time": "2026-11-02T21:46:09.891", "first": "2026-11-02T21:46:09.000", "template": info[0]["name"], "cc": 0.35,
     "amp_ratio": 0.1, "known": "", "known_start": None},
    {"time": "2026-11-02T22:00:00.000", "first": "2026-11-02T21:59:59.000", "template": info[1]["name"], "cc": 0.3,
     "amp_ratio": 0.1, "known": "", "known_start": None},
    {"time": "2026-11-03T03:17:42.000", "first": "2026-11-03T03:17:41.000", "template": info[1]["name"], "cc": 1.0,
     "amp_ratio": 1.0, "known": "有珠山リスト", "known_start": "2026-11-03 03:17:40"},
  ]
  picked = tm.review_rows(rows, [datetime(2026, 11, 2, 22, 0, 1)])
  assert [d["time"] for d in picked] == [rows[0]["time"]]   # 判定済み・有珠山リストのものは除く
  img_dir = tmp_path / "images_app"
  img_dir.mkdir()
  (img_dir / tm.image_name(rows[0])).write_bytes(b"")
  path = tm.write_review_xlsx(tmp_path / "review.xlsx", picked, info, {info[0]["name"]: "G1"}, img_dir)
  ws = openpyxl.load_workbook(path).active
  hdr = [c.value for c in ws[1]]
  assert hdr == tm.REVIEW_FIELDS
  r = {h: c for h, c in zip(hdr, ws[2])}
  assert r["開始"].value == datetime(2026, 11, 2, 21, 46, 4)
  assert r["画像"].hyperlink.target == f"images_app/{tm.image_name(rows[0])}"
  assert r["KBBY 北屏風山"].value == "11/02 21時 46分の段"
  assert "stcd=V.KBBY&tm=2026110221" in r["KBBY 北屏風山"].hyperlink.target
  # すでにあるファイルは上書きしない
  path2 = tm.write_review_xlsx(tmp_path / "review.xlsx", picked, info, {}, img_dir)
  assert path2.name == "review_2.xlsx"


def test_combine_rows_merges_usu_list_and_judged_lists(tmp_path):
  import openpyxl
  usu = [{"start": datetime(2026, 1, 1, 0, 0, 0), "end": datetime(2026, 1, 1, 0, 0, 30), "type": "地震",
          "row": {"種別": "地震", "メモ": "ひな形の地震", "気象庁: 発震時刻": "", "緯度": "", "経度": "", "深さ[km]": "",
                  "M": "", "震央地名": "", "有珠山の地震らしさ": "高"}},
         {"start": datetime(2026, 1, 5, 12, 0, 0), "end": datetime(2026, 1, 5, 12, 0, 30), "type": "発破", "row": {"種別": "発破"}}]
  judged = [
    {"time": datetime(2026, 1, 1, 0, 0, 5), "cc": 1.0, "mad": 40, "ch": 9, "tpl": "20260101_000000", "m": None,
     "known": "有珠山リスト", "judge": None, "flag": None, "other": None, "remark": None,
     "links": {"画像": ("a.png", "images/a.png")}, "src": "前回"},
    {"time": datetime(2026, 1, 3, 1, 0, 0), "cc": 0.4, "mad": 15, "ch": 9, "tpl": "20260101_000000", "m": None,
     "known": None, "judge": "地震", "flag": 1, "other": "昭和新山下か", "remark": None, "links": {}, "src": "前回"},
    {"time": datetime(2026, 1, 3, 1, 0, 1), "cc": 0.4, "mad": 15, "ch": 9, "tpl": "20260101_000000", "m": None,
     "known": None, "judge": "判別困難", "flag": None, "other": None, "remark": "今回のメモ", "links": {}, "src": "今回"},
  ]
  rows = tm.combine_rows(usu, judged, {"20260101_000000"}, {"20260101_000000": "G1"})
  assert [r["kind"] for r in rows] == ["有珠山リスト", "似た波形で見つけたもの", "有珠山リスト"]
  assert rows[0]["tpl_self"] == "ひな形" and rows[0]["group"] == "G1" and rows[0]["memo"] == "ひな形の地震"
  assert rows[0]["links"]["画像"][1] == "images/a.png"   # そのものを拾った検出の画像を使う
  assert rows[2]["tpl_self"] == "ひな形にしない(発破)"
  # 2つの一覧にある同じ揺れは1行。判定は前の一覧のまま、空いている備考だけ埋める
  assert rows[1]["judge"] == "地震" and rows[1]["remark"] == "今回のメモ" and rows[1]["src"] == "前回・今回"
  path = tm.write_combined_xlsx(tmp_path / "all.xlsx", rows)
  wb = openpyxl.load_workbook(path)
  ws = wb["有珠山の地震"]
  assert ws.max_row == 4
  assert "stcd=V.KBBY" in ws.cell(4, len(tm.COMBINE_COLS) + 2).hyperlink.target   # リンクの無い行は時刻から作る
  assert tm.write_combined_xlsx(tmp_path / "all.xlsx", rows).name == "all_2.xlsx"


def test_days_with_zips_keeps_only_days_that_have_a_zip(tmp_path):
  import win_pipeline
  (tmp_path / "sub").mkdir()
  (tmp_path / "261101.zip").write_bytes(b"")
  (tmp_path / "sub" / "261103.zip").write_bytes(b"")
  (tmp_path / "._261102.zip").write_bytes(b"")   # Mac の付属ファイルは数えない
  days = [datetime(2026, 11, d) for d in (1, 2, 3, 4)]
  assert win_pipeline.days_with_zips(tmp_path, days) == [days[0], days[2]]


def test_scan_day_cached_uses_saved_results_when_zip_is_gone(tmp_path, monkeypatch):
  data, offs, info = make_templates()
  keys = tm.template_keys(data[:2], offs[:2], info[:2])
  det = {"time": "2026-11-02T01:00:00.000", "first": "2026-11-02T00:59:59.000", "cc": 0.5, "template": info[0]["name"]}
  saved = {"day": "2026-11-02", "templates": {keys[0]: {"stats": {}, "detections": [det]}}}
  (tmp_path / "20261102.json").write_text(json.dumps(saved), encoding="utf-8")
  monkeypatch.setattr(tm, "load_filtered", lambda t0, t1: ({}, t0))
  monkeypatch.setattr(tm, "DAYS_DIR", tmp_path)
  monkeypatch.setattr(tm, "load_templates", lambda: (data[:2], offs[:2], info[:2]))
  r = tm.scan_day_cached(DAY)
  assert not r["no_data"] and len(r["detections"]) == 1
