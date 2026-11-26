# 地下構造の想定と震源の位置のページ(quake_model_sim.py)のテスト
#   震源は作り物の観測点と到着時刻で求め直す(本物の地震の一覧・波形は使わない)
import json
import re

import numpy as np
import pytest

import quake_model_sim as qm
import velocity_model
from hypocenter import LocalProjection

ZS = np.linspace(-0.8, 6, 50)


# 既定の想定は、アプリの震源推定の構造(velocity_model の勾配の構造)と同じ
def test_default_model_is_the_app_model():
  d = qm.DEFAULT
  m, app = qm.build_model(*qm.default_model()), velocity_model.MODELS[velocity_model.GRADIENT]
  assert np.allclose(m.vp(ZS), app.vp(ZS)) and np.allclose(m.vs(ZS), app.vs(ZS))


# 勾配の構造は海面下 1 km と地表付近の速さだけを変える(組み合わせ)。一様の構造はどの深さも同じ。vp/vs は一定の値も選べる
def test_model_variants():
  fast, slow = qm.build_model("gradient", 4, 2, "mid"), qm.build_model("gradient", 0, 2, "mid")
  assert fast.vp(np.array([1.0]))[0] == qm.SPEEDS["gradient"][4] and slow.vp(np.array([1.0]))[0] == qm.SPEEDS["gradient"][0]
  assert np.allclose(fast.vp(np.array([-0.5, 2.0, 5.0])), slow.vp(np.array([-0.5, 2.0, 5.0])))   # 1 km の点の外は同じ
  both = qm.build_model("gradient", 4, 0, "mid")
  assert np.allclose(both.vp(np.array([-1.0, -0.5, 1.0, 2.0])), [qm.TOP_SPEEDS[0], qm.TOP_SPEEDS[0], qm.SPEEDS["gradient"][4], 4.7])
  uni = qm.build_model("uniform", 1, 0, "1.80")
  assert np.allclose(uni.vp(ZS), qm.SPEEDS["uniform"][1]) and np.allclose(uni.vp(ZS) / uni.vs(ZS), 1.80)
  # vp/vs: 先行研究の式は Vp = 1.45 Vs + 0.9、1:1 は式と 1.74 の S波の遅さの平均
  vp = qm.build_model("gradient", 2, 2, "formula").vp(ZS)
  vs_f, vs_h = qm.build_model("gradient", 2, 2, "formula").vs(ZS), qm.build_model("gradient", 2, 2, "half").vs(ZS)
  assert np.allclose(vp, 1.45 * vs_f + 0.9) and np.allclose(1 / vs_h, (1 / vs_f + 1.74 / vp) / 2)
  n = (5 * 5 + 5) * 7
  assert len(qm.all_models()) == n and len({qm.model_key(*m) for m in qm.all_models()}) == n


# 作り物の地震: 正しい構造で求め直すと震源の位置に戻り、観測ごとのずれはほぼ 0
@pytest.fixture(scope="module")
def synthetic():
  stations = {f"S{k}": (np.cos(a) * 4.0, np.sin(a) * 4.0, 0.1) for k, a in enumerate(np.linspace(0, 2 * np.pi, 6, endpoint=False))}
  stations["C"] = (0.5, -0.3, 0.1)
  model = qm.build_model(*qm.default_model())
  pos = np.array(list(stations.values()) * 2)
  phases = ["P"] * len(stations) + ["S"] * len(stations)
  src = (0.6, 0.4, 0.8)
  tt = velocity_model.travel_function(model, pos, phases)(np.array([src[0]]), np.array([src[1]]), np.array([src[2]]))[0]
  n = len(stations)
  names = list(stations)
  ev = {"no": 1, "key": "x", "picks": {nm: round(1.0 + tt[k], 4) for k, nm in enumerate(names)},
        "s_picks": {nm: round(1.0 + tt[n + k], 4) for k, nm in enumerate(names)},
        "xyz": {nm: list(v) for nm, v in stations.items()}, "confirmed": True}
  return ev, model, src

def test_relocate_recovers_the_source(synthetic):
  ev, model, src = synthetic
  proj = LocalProjection([(42.55, 140.85, 0)])
  r = qm.relocate(ev, model, proj, None)
  assert abs(r[0] - src[0]) < 0.06 and abs(r[1] - src[1]) < 0.06 and abs(r[2] - src[2]) < 0.06
  assert r[3] < 0.01 and len(r[6]) == len(qm.observations(ev)) == 14 and max(abs(v) for v in r[6]) < 0.02
  assert r[4] <= r[2] <= r[5]
  # 拠点ごとの補正は、到着時刻から引いてから求める(すべて 0.05 秒遅らせると、発生時刻だけがずれて位置は同じ)
  corr = {(nm, ph): 0.05 for nm in ev["picks"] for ph in ("P", "S")}
  r2 = qm.relocate(ev, model, proj, corr)
  assert r2[:3] == r[:3]

