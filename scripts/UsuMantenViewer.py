# 必要なライブラリのインポート
import argparse                           # 入力引数の処理
from datetime import datetime, timedelta  # 日付・時刻の処理
import json
import os
import tkinter as tk                      # GUIツール
from tkinter import filedialog, messagebox, ttk  # GUIの拡張ツール(ttk = Themed Tkinter)
import warnings
import traceback
import re

import app_paths                       # matplotlib より先に読み込む(ビルドしたアプリ向けの設定のため)
import numpy as np
from obspy import read, Stream         # SACファイルの読み込み
from obspy import UTCDateTime as obspy_time
import matplotlib.pyplot as plt        # グラフ表示ツール
from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk

from plot_common import setup_japanese_font
from log_common import log_message, set_console, set_log_widget
import win_pipeline
import cache_manager
from cache_dialog import OutputCacheDialog
from stack_plot import StackFigure, StackWindow, find_trace
from stack_overlays import QuakeOverlay, TrainOverlay, draw_on_image
from waveform_window import open_waveform_window
from drive_downloader import load_folder_id, download_file_from_drive, DriveFileNotFoundError, DownloadCancelled
from hypocenter import load_stations, LocalProjection
import quake_detect
import quake_features
import quake_locate
import pick_store
from quake_locate_window import open_locate_window
from hypocenter_window import plot_hypocenter
from pathlib import Path
import jma_match
import jma_hypocenter

# MacのTcl/Tk非推奨警告を抑制
os.environ["TK_SILENCE_DEPRECATION"] = "1"

# ObsPyのSAC読み込み時のサンプル間隔に関する丸め警告を抑制
warnings.filterwarnings(
  "ignore",
  category=UserWarning,
  module="obspy.io.sac.util",
  message=".*Sample spacing read from SAC file.*"
)

# TODO
# [完了]1. 1時間ごとの波形表示をバッチ処理化(UIは作らず、daily_zipsにあるZipファイルについて一括処理。パラメータとして、チャンネル名または番号を指定)
# [完了]2. 4拠点のグラフ表示を2x2のグリッド表示させる
# [完了]4. Googleドライブからのダウンロード中に中止処理を追加
# [完了]5. log_messageのprintへの出力用にデバッグオプション(コマンドラインオプション)を追加する
# [完了]6. 震度の推測
# 7. WINファイル、SACファイルのキャッシュ処理の最適化(フルにキャッシュする、最小限にする(バッチ高速処理用)のオプションを用意するなど)
# [完了]8. 1時間ごとのグラフ画面で、拠点・チャンネルの切り替え機能を追加
# [完了]9. キャッシュ整理処理で、WINファイルのみ、結合前WINファイルのみなどのオプション追加
# [完了]10. P波、S波の到着時刻計算
# [完了]11. 震源位置、地震発生時刻の推測(4拠点のP波到着時刻から。遠い地震は到来方向のみ。配置図の背景に地理院タイル)
# 12. 変更しないファイルと、使っているうちに作られる・変わるファイルの置き場所を分ける
#     変更しないもの(動作に必須のものを含む): tools、service_account、etc の設定ファイル(win.prm、*.ch、*.tbl)
#       → アプリと一緒に置く(今と同じ)
#     ユーザーの操作で作られる・変わるもの: etc/ps_picker_params.json、etc/hypocenter_params.json、output(キャッシュ・history.json・picks)、
#       daily_zips(ダウンロードしたZIP)、batch_plots(バッチ処理の画像)
#       → ユーザープロファイル配下に置く(例: Mac は ~/Library/Application Support/UsuMantenViewer、
#          Windows は %APPDATA%\UsuMantenViewer や %LOCALAPPDATA%\UsuMantenViewer)
#     場所は app_paths.py でまとめて決める。アプリを .app の中や Program Files など、
#     書き込めない場所に置けるようになり、アプリを入れ替えても設定やキャッシュが残る
# [完了]13. 地震らしい波形の自動検出(--detect-quakes。2〜20 Hz で3拠点以上が同時に大きく揺れたものを、
#     指定した期間から地震の候補として一覧にする。履歴への追加・確認用の画像はオプション)
# [完了]14. S-P時間と到来方向を組み合わせた震源推定(震源推定の画面で、近い地震として位置が決まらないとき、
#     P波の到来方向と S-P時間(大森公式)の距離から震央を求める)
# 15. 外部ツール(win2sac_32、wchch_32)のビルド方法をドキュメントに書く
#     防災科研「連続波形ダウンロード」のソースコードを、Mac は gcc(Xcode のコマンドラインツール)、
#     Windows は MinGW の gcc で、それぞれの Makefile を使ってビルドしている
#     どちらもそのままではビルドできないため、当てているパッチをリポジトリに追加する
# [完了]16. 気象庁の震源リストとの自動照合(--detect-quakes で、震源から拠点への P波・S波の到達見込みを
#     計算して候補と照合する。見込みの振幅が大きいのに候補が無い地震も「見逃し?」として一覧にする)
# [完了]17. 地震の候補の震源推定(--locate-quakes でまとめて計算し、一覧と震央の地図・断面図を出す。
#     メイン画面の「候補の一覧(CSV)を選んで震源を表示」で、結果の表と地図・断面図を画面に表示し、
#     候補ごとに震源推定の画面を開いて到着時刻を手で直して確かめられる)
# 18. 震源推定の画面の使い方をドキュメントに書く(とくに遠い地震)
#     遠い地震は、P波の揺れの中から S波らしい揺れを見つけるため、パンツール(右ドラッグで拡大縮小)で
#     表示の範囲を合わせながら、水平動の表示で S波を手で指定する必要がある
#     大森係数 k は距離によって変わる(2026/8/23 02:00 の茨城県南部(730 km)では、S-P 約75秒で k ≒ 9.7 になる。
#     200〜300 km の地震では 8.4〜9.0 だった)。k の目安を距離ごとに書くか、S-P時間から走時表(TauP)で
#     距離を求める方法を検討する
# 19. 地震の候補の震源(--locate-quakes)で、「近い地震」と判定する基準を厳しくする
#     到着時刻(P波・S波を合わせて)が少ないと、未知数(位置3つと発生時刻)に対して余裕がなく、
#     残差が小さくても位置が当てにならない。「到着時刻が6つ以上」のような条件を足す
#     (2026/8/3 00:51 は、到着時刻5つ(P波3・S波2)で残差 0.083秒の「近い地震」になったが、
#      波形は Hokudai 以外は雑音並みで、地震とは言えなかった)
# [完了]20. 地震の候補の震源の画面で、震源推定の画面での手直しを一覧に反映する
#     (到着時刻とパラメータを output/picks に保存し(pick_store.py)、震源推定・P波・S波検出・地震の候補の震源の
#      画面とバッチ処理で再現する。一覧の画面では「保存して一覧に反映」で表・地図を更新し、CSV に「手直し」の列を足す)
# 21. 気象庁や防災科研の観測点の波形データ(とくに有珠山の南側の観測点)を組み合わせることで、
#     震源位置の特定の精度が上がるかどうかを検証する
#     (今の4拠点には南側の拠点が無く、深さと位置が決まりにくい)
# 22. 震源リストに無い、よく似た小さな地震2件(2026/6/23 12:58、7/8 11:20)の震源を確かめる
#     4拠点で同時に揺れたが、今の基準(10秒窓で3拠点が雑音の20倍以上)には届かない(5秒窓・13倍なら拾える)
#     P波は Bamba が最初で Nizaemn が約1.1秒遅れ、S-P時間はどの拠点も約2.2秒とそろう。周波数は有珠山の地震と同じ 4〜8 Hz
#     手で直すと有珠山の西・深さ7〜8 km に決まるが、速度一定(3 km/s)のモデルでは深さが決めきれず、
#     西に離れた場所の可能性もある。速度構造(深いほど速い)や、気象庁・防災科研の観測点(TODO 21)で確かめる

