# 手元の観測データを使うテスト(データ・外部ツールが無ければ飛ばす)
#   1. WIN → SAC の変換とチャンネルの読み替えの結果が変わらないか(SAC ファイルのハッシュ値で比べる)
#   2. 有珠山リストなどの地震の震源と判定が変わらないか(震源推定の画面で保存した到着時刻も使う)
#   基準は tests/baseline/*.json。観測データは再配布できないので、数値とハッシュ値だけを記録する
#   計算の方法を意図して変えたときは、python3 -m pytest -m realdata --update-baseline で作り直す
import hashlib
import json
import shutil
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import app_paths
import win_pipeline

pytestmark = pytest.mark.realdata

BASELINE_DIR = Path(__file__).resolve().parent / "baseline"
QUAKES_DIR = app_paths.BATCH_PLOTS_DIR / "quakes"
MIMATSU = "0970355225"   # チャンネルの読み替え(etc/0970355225_conv.tbl)が要る拠点


def has_tools():
  return all(win_pipeline.tool_path(app_paths.TOOLS_DIR, t).exists() for t in ("win2sac_32", "wchch_32"))


def zip_for(day):
  return app_paths.ZIPS_DIR / f"{day:%y%m%d}.zip"


def require(*paths):
  missing = [str(p) for p in paths if not Path(p).exists()]
  if missing:
    pytest.skip("手元のデータがありません: " + ", ".join(missing))
  if not has_tools():
    pytest.skip(f"外部ツール(win2sac_32・wchch_32)がありません: {app_paths.TOOLS_DIR}")


def sha256(path):
  return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# 基準と比べる(--update-baseline のときは、基準を今の結果で作り直す)
