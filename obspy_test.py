from obspy import read
import matplotlib.pyplot as plt

# 1. 3成分のSACファイルをまとめて読み込む（ワイルドカードが使えます）
# ※ファイル名が異なる場合は適宜書き換えてください
sac_files = "Mimatsu.*.output_sac"
st = read(sac_files)

# 読み込んだストリームの情報を確認
print(st)

# 2. ObsPyの標準機能で3成分をきれいに並べてプロット
# equal_scale=False にすると、各成分の振幅の大きさに応じてスケールを個別に最適化します
st.plot(equal_scale=False)

# （参考）もし個別にグラフをカスタマイズしたい場合は、Matplotlibと組み合わせて描画することも可能です
# fig, axes = plt.subplots(3, 1, sharex=True, figsize=(10, 8))
# for tr, ax in zip(st, axes):
#     ax.plot(tr.times(), tr.data, color='k', linewidth=0.8)
#     ax.set_ylabel(f"{tr.stats.channel}\n({tr.stats.station})")
# plt.xlabel("Time [s]")
# plt.show()