from numpy.fft import fft, ifft, fftfreq
import numpy as np
import matplotlib.pyplot as plt
#from plot_common import plot_components

# 震度推定画面を表示
#    stream ... 波形データ(3要素)
# 参考
#   https://www.jma.go.jp/jma/kishou/know/jishin/kyoshin/kaisetsu/calc_sindo.html
def analyze_jma_scale(stream):
  """すべての解析ステップを1つのグリッド画面にまとめてプロット・計算する関数"""
  # 波形データを複写する
  st = stream.copy()

  # --- 単位の変換 (nm/s -> m/s) ---
  for tr in st:
    tr.data = tr.data * 1e-9

  # 波形の色分けの定義
  component_colors = {"U": "#1f77b4", "N": "#ff7f0e", "E": "#2ca02c"}

  # 3行2列のサブプロット（グリッド）を作成
  fig, axes = plt.subplots(3, 2, figsize=(15, 9))
  axes = axes.flatten()

  # 共通のプロット処理を行う内部関数（色分け適用）
  #   ax         ... サブプロット(グリッド)
  #   stream_obj ... 波形データ
  #   titie      ... グラフに表示させるタイトル
  #   ylabel     ... 縦軸のラベル
  def plot_three_components( ax, stream_obj, title, ylabel ):
    # 波形データに含まれる成分をひとつひとつ抽出
    for tr in stream_obj:
      # チャンネル名
      ch_name = tr.stats.channel
      # チャンネル名に含まれる文字（E, N, U）から色を決定
      color = 'gray' # デフォルト
      for key, col in component_colors.items():
        if key in ch_name:
          color = col
          break
      # 指定された色で波形を描画
      ax.plot(
        tr.times(),
        tr.data,
        label=ch_name,
        alpha=0.8,
        linewidth=0.5,
        color=color
      )

    # タイトル、X軸、Y軸のラベルを設定
    ax.set_title( title )
    ax.set_xlabel( "時刻 (s)" )
    ax.set_ylabel( ylabel )
    ax.legend( loc="upper right", fontsize="small" )
    ax.grid(True)

  # 計測震度から気象庁震度階級へ変換
  #   I ... 計測震度
  def convert_to_jma_scale( I ):
    """計測震度を気象庁震度階級に変換する関数"""
    if I < 0.5: return "0"
    elif I < 1.5: return "1"
    elif I < 2.5: return "2"
    elif I < 3.5: return "3"
    elif I < 4.5: return "4"
    elif I < 5.0: return "5弱"
    elif I < 5.5: return "5強"
    elif I < 6.0: return "6弱"
    elif I < 6.5: return "6強"
    else: return "7"

  # 気象庁震度計測のためのフィルター
  #   data          ... 波形データ
  #   sampling_rate ... サンプリングレート(Hz)
  def apply_jma_filter(data, sampling_rate):
    """気象庁定義のフィルターを加速度波形データに適用する関数"""
    n = len( data )
    # フーリエ変換
    # yfは周波数ごとのデータの配列。要素数はn
    yf = fft( data )

    # 周波数軸を作成
    # xfは周波数ごとのラベルの配列。要素数はn
    xf = fftfreq( n, 1 / sampling_rate )

    # --- ここからフィルターを数式通りに作成 ---
    # 周波数が0や負の値の場合を考慮してエラーを回避
    xf_positive = np.abs( xf )

    # 1. ローカットフィルター
    # 1Hz未満のものを小さく
    fl = np.sqrt( 1 - np.exp( -(xf_positive / 0.5)**3 ) )

    # 2. ハイカットフィルター
    # 10Hz以上くらいのものを小さく
    y = xf_positive / 10.0
    fh = ( 1 + 0.694*y**2 + 0.241*y**4 + 0.0557*y**6 + 0.009664*y**8 + 0.00134*y**10 + 0.000155*y**12 )**( -0.5 )

    # 3. 周期効果フィルター (f=0でのゼロ割を避ける)
    # 周波数の小さいものを少し大きく、大きいものを少し小さく
    ff = np.sqrt( 1 / np.where( xf_positive == 0, 1e-9, xf_positive ) )

    # 総合フィルター（1～3のフィルタを合成）
    # 配列を要素ごとにかけ合わせる
    total_filter = fl * fh * ff

    # フィルターを適用
    # 配列を要素ごとにかけあわせる
    yf_filtered = yf * total_filter

    # 逆フーリエ変換で時間領域の波形に戻す
    # 結果は複素数になるため、実部を取り出す
    return np.real( ifft( yf_filtered ) )

  # --- 1. 元の速度波形 ---
  plot_three_components(axes[0], st, "1. 元の速度波形 (Original Velocity)", "速度 (m/s)")

  # --- ステップ1: 加速度への変換(微分) (Fig.1 相当) ---
  st_acc = st.copy()
  st_acc.differentiate()  # このst_accは加速度になっている
  plot_three_components(axes[1], st_acc, "2. 加速度波形 (Acceleration) (JMA Fig.1 相当)", "加速度 (m/s^2)")

  # --- ステップ2: フィルター処理（加速度） (Fig.4 相当) ---
  # 気象庁サイトに記載のフィルターを適用
  st_filt = st_acc.copy()
  # 各要素にフィルターを適用
  for tr in st_filt:
    tr.data = apply_jma_filter(tr.data, tr.stats.sampling_rate)
  plot_three_components(axes[2], st_filt, "3. フィルター処理済加速度 (Filtered Accel) (JMA Fig.4 相当)", "加速度 (m/s^2)")

  # --- フィルター後速度(積分) ---
  st_vel_fig4 = st_filt.copy()
  st_vel_fig4.integrate()  # 積分して加速度 -> 速度に戻す
  plot_three_components(axes[3], st_vel_fig4, "4. フィルター後速度", "速度 (m/s)")

  # --- ステップ3 & 4: 3成分合成加速度 と 閾値 a の算出 ---
  # 各成分のデータをNumPy配列として取り出す
  tr_e = st_filt.select(channel="*E*")[0]
  tr_n = st_filt.select(channel="*N*")[0]
  tr_u = st_filt.select(channel="*U*")[0]

  # 3成分のベクトル合成
  combined_acc = np.sqrt(tr_e.data**2 + tr_n.data**2 + tr_u.data**2)

  # 揺れの大きさ a の算出
  # 合成加速度の絶対値を降順にソート
  sorted_abs_acc = np.sort(np.abs(combined_acc))[::-1]

  # サンプリングレートを取得
  fs = st[0].stats.sampling_rate

  # 0.3秒に相当するデータ点数を計算
  # このインデックスの値が a となる
  duration_index = int(0.3 * fs)

  if len(sorted_abs_acc) > duration_index:
    a = sorted_abs_acc[duration_index]
  else:
    a = 0.0

  # --- 5. 3成分合成加速度波形 (Fig.5 相当) ---
  ax = axes[4]
  ax.plot(tr_e.times(), combined_acc, color='tab:red', alpha=0.8, linewidth=0.5, label='合成加速度')
  # 閾値の a の横線を描画
  ax.axhline(y=a, color='blue', linestyle='--', linewidth=1.2, label=f'閾値 a = {a:.4f} m/s^2')
  ax.set_title("5. 3成分合成加速度波形 (Combined Accel & 閾値 a)  (JMA Fig.5 相当)")
  ax.set_xlabel("時刻 (s)")
  ax.set_ylabel("合成加速度 (m/s^2)")
  ax.legend(loc="upper right", fontsize="small")
  ax.grid(True)

  # --- ステップ5: 計測震度の算出 ---
  # a は cm/s^2 に変換して計算することが多いので、m/s^2 から変換(100倍)
  a_cmss = a * 100  # cm/s^2 (Gal) に変換

  # 計測震度を計算
  instrumental_intensity = 2 * np.log10(a_cmss) + 0.94 if a_cmss > 0 else 0.0

  # 気象庁震度階級に置き換え
  jma_scale = convert_to_jma_scale(instrumental_intensity)

  # --- 6番目の枠：計算結果サマリー ---
  # --- メタデータ（拠点名・開始時刻）の取得 ---
  station_name = st[0].stats.station
  # 区間の開始時刻
  # UTC -> JST への変換が必要な想定だったが、JST で出ているようなのでそのまま
  start_time_jst = st[0].stats.starttime  # UTCからの変換が必要な場合は加える⇒ + 9 * 3600
  time_str = start_time_jst.strftime("%Y-%m-%d %H:%M:%S")

  ax_text = axes[5]
  ax_text.axis('off')

  # サマリーに表示する内容
  result_text = f"[ 解析結果サマリー ]\n" \
                f"• 拠点名 (Station): {station_name}\n" \
                f"• 開始時刻 (JST): {time_str}\n\n" \
                f"• 揺れの指標 a: {a:.4f} m/s^2\n" \
                f"  (約 {a_cmss:.2f} cm/s^2 [Gal])\n\n" \
                f"• 計測震度: {instrumental_intensity:.2f}\n" \
                f"• 推定震度階級: 震度 {jma_scale}"

  # テキストを描画
  ax_text.text(0.05, 0.5, result_text, fontsize=10.5,
              bbox=dict(boxstyle='round,pad=1', facecolor='lightyellow', edgecolor='orange', alpha=0.8),
              verticalalignment='center')

  # レイアウト調整と保存
  plt.tight_layout()

  plt.show()

  return instrumental_intensity, jma_scale
