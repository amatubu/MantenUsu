import matplotlib.pyplot as plt
from obspy import Stream, read

# 1. 読み込み
st = read("Mimatsu.*.output_sac")

# 2. U -> N -> E の順番に並び替える（カスタムソート）
desired_order = ["U", "N", "E"]
st = Stream(
    sorted(
        st,
        key=lambda tr: (
            desired_order.index(tr.stats.channel[-1])
            if tr.stats.channel[-1] in desired_order
            else 99
        ),
    )
)

# 3. 3段のグラフエリアを作成
fig, axes = plt.subplots(3, 1, sharex=True, figsize=(10, 6))

# 上から順に U, N, E に対応する色を設定
colors = ["#1f77b4", "#ff7f0e", "#2ca02c"]  # 青、オレンジ、緑

for tr, ax, col in zip(st, axes, colors):
  times = tr.times()
  data = tr.data

  # プロット
  ax.plot(times, data, color=col, linewidth=0.8)

  # ラベル設定
  channel_name = tr.stats.channel
  station_name = tr.stats.station
  ax.set_ylabel(f"{station_name}\n({channel_name})", fontsize=10)
  ax.grid(True, linestyle="--", alpha=0.5)

# 全体の装飾
axes[-1].set_xlabel("Time [s]", fontsize=12)
plt.suptitle("Mimatsu Seismic Waveforms (U-N-E)", fontsize=14)
plt.tight_layout()
plt.show()