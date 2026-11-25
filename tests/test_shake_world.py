# 世界の地震が届くページ(shake_world.py)のテスト。波形のデータは作り物を使う(通り道は iasp91 で計算する)
from datetime import datetime, timedelta
import base64
import json
import re

import numpy as np
import obspy
import pytest

import shake_world as sw

T0 = datetime(2026, 7, 11, 19, 26, 12)


def fake_loader(rate=250.0):
  # 呼ばれた範囲の Nizaemn の上下動(雑音 + T0 から 100 秒後に始まる 0.3 Hz の揺れ)
  def load(t0, t1):
    n = int((t1 - t0).total_seconds() * rate)
    sec = np.arange(n) / rate + (t0 - T0).total_seconds()
    x = np.random.default_rng(0).normal(size=n) + np.where(sec >= 100, np.sin(2 * np.pi * 0.3 * sec) * 100, 0)
    return obspy.Stream([obspy.Trace(data=x, header={"station": "Nizaemn", "channel": "U", "sampling_rate": rate,
                                                      "starttime": obspy.UTCDateTime(t0)})])
  return load


def test_waveforms_bands_and_rates(monkeypatch):
  monkeypatch.setattr("quake_wave_view.count_steps", lambda etc: {("Nizaemn", "U"): 2.0})
  wf = sw.waveforms(T0, 200, load=fake_loader())
  x = wf["L"]["Nizaemn"]
  assert x.shape == (200 * sw.RATE,)
  assert np.isnan(wf["L"]["Bamba"]).all()                                     # 記録の無い拠点は NaN
  assert np.abs(x[1200:1800]).max() > 20 * np.abs(x[300:800]).max()          # 0.3 Hz の揺れは L(0.1〜0.5 Hz)に入る
  snd = sw.waveforms(T0, 200, {"S": sw.SOUND_BAND}, sw.SOUND_RATE, load=fake_loader())["S"]["Nizaemn"]
  assert snd.shape == (200 * sw.SOUND_RATE,) and np.isfinite(snd).any()      # 音は 50 Hz(250 Hz の記録を割り切れる)


def test_usu_rays_far_and_near():
  far = sw.usu_rays(26.0, 165.3)
  assert [r["name"] for r in far] == ["PKIKP", "PKP", "PP", "SKIKS"]          # 地球の反対側: 核を通る波と、地表ではね返った P波
  assert far[0]["time"] == pytest.approx(1201, abs=2)
  near = sw.usu_rays(35.0, 16.4)
  assert [r["name"] for r in near] == ["P", "S"]                              # 近いと PP・SS は P・S に近すぎるので描かない
  pts = np.frombuffer(base64.b64decode(near[0]["xy"]), dtype="<i2").reshape(-1, 2)
  assert np.hypot(*pts[0]) == pytest.approx(sw.R_EARTH - 35, abs=1)          # 震源から出て
  assert np.degrees(np.arctan2(pts[-1][0], pts[-1][1])) == pytest.approx(16.4, abs=0.1)   # 16.4° 先の地表に着く


def test_usu_rays_shadow():
  # P波の影: まっすぐの P波は無く、核のふちを回り込む P波(弱い)と、核を通る P波・地表ではね返った P波
  col = sw.usu_rays(116.0, 122.0)
  assert [(r["name"], r["weak"]) for r in col][:3] == [("Pdiff", True), ("PKiKP", False), ("PP", False)]   # 内核をかすめるだけなので、はね返る波にする
  mex = sw.usu_rays(22.0, 104.7)
  assert [r["name"] for r in mex] == ["Pdiff", "PP", "SKS"]                  # 影の入り口: 最初に揺れるのは PP
  assert not mex[1].get("refl")                                               # 最初の P波の仲間として太く描く
  far = sw.usu_rays(26.0, 165.3)
  assert all(r["name"] != "Pdiff" for r in far)                              # 影より遠ければ描かない
  assert "PKIKP" in [r["name"] for r in far] and "PKiKP" not in [r["name"] for r in far]   # 内核の深くを通るので、内核を通る P波のまま


def test_encode_wave_and_sound():
  y = np.concatenate([np.zeros(10), np.sin(np.linspace(0, 20, 200)), [np.nan]])
  w = sw.encode_wave(y, 10)
  v = np.frombuffer(base64.b64decode(w["d"]), dtype=np.int8)
  assert v[-1] == -128 and np.abs(v[:-1]).max() <= 127                      # 欠測は -128
  q = np.frombuffer(base64.b64decode(sw.encode_sound(y)), dtype=np.int8).astype(float) / 127
  back = np.sign(q) * ((1 + sw.MU) ** np.abs(q) - 1) / sw.MU                 # ページと同じ μ-law の戻し方
  assert back[10:210] == pytest.approx(np.clip(y[10:210] / np.percentile(np.abs(y[np.isfinite(y)]), 99.9), -1, 1), abs=0.03)
  assert sw.encode_sound(np.zeros(5)) is None


def test_write_page(tmp_path, monkeypatch):
  import station_names
  monkeypatch.setattr(station_names, "_enabled", True)
  monkeypatch.setattr(station_names, "_table", {"Nizaemn": ("Nizaemon", "NZMN")})
  monkeypatch.setattr(sw, "FRONT_SETS", [(np.arange(0.5, 180.0, 30.0), ["P", "S"])])   # 波面は粗くして速くする
  monkeypatch.setattr(sw, "cached_waveforms", lambda no, t0, seconds, bands=None, rate=sw.RATE: {
    b: {nm: np.sin(np.arange(int(seconds * rate)) / 3.0) for nm in sw.sq.STATIONS} for b in (bands or sw.BANDS)})
  sw._fronts.clear()
  a = {"日時": T0, "深さ[km]": 26.0, "deg": 165.3, "dist_km": 18384, "M": 6.4, "震央地名": "South Sandwich Islands region"}
  ev = sw.event_data(40, a, "南サンドウィッチ諸島付近")
  assert ev["front"] == "50" and ev["band"] == "L" and ev["end"] >= ev["first"] + sw.AFTER
  size = sw.write_page(tmp_path / "world.html", [ev])
  text = (tmp_path / "world.html").read_text(encoding="utf-8")
  meta = json.loads(re.search(r"const META = (\{.*?\});\n", text, re.S).group(1))
  assert size > 0 and meta["events"][0]["place"] == "南サンドウィッチ諸島付近"
  assert set(meta["fronts"]) == {"50"} and meta["events"][0]["sound"]["d"]["Nizaemn"]
  assert [s["label"] for s in meta["stations"]][-1] == "Nizaemon"           # 表示は発表用の名前


def test_page_has_haptics(tmp_path):
  # 振動(TODO 58)の部品と、「振動」の切り替えが入る
  import haptic_page
  import shake_world as sw
  path = tmp_path / "world.html"
  sw.write_page(path, [])
  text = path.read_text(encoding="utf-8")
  assert "__HAPTIC_JS__" not in text and "function hapticPlay(" in text and 'id="feel"' in text
  assert '"haptic":' in text and str(haptic_page.HAPTIC["gamma"]) in text
