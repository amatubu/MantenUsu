# magnitude.py
# 震源が決まった地震のマグニチュード(M)を推定する(TODO 26)
#   渡辺の式(0.85 M - 2.50 = log A + 1.73 log r。A: 上下動の速度の最大振幅 [cm/s]、r: 震源距離 [km])で
#   拠点ごとに M を求め、拠点ごとの補正を引いた値の中央値とする
#   満点地震計の感度・ゲインは推定した値(etc/manten_ch.tbl)なので、求めた M も推計である
import numpy as np

from plot_common import NM_TO_CM

# 最大振幅を測る帯域 [Hz](地震の候補の検出と同じ)
BAND = (2.0, 20.0)
# 近い地震で最大振幅を測る範囲: 発生時刻のこの秒数前から、この秒数後まで
SIGNAL_BEFORE, SIGNAL_AFTER = 1.0, 25.0
# 震源距離がこれより遠い地震では求めない [km]
#   (渡辺の式は 200 km くらいまでの地震向け。補正もこの範囲の地震で決めた)
MAX_DISTANCE = 200.0
# 最大振幅が、測る範囲より前の雑音の最大のこの倍数に届かない拠点は使わない
MIN_SNR = 3.0
# 雑音を測る長さがこれより短いときは、雑音との比を確かめない [秒]
MIN_NOISE_SECONDS = 2.0
# 震源距離の下限 [km](震源のすぐ近くで M が大きくなりすぎないように)
MIN_DISTANCE = 0.5
# 拠点ごとの補正(渡辺の式の M − 気象庁の M の中央値)
#   (2025/7〜2026/8 の震源リストの地震のうち、震源距離 200 km までで揺れが見えた56件から求めた。
#    渡辺の式のままでは約1.1大きく出て、拠点ごとの差は地盤による揺れやすさの違いとみられる。
#    補正を引いた M と気象庁の M の差は、平均ほぼ0、ばらつき ±0.45 程度)
STATION_CORRECTION = {"Bamba": 0.93, "Hokudai": 1.11, "Mimatsu": 1.44, "Nizaemn": 0.93}
# 推定の確かさの目安(画面に表示する)
UNCERTAINTY = 0.5

# 上下動を最大振幅を測る帯域に通したもの(cm/s)。画面を開いたときに1回だけ作る
#   traces ... {拠点名: 上下動の Trace(nm/s)}
#   戻り値 ... {拠点名: Trace(cm/s)}(補正の無い拠点は除く)
def filtered_traces(traces):
  out = {}
  for nm, tr in traces.items():
    if nm not in STATION_CORRECTION:
      continue
    tr = tr.copy()
    tr.data = tr.data.astype(float)
    tr.detrend("demean")
    tr.taper(0.05)
    tr.filter("bandpass", freqmin=BAND[0], freqmax=BAND[1], corners=4, zerophase=True)
    tr.data = tr.data * NM_TO_CM
    out[nm] = tr
  return out

# 拠点ごとの M と、その中央値
#   filtered ... filtered_traces の戻り値
#   t0, t1   ... 最大振幅を測る範囲(UTCDateTime。t1 が None なら波形の終わりまで)。雑音は t0 より前で測る
#   distances ... {拠点名: 震源距離 [km]}
#   戻り値 ... (M の中央値 または None, {拠点名: 補正後の M})
def estimate(filtered, t0, t1, distances):
  values = {}
  for nm, tr in filtered.items():
    if nm not in distances:
      continue
    sig = tr.slice(t0, t1).data
    if len(sig) == 0:
      continue
    amp = float(np.max(np.abs(sig)))
    noise = tr.slice(tr.stats.starttime, t0)
    if noise.stats.endtime - noise.stats.starttime >= MIN_NOISE_SECONDS:
      if amp < MIN_SNR * float(np.max(np.abs(noise.data))):
        continue
    if amp <= 0:
      continue
    r = max(distances[nm], MIN_DISTANCE)
    values[nm] = (np.log10(amp) + 1.73 * np.log10(r) + 2.50) / 0.85 - STATION_CORRECTION[nm]
  return (float(np.median(list(values.values()))) if values else None), values
