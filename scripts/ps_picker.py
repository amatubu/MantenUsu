import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, Button
import obspy
from obspy.signal.trigger import recursive_sta_lta
import json
from pathlib import Path

from plot_common import plot_components

# ==========================================
# 設定ファイル・プリセット
# ==========================================
# カレントディレクトリ基準(./output と同じ扱い)
SETTINGS_FILE = Path(__file__).resolve().parent.parent / "etc" / "ps_picker_params.json"

# スライダー定義: (キー, 表示名, 最小, 最大, 刻み)
SLIDER_SPECS = [
    ("p_sta",   "P STA [s]",      0.1,  10.0,  0.1),
    ("p_lta",   "P LTA [s]",      1.0,  60.0,  0.5),
    ("p_thr",   "P threshold",    1.0,  8.0,   0.1),
    ("s_sta",   "S STA [s]",      0.1,  20.0,  0.1),
    ("s_lta",   "S LTA [s]",      1.0,  100.0, 0.5),
    ("s_thr",   "S threshold",    1.0,  10.0,  0.1),
    ("omori_k", "Omori k [km/s]", 1.0,  15.0,  0.1),
]

# パラメータのデフォルト値
DEFAULT_PARAMS = dict(
    p_sta=2.5, p_lta=10.0, p_thr=1.8,
    s_sta=4.0, s_lta=20.0, s_thr=3.0,
    omori_k=8.0,
)

# プリセット(大森係数は触らず、現在値を維持する)
PRESETS = {
    "Standard":      dict(
        p_sta=2.5, p_lta=10.0, p_thr=1.8, s_sta=4.0, s_lta=20.0, s_thr=3.0),
    "Small quake":   dict(
        p_sta=1.0, p_lta=10.0, p_thr=1.5, s_sta=2.0, s_lta=15.0, s_thr=2.0),
    "Distant quake": dict(
        p_sta=5.0, p_lta=30.0, p_thr=2.0, s_sta=8.0, s_lta=60.0, s_thr=2.5),
    "Near / strong": dict(
        p_sta=0.5, p_lta=5.0,  p_thr=3.0, s_sta=1.0, s_lta=10.0, s_thr=3.5),
}

# JSONからパラメータを読み込み
def load_params():
    """JSONから前回のパラメータを読み込む。無い・壊れている場合は既定値。"""
    params = dict(DEFAULT_PARAMS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            saved = json.load(f).get("params", {})
        for key, _, vmin, vmax, _ in SLIDER_SPECS:
            val = saved.get(key)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                params[key] = min(max(float(val), vmin), vmax)  # 範囲外は丸める
        print(f"前回の設定を読み込みました: {SETTINGS_FILE}")
    except FileNotFoundError:
        print("設定ファイルが無いため、既定値を使用します。")
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        print(f"設定ファイルを読めなかったため、既定値を使用します: {e}")
    return params

# JSONへパラメータを保存
def save_params(params):
    """現在のパラメータをJSONに保存する。"""
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1,
                "params": {k: round(float(v), 3) for k, v in params.items()}}
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"設定を保存しました: {SETTINGS_FILE}")
    except OSError as e:
        print(f"設定の保存に失敗しました: {e}")

# 波形データをファイルから読み込む
def read_waveform_data(ew_file, ns_file, ud_file):
    """3成分のSACファイルを読み込む関数"""
    try:
        st = obspy.read(ew_file)
        st += obspy.read(ns_file)
        st += obspy.read(ud_file)
        print("波形データを読み込みました。")
        return st
    except Exception as e:
        print(f"ファイルの読み込みに失敗しました: {e}")
        return None

