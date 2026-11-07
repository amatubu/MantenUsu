# quake_locate.locate_all_cached(画面で候補の一覧を開くときの、震源の計算結果のキャッシュ)のテスト
#   波形の読み込みと震源の計算は差し替え、キャッシュを使うか・計算し直すかだけを確かめる
import json
from datetime import datetime, timedelta

import pytest

import pick_store
import quake_locate
from hypocenter import LocalProjection


def cand(start):
  return {"start": start, "end": start + timedelta(seconds=10), "jma": None, "row": {}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
  calls = []
  def locate_candidate(st, c, stations, proj):
    calls.append(c["start"])
    return {"kind": "近い地震", "picks": {}, "s_picks": {}, "traces": "波形", "n": len(calls)}
  monkeypatch.setattr(quake_locate, "locate_candidate", locate_candidate)
  monkeypatch.setattr(quake_locate, "window_stream", lambda t0, t1, load_hour, cache: None)
  monkeypatch.setattr(pick_store, "PICKS_DIR", tmp_path / "picks")
  return calls, tmp_path / "locate.pkl"


def run(stations, cands, cache_path, force=False):
  proj = LocalProjection(list(stations.values()))
  return quake_locate.locate_all_cached(cands, None, stations, proj, force=force, cache_path=cache_path)


def test_second_time_uses_cache(stations, fake):
  calls, path = fake
  cands = [cand(datetime(2026, 6, 4, 12, 0, 20)), cand(datetime(2026, 6, 4, 12, 10, 0))]
  items, computed = run(stations, cands, path)
  assert computed == 2 and len(calls) == 2
  assert all("traces" not in r for _c, r in items)   # 波形は保存しない
  items, computed = run(stations, cands, path)
  assert computed == 0 and len(calls) == 2
  assert [r["n"] for _c, r in items] == [1, 2]


def test_force_recomputes_all(stations, fake):
  calls, path = fake
  cands = [cand(datetime(2026, 6, 4, 12, 0, 20))]
  run(stations, cands, path)
  _items, computed = run(stations, cands, path, force=True)
  assert computed == 1 and len(calls) == 2


def test_saved_picks_change_recomputes_that_candidate(stations, fake):
  calls, path = fake
  a, b = cand(datetime(2026, 6, 4, 12, 0, 20)), cand(datetime(2026, 6, 4, 12, 10, 0))
  run(stations, [a, b], path)
  # a の範囲に到着時刻を保存する(震源推定の画面で手直しして保存したのと同じ)
  pick_store.PICKS_DIR.mkdir()
  t = a["start"] + timedelta(seconds=1)
  (pick_store.PICKS_DIR / f"{t:%Y%m%d_%H%M%S}.json").write_text(
    json.dumps({"stations": {"Bamba": {"p": t.isoformat()}}}), encoding="utf-8")
  assert pick_store.find_record(*quake_locate.candidate_window(a))[0] is not None
  _items, computed = run(stations, [a, b], path)
  assert computed == 1 and calls[-1] == a["start"]