# クラス定義
class MantenUsuSeismographApp:
  # バージョン情報
  APP_VERSION = "3.0.0"
  # 履歴に残す件数の上限(地震の候補のバッチ処理で、まとめて追加することがあるため多めにしている)
  MAX_HISTORY = 1000

  # クラスの初期化
  def __init__(self, root):
    # 表示する波形の時刻等の入力や履歴表示等を行うUIを構築する
    self.root = root
    # ウィンドウタイトル
    self.root.title(f"有珠満点地震計プロジェクト 波形ビューワ v{self.APP_VERSION}")
    # ウィンドウサイズ
    self.root.geometry("460x700")
    # リサイズを許可しない
    self.root.resizable(False, False)

    # パス設定(場所は app_paths で決める。開発中とビルドしたアプリで共通)
    # WINファイル・SACファイルなどの出力先
    self.output_base = app_paths.OUTPUT_DIR
    # 日ごとのZipファイルの格納場所
    self.zips_root = app_paths.ZIPS_DIR
    # win2sac_32の格納場所
    self.tools_dir = app_paths.TOOLS_DIR
    # win2sac_32が使用するチャンネル定義ファイル等の格納場所
    self.etc_dir = app_paths.ETC_DIR
    # Googleドライブのサービスアカウントファイルの格納場所
    self.service_account_dir = app_paths.SERVICE_ACCOUNT_DIR
    # バッチ処理で保存する画像の格納場所
    self.batch_output_dir = app_paths.BATCH_PLOTS_DIR
    # GoogleドライブのフォルダIDの定義ファイル(JSON)
    self.folder_config_file = self.service_account_dir / "folder_config.json"
    self.target_folder_id = load_folder_id(self.folder_config_file)
    # 履歴を格納するファイル(JSON)
    self.history_file = self.output_base / "history.json"

    # WINファイル等の出力先フォルダを作成(既にあればok)
    self.output_base.mkdir(parents=True, exist_ok=True)

    # 「clam」テーマの適用
    style = ttk.Style()
    if "clam" in style.theme_names():
      style.theme_use("clam")

    # --- UIレイアウト ---
    panel = tk.Frame(root, padx=20, pady=20)
    panel.pack(fill=tk.BOTH, expand=True)

    # 1. 地震タイトル / コメント
    # ラベル
    tk.Label(
      panel,
      text = "地震タイトル / コメント:",
      font = ("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(0, 2))
    # テキスト入力項目の定義
    self.entry_title = tk.Entry(
      panel,
      font = ("sans-serif", 11),
      bg = "white",
      fg = "black",
      insertbackground = "black",
      relief = "solid",
      bd = 1,                      # 枠線の太さ
    )
    # 初期表示
    self.entry_title.insert(0, "浦河沖地震")
    # 入力項目を横に広げ、下余白をとる
    self.entry_title.pack(fill=tk.X, pady=(0, 8))

    # 2. 開始日時
    # ラベル
    tk.Label(
      panel,
      text = "開始日時 (YYYYMMDDHHmm):",
      font = ("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(0, 2))
    # テキスト入力項目の定義
    self.entry_start = tk.Entry(
      panel,
      font = ("sans-serif", 11),
      bg = "white",
      fg = "black",
      insertbackground = "black",
      relief = "solid",
      bd = 1,
    )
    # 初期表示
    self.entry_start.insert(0, "202608232245")
    self.entry_start.pack(fill=tk.X, pady=(0, 8))

    # 3. 終了日時 ＆ コピーボタン用フレーム
    # 日時とボタンを横に並べるためのフレームを定義
    end_frame = tk.Frame(panel)
    end_frame.pack(fill=tk.X, pady=(0, 2))

    # ラベル
    tk.Label(
      end_frame,
      text = "終了日時 (YYYYMMDDHHmm):",
      font = ("sans-serif", 10, "bold")
    ).pack(side=tk.LEFT)

    # 開始時間をコピーするためのボタン定義
    btn_copy = tk.Button(
      end_frame,
      text = "開始時間をコピー",            # ボタンに表示されるラベル
      font = ("sans-serif", 9),
      command = self.copy_start_to_end,  # ボタンが押された際に実行されるメソッド
    )
    # ボタンは右側に揃える
    btn_copy.pack(side=tk.RIGHT)

    # テキスト入力項目の定義
    self.entry_end = tk.Entry(
      panel,
      font = ("sans-serif", 11),
      bg = "white",
      fg = "black",
      insertbackground = "black",
      relief = "solid",
      bd = 1,
    )
    # 初期表示
    self.entry_end.insert(0, "202608232249")
    self.entry_end.pack(fill=tk.X, pady=(0, 2))

    # 時間間隔（●分間）のリアルタイム表示ラベル
    self.lbl_duration = tk.Label(
      panel, text = "",
      font = ("sans-serif", 9, "italic"),
      fg = "#0066cc"
    )
    self.lbl_duration.pack(anchor="w", pady=(0, 12))

    # 入力変更をリアルタイムで検知して時間間隔を更新するバインド
    # 開始時刻、終了時刻でキー操作があったら時間間隔の表示を更新する
    # lambda は無名関数の定義
    self.entry_start.bind("<KeyRelease>", lambda e: self.update_duration_display())
    self.entry_end.bind("<KeyRelease>", lambda e: self.update_duration_display())

    # 4. 実行ボタン
    # ボタンの定義
    btn_run = tk.Button(
      panel,
      text = "抽出・変換・プロット実行 (自動判定)",    # ボタンに表示されるラベル
      font = ("sans-serif", 11, "bold"),
      bg = "#4CAF50",
      fg = "white",
      activebackground = "#45a049",
      command = self.execute_pipeline,  # ボタンが押された際に実行されるメソッド
    )
    btn_run.pack(fill=tk.X, ipady=4, pady=(0, 15))

    # 5. 履歴セクション
    # ラベル
    tk.Label(
      panel,
      text = "過去の実行履歴 (再表示・削除):",
      font = ("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(0, 2))

    # 履歴のプルダウン、再表示・削除ボタンを配置するパネル
    hist_frame = tk.Frame(panel)
    hist_frame.pack(fill=tk.X, pady=(0, 5))

    # 履歴のプルダウンの定義
    self.history_combo = ttk.Combobox(
      hist_frame,
      font = ("sans-serif", 10),
      state = "readonly"          # 直接入力不可
    )
    self.history_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

    # 履歴の再表示ボタン(履歴の内容を呼び出して反映させる)
    btn_load_hist = tk.Button(
      hist_frame,
      text="再表示",
      font=("sans-serif", 9),
      command=self.load_from_history,  # ボタンが押された際に実行されるメソッド
    )
    btn_load_hist.pack(side=tk.LEFT, padx=(0, 3))

    # 履歴の削除ボタン(履歴の内容を削除する)
    btn_del_hist = tk.Button(
      hist_frame,
      text = "削除",
      font = ("sans-serif", 9),
      fg = "red",
      command = self.delete_history  # ボタンが押された際に実行されるメソッド
    )
    btn_del_hist.pack(side=tk.LEFT)

    # ==========================================
    # 区切り線 1 (期間波形機能 と スタック機能 の間)
    # ==========================================
    ttk.Separator(panel, orient="horizontal").pack(fill=tk.X, pady=10)

    # --- 7. 【新機能】1時間スタック波形表示セクション ---
    tk.Label(
      panel,
      text="1時間スタック波形表示 (スダレ状):",
      font=("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(8, 2))

    stack_frame = tk.Frame(panel)
    stack_frame.pack(fill=tk.X, pady=(0, 8))

    # 日付 (YYYYMMDD)
    tk.Label(stack_frame, text="日付:", font=("sans-serif", 9)).pack(side=tk.LEFT, padx=(0, 2))
    self.entry_stack_date = tk.Entry(stack_frame, font=("sans-serif", 10), width=9)
    self.entry_stack_date.insert(0, "20260823")
    self.entry_stack_date.pack(side=tk.LEFT, padx=(0, 6))

    # 時間 (HH)
    tk.Label(stack_frame, text="時間:", font=("sans-serif", 9)).pack(side=tk.LEFT, padx=(0, 2))
    self.entry_stack_hour = tk.Entry(stack_frame, font=("sans-serif", 10), width=3)
    self.entry_stack_hour.insert(0, "22")
    self.entry_stack_hour.pack(side=tk.LEFT, padx=(0, 6))

    # チャンネルコンボボックス (manten_ch.tblから自動読込)
    self.channel_combo = ttk.Combobox(
      stack_frame,
      font=("sans-serif", 9),
      state="readonly",
      width=13
    )
    channel_list = self.load_channel_options()
    self.channel_combo["values"] = channel_list
    if channel_list:
      self.channel_combo.current(0)
    self.channel_combo.pack(side=tk.LEFT, padx=(0, 5))

    # 実行ボタン
    btn_stack_run = tk.Button(
      stack_frame,
      text="スタック表示",
      font=("sans-serif", 9, "bold"),
      activebackground="#1e88e5",
      command=self.execute_stack_pipeline
    )
    btn_stack_run.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # --- 地震の候補の震源(候補の一覧の CSV を選んで、震源を求めて表示する) ---
    tk.Label(
      panel,
      text="地震の候補の震源:",
      font=("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(4, 2))
    tk.Button(
      panel,
      text="候補の一覧(CSV)を選んで震源を表示",
      font=("sans-serif", 9),
      command=self.open_quake_locations,
    ).pack(fill=tk.X, pady=(0, 4))

    # ==========================================
    # 区切り線 2 (スタック機能 と メンテナンス機能 の間)
    # ==========================================
    ttk.Separator(panel, orient="horizontal").pack(fill=tk.X, pady=10)

    # --- メンテナンス・整理セクション ---
    # 5. 実行履歴セクションの下あたりに「実行ログ」エリアを追加
    tk.Label(
      panel,
      text="メンテナンス:",
      font=("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(10, 2))

    maint_frame = tk.Frame(panel)
    maint_frame.pack(fill=tk.X, pady=(0, 0))

    # output フォルダの整理
    btn_clean_output = tk.Button(
      maint_frame,
      text="出力データキャッシュ整理",
      font=("sans-serif", 9),
      command=self.clean_output_cache,
    )
    btn_clean_output.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

    # daily_zips フォルダの整理
    btn_clean_zips = tk.Button(
      maint_frame,
      text="波形Zipファイル整理",
      font=("sans-serif", 9),
      command=self.clean_daily_zips,
    )
    btn_clean_zips.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

    # クラスの初期化内でのUI追加例
    # 5. 実行履歴セクションの下あたりに「実行ログ」エリアを追加
    tk.Label(
      panel,
      text="実行ログ:",
      font=("sans-serif", 10, "bold")
    ).pack(anchor="w", pady=(10, 2))

    # ログを表示するテキストエリア（スクロール付き、または読み取り専用）
    self.log_text = tk.Text(
      panel,
      height=6,
      font=("sans-serif", 9),
      bg="#f9f9f9",
      fg="#333333",
      relief="solid",
      bd=1,
      state=tk.DISABLED  # 初期状態は書き込み禁止（プログラムからのみ追加）
    )
    self.log_text.pack(fill=tk.X, pady=(0, 5))
    # log_message の出力先として登録
    set_log_widget(self.log_text)

    # 履歴データの読み込みと初期の時間間隔計算
    self.update_history_ui()
    self.update_duration_display()

  # manten_ch.tbl から 「拠点名.コンポーネント」 のリストを自動生成するヘルパー
  def load_channel_options(self):
    tbl_path = self.etc_dir / "manten_ch.tbl"
    channels = []
    if tbl_path.exists():
      try:
        with open(tbl_path, "r", encoding="utf-8") as f:
          for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
              continue
            parts = line.split()
            if len(parts) >= 5:
              station = parts[3]
              comp = parts[4]
              channels.append(f"{station}.{comp}")
      except Exception as e:
        log_message(f"チャンネル定義ファイルの読み込みエラー: {e}")

    # 万が一読み込めなかった場合のフォールバック
    if not channels:
      channels = ["Hokudai.U", "Hokudai.N", "Hokudai.E"]
    return channels

  # 時間間隔の表示を更新
  def update_duration_display(self):
    # 開始・終了日時を取得(あわせて前後の空白を除去)
    start_str = self.entry_start.get().strip()
    end_str = self.entry_end.get().strip()

    # 指定された日時文字列をYYYYMMDDHHmmとみなして日付・時間形式に変換
    start_dt = self.parse_datetime(start_str)
    end_dt = self.parse_datetime(end_str)

    if start_dt and end_dt:
      # ★ 両端の分（開始日時の分と終了日時の分）を両方含めた区間（個数・分分）として計算するため +1分を加算
      diff = (end_dt - start_dt) + timedelta(minutes=1)
      # 秒単位の間隔
      secs = int(diff.total_seconds())

      # 開始日時終了日時だったらエラー表示
      if start_dt > end_dt:
        self.lbl_duration.config(
          text="⚠️ エラー: 終了日時が開始日時より前です", fg="red"
        )
        return

      # 分単位、時間単位を計算
      mins = secs // 60
      rem_secs = secs % 60
      hours = mins // 60
      rem_mins = mins % 60

      # 1時間以上であれば時間、分、秒を表示
      if hours > 0:
        duration_text = (
          f"間隔: {hours}時間 {rem_mins}分 ({secs:,}秒間)"
        )
      elif mins > 0:
        # 1分以上であれば分を表示
        duration_text = f"間隔: {mins}分 {rem_secs}秒 ({secs:,}秒間)"
      else:
        # 1分未満であれば秒のみ表示
        duration_text = f"間隔: {secs}秒間"

      # 間隔をテキスト表示
      self.lbl_duration.config(text=duration_text, fg="#0066cc")
    else:
      # 日付・時間形式に変換できなかったときはエラー表示
      self.lbl_duration.config(
        text="形式: YYYYMMDDHHmm (例: 202608232245)", fg="gray"
      )

  # 開始日時を終了日時にコピー
  def copy_start_to_end(self):
    # 開始日時の値
    start_val = self.entry_start.get()
    # 終了日時をいったん消してから開始日時を挿入
    self.entry_end.delete(0, tk.END)
    self.entry_end.insert(0, start_val)
    # 時間間隔の表示を更新
    self.update_duration_display()

  # 履歴ファイル(JSON)の読み込み(履歴のリストを返す)
  def load_history_data(self):
    # 履歴ファイルがなければ、空のリストを返す
    if not self.history_file.exists():
      return []
    try:
      with open(self.history_file, "r", encoding="utf-8") as f:
        # ファイルを開き、JSONを読み込んだ結果を返す
        return json.load(f)
    except Exception:
      return []

  # 履歴ファイルへの保存
  #   history_list ... 履歴のリスト
  def save_history_data(self, history_list):
    try:
      with open(self.history_file, "w", encoding="utf-8") as f:
        # 履歴のリストをJSONとして出力
        json.dump(
          history_list,
          f,
          ensure_ascii = False,  # 日本語がそのまま読める形式
          indent = 2             # インデントの設定
        )
    except Exception as e:
      # エラーが発生した際はその旨を表示
      log_message(f"履歴保存エラー: {e}")

  # 履歴のプルダウンを更新
  def update_history_ui(self):
    # 履歴ファイルの読み込み
    history = self.load_history_data()
    # 履歴リストからひとつひとつを取り出し、開始・終了日時とタイトルを取得
    # プルダウンに表示するリストに再構築
    display_list = [
      f"{item['start']} ～ {item['end']} [{item.get('title', '')}]"
      for item in history
    ]
    # 履歴プルダウンに設定
    self.history_combo["values"] = display_list
    # 履歴の内容があれば1行目を選択
    if display_list:
      self.history_combo.current(0)
    else:
      self.history_combo.set("")

  # 履歴の追加(描画実行時に記録)
  def add_history(self, start_str, end_str, title_str):
    # 履歴を読み込む
    history = self.load_history_data()
    # 設定されている情報を取得
    new_entry = {"start": start_str, "end": end_str, "title": title_str}
    # 設定されている情報と重複する履歴以外をリストとして取得
    history = [h for h in history if h != new_entry]
    # 履歴の最初に今回の情報を追加
    # 既存の履歴と重複しなければリストの先頭に追加
    # 重複していれば過去の履歴を削除して今回の内容を先頭に追加(=先頭に移動)
    history.insert(0, new_entry)
    # 履歴が上限を超えたら古いものから削除
    history = history[:self.MAX_HISTORY]
    # 履歴ファイルへ保存
    self.save_history_data(history)
    # 履歴のプルダウンを更新
    self.update_history_ui()

  # 履歴をまとめて追加する(地震の候補のバッチ処理から)
  #   entries ... [{"start", "end", "title"}, ...]。後ろのものほど上(新しい履歴)になる
  #   以前のバッチ処理で追加した同じ期間の履歴(タイトルが TITLE_PREFIX で始まるもの)は、置き換えて上に移す
  #   同じ期間を自分で表示した履歴があれば、そちらのタイトルを残して追加しない
  #   戻り値 ... (追加した履歴の数, 上限を超えて削除した履歴の数)
  def add_history_entries(self, entries, title_prefix):
    period = lambda h: (h.get("start"), h.get("end"))
    history = self.load_history_data()
    manual = {period(h) for h in history if not h.get("title", "").startswith(title_prefix)}
    entries = [e for e in entries if period(e) not in manual]
    periods = {period(e) for e in entries}
    history = [h for h in history if period(h) not in periods]
    history = list(reversed(entries)) + history
    dropped = max(0, len(history) - self.MAX_HISTORY)
    self.save_history_data(history[:self.MAX_HISTORY])
    self.update_history_ui()
    return len(entries), dropped

  # 履歴の内容を取得して波形表示
  def load_from_history(self):
    # 履歴のプルダウンから選択されている項目を取得
    selected = self.history_combo.get()
    # 選択されていなければ何もしない
    if not selected:
      return
    # 履歴リストをファイルから再取得
    history = self.load_history_data()
    # 選択されているインデックスを得る
    idx = self.history_combo.current()
    if 0 <= idx < len(history):
      # インデックスが履歴リストの範囲内
      # 履歴リストから該当インデックスの内容を取得
      item = history[idx]
      # UIへ反映
      self.entry_title.delete(0, tk.END)
      self.entry_title.insert(0, item.get("title", ""))
      self.entry_start.delete(0, tk.END)
      self.entry_start.insert(0, item["start"])
      self.entry_end.delete(0, tk.END)
      self.entry_end.insert(0, item["end"])
      # 時間間隔表示の更新
      self.update_duration_display()
      # 波形表示
      self.execute_pipeline(is_history_reload=True)

  # 選択された履歴の削除
  def delete_history(self):
    # 履歴のプルダウンから選択されている項目を取得
    selected = self.history_combo.get()
    # 選択されていなければ警告を表示して終了
    if not selected:
      messagebox.showwarning("警告", "削除する履歴が選択されていません。")
      return

    # 履歴リストをファイルから再取得
    history = self.load_history_data()
    # 選択されているインデックスを得る
    idx = self.history_combo.current()
    if 0 <= idx < len(history):
      # インデックスが履歴リストの範囲内
      # 削除してもいいかどうかの確認
      # selected には選択されている項目のテキストが入っている
      if messagebox.askyesno(
        "確認", f"以下の履歴を削除しますか？\n\n{selected}"
      ):
        # Yesが押されたら該当の履歴を削除する
        # 選択されているインデックスの履歴リストを削除
        del history[idx]
        # 履歴ファイルの保存
        self.save_history_data(history)
        # UIの更新
        self.update_history_ui()

  # WINファイルの取得から波形プロットまでのメイン処理
  #   return_to ... 波形表示のウィンドウを閉じたときに戻るウィンドウ(Tk)。None ならOS任せ
  def execute_pipeline(self, is_history_reload=False, return_to=None):
    # 設定されているタイトル、開始・終了日時の取得(前後の空白は除去)
    title_str = self.entry_title.get().strip()
    start_str = self.entry_start.get().strip()
    end_str = self.entry_end.get().strip()

    # 開始・終了日時をYYYYMMDDHHmmとみなして日付・時間形式に変換
    start_dt = self.parse_datetime(start_str)
    end_dt = self.parse_datetime(end_str)
    if not start_dt or not end_dt:
      # 変換ができなければエラー
      messagebox.showerror(
        "エラー",
        "日時の形式が正しくありません。\nYYYYMMDDHHmm (例: 202608232245)"
        " で入力してください。",
      )
      return

    # 開始日時終了日時だったらエラー表示
    if start_dt > end_dt:
      messagebox.showerror(
        "エラー", "終了日時が開始日時より前です。"
      )
      return

    # ★ 【追加】期間が1日（24時間）以上の場合は確認メッセージを表示する
    if (end_dt - start_dt) >= timedelta(days=1):
      if not messagebox.askyesno(
        "確認 - 長期期間の処理",
        "指定された期間が1日以上です。\n"
        "ZIPファイルのダウンロードや処理に時間がかかる場合がありますが、実行しますか？"
      ):
        return

    # 日時を元にしたWINファイル等の出力先ディレクトリを作成(既にあればok)
    dest_dir = self.output_base / f"{start_str}_{end_str}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # --- キャッシュチェック (Smart Caching) ---
    # フォルダ内にSACファイルがあるかどうかを確認
    existing_sacs = win_pipeline.find_sacs(dest_dir)

    if existing_sacs:
      # 既存のSACファイルがある場合はそれを利用して波形を描画
      self.plot_waveforms(dest_dir, start_str, end_str, title_str, return_to)
      if not is_history_reload:
        # 履歴からの再表示でないときは履歴に追加
        self.add_history(start_str, end_str, title_str)
      return

    # --- Step 1: 抽出 (なければ実行) ---
    # フォルダ内に結合前のWINファイルがあるかどうかを確認
    # (キャッシュ整理で結合前のWINだけ消した場合などは、ZIPから取り出し直す)
    existing_wins = win_pipeline.find_raw_wins(dest_dir)

    if not existing_wins:
      extracted_count = self.extract_win_files(start_dt, end_dt, dest_dir)
      if extracted_count == 0:
        return

    # --- Step 2: 結合 ＆ SAC変換 ---
    success = self.convert_wins_to_sacs(dest_dir)
    if not success:
      return

    # --- 履歴に追加してプロット ---
    self.add_history(start_str, end_str, title_str)
    self.plot_waveforms(dest_dir, start_str, end_str, title_str, return_to)

  # スタック表示用の出力ディレクトリ(1時間ごと)
  #   dt ... その時間の開始日時(datetime)
  def stack_dest_dir(self, dt):
    return self.output_base / f"stack_{dt.strftime('%Y%m%d_%H')}"

  # 指定期間のSACファイルを用意する(キャッシュがあればそれを使う)
  #   start_dt, end_dt ... 期間
  #   dest_dir         ... WIN/SACの保存先
  #   show_dialog      ... 警告ダイアログを出すか(バッチ処理ではFalse)
  #   戻り値            ... 用意できたら True、失敗したら False
  def ensure_sacs(self, start_dt, end_dt, dest_dir, show_dialog=True):
    dest_dir.mkdir(parents=True, exist_ok=True)
    # すでにSACがあれば何もしない
    if win_pipeline.find_sacs(dest_dir):
      return True
    # 結合前のWINがなければZIPから抽出
    if not win_pipeline.find_raw_wins(dest_dir):
      if self.extract_win_files(start_dt, end_dt, dest_dir, show_dialog) == 0:
        return False
    # SACに変換
    return self.convert_wins_to_sacs(dest_dir, show_dialog)

  # 1時間スタック波形表示の実行
  def execute_stack_pipeline(self):
    date_str = self.entry_stack_date.get().strip()
    hour_str = self.entry_stack_hour.get().strip()
    channel_sel = self.channel_combo.get()

    # 入力チェック
    if not date_str.isdigit() or len(date_str) != 8:
      messagebox.showerror("エラー", "日付は YYYYMMDD 形式（例: 20260823）で入力してください。")
      return

    if not hour_str.isdigit() or not (0 <= int(hour_str) <= 23):
      messagebox.showerror("エラー", "時間は 0 〜 23 の数字で入力してください。")
      return

    if not channel_sel or "." not in channel_sel:
      messagebox.showerror("エラー", "チャンネルが正しく選択されていません。")
      return

    station_name, comp_name = channel_sel.split(".", 1)

    # 開始日時 (HH:00) と 終了日時 (HH+1:00) の文字列を作成
    start_dt_str = f"{date_str}{hour_str.zfill(2)}00"

    # 1時間後の日時を計算
    start_dt = self.parse_datetime(start_dt_str)
    if not start_dt:
      messagebox.showerror("エラー", "日時の解釈に失敗しました。")
      return
    end_dt = start_dt + timedelta(hours=1)

    log_message(f"スタック表示処理を開始します: {date_str} {hour_str}時台 ({channel_sel})")

    # 1時間分のSACファイルを用意(抽出・変換)
    if not self.ensure_sacs(start_dt, end_dt, self.stack_dest_dir(start_dt)):
      return

    # 用意できたSACから、1分ごとにスライスしてスタックプロット
    self.plot_stack_waveforms(start_dt, station_name, comp_name)

  # 1時間分のスタック波形を表示する
  def plot_stack_waveforms(self, start_dt, station_name, comp_name):
    # ツールバーから「保存」以外のボタン(ズーム・パン等)を外す。この窓の作成中だけ有効にする
    original_items = NavigationToolbar2Tk.toolitems
    NavigationToolbar2Tk.toolitems = [t for t in original_items if t[0] == "Save"]
    overlays = self.make_overlays()
    try:
      window = StackWindow(start_dt, station_name, comp_name,
                           load_stream=self.load_stack_stream,
                           channels=self.load_channel_options(),
                           log=log_message,
                           show_error=lambda msg: messagebox.showerror("エラー", msg),
                           on_compare=self.compare_from_stack,
                           overlays=overlays)
    finally:
      NavigationToolbar2Tk.toolitems = original_items
    window.show()

  # 1時間スタック画面の右クリックメニューから、指定期間の波形表示(全拠点)を開く
  #   start_dt      ... 開始日時(クリックした段の分)
  #   minutes       ... 表示する長さ(分)
  #   station, comp ... スタック画面で表示中の拠点名・成分(タイトル用)
  #   window        ... スタック画面のウィンドウ(波形表示を閉じたらここに戻る)
  def compare_from_stack(self, start_dt, minutes, station, comp, window):
    # 終了日時はその分も含むので、N分間なら開始 + (N-1)分
    end_dt = start_dt + timedelta(minutes=minutes - 1)
    # 入力欄に設定してから、通常の「波形表示」と同じ処理を実行する(履歴にも残る)
    for entry, value in ((self.entry_title, f"1時間スタックから ({station}.{comp})"),
                         (self.entry_start, start_dt.strftime("%Y%m%d%H%M")),
                         (self.entry_end, end_dt.strftime("%Y%m%d%H%M"))):
      entry.delete(0, tk.END)
      entry.insert(0, value)
    self.update_duration_display()
    log_message(f"スタック画面から比較表示: {start_dt.strftime('%Y/%m/%d %H:%M')} から {minutes}分間")
    self.execute_pipeline(return_to=window)

  # 指定した時間のSACを用意して、全チャンネルをまとめて読み込んで返す(失敗したら None)
  def load_stack_stream(self, dt):
    dest_dir = self.stack_dest_dir(dt)
    if not self.ensure_sacs(dt, dt + timedelta(hours=1), dest_dir):
      return None

    st = read(str(dest_dir / win_pipeline.SAC_PATTERN))
    if len(st) == 0:
      messagebox.showerror("エラー", "SACファイルを開けませんでした。")
      return None
    return st

  # 指定期間のWINファイルをZIPから抽出する(ローカルに無い日のZIPはGoogleドライブから取得)
  #   戻り値 ... 取り出したWINファイルの数(0なら失敗)
  def extract_win_files(self, start_dt, end_dt, dest_dir, show_dialog=True):
    # Zip保存ディレクトリがなければ作成
    self.zips_root.mkdir(parents=True, exist_ok=True)

    # 期間に含まれる日付(YYMMDD)
    dates = win_pipeline.target_dates(start_dt, end_dt)

    # 期間内の各日付について(古い日から順に)、ローカルになければGoogleドライブからダウンロードを試みる
    for d_str in sorted(dates):
      if not list(self.zips_root.glob(f"*{d_str}*.zip")):
        # （※ ドライブ上のファイル名が 「260826.zip」 のような形式である場合）
        zip_filename = f"{d_str}.zip"
        log_message(f"ローカルに存在しないため、Googleドライブから取得を試みます: {zip_filename}")
        try:
          downloaded = self.download_zip_from_drive(zip_filename)
        except DownloadCancelled:
          # 中止されたら、残りの日のダウンロードや抽出もやめる
          return 0
        if downloaded:
          log_message(f"ダウンロード完了: {zip_filename}")
        else:
          log_message(f"ドライブからの取得に失敗したか、ファイルが存在しませんでした: {zip_filename}")

    # 期間に該当するZipファイルが無ければエラー
    target_zips = win_pipeline.find_zips(self.zips_root, dates)
    if not target_zips:
      if show_dialog:
        messagebox.showwarning(
          "警告", "指定された期間に一致するZIPファイルが見つかりませんでした。"
        )
      return 0

    # 該当するWINファイルがひとつもなかったときはエラー
    extracted_count = win_pipeline.extract_wins_from_zips(target_zips, start_dt, end_dt, dest_dir)
    if extracted_count == 0:
      if show_dialog:
        messagebox.showwarning(
          "警告",
          "指定範囲内に該当するWINファイルが存在しませんでした（0件抽出）。",
        )
      return 0

    return extracted_count

  # WINファイルの結合とSACファイルへの変換処理
  #   戻り値 ... 成功したら True(拠点ごとの変換エラーはログに出して続行する)
  #   show_dialog ... エラーをダイアログで知らせるか(バッチ処理ではFalse。ログにだけ出す)
  def convert_wins_to_sacs(self, dest_dir, show_dialog=True):
    try:
      win_pipeline.convert_wins_to_sacs(dest_dir, self.tools_dir, self.etc_dir)
    except win_pipeline.PipelineError as e:
      if show_dialog:
        messagebox.showerror("エラー", str(e))
      else:
        log_message(f"     [エラー] {e}")
      return False
    return True

  # SACファイルの波形を描画
  #   dest_dir   ... SACファイルが格納されているフォルダ
  #   start_str  ... 開始日時(YYYYMMDDHHmm)
  #   end_str    ... 終了日時(YYYYMMDDHHmm)
  #   title_str  ... 波形上部に表示するタイトル(日本語表示可)
  #   return_to ... このウィンドウを閉じたときに戻るウィンドウ(Tk)。None ならOS任せ
  def plot_waveforms(self, dest_dir, start_str, end_str, title_str, return_to=None):
    # 出力フォルダにあるSACファイルをObspyですべて読み込み
    st = read(str(dest_dir / win_pipeline.SAC_PATTERN))
    # ファイルが存在しない、もしくは読み込めなかった場合はエラー
    if len(st) == 0:
      messagebox.showerror("エラー", "SACファイルを開くことができません。WINファイルからの変換に失敗した可能性があります。")
      return
    open_waveform_window(self.root, st, title_str, return_to)

  # Googleドライブから指定されたZIPファイルをダウンロードする
  #   戻り値 ... 成功したら True、失敗したら False
  #   中止ボタンで中止したときは DownloadCancelled を送出する(呼び出し側で残りの処理もやめる)
  def download_zip_from_drive(self, zip_filename):
    # キーファイルのパス
    creds_path = self.service_account_dir / "manten_usu_downloader.json"

    if not creds_path.exists():
      log_message(f"サービスアカウントのキーファイルが見つかりません: {creds_path}")
      return False

    # 進捗表示用の変数
    progress_var = tk.StringVar(value=f"Googleドライブからダウンロード中...\n\n{zip_filename}\n(0%)")
    # 中止ボタン(またはウィンドウを閉じる)で True にする
    cancel = {"requested": False}

    # --- 処理中ポップアップの表示 ---
    loading_win = tk.Toplevel(self.root)
    loading_win.title("通信中")
    loading_win.geometry("300x150")
    loading_win.resizable(False, False)
    # 親ウインドウの前面に固定し、操作を一時的にロックする
    loading_win.transient(self.root)
    loading_win.grab_set()

    tk.Label(
      loading_win,
      textvariable=progress_var,
      font=("sans-serif", 10),
      justify=tk.CENTER
    ).pack(expand=True, padx=10, pady=(10, 0))

    # 中止ボタン。押されたら次の区切り(CHUNK_SIZE ごと)でダウンロードを止める
    def request_cancel():
      cancel["requested"] = True
      btn_cancel.config(text="中止しています...", state=tk.DISABLED)
    btn_cancel = ttk.Button(loading_win, text="中止", command=request_cancel)
    btn_cancel.pack(pady=(0, 10))
    loading_win.protocol("WM_DELETE_WINDOW", request_cancel)

    # 描画を確実に反映させる
    loading_win.update()

    # 進捗率をポップアップに反映する(モジュールから呼ばれる)。中止ボタンの操作もここで受け付ける
    def on_progress(pct):
      progress_var.set(f"Googleドライブからのダウンロード中...\n\n{zip_filename}\n({pct}%)")
      loading_win.update()

    try:
      # フォルダIDが未取得なら、設定ファイルから読み直す
      if not self.target_folder_id:
        self.target_folder_id = load_folder_id(self.folder_config_file)

      download_file_from_drive(
        creds_path, self.target_folder_id, zip_filename,
        self.zips_root / zip_filename, on_progress,
        cancelled=lambda: cancel["requested"])
      return True
    except DownloadCancelled as e:
      log_message(str(e))
      raise
    except DriveFileNotFoundError as e:
      log_message(str(e))
      return False
    except Exception as e:
      log_message(f"Googleドライブからのダウンロード中にエラーが発生しました: {e}")
      return False
    finally:
      loading_win.destroy()   # 成功・失敗・中止どれでも必ずポップアップを閉じる

  # outputキャッシュの整理(削除するファイルの種類を選ぶ画面を開く。履歴ファイルは残す)
  def clean_output_cache(self):
    if not self.output_base.exists():
      messagebox.showinfo("情報", "outputディレクトリが存在しません。")
      return
    OutputCacheDialog(self.root, self.output_base)

  # daily_zipsの整理
  def clean_daily_zips(self):
    if not self.zips_root.exists():
      messagebox.showinfo("情報", "daily_zipsディレクトリが存在しません。")
      return

    # zipファイルのみを対象にする
    zip_files = cache_manager.collect_files(self.zips_root, "*.zip")
    if not zip_files:
      messagebox.showinfo("情報", "対象のZIPファイルが見つかりません。")
      return

    msg = (
      f"daily_zipsディレクトリ内のZIPファイルを削除します。\n\n"
      f"・ZIPファイル数: {len(zip_files):,} 件\n"
      f"・総容量: {cache_manager.format_size(cache_manager.total_size(zip_files))}\n\n"
      f"本当によろしいですか？"
    )

    if messagebox.askyesno("確認 - daily_zips整理", msg):
      try:
        cache_manager.delete_files(zip_files)
        messagebox.showinfo("完了", "ZIPファイルの整理が完了しました。")
      except Exception as e:
        messagebox.showerror("エラー", f"削除中にエラーが発生しました:\n{e}")

  @staticmethod
  def parse_datetime(dt_str):
    """YYYYMMDDHHmm形式の文字列をdatetimeオブジェクトに変換する

    引数:
        dt_str: 日時を表す文字列 (例: "202608232245")
    戻り値:
        変換成功時は datetime オブジェクト、失敗時は None
    """
    if not dt_str:
      return None
    try:
      return datetime.strptime(dt_str.strip(), "%Y%m%d%H%M")
    except (ValueError, TypeError):
      return None

  # 1時間スタック波形に重ねる表示(地震・列車)。画面とバッチ処理で共通
  #   時刻表は etc/train_timetable.csv(Git の管理外。無ければ列車の重ね表示は使えない)
  #   names ... 作るものの名前(None なら全部)
  def make_overlays(self, names=None):
    overlays = [QuakeOverlay(load_stations(self.etc_dir)),
                TrainOverlay(self.etc_dir / "train_timetable.csv",
                             lambda dt: self.load_hour_stream(dt, vertical_only=True))]
    return [ov for ov in overlays if names is None or ov.name in names]

  # --- 【新機能】バッチ処理実行メソッド（UIなしでの一括処理） ---
  #   quakes ... 地震(震源リストの到達見込み・地震の候補)を重ねる
  #   trains ... 列車(時刻表と対応した揺れ・貨物列車の候補)を重ねる(Bamba だけ)
  #   重ねた画像は、ファイル名の最後に _quakes・_trains を付けて、重ねない画像と別に保存する
  def run_batch_processing(self, channel, start_date=None, end_date=None, overwrite=False, quakes=False, trains=False):
    log_message(f"=== 1時間スタック波形バッチ処理を開始します ===")
    log_message(f"  - チャンネル: {channel}")
    log_message(f"  - 開始日: {start_date or '指定なし (全対象)'}")
    log_message(f"  - 終了日: {end_date or '指定なし (全対象)'}")
    overlay_names = [n for n, on in ((QuakeOverlay.name, quakes), (TrainOverlay.name, trains)) if on]
    overlays = self.make_overlays(overlay_names)
    suffix = ("_quakes" if quakes else "") + ("_trains" if trains else "")
    if overlays:
      log_message(f"  - 重ね表示: {'・'.join(overlay_names)}")

    if "." in channel:
      station_name, comp_name = channel.split(".", 1)
    else:
      station_name = "Hokudai"
      comp_name = channel

    self.zips_root.mkdir(parents=True, exist_ok=True)
    zip_files = sorted(list(self.zips_root.glob("**/*.zip")))

    if not zip_files:
      log_message("[エラー] daily_zips内にZIPファイルが見つかりません。")
      return

    target_zips = []
    for zpath in zip_files:
      stem = zpath.stem
      digits = re.findall(r'\d+', stem)
      if not digits:
        continue
      d_str_raw = digits[-1]
      if len(d_str_raw) == 6:
        full_date_str = f"20{d_str_raw}"
      elif len(d_str_raw) == 8:
        full_date_str = d_str_raw
      else:
        continue

      if start_date and full_date_str < start_date:
        continue
      if end_date and full_date_str > end_date:
        continue

      target_zips.append((full_date_str, zpath))

    if not target_zips:
      log_message("[警告] 指定された条件に一致するZIPファイルがありません。")
      return

    batch_output_dir = self.batch_output_dir
    batch_output_dir.mkdir(parents=True, exist_ok=True)

    for date_str, zpath in target_zips:
      log_message(f"\n[処理中] 日付: {date_str} (ZIP: {zpath.name})")
      for hour in range(24):
        hour_str = f"{hour:02d}"
        start_dt_str = f"{date_str}{hour_str}00"
        start_dt = self.parse_datetime(start_dt_str)
        if not start_dt:
          continue
        end_dt = start_dt + timedelta(hours=1)

        log_message(f"  -> {date_str} {hour_str}:00 枠を処理中...")
        dest_dir = self.output_base / f"stack_{date_str}_{hour_str}"
        dest_dir.mkdir(parents=True, exist_ok=True)

        # ★【追加】最終的な出力予定のPNG画像のパスを事前に定義
        out_img_path = batch_output_dir / f"stack_{date_str}_{hour_str}_{station_name}_{comp_name}{suffix}.png"

        # ★【追加】画像が既に存在し、かつ上書きモードでない（＝デフォルトのスキップ）場合はここで丸ごとスキップ
        if out_img_path.exists() and not overwrite:
          log_message(f"     [スキップ] 既に画像が存在します: {out_img_path.name}")
          continue

        # SACを用意(バッチ処理なのでダイアログは出さない)
        if not self.ensure_sacs(start_dt, end_dt, dest_dir, show_dialog=False):
          log_message("     [スキップ] WINファイルの抽出またはSAC変換に失敗しました。")
          continue

        try:
          st = read(str(dest_dir / win_pipeline.SAC_PATTERN))
          if len(st) == 0:
            continue

          target_trace = find_trace(st, station_name, comp_name)
          if target_trace is None:
            log_message(f"     [スキップ] チャンネル {channel} が見つかりませんでした。")
            continue

          plt.ioff()

          # 波形の描画
          stack = StackFigure(for_screen=False)
          stack.render(target_trace, start_dt, station_name, comp_name)
          if overlays:
            draw_on_image(stack, overlays, start_dt, station_name)
          fig = stack.fig

          # 画像を保存(150dpi)
          fig.savefig(out_img_path, dpi=150)

          plt.close(fig)
          log_message(f"     [保存完了] {out_img_path.name}")

        except Exception as e:
          log_message(f"     [エラー] プロット生成中にエラー: {e}")
          traceback.print_exc()

    log_message("\n=== バッチ処理がすべて完了しました ===")

  # 1時間分のSACを用意して読み込む(バッチ処理用。1時間スタック波形と同じフォルダを使う)
  #   vertical_only ... 上下動だけ読み込む
  #   戻り値 ... Stream、用意できなければ None
  def load_hour_stream(self, dt, vertical_only=False):
    dest_dir = self.stack_dest_dir(dt)
    if not self.ensure_sacs(dt, dt + timedelta(hours=1), dest_dir, show_dialog=False):
      return None
    st = Stream()
    for path in win_pipeline.find_sacs(dest_dir):
      if not vertical_only or ".U." in path.name:
        st += read(str(path))
    return st

  # --- 地震の候補を探すバッチ処理(UIなし) ---
  #   記録の期間から、3拠点以上が同時に大きく揺れたところを地震の候補として一覧にし、
  #   気象庁の震源リスト(jma_hypocenter.py で保存したもの)と照合する
  #   start_date, end_date ... 期間(YYYYMMDD)。省略すると daily_zips の最初・最後の日
  #   add_history ... 候補の期間を履歴(history.json)に追加する
  #   save_images ... 候補ごとに確認用の波形の画像を保存する
  #   overwrite   ... 画像を上書きする
  #   (保存した RMS を計算し直すときは、output/quake_rms を削除する)
  def run_quake_detection(self, start_date=None, end_date=None, add_history=False, save_images=False,
                          overwrite=False):
    zip_days = jma_hypocenter.zip_days()
    try:
      start_day = datetime.strptime(start_date, "%Y%m%d") if start_date else zip_days[0]
      end_day = datetime.strptime(end_date, "%Y%m%d") if end_date else zip_days[-1]
    except (ValueError, IndexError):
      log_message("[エラー] 期間を -s / -e で YYYYMMDD の形式で指定してください(daily_zips にZIPが無いときは必須)。")
      return
    if end_day < start_day:
      log_message("[エラー] 終了日が開始日より前です。")
      return
    days = [start_day + timedelta(days=i) for i in range((end_day - start_day).days + 1)]
    period = f"{start_day:%Y%m%d}_{end_day:%Y%m%d}"
    log_message("=== 地震の候補を探すバッチ処理を開始します ===")
    log_message(f"  - 期間: {start_day:%Y-%m-%d}〜{end_day:%Y-%m-%d}({len(days)}日)")
    log_message(f"  - 基準: {quake_detect.QUAKE_BAND[0]:g}〜{quake_detect.QUAKE_BAND[1]:g} Hz の上下動で、"
                f"{quake_detect.QUAKE_MIN_STATIONS}拠点以上が雑音の{quake_detect.QUAKE_RATIO:g}倍超"
                f"(または {quake_detect.HOKUDAI} が{quake_detect.HOKUDAI_RATIO:g}倍以上で、"
                f"ほかの{quake_detect.HOKUDAI_OTHERS}拠点以上が{quake_detect.HOKUDAI_OTHERS_RATIO:g}倍以上)")

    # --- 1. 日ごとの RMS(保存したものがあれば使う) ---
    stations = load_stations(self.etc_dir)
    names = sorted(stations)
    rms = {nm: [] for nm in names}
    local_days = set(zip_days)
    for day in days:
      values = quake_detect.load_rms_cache(day)
      cached = values is not None
      if not cached and day not in local_days:
        # 観測していない日などに、1時間ごとにGoogleドライブへ問い合わせ続けないよう、ZIPが手元に無い日は飛ばす
        log_message(f"  {day:%Y-%m-%d}: daily_zips にZIPが無いため飛ばします")
        values = {}
      elif not cached:
        hours = [(day + timedelta(hours=h), self.load_hour_stream(day + timedelta(hours=h), vertical_only=True))
                 for h in range(24)]
        values = quake_detect.day_rms(day, quake_detect.vertical_traces(quake_detect.merge_hours(hours)))
        if values:
          quake_detect.save_rms_cache(day, values)
      present = [nm for nm in names if nm in values and np.isfinite(values[nm]).any()]
      if values or cached:
        log_message(f"  {day:%Y-%m-%d}: データのある拠点 {len(present)}({' '.join(present) or 'なし'})"
                    + ("  [保存した RMS]" if cached else ""))
      for nm in names:
        rms[nm].append(values.get(nm, np.full(quake_detect.WINDOWS_PER_DAY, np.nan)))
    rms = {nm: np.concatenate(v) for nm, v in rms.items()}

    # --- 2. 地震の候補 ---
    candidates, info = quake_detect.find_candidates(start_day, rms)
    log_message(f"地震の候補: {len(candidates)}件(うち Hokudai の例外で見つけたもの "
                f"{sum(1 for c in candidates if c['rule'] == quake_detect.RULE_HOKUDAI)}件)")

    # --- 3. 気象庁の震源リストとの照合 ---
    period_end = end_day + timedelta(days=1)
    catalog, missing = jma_match.load_catalog(start_day, period_end, jma_match.array_center(stations))
    if missing:
      log_message(f"  気象庁の震源リストが無い日: {len(missing)}日(scripts/jma_hypocenter.py で取得できます)")
    jma_match.match_candidates(candidates, catalog)
    missed = jma_match.find_missed(catalog, lambda t0, t1: quake_detect.ratios_between(info, t0, t1),
                                   quake_detect.QUAKE_MIN_STATIONS, start_day, period_end)
    matched = sum(1 for c in candidates if c["match"])
    log_message(f"  震源リストと対応した候補: {matched}件、該当なし: {len(candidates) - matched}件")
    log_message(f"  見込みの振幅が大きいのに候補が無い地震(見逃し?): {len(missed)}件")
    for cand in candidates:
      log_message(f"    {quake_detect.candidate_label(cand)}")

    out_dir = quake_detect.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 4. 候補ごとの波形の解析(有珠山の地震らしさ)と確認用の画像 ---
    loaded = {}   # 読み込んだ時間の波形(直近のものだけ残す)
    def window_stream(t0, t1):
      first = t0.replace(minute=0, second=0, microsecond=0)
      hours, h = [], first
      while h <= t1:
        if h not in loaded:
          loaded[h] = self.load_hour_stream(h)
        hours.append((h, loaded[h]))
        h += timedelta(hours=1)
      for old in [k for k in loaded if k < first]:
        del loaded[old]
      return quake_detect.merge_hours(hours)

    log_message("候補ごとの波形を解析しています")
    for cand in candidates:
      t0 = cand["start"] - timedelta(seconds=40)
      t1 = min(cand["end"] + timedelta(seconds=40), cand["start"] + timedelta(minutes=10))
      st = window_stream(t0, t1)
      try:
        cand["features"], cand["picks"] = quake_detect.analyze_candidate(st, t0, t1, stations)
      except Exception as e:
        log_message(f"  [エラー] 波形を解析できませんでした({cand['start']:%Y-%m-%d %H:%M:%S}): {e}")
        cand["features"], cand["picks"] = None, {}
      # 有珠山の地震らしさは、震源リストに無い候補と、震源リストで有珠山の地震とされたものに付ける
      if not cand["match"]:
        cand["usu"] = quake_features.usu_likeness(cand["features"]) if cand["features"] else "低"
      elif quake_features.is_usu_quake(cand["match"]):
        cand["usu"] = quake_features.USU_CONFIRMED
      if not save_images:
        continue
      path = out_dir / f"{cand['start']:%Y%m%d_%H%M%S}.png"
      cand["image"] = path.name
      if path.exists() and not overwrite:
        continue
      traces = quake_detect.vertical_traces(st)
      if not traces:
        continue
      try:
        quake_detect.save_candidate_image(path, cand, traces, cand["match"], quake_detect.candidate_title(cand))
      except Exception as e:
        log_message(f"  [エラー] 画像を保存できませんでした({path.name}): {e}")
        traceback.print_exc()
    usu_counts = {k: sum(1 for c in candidates if c.get("usu") == k) for k in ("高", "中", "低")}
    log_message(f"  震源リストで有珠山の地震とされたもの: "
                f"{sum(1 for c in candidates if c.get('usu') == quake_features.USU_CONFIRMED)}件")
    log_message("  震源リストに無い候補の有珠山の地震らしさ: " + "、".join(f"{k} {v}件" for k, v in usu_counts.items()))

    if save_images:
      # 見逃した地震も、到達見込みの前後の波形を画像にする(ファイル名は missed_ で始める)
      for q, available, ratios in missed:
        path = out_dir / f"missed_{q['日時']:%Y%m%d_%H%M%S}.png"
        q["image"] = path.name
        if path.exists() and not overwrite:
          continue
        pseudo = {"start": q["p_time"], "end": q["s_time"] + timedelta(seconds=60), "ratios": ratios}
        t0 = q["p_time"] - timedelta(seconds=40)
        traces = quake_detect.vertical_traces(window_stream(t0, min(pseudo["end"], q["p_time"] + timedelta(minutes=10))
                                                            + timedelta(seconds=40)))
        if traces:
          quake_detect.save_candidate_image(path, pseudo, traces, q, quake_detect.missed_title(q, available, ratios),
                                            missed=True)

      # 種類ごとにPDFにまとめる
      groups = [("matched", "照合できたもの", [c for c in candidates if c["match"]]),
                ("usu_high", "有珠山の地震(震源リストで確定)・らしさ 高",
                 [c for c in candidates if c.get("usu") in (quake_features.USU_CONFIRMED, "高")]),
                ("usu_mid", "有珠山の地震らしさ 中", [c for c in candidates if c.get("usu") == "中"]),
                ("missed", "見逃し?", [q for q, _, _ in missed])]
      for key, label, items in groups:
        pdf_path = out_dir / f"quakes_{period}_{key}.pdf"
        if quake_detect.save_pdf(pdf_path, [out_dir / it["image"] for it in items if it.get("image")]):
          log_message(f"  PDF({label} {len(items)}件): {pdf_path.name}")
        elif pdf_path.exists():
          pdf_path.unlink()   # 該当が無くなったときは、前回のPDFを残さない
      log_message(f"確認用の画像: {out_dir}")

    # --- 5. 一覧 ---
    csv_path = out_dir / f"quake_candidates_{period}.csv"
    quake_detect.write_candidates_csv(csv_path, candidates, names)
    log_message(f"候補の一覧: {csv_path}")
    missed_path = out_dir / f"jma_missed_{period}.csv"
    quake_detect.write_missed_csv(missed_path, missed, names)
    log_message(f"見逃し?の一覧: {missed_path}")

    # --- 6. 履歴(指定期間の波形表示で、あとから見られるようにする) ---
    if add_history and candidates:
      entries = []
      for cand in candidates:
        start = (cand["start"] - timedelta(seconds=30)).replace(second=0, microsecond=0)
        end = max((cand["end"] + timedelta(seconds=30)).replace(second=0, microsecond=0),
                  start + timedelta(minutes=1))
        entries.append({"start": start.strftime("%Y%m%d%H%M"), "end": end.strftime("%Y%m%d%H%M"),
                        "title": quake_detect.candidate_label(cand)})
      added, dropped = self.add_history_entries(entries, quake_detect.LABEL_PREFIX)
      log_message(f"履歴に {added}件を追加しました" +
                  (f"(同じ期間を表示した履歴がある {len(entries) - added}件は、そちらを残しました)"
                   if added < len(entries) else "") +
                  (f"(上限 {self.MAX_HISTORY}件を超えたため、古い履歴 {dropped}件を削除)" if dropped else ""))

    log_message("\n=== 地震の候補を探すバッチ処理が完了しました ===")

  # --- 地震の候補の震源を画面に表示する ---
  #   候補の一覧(--detect-quakes の quake_candidates_*.csv。「候補」の列があれば印の付いた行だけ)を選び、
  #   バッチ処理(--locate-quakes)と同じ方法で震源を求めて、表と地図・断面図の画面を開く
  def open_quake_locations(self):
    path = filedialog.askopenfilename(
      parent=self.root, title="地震の候補の一覧(CSV)を選んでください",
      initialdir=str(quake_detect.OUTPUT_DIR if quake_detect.OUTPUT_DIR.exists() else self.batch_output_dir),
      filetypes=[("CSV ファイル", "*.csv"), ("すべてのファイル", "*.*")])
    if not path:
      return
    path = Path(path)
    candidates = quake_locate.read_candidates(path)
    if not candidates:
      messagebox.showwarning("警告", "一覧に地震の候補がありません(「開始」の列が必要です)。")
      return
    # 1件あたり約0.3秒かかる(初めて読む時間は、SACファイルの準備でさらにかかる)
    if len(candidates) > 50 and not messagebox.askyesno(
        "確認", f"候補が {len(candidates)}件あります。計算に1分以上かかることがありますが、続けますか?"):
      return
    stations = load_stations(self.etc_dir)
    proj = LocalProjection(list(stations.values()))

    # 進み具合の表示(計算中は画面の操作を止める)
    progress = tk.Toplevel(self.root)
    progress.title("計算中")
    progress.geometry("320x110")
    progress.resizable(False, False)
    progress.transient(self.root)
    progress.grab_set()
    progress_var = tk.StringVar(value=f"震源を計算しています... 0 / {len(candidates)}")
    tk.Label(progress, textvariable=progress_var, font=("sans-serif", 10)).pack(expand=True)
    progress.update()

    def on_result(i, n, cand, r, error):
      if error is not None:
        log_message(f"[エラー] {cand['start']:%Y-%m-%d %H:%M:%S}: {error}")
      progress_var.set(f"震源を計算しています... {i} / {n}")
      progress.update()
    try:
      items = quake_locate.locate_all(candidates, self.load_hour_stream, stations, proj, on_result)
    finally:
      progress.destroy()
    log_message(f"地震の候補の震源({path.name}): {quake_locate.count_summary(items)}")
    if not items:
      messagebox.showwarning("警告", "震源を計算できた候補がありませんでした。")
      return

    # 候補の前後の波形(震源推定の画面と、求め直すときに使う)
    def event_stream(cand):
      t0, t1 = quake_locate.candidate_window(cand)
      st = quake_locate.window_stream(t0, t1, self.load_hour_stream, {}).slice(
        obspy_time(t0), obspy_time(t1)).copy()
      for tr in st:
        if np.ma.isMaskedArray(tr.data):
          tr.data = tr.data.filled(0)
      return Stream([tr for tr in st if tr.stats.npts > 0 and np.any(tr.data)])

    # 候補の震源推定の画面を開く(同じ範囲の波形。遠い地震は S波を探す範囲を広げる)
    #   画面で「保存して一覧に反映」を押すと、保存した到着時刻から震源を求め直して、一覧の行と図を更新する(apply)
    def open_event(cand, r, apply):
      st = event_stream(cand)
      if len({tr.stats.station for tr in st}) < 3:
        messagebox.showwarning("警告", "3拠点以上の波形がありません。")
        return
      def on_apply(_path):
        apply(quake_locate.locate_candidate(st, cand, stations, proj))
      plot_hypocenter(st, overrides={"s_search": r.get("s_search", quake_locate.LOCAL_S_SEARCH)}, on_apply=on_apply)

    # 保存した到着時刻を消して、自動の検出で求め直す
    def revert(cand, r):
      path = pick_store.PICKS_DIR / r["manual"]
      if path.exists():
        path.unlink()
        log_message(f"保存した到着時刻を消しました: {path}")
      return quake_locate.locate_candidate(event_stream(cand), cand, stations, proj)

    # 一覧(CSV)と地図・断面図を保存する(バッチ処理と同じ場所・名前)
    def save():
      out_dir = quake_locate.OUTPUT_DIR
      out_dir.mkdir(parents=True, exist_ok=True)
      out_csv = out_dir / f"{path.stem}_locations.csv"
      out_png = out_dir / f"{path.stem}_locations.png"
      quake_locate.write_csv(out_csv, items, sorted(stations))
      quake_locate.save_location_map(out_png, items, stations, proj, quake_locate.map_title(path.stem, items))
      log_message(f"保存しました: {out_csv} / {out_png}")
      messagebox.showinfo("保存", f"保存しました。\n\n{out_csv}\n{out_png}")

    open_locate_window(self.root, items, stations, proj, path.stem, open_event, save, revert)

  # --- 地震の候補の震源を求めるバッチ処理(UIなし) ---
  #   csv_path    ... 地震の候補の一覧(--detect-quakes の quake_candidates_*.csv。「候補」の列があれば印の付いた行だけ)
  #   save_images ... 候補ごとに確認用の画像(P波・S波の位置)も保存する
  #   出力: batch_plots/quakes/locate/<一覧の名前>_locations.csv と _locations.png(震央の地図と断面図)
  def run_quake_location(self, csv_path, save_images=False):
    csv_path = Path(csv_path)
    log_message("=== 地震の候補の震源を求めるバッチ処理を開始します ===")
    log_message(f"  - 候補の一覧: {csv_path}")
    if not csv_path.exists():
      log_message("[エラー] 候補の一覧が見つかりません。")
      return
    candidates = quake_locate.read_candidates(csv_path)
    log_message(f"  - 候補: {len(candidates)}件")
    if not candidates:
      return
    stations = load_stations(self.etc_dir)
    names = sorted(stations)
    proj = LocalProjection(list(stations.values()))
    out_dir = quake_locate.OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = csv_path.stem

    def on_result(_i, _n, cand, r, error):
      if error is not None:
        log_message(f"  [エラー] {cand['start']:%Y-%m-%d %H:%M:%S}: {error}")
        return
      log_message("  " + quake_locate.check_title(cand, r).replace("\n", " / "))
      if save_images:
        path = out_dir / f"{stem}_{cand['start']:%Y%m%d_%H%M%S}.png"
        if quake_locate.save_check_image(path, cand, r, quake_locate.check_title(cand, r)):
          r["image"] = path.name
    items = quake_locate.locate_all(candidates, self.load_hour_stream, stations, proj, on_result)
    log_message("結果: " + quake_locate.count_summary(items))
    out_csv = out_dir / f"{stem}_locations.csv"
    quake_locate.write_csv(out_csv, items, names)
    log_message(f"一覧: {out_csv}")
    out_png = out_dir / f"{stem}_locations.png"
    quake_locate.save_location_map(out_png, items, stations, proj, quake_locate.map_title(stem, items))
    log_message(f"地図・断面図: {out_png}")
    if save_images:
      log_message(f"確認用の画像: {out_dir}")
    log_message("\n=== 地震の候補の震源を求めるバッチ処理が完了しました ===")

# メインルーチン
def main():
  # 日本語フォント設定
  setup_japanese_font()

  # コマンドラインオプションの取得
  parser = argparse.ArgumentParser(description="有珠満点地震計プロジェクト 波形ビューワ / バッチ処理ツール")
  parser.add_argument("--batch", action="store_true", help="UIを起動せず、バッチ処理モードで実行する")
  parser.add_argument("--detect-quakes", action="store_true",
                      help="UIを起動せず、地震の候補を探して気象庁の震源リストと照合する (期間は -s / -e)")
  parser.add_argument("--locate-quakes", metavar="CSV",
                      help="UIを起動せず、地震の候補の一覧(CSV)から候補ごとに震源を求める")
  parser.add_argument("--history", action="store_true", help="地震の候補の期間を履歴に追加する (--detect-quakes 用)")
  parser.add_argument("--images", action="store_true",
                      help="地震の候補の確認用の画像を保存する (--detect-quakes・--locate-quakes 用)")
  parser.add_argument("--channel", "-c", type=str, default="Hokudai.U", help="対象チャンネル名 (例: Hokudai.U または U)")
  parser.add_argument("--start-date", "-s", type=str, help="バッチ処理の開始日 (YYYYMMDD形式)")
  parser.add_argument("--end-date", "-e", type=str, help="バッチ処理の終了日 (YYYYMMDD形式)")
  parser.add_argument("--quakes", action="store_true",
                      help="--batch の画像に、地震(震源リストの到達見込み・地震の候補)を重ねる")
  parser.add_argument("--trains", action="store_true",
                      help="--batch の画像に、列車(時刻表と対応した揺れ・貨物列車の候補)を重ねる(Bamba だけ)")
  parser.add_argument("--overwrite", action="store_true",
                      help="既存の画像を上書きする (デフォルトはスキップ)")
  parser.add_argument("--verbose", "-v", action="store_true", help="画面のログをコンソールにも表示する (バッチ処理モードでは常に表示)")

  args = parser.parse_args()

  # コンソールへのログ表示(バッチ処理モードは画面が無いので常に表示)
  set_console(args.verbose or args.batch or args.detect_quakes or args.locate_quakes)

  # GUIのウィンドウを生成
  root = tk.Tk()
  # MantenUsuSeismographApp クラスのインスタンスを生成
  app = MantenUsuSeismographApp(root)

  if args.locate_quakes:
    root.withdraw() # GUIウィンドウを非表示にする
    app.run_quake_location(args.locate_quakes, save_images=args.images)
    root.destroy()
  elif args.detect_quakes:
    root.withdraw() # GUIウィンドウを非表示にする
    app.run_quake_detection(
      start_date=args.start_date,
      end_date=args.end_date,
      add_history=args.history,
      save_images=args.images,
      overwrite=args.overwrite
    )
    root.destroy()
  elif args.batch:
    root.withdraw() # GUIウィンドウを非表示にする
    app.run_batch_processing(
      channel=args.channel,
      start_date=args.start_date,
      end_date=args.end_date,
      overwrite=args.overwrite,
      quakes=args.quakes,
      trains=args.trains
    )
    root.destroy()
  else:
    root.mainloop()  # イベントループ処理

# コマンドを実行されたときは main() を呼び出す
if __name__ == "__main__":
  main()