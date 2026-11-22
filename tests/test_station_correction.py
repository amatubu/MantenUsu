# station_correction.py(拠点ごとの補正)のテスト
import numpy as np
import pytest

import station_correction
from hypocenter import LocalProjection, locate_event


def write(tmp_path, text):
  p = tmp_path / "station_corrections.txt"
  p.write_text(text, encoding="utf-8")
  return p


def test_load_reads_values_and_comments(tmp_path):
  p = write(tmp_path, "# 説明\nUSSW  P  -0.146  # 26件\n\nKBBY S +0.166\n")
  assert station_correction.load(p) == {("USSW", "P"): -0.146, ("KBBY", "S"): 0.166}


def test_load_missing_file_is_empty(tmp_path):
  assert station_correction.load(tmp_path / "無い.txt") == {}


def test_load_rejects_unknown_phase(tmp_path):
  with pytest.raises(ValueError):
    station_correction.load(write(tmp_path, "USSW X 0.1\n"))


def test_read_enabled():
  assert station_correction.read_enabled({}) is station_correction.DEFAULT_ON
  assert station_correction.read_enabled({"station_correction": 0.0}) is False
  assert station_correction.read_enabled({"station_correction": 1.0}, default=False) is True


def test_apply_subtracts_only_known_stations():
  p, s = station_correction.apply({"A": 1.0, "B": 2.0}, {"A": 1.5}, {("A", "P"): 0.1, ("A", "S"): -0.2})
  assert p == pytest.approx({"A": 0.9, "B": 2.0}) and s == pytest.approx({"A": 1.7})


# 補正を渡したときは、到着時刻をずらして求めたときと同じ震源になる(保存した到着時刻は変えずに計算だけ変える)
def test_locate_event_with_corrections_equals_shifted_picks(stations):
  proj = LocalProjection(list(stations.values()))
  xyz = {nm: np.array([*proj.to_xy(*c[:2]), c[2] / 1000]) for nm, c in stations.items()}
  picks = {"Bamba": 2.05, "Hokudai": 1.62, "Mimatsu": 1.70, "Nizaemn": 1.95}
  s_picks = {"Hokudai": 2.30, "Mimatsu": 2.45, "Nizaemn": 2.90}
  corr = {("Hokudai", "P"): 0.05, ("Nizaemn", "P"): -0.08, ("Hokudai", "S"): 0.09}
  a = locate_event(xyz, picks, s_picks, proj, corrections=corr)
  shifted_p, shifted_s = station_correction.apply(picks, s_picks, corr)
  b = locate_event(xyz, shifted_p, shifted_s, proj)
  assert a["lat"] == pytest.approx(b["lat"]) and a["loc"]["depth"] == pytest.approx(b["loc"]["depth"])
  assert a["loc"]["rms"] == pytest.approx(b["loc"]["rms"])


# 合成した到着時刻に決まったずれを足しておくと、そのずれ(平均を 0 にしたもの)をほぼ求め直せる
#   (ずれの一部は震源の位置・発生時刻に吸収されるので、ぴったりは戻らない。向きと大きさが合い、残差が大きく下がること)
def test_estimate_recovers_synthetic_terms():
  rng = np.random.default_rng(0)
  names = [f"S{i}" for i in range(7)]
  pos = {nm: np.array([np.cos(a) * 3, np.sin(a) * 3, 0.1]) for nm, a in zip(names, np.linspace(0, 2 * np.pi, 7, endpoint=False))}
  true = {(nm, ph): v for nm, v in zip(names, rng.normal(0, 0.08, 7)) for ph in ("P",)}
  true.update({(nm, "S"): v for nm, v in zip(names, rng.normal(0, 0.1, 7))})
  for ph in ("P", "S"):
    m = np.mean([v for (nm, p), v in true.items() if p == ph])
    for nm in names:
      true[(nm, ph)] -= m
  vp, vs = 3.0, 3.0 / 1.73

  def travel(src, nm, v):
    return float(np.linalg.norm(src - pos[nm] * [1, 1, -1])) / v

  events = []
  for _ in range(12):
    src = np.array([*rng.uniform(-1.5, 1.5, 2), rng.uniform(0.3, 2.0)])
    events.append(({nm: travel(src, nm, vp) + true[(nm, "P")] for nm in names},
                   {nm: travel(src, nm, vs) + true[(nm, "S")] for nm in names}))

  # 均質な構造での簡単な震源の求め方(格子で探す)と残差
  grid = np.stack(np.meshgrid(np.arange(-2, 2.01, 0.2), np.arange(-2, 2.01, 0.2), np.arange(0.0, 2.51, 0.2),
                              indexing="ij"), -1).reshape(-1, 3)
  def locate(p, s):
    obs = [(nm, "P", t, vp) for nm, t in p.items()] + [(nm, "S", t, vs) for nm, t in s.items()]
    tt = np.array([[np.linalg.norm(g - pos[nm] * [1, 1, -1]) / v for nm, _ph, _t, v in obs] for g in grid])
    t = np.array([o[2] for o in obs])
    origin = (t - tt).mean(axis=1)
    rms = np.sqrt(((t - tt - origin[:, None]) ** 2).mean(axis=1))
    i = int(np.argmin(rms))
    return None, {(nm, ph): t[j] - tt[i, j] - origin[i] for j, (nm, ph, _t, _v) in enumerate(obs)}

  values, counts = station_correction.estimate(events, locate, iterations=3)
  assert set(counts.values()) == {12}
  keys = sorted(true)
  est, tru = np.array([values[k] for k in keys]), np.array([true[k] for k in keys])
  assert np.corrcoef(est, tru)[0, 1] > 0.9
  assert np.abs(est - tru).max() < 0.08
  def rms(vals):
    res = [locate(*station_correction.apply(p, s, vals))[1] for p, s in events]
    return np.sqrt(np.mean([r ** 2 for d in res for r in d.values()]))
  assert rms(values) < 0.5 * rms({})


def test_format_file_round_trip(tmp_path):
  text = station_correction.format_file({("USSW", "P"): -0.146, ("USSW", "S"): -0.151}, {("USSW", "P"): 26}, "メモ")
  assert station_correction.load(write(tmp_path, text)) == {("USSW", "P"): -0.146, ("USSW", "S"): -0.151}
