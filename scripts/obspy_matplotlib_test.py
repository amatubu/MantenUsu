from obspy import Stream, read
from pathlib import Path
import matplotlib.pyplot as plt
import sys

# 引数でフォルダを指定できるようにする（指定がない場合はデフォルトのパス）
target_dir = sys.argv[1] if len(sys.argv) > 1 else "../output/202608232245"
target_path = Path(target_dir)

if not target_path.exists():
  print(f"エラー: フォルダが見つかりません: {target_path}")
  sys.exit(1)

# 1. フォルダ内のすべてのSACファイルを読み込む
# （win2sac_32が出力したファイル群をワイルドカードで一括取得）
st = read(str(target_path / "*_sac*"))

if len(st) == 0:
  print("警告: SACファイルが見つかりませんでした。")
  sys.exit(1)

# 2. 拠点名（station）でグループ化しつつ、U -> N -> E の順にソートする
desired_order = ["U", "N", "E"]
st = Stream(
    sorted(
        st,
        key=lambda tr: (
            tr.stats.station,
            desired_order.index(tr.stats.channel[-1])
            if tr.stats.channel[-1] in desired_order
            else 99,
        ),
    )
)

num_traces = len(st)
print(f"読み込んだトレース数: {num_traces} (拠点数換算: {num_traces // 3} 拠点)")

# 3. トレース数に応じて動的にサブプロットの段数を決定
# 1トレースあたりの高さを 1.25 インチ程度に設定して、見やすさを維持する
fig_height = max(6, num_traces * 1.25)
fig, axes = plt.subplots(
  num_traces, 1, sharex=True, figsize=(10, fig_height), gridspec_kw={"hspace": 0}
)

# トレースが1つだけの場合（念のためリスト化）
if num_traces == 1:
  axes = [axes]

# 成分ごとのカラー定義（U, N, E で色を統一すると見やすいです）
component_colors = {"U": "#1f77b4", "N": "#ff7f0e", "E": "#2ca02c"}

for tr, ax in zip(st, axes):
  times = tr.times()
  data = tr.data

  # 成分の末尾文字（U, N, E など）から色を決定
  ch = tr.stats.channel
  comp = ch[-1] if ch else ""
  col = component_colors.get(comp, "#333333")

  # プロット
  ax.plot(times, data, color=col, linewidth=0.7)

  # ラベル設定
  station_name = tr.stats.station
  ax.set_ylabel(f"{station_name}\n({ch})", fontsize=9)
  ax.grid(True, linestyle="--", alpha=0.5)
  # Y軸のラベル文字が重ならないように調整
  ax.tick_params(axis="y", labelsize=8)

# 全体の装飾
axes[-1].set_xlabel("Time [s]", fontsize=11)
plt.suptitle(
    f"Seismic Waveforms ({target_path.name}) - Total Traces: {num_traces}",
    fontsize=13,
)
plt.tight_layout()
plt.show()