def check_baseline(name, actual, update, compare):
  path = BASELINE_DIR / f"{name}.json"
  if update or not path.exists():
    path.write_text(json.dumps(actual, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not update:
      pytest.skip(f"基準が無かったので作りました: {path.name}(もう一度実行すると比べます)")
    return
  compare(json.loads(path.read_text(encoding="utf-8")), actual)


# 1時間分の波形を ZIP から取り出して SAC に変換し、読み込む(アプリの load_hour_stream と同じ流れ)
def make_hour_loader(work_dir):
  from obspy import Stream, read

  def load_hour(hour):
    dest = work_dir / f"stack_{hour:%Y%m%d_%H}"
    if not win_pipeline.find_sacs(dest):
      dest.mkdir(parents=True, exist_ok=True)
      zips = win_pipeline.find_zips(app_paths.ZIPS_DIR, win_pipeline.target_dates(hour, hour + timedelta(hours=1)))
      if win_pipeline.extract_wins_from_zips(zips, hour, hour + timedelta(hours=1), dest) == 0:
        return None
      win_pipeline.convert_wins_to_sacs(dest, app_paths.TOOLS_DIR, app_paths.ETC_DIR)
      win_pipeline.remove_wins(dest)
    st = Stream()
    for p in win_pipeline.find_sacs(dest):
      st += read(str(p))
    return st
  return load_hour


# --- 1. 変換 ---

# 1分の WIN ファイルを変換した SAC ファイルのハッシュ値
#   2025/8/15 10:00 の Mimatsu はチャンネルの読み替えが起きる(2025/7/27〜9/5 のファイル)
CONVERT_CASES = [
  (datetime(2025, 8, 15), f"{MIMATSU}/250815/{MIMATSU}_2508151000.win"),
  (datetime(2026, 8, 23), f"{MIMATSU}/260823/{MIMATSU}_2608230200.win"),
  (datetime(2026, 8, 23), "1070432002/260823/1070432002_2608230200.win"),
]


def test_win_to_sac(tmp_path, update_baseline):
  require(*{zip_for(day) for day, _ in CONVERT_CASES})
  actual = {}
  for day, member in CONVERT_CASES:
    dest = tmp_path / Path(member).stem
    dest.mkdir()
    with zipfile.ZipFile(zip_for(day)) as zf:
      (dest / Path(member).name).write_bytes(zf.read(member))
    win_pipeline.convert_wins_to_sacs(dest, app_paths.TOOLS_DIR, app_paths.ETC_DIR)
    sacs = sorted(win_pipeline.find_sacs(dest))
    assert sacs, f"SAC ファイルができませんでした: {member}"
    actual[member] = {p.name: sha256(p) for p in sacs}

  def compare(expected, actual):
    assert actual == expected
  check_baseline("win_to_sac", actual, update_baseline, compare)


def test_channel_conversion_changes_mimatsu(tmp_path):
  # 読み替えが要る期間のファイルは、wchch_32 を通すと中身が変わる
  day, member = CONVERT_CASES[0]
  require(zip_for(day))
  src = tmp_path / "in.win"
  with zipfile.ZipFile(zip_for(day)) as zf:
    src.write_bytes(zf.read(member))
  out = win_pipeline.correct_channels(src, MIMATSU, app_paths.TOOLS_DIR, app_paths.ETC_DIR)
  assert out != src and out.read_bytes() != src.read_bytes()


# --- 2. 震源と判定 ---

# 調べる地震: 有珠山リストの24件と、全期間の候補のうち判定の決まり方を確かめるもの
#   2026/7/2 03:59・8/3 00:51 ... 自動の検出で到着時刻が5つ → 近い地震(到着時刻少)
#   2025/7/22 08:49           ... 到着時刻は5つだが、手で確かめて保存した → 近い地震
#   2026/8/23 02:02           ... 茨城県南部の遠い地震(走時表で 718 km。震源リストでは 730 km)
EXTRA = {"2026-07-02 03:59:40", "2026-08-03 00:51:50", "2026-08-23 02:02:10"}


def selected_candidates():
  import quake_locate
  usu_csv = QUAKES_DIR / "usu_quakes_24.csv"
  all_csv = QUAKES_DIR / "quake_candidates_20250708_20260826.csv"
  require(usu_csv, all_csv, app_paths.ZIPS_DIR)
  cands = quake_locate.read_candidates(usu_csv)
  starts = {c["start"] for c in cands}
  cands += [c for c in quake_locate.read_candidates(all_csv)
            if f"{c['start']:%Y-%m-%d %H:%M:%S}" in EXTRA and c["start"] not in starts]
  return sorted(cands, key=lambda c: c["start"])


def summary(r):
  loc, far = r["loc"], r["far"]
  out = {"kind": r["kind"], "n_p": r["n_p"], "n_s": r["n_s"], "manual": bool(r.get("manual"))}
  if r["lat"] is not None:
    out.update(lat=round(r["lat"], 4), lon=round(r["lon"], 4))
  if loc is not None:
    out.update(depth=round(loc["depth"], 2), rms=round(loc["rms"], 3))
  if far is not None:
    out.update(far_distance=round(far["distance"], 1), far_sp=round(far["sp"], 2))
  return out


@pytest.fixture(scope="module")
def located(tmp_path_factory):
  import quake_locate
  from hypocenter import LocalProjection, load_stations
  cands = selected_candidates()
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  work = tmp_path_factory.mktemp("hours")
  items = quake_locate.locate_all(cands, make_hour_loader(work), stations, proj)
  shutil.rmtree(work, ignore_errors=True)
  return {f"{c['start']:%Y-%m-%d %H:%M:%S}": summary(r) for c, r in items}


def test_locations_match_baseline(located, update_baseline):
  def compare(expected, actual):
    assert sorted(actual) == sorted(expected), "計算できた地震が基準と違います"
    for start, exp in expected.items():
      act = actual[start]
      assert act["kind"] == exp["kind"], start
      assert (act["n_p"], act["n_s"], act["manual"]) == (exp["n_p"], exp["n_s"], exp["manual"]), start
      for key, tol in (("lat", 0.0005), ("lon", 0.0005), ("depth", 0.05), ("rms", 0.002), ("far_distance", 1.0),
                       ("far_sp", 0.02)):
        assert (key in act) == (key in exp), (start, key)
        if key in exp:
          assert act[key] == pytest.approx(exp[key], abs=tol), (start, key)
  check_baseline("locations", located, update_baseline, compare)


def test_judgement_rules(located):
  # 基準に関係なく、判定の決まり方として守ること
  usu = [s for s in located if s not in EXTRA]
  assert len(usu) == 24
  assert all(located[s]["kind"] in ("近い地震", "近い地震(残差大)") for s in usu)
  assert located["2025-07-22 08:49:30"]["kind"] == "近い地震"   # 5つだが手で確かめたもの
  assert located["2026-07-02 03:59:40"]["kind"] == "近い地震(到着時刻少)"
  assert located["2026-08-03 00:51:50"]["kind"] == "近い地震(到着時刻少)"
  far = located["2026-08-23 02:02:10"]
  assert far["kind"] == "遠い地震"
  assert far["far_distance"] == pytest.approx(730, abs=25)