# P波・S波の到着時刻を推定し、結果を辞書で返す
#   tr_u     ... 垂直方向(U)の波形データ
#   hor_data ... 水平方向(EとNの合成)の波形データ
#   df       ... サンプリングレート(Hz)
#   params   ... 計算用のパラメータ
def detect_arrivals(tr_u, hor_data, df, params):
    """P波・S波の検出とCFT計算。結果を辞書で返す。"""
    # パラメータ設定を読み取る
    p_sta, p_lta, p_thr = params["p_sta"], params["p_lta"], params["p_thr"]
    s_sta, s_lta, s_thr = params["s_sta"], params["s_lta"], params["s_thr"]

    # LTA > STA でないと recursive_sta_lta が意味を持たないためチェック
    if p_lta <= p_sta or s_lta <= s_sta:
        return None

    # --- P波 ---
    # 上下方向(U)の波形からCFTを計算
    cft_p = recursive_sta_lta(
        tr_u.data, max(1, int(p_sta * df)), max(2, int(p_lta * df)))
    # 閾値を超えた最初のインデックス
    p_idx = np.where(cft_p > p_thr)[0]
    # インデックスを秒に変換（記録開始からの相対時間）
    p_time = p_idx[0] / df if len(p_idx) > 0 else None

    # --- S波(P波到着以降で探索) ---
    # 水平方向(EとNの合成)の波形からCFTを計算
    cft_s = recursive_sta_lta(
        hor_data, max(1, int(s_sta * df)), max(2, int(s_lta * df)))
    s_time = None
    if p_time is not None:
        # P波が観測できたときだけ計算
        # P波到着時刻のインデックス
        p_i = int(p_time * df)
        # P波到着後のデータだけを抽出し、以降で閾値を超えた最初のインデックスを取得
        s_idx = np.where(cft_s[p_i:] > s_thr)[0]
        if len(s_idx) > 0:
            # 該当があればS波到着時間を計算
            s_time = (p_i + s_idx[0]) / df

    return {"cft_p": cft_p, "cft_s": cft_s, "p_time": p_time, "s_time": s_time}