# 浅い所を速く想定すると(この並びの観測点では)震源は浅くなる
def test_faster_shallow_speed_moves_the_source_up(synthetic):
  ev, _model, _src = synthetic
  proj = LocalProjection([(42.55, 140.85, 0)])
  fast = qm.relocate(ev, qm.build_model("gradient", 4, 2, "mid"), proj, None)
  slow = qm.relocate(ev, qm.build_model("gradient", 0, 2, "mid"), proj, None)
  assert fast[2] < slow[2] and fast[3] > 0.005 and slow[3] > 0.005


# 求め直した結果は保存し、入力が同じなら計算しない
def test_relocate_all_uses_the_cache(synthetic, tmp_path, monkeypatch):
  ev, _model, _src = synthetic
  proj = LocalProjection([(42.55, 140.85, 0)])
  monkeypatch.setattr(qm, "all_models", lambda: [("gradient", 2, 2, "mid")])
  monkeypatch.setattr(qm.station_correction, "load", lambda: {})
  monkeypatch.setattr(qm.station_correction, "signature", lambda: "none")
  cache = tmp_path / "results.json"
  res = qm.relocate_all([ev], proj, workers=1, cache=cache, log=lambda m: None)
  assert set(res) == {"gradient-2-2-mid"} and res["gradient-2-2-mid"]["c"][0] == res["gradient-2-2-mid"]["n"][0]
  monkeypatch.setattr(qm, "ProcessPoolExecutor", None)   # 2回めは計算しない(呼ぶと失敗する)
  assert qm.relocate_all([ev], proj, workers=1, cache=cache, log=lambda m: None) == res
  # 速さの並びが変わっても、同じ中身の想定は使い回す(見分けは今の並びにつけ直す)
  monkeypatch.setattr(qm, "SPEEDS", {**qm.SPEEDS, "gradient": (3.25, 3.5)})
  monkeypatch.setattr(qm, "all_models", lambda: [("gradient", 1, 2, "mid")])
  assert qm.relocate_all([ev], proj, workers=1, cache=cache, log=lambda m: None) == {"gradient-1-2-mid": res["gradient-2-2-mid"]}
  # 到着時刻が変わると求め直す
  ev2 = dict(ev, picks={**ev["picks"], "C": ev["picks"]["C"] + 0.1})
  with pytest.raises(TypeError):
    qm.relocate_all([ev2], proj, workers=1, cache=cache, log=lambda m: None)


# ページは外部のファイルを読まない HTML 1つで、想定ごとの結果と地図・断面の情報が入る
def test_write_page(synthetic, tmp_path):
  ev, model, _src = synthetic
  proj = LocalProjection([(42.55, 140.85, 0)])
  r = qm.relocate(ev, model, proj, None)
  results = {qm.model_key(*m): {"c": [r], "n": [None]} for m in qm.all_models()}
  ev = dict(ev, time="2026-05-20 23:51", type="地震", app={"layered": True, "corrected": True})
  map_info = {"src": "", "x0": -6.0, "x1": 6.0, "y0": -5.0, "y1": 5.0, "credit": "出典"}
  profile = qm.profiles(map_info, proj, (42.55, 140.85), elevations=lambda la, lo: np.full(np.shape(la), 300.0))
  assert profile["ew"]["h"][0] == 0.3 and len(profile["ns"]["pos"]) == len(profile["ns"]["h"])
  path = tmp_path / "sim.html"
  size = qm.write_page(path, [ev], results, {"S0": (42.55, 140.9, 100.0)}, proj, map_info, profile, (42.55, 140.85))
  text = path.read_text(encoding="utf-8")
  assert size == path.stat().st_size and "__META__" not in text and "__TITLE__" not in text
  assert not re.search(r"""(?:src|href)=["']?https?:""", text)
  meta = json.loads(re.search(r'id="meta">(.*?)</script>', text, re.S).group(1))
  assert len(meta["results"]) == 210 and meta["events"][0]["obs"][0][1] == "P" and len(meta["events"][0]["obs"]) == 14
  assert meta["default"] == qm.DEFAULT and len(meta["curves"]["vs"]) == 210 and len(meta["curves"]["vp"]) == 30
  assert meta["stations"][0]["manten"] and any(not s["manten"] for s in meta["stations"])
  # 通る跡で vp/vs を変える並びは、比の小さい順(先行研究の式は浅い所で比が大きく、いちばん後ろ)
  assert sorted(meta["ratio_order"]) == sorted(qm.RATIOS) and meta["ratio_order"][0] == "1.70" and meta["ratio_order"][-1] == "formula"
