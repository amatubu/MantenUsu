# plot_common.py
import matplotlib.pyplot as plt

# 波形の色分け(U:青 / N:オレンジ / E:緑)
COMPONENT_COLORS = {"U": "#1f77b4", "N": "#ff7f0e", "E": "#2ca02c"}

# 速度の単位変換係数(nm/s -> cm/s)。SACの波形データは nm/s で入っている
NM_TO_CM = 1e-7

def get_component_color(channel, default="gray"):
  """チャンネル名の末尾1文字(U/N/E)から色を返す。該当なしは default"""
  return COMPONENT_COLORS.get(channel, default)

def plot_components(ax, stream_obj):
  """Stream内の各成分を、成分ごとの色で ax に描画する"""
  for tr in stream_obj:
    ch = tr.stats.channel
    ax.plot(tr.times(), tr.data, label=ch, alpha=0.8,
            linewidth=0.5, color=get_component_color(ch))

def setup_japanese_font():
  """日本語表示用のフォント設定(左から優先的に探す)"""
  plt.rcParams["font.family"] = "sans-serif"
  plt.rcParams["font.sans-serif"] = [
    "Hiragino Sans",  # Mac
    "Meiryo",         # Windows
    "Yu Gothic",      # Windows
    "MS Gothic",      # Windows
    "sans-serif",     # 最後の砦
  ]
  plt.rcParams["axes.unicode_minus"] = False
