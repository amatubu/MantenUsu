# 1日の記録の画面の版(shake_day_web.py)のテスト。波形のデータは作り物を使う
from datetime import datetime
import json

import numpy as np

import shake_day as sd
import shake_day_web as sw
import station_names
from quake_wave_view import decode_samples

DAY = datetime(2026, 7, 8)


def test_hour_data_round_trip():
  rng = np.random.default_rng(2)
  x = rng.normal(size=3600 * sd.RATE) * 100.0
  x[1000:1500] = np.nan
  d = sw.hour_data(x, 80.0)
  assert d["q"] == 80.0 / sw.FINE and d["g"] == [[1000, 1500]]
  v = decode_samples(d["d"]) * d["q"]
  ok = np.isfinite(x)
  assert np.abs(v[ok] - x[ok]).max() <= d["q"] / 2 + 1e-9                     # 細かさ(ふだんの揺れの 1/4)の半分まで
  assert (v[~ok] == 0).all()


def test_write_day(tmp_path):
  from plot_common import setup_japanese_font
  setup_japanese_font()
  n = 86400 * sd.RATE
  rng = np.random.default_rng(3)
  wave = rng.normal(size=n).astype(np.float32) * 10
  wf = {"Hokudai": wave}
  events = [{"no": 1, "kind": "有珠山の地震", "start": 19 * 3600 + 100, "end": 19 * 3600 + 120, "text": "x", "vis": "", "note": ""}]
  page = sw.write_day(tmp_path, DAY, wf, wf, ["Hokudai"], [19], events)
  label = station_names.full("Hokudai")                                          # ファイル名は発表用の名前
  assert (tmp_path / "img" / f"{label}_19.png").read_bytes()[:4] == b"\x89PNG"
  data = json.loads((tmp_path / "data" / f"{label}_19.json").read_text())
  assert len(decode_samples(data["d"])) == 3600 * sd.RATE
  text = page.read_text(encoding="utf-8")
  meta = json.loads(text.split('id="meta">')[1].split("</script>")[0])
  assert meta["stations"] == [{"id": "Hokudai", "label": label, "hours": [19]}]
  assert [f["key"] for f in meta["filters"]] == ["A", "B", "C", "L"] and all(f["high"] < sd.RATE / 2 for f in meta["filters"])
  assert meta["events"][0]["color"] == sd.KIND_COLORS["有珠山の地震"]
  assert "__" not in text.replace("__proto__", "")                              # 置き換え忘れが無い


def test_image_geometry():
  # 画面の JS は、図の中の波形の枠(AX_BOX)と縦の範囲(HOUR_YLIM)からタップした時刻を出す。段 r の中心が y = r にあること
  import matplotlib
  matplotlib.use("Agg")
  import matplotlib.pyplot as plt
  fig = plt.figure(figsize=sw.FIG_SIZE)
  ax = fig.add_axes(sw.AX_BOX)
  sd.draw_hour(ax, np.zeros(3600 * sd.RATE), 0)
  y0, y1 = ax.get_ylim()
  assert (y0, y1) == sd.HOUR_YLIM and ax.get_xlim() == (0, 60)
  # JS と同じ式: 図の上からの割合 fy → 段
  top = 1 - (sw.AX_BOX[1] + sw.AX_BOX[3])
  for r in (0, 30, 59):
    fy = top + (r - y1) / (y0 - y1) * sw.AX_BOX[3]
    disp = ax.transData.transform((0, r))[1] / fig.bbox.height                     # 下からの割合
    assert abs((1 - disp) - fy) < 1e-6
  plt.close(fig)


def test_write_index(tmp_path):
  from plot_common import setup_japanese_font
  setup_japanese_font()
  wf = {"Hokudai": np.random.default_rng(4).normal(size=86400 * sd.RATE).astype(np.float32)}
  events = [{"no": 1, "kind": "発破", "start": 100, "end": 120, "text": "x", "vis": "", "note": ""}]
  sw.write_day(tmp_path / "20260708", DAY, wf, wf, ["Hokudai"], [0], events)
  text = sw.write_index(tmp_path).read_text(encoding="utf-8")
  assert 'href="20260708/"' in text and "発破 1" in text and "__ITEMS__" not in text