# 波形・CFT・推定結果を、スライダー付きのウィンドウで表示する
def plot_ps_arrival_time(st):
    # --- データ取得 ---
    tr_u = st.select(channel="*U*")[0]
    tr_n = st.select(channel="*N*")[0]
    tr_e = st.select(channel="*E*")[0]
    df = tr_u.stats.sampling_rate

    # 観測点名と記録開始時刻(結果パネルの先頭に表示)
    station = tr_u.stats.station or "-"
    start_str = tr_u.stats.starttime.strftime("%Y-%m-%d %H:%M:%S")
    header = (f"Station   : {station}\n"
              f"Start     : {start_str}\n")

    # 検出用の水平合成(表示には使わない)
    hor_data = np.sqrt(tr_e.data**2 + tr_n.data**2)
    t_u = tr_u.times()
    t_h = tr_n.times()

    # 検出に関わる線の色(波形の色と紛らわしくならないよう、P=赤 / S=マゼンタ)
    p_color, s_color, cft_color = "red", "magenta", "black"

    # ==========================================
    # 図のレイアウト: 左に4段グラフ、右にコントロールパネル
    # ==========================================
    # ウィンドウを準備
    fig = plt.figure(figsize=(15, 9))
    # グラフ領域の左余白と幅(0.0〜1.0の割合で定義)
    left, width = 0.06, 0.64
    # グラフ1つの縦幅、間隔、下余白
    h, gap, bottom = 0.19, 0.04, 0.08
    # 各グラフの下端の座標(下余白から縦幅・間隔を元に計算)
    ys = [bottom + (3 - i) * (h + gap) for i in range(4)]

    # グラフ領域を定義
    # X軸は ax1 のものを共有(連動して動く)
    ax1 = fig.add_axes([left, ys[0], width, h])
    ax2 = fig.add_axes([left, ys[1], width, h], sharex=ax1)
    ax3 = fig.add_axes([left, ys[2], width, h], sharex=ax1)
    ax4 = fig.add_axes([left, ys[3], width, h], sharex=ax1)
    # 上3つのグラフはX軸の目盛の数字を非表示にしてすっきりさせる
    for ax in (ax1, ax2, ax3):
        plt.setp(ax.get_xticklabels(), visible=False)

    # --- 波形(共通ルーチンで色分け) ---
    plot_components(ax1, obspy.Stream([tr_u]))          # 1段目: 上下動
    plot_components(ax3, obspy.Stream([tr_e, tr_n]))    # 3段目: 東西・南北を重ねる

    # --- P波・S波のCFTの描画 ---
    # 初期は y=0 の仮の線。実際の内容は update() の set_ydata で設定
    line_cft_p, = ax2.plot(t_u, np.zeros_like(t_u), "-", color=cft_color,
                           lw=0.5, label="P-CFT")
    line_cft_s, = ax4.plot(t_h, np.zeros_like(t_h), "-", color=cft_color,
                           lw=0.5, label="S-CFT")

    # --- 更新される線(閾値・到着線) ---
    # 閾値設定をCFTのグラフに描画
    # axhlineは水平の点線( ls=":" )を描画
    # 初期の座標は y=0 とする(update()で正しい位置に更新)
    thr_p_line = ax2.axhline(0, color=p_color, ls=":")
    thr_s_line = ax4.axhline(0, color=s_color, ls=":")
    # P波・S波の到着時刻の垂直の破線( ls="--" )を波形・CFTのそれぞれのグラフに描画
    # 初期の座標は x=0 とする(update()で正しい位置に更新)
    # 初期は非表示とする( visible=False )
    p_lines = [ax.axvline(0, color=p_color, ls="--", visible=False) for ax in (ax1, ax2)]
    s_lines = [ax.axvline(0, color=s_color, ls="--", visible=False) for ax in (ax3, ax4)]

    # Y軸のラベル設定
    ax1.set_ylabel("Amplitude")
    ax2.set_ylabel("P-CFT")
    ax3.set_ylabel("Amplitude")
    ax4.set_ylabel("S-CFT")
    # X軸のラベル設定
    ax4.set_xlabel("Time [s]")
    # グリッドを表示
    for ax in (ax1, ax2, ax3, ax4):
        ax.grid(True)

    # 結果のテキスト表示エリアの定義
    result_text = fig.text(0.76, 0.15, "", fontsize=10, va="top",
                           family="monospace")

    # ==========================================
    # スライダー(右パネル): 前回の設定で初期化
    # ==========================================
    # 初期設定は設定ファイル(JSON)から読み込む
    initial = load_params()
    # スライダの定義
    sliders = {}
    # 最初のスライダーの下端Y座標、スライダー間の縦間隔
    top, step_y = 0.90, 0.07
    # パラメータをループさせてスライダーを描画
    for i, (key, label, vmin, vmax, vstep) in enumerate(SLIDER_SPECS):
        sax = fig.add_axes([0.82, top - i * step_y, 0.14, 0.03])
        sliders[key] = Slider(sax, label, vmin, vmax,
                              valinit=initial[key], valstep=vstep)

    # 右パネルのラベル定義
    fig.text(0.76, 0.96, "Parameters", fontsize=13, weight="bold")

    # ==========================================
    # 更新処理(パラメータが変更されたときに結果表示を更新)
    # ==========================================
    def update(_=None):
        # パラメータ設定
        # 現在のスライダーの値をすべて辞書にまとめる(大森係数kも含む)
        params = {k: s.val for k, s in sliders.items()}
        # P波・S波の到着時刻等を計算
        res = detect_arrivals(tr_u, hor_data, df, params)

        # STA/LTAのウィンドウの長さが不正(LTA ≦ STA)のときは警告を表示して終了
        if res is None:
            result_text.set_text("LTA must be longer than STA.\n"
                                 "Adjust the sliders.")
            fig.canvas.draw_idle()
            return

        # P波・S波の到着時刻を結果から取得
        p_time, s_time = res["p_time"], res["s_time"]

        # CFTのグラフの内容を更新
        line_cft_p.set_ydata(res["cft_p"])
        line_cft_s.set_ydata(res["cft_s"])
        # 閾値の横線を更新
        # グラフの左端と右端のY軸の値を必要とするため、「*2」でふたつの配列にしている
        thr_p_line.set_ydata([params["p_thr"]] * 2)
        thr_s_line.set_ydata([params["s_thr"]] * 2)
        # 閾値のラベル文字列を更新(表示更新は別途)
        thr_p_line.set_label(f"Threshold ({params['p_thr']:.1f})")
        thr_s_line.set_label(f"Threshold ({params['s_thr']:.1f})")

        # P波到着時刻の処理
        for ln in p_lines:
            if p_time is not None:
                # 縦線の下端・上端のX座標を到着時刻に設定し描画
                ln.set_xdata([p_time, p_time])
                ln.set_visible(True)
            else:
                # 検出されなかったときは非表示
                ln.set_visible(False)
        # S波到着時刻の処理
        for ln in s_lines:
            if s_time is not None:
                # 縦線の下端・上端のX座標を到着時刻に設定し描画
                ln.set_xdata([s_time, s_time])
                ln.set_visible(True)
            else:
                # 検出されなかったときは非表示
                ln.set_visible(False)

        # P波・S波到着時刻のラベル文字列を更新(表示更新は別途)
        p_lines[0].set_label(f"P-arrival ({p_time:.2f} s)" if p_time is not None
                             else "P-arrival (not found)")
        s_lines[0].set_label(f"S-arrival ({s_time:.2f} s)" if s_time is not None
                             else "S-arrival (not found)")

        # CFTグラフの調整(Y軸の範囲調整)
        for ax in (ax2, ax4):
            # 最大値・最小値の再計算
            ax.relim()
            # Y軸の表示のみ自動調整
            ax.autoscale_view(scalex=False)

        # ラベル付きの線だけが凡例に入る(波形は成分名、到着線・閾値は上で設定)
        for ax in (ax1, ax2, ax3, ax4):
            ax.legend(loc="upper right", fontsize=8)

        # 結果サマリの処理
        if p_time is not None and s_time is not None:
            # P波・S波とも検出できた場合
            # 大森公式で震源までの距離を推定
            sp = s_time - p_time
            dist = params["omori_k"] * sp
            # 表示メッセージを構築
            msg = (header +
                   f"P arrival : {p_time:8.2f} s\n"
                   f"S arrival : {s_time:8.2f} s\n"
                   f"S-P time  : {sp:8.2f} s\n"
                   f"Omori k   : {params['omori_k']:8.1f} km/s\n"
                   f"Distance  : {dist:8.1f} km")
            # グラフタイトルを更新
            ax1.set_title(f"S-P Time: {sp:.2f}s | Est. Distance: {dist:.1f} km "
                          f"(k={params['omori_k']:.1f})", fontsize=11)
        else:
            # P波またはS波が検出できなかった場合
            # 表示メッセージを構築
            msg = (header +
                   f"P arrival : {'-' if p_time is None else f'{p_time:.2f} s'}\n"
                   f"S arrival : {'-' if s_time is None else f'{s_time:.2f} s'}\n"
                   "Distance  : N/A")
            # グラフタイトルを更新
            ax1.set_title("Waveform (P/S arrival not both detected)", fontsize=11)
        # 結果サマリを表示
        result_text.set_text(msg)
        fig.canvas.draw_idle()

    for s in sliders.values():
        s.on_changed(update)

    # --- 複数のスライダーをまとめて設定する(再計算は最後に1回だけ) ---
    def apply_params(values):
        for key, val in values.items():
            s = sliders[key]
            s.eventson = False      # 個別の更新イベントを止める
            s.set_val(val)
            s.eventson = True
        update()

    # --- プリセットボタン(2x2) ---
    fig.text(0.76, 0.44, "Presets", fontsize=11, weight="bold")
    preset_buttons = []
    for i, (name, preset) in enumerate(PRESETS.items()):
        bx = 0.77 + (i % 2) * 0.10
        by = 0.37 - (i // 2) * 0.07
        bax = fig.add_axes([bx, by, 0.095, 0.05])
        btn = Button(bax, name)
        btn.label.set_fontsize(8)
        btn.on_clicked(lambda _evt, p=preset: apply_params(p))
        preset_buttons.append(btn)

    # --- Reset(既定値に戻す) ---
    rax = fig.add_axes([0.82, 0.17, 0.08, 0.04])
    btn_reset = Button(rax, "Reset")
    btn_reset.on_clicked(lambda _: apply_params(DEFAULT_PARAMS))

    # --- Print(結果表示)ボタン ---
    pax = fig.add_axes([0.91, 0.17, 0.06, 0.04])
    btn_print = Button(pax, "Print")

    # 結果表示(コンソール)
    def print_result(_):
        params = {k: s.val for k, s in sliders.items()}
        res = detect_arrivals(tr_u, hor_data, df, params)
        print("--- パラメータ ---")
        for k, v in params.items():
            print(f"{k}: {v}")
        if res and res["p_time"] is not None and res["s_time"] is not None:
            sp = res["s_time"] - res["p_time"]
            print("--- 震源距離の推定 ---")
            print(f"P波到着: {res['p_time']:.2f} s / S波到着: {res['s_time']:.2f} s")
            print(f"S-P時間: {sp:.2f} s")
            print(f"推定震源距離: {params['omori_k'] * sp:.1f} km "
                  f"(大森係数 k={params['omori_k']})")
        else:
            print("P波またはS波が検出されなかったため、距離を計算できませんでした。")
    btn_print.on_clicked(print_result)

    # --- ウィンドウを閉じるとき、最後のパラメータを保存 ---
    def on_close(_event):
        save_params({k: s.val for k, s in sliders.items()})
    fig.canvas.mpl_connect("close_event", on_close)

    update()
    fig._widgets = (sliders, btn_reset, btn_print, preset_buttons)
    plt.show()

if __name__ == "__main__":
    # ==========================================
    # ファイル指定と実行
    # ==========================================
    ew_sac_file = "./output/202507082130_202507082130/Hokudai.E.0970355243_sac"
    ns_sac_file = "./output/202507082130_202507082130/Hokudai.N.0970355243_sac"
    ud_sac_file = "./output/202507082130_202507082130/Hokudai.U.0970355243_sac"

    # 青森県東方沖
    ew_sac_file = "./output/202512082314_202512082324/Bamba.E.1070432003_sac"
    ns_sac_file = "./output/202512082314_202512082324/Bamba.N.1070432003_sac"
    ud_sac_file = "./output/202512082314_202512082324/Bamba.U.1070432003_sac"

    st = read_waveform_data(ew_sac_file, ns_sac_file, ud_sac_file)
    if st is not None:
        plot_ps_arrival_time(st)
