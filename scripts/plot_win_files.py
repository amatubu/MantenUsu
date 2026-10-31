# 必要なライブラリのインポート
import argparse                           # 入力引数の処理
from datetime import datetime, timedelta  # 日付・時刻の処理
import json
import os
from pathlib import Path
import tkinter as tk                      # GUIツール
from tkinter import messagebox, ttk       # GUIの拡張ツール(ttk = Themed Tkinter)
import warnings
import traceback
import re

from obspy import Stream, read         # SACファイルの波形表示
import matplotlib.pyplot as plt        # グラフ表示ツール
from matplotlib.widgets import Button
from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk, FigureCanvasTkAgg

from ps_picker import plot_ps_arrival_time
from jma_intensity import analyze_jma_scale
from plot_common import NM_TO_CM, get_component_color, setup_japanese_font
from log_common import log_message, set_console, set_log_widget
import win_pipeline
import cache_manager
from cache_dialog import OutputCacheDialog
from stack_plot import StackFigure, StackWindow, find_trace
from drive_downloader import load_folder_id, download_file_from_drive, DriveFileNotFoundError

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
# 2. 4拠点のグラフ表示を2x2のグリッド表示させる
# 4. Googleドライブからのダウンロード中に中止処理を追加
# [完了]5. log_messageのprintへの出力用にデバッグオプション(コマンドラインオプション)を追加する
# [完了]6. 震度の推測
# 7. WINファイル、SACファイルのキャッシュ処理の最適化(フルにキャッシュする、最小限にする(バッチ高速処理用)のオプションを用意するなど)
# [完了]8. 1時間ごとのグラフ画面で、拠点・チャンネルの切り替え機能を追加
# [完了]9. キャッシュ整理処理で、WINファイルのみ、結合前WINファイルのみなどのオプション追加
# [実験中]10. P波、S波の到着時刻計算
# 11. 震源位置、地震発生時刻の推測

# クラス定義
class MantenUsuSeismographApp:
  # バージョン情報
  APP_VERSION = "2.2.0"

  # クラスの初期化
  def __init__(self, root):
    # 表示する波形の時刻等の入力や履歴表示等を行うUIを構築する
    self.root = root
    # ウィンドウタイトル
    self.root.title(f"有珠満点地震計プロジェクト 波形ビューワ v{self.APP_VERSION}")
    # ウィンドウサイズ
    self.root.geometry("460x640")
    # リサイズを許可しない
    self.root.resizable(False, False)

    # パス設定
    # スクリプトの親ディレクトリの絶対パス
    self.base_dir = Path(__file__).parent.resolve()
    
    # WINファイル・SACファイルなどの出力先
    self.output_base = self.base_dir.parent / "output"
    # 日ごとのZipファイルの格納場所
    self.zips_root = self.base_dir.parent / "daily_zips"
    # win2sac_32の格納場所
    self.tools_dir = self.base_dir.parent / "tools"
    # win2sac_32が使用するチャンネル定義ファイル等の格納場所
    self.etc_dir = self.base_dir.parent / "etc"
    # Googleドライブのサービスアカウントファイルの格納場所
    self.service_account_dir = self.base_dir.parent / "service_account"
    # GoogleドライブのフォルダIDの定義ファイル(JSON)
    self.folder_config_file = self.service_account_dir / "folder_config.json"
    self.target_folder_id = self.target_folder_id = load_folder_id(self.folder_config_file)
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
    # 履歴が50件以上になったら古いものから削除
    history = history[:50]
    # 履歴ファイルへ保存
    self.save_history_data(history)
    # 履歴のプルダウンを更新
    self.update_history_ui()

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
    return self.convert_wins_to_sacs(dest_dir)

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
    try:
      window = StackWindow(start_dt, station_name, comp_name,
                           load_stream=self.load_stack_stream,
                           channels=self.load_channel_options(),
                           log=log_message,
                           show_error=lambda msg: messagebox.showerror("エラー", msg),
                           on_compare=self.compare_from_stack)
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

    # 期間内の各日付について、ローカルになければGoogleドライブからダウンロードを試みる
    for d_str in dates:
      if not list(self.zips_root.glob(f"*{d_str}*.zip")):
        # （※ ドライブ上のファイル名が 「260826.zip」 のような形式である場合）
        zip_filename = f"{d_str}.zip"
        log_message(f"ローカルに存在しないため、Googleドライブから取得を試みます: {zip_filename}")
        if self.download_zip_from_drive(zip_filename):
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
  def convert_wins_to_sacs(self, dest_dir):
    try:
      win_pipeline.convert_wins_to_sacs(dest_dir, self.tools_dir, self.etc_dir)
    except win_pipeline.PipelineError as e:
      messagebox.showerror("エラー", str(e))
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

    # チャンネルの並び順
    # U(上下)、N(南北)、E(東西)の順に並び替える
    desired_order = ["U", "N", "E"]
    # 並び替え後のリストを得る
    ordered_st = Stream(
        sorted(
            st,  # 並び替えたいStreamオブジェクト
            key=lambda tr: (  # st からひとつひとつ取り出して tr に格納し、繰り返す
                tr.stats.station,  # 拠点名
                desired_order.index(tr.stats.channel[-1])  # チャンネルの並び順を末尾の文字から設定
                if tr.stats.channel[-1] in desired_order
                else 99,  # U、N、E 以外だったら 99番にして最後に並べる
            ),
        )
    )

    # 波形データの数
    num_traces = len(ordered_st)
    # グラフの横幅(インチ単位)
    fig_width = 11.5
    # グラフの縦の高さ(インチ単位)
    # 最低6インチ、波形データの数に応じて1インチずつ拡張
    fig_height = max(6, num_traces * 1.0)

    # 波形をプロットする領域を準備する
    #   fig  ... 波形を描くための全体領域
    #   axes ... 個々の波形を描くグラフ部分
    fig, axes = plt.subplots(
        num_traces,                         # 縦に波形の数だけグリッドに分割
        1,                                  # 横は分割しない
        sharex = True,                      # 横(時間)軸を同期させる
        figsize = (fig_width, fig_height),  # 描画領域のサイズ
        gridspec_kw = {"hspace": 0},        # グリッド間の縦の隙間を0にする(ぴったりくっつける)
    )
    # 波形がひとつだけの場合でも軸をリストにしておく
    if num_traces == 1:
      axes = [axes]

    # 描画領域の周りの余白を調整
    fig.subplots_adjust(
      left   = 0.09,  # 左: 9%(Y軸のラベルの幅を確保)
      right  = 0.97,  # 右: 3%(狭め)
      bottom = 0.08,  # 下: 8%(X軸の時間軸ラベルの高さを確保)
      top    = 0.92   # 上: 8%(タイトルの高さを確保)
    )

    # 波形データ(ordered_st)と描画領域(axes)をzipでペアにしてループさせる
    for tr, ax in zip(ordered_st, axes):
      # 時間軸データ(横軸)の取得
      times = tr.times()
      # 波形の実データ(nm/s -> cm/s に変換)
      data = tr.data * NM_TO_CM
      # チャンネル名
      ch = tr.stats.channel
      # チャンネル名を元に色を決定(U、N、E以外はデフォルト色)
      col = get_component_color(ch)

      # 波形を描画する
      ax.plot(
        times,           # 横軸(時間軸データ)
        data,            # 波形の実データ
        color = col,     # 色
        linewidth = 0.4  # 線の太さ(単位はpt)
      )
      # 拠点名
      station_name = tr.stats.station

      # Y(振幅)軸のラベルを定義
      ax.set_ylabel(
          f"{station_name}\n({ch})\n[cm/s]",  # ラベルの内容
          fontsize = 8,               # 文字サイズ
          rotation = 90,              # 90度回転(反時計周り)
          ha = "center",              # 水平方向中揃え(horizontal alignment)
          va = "center",              # 垂直方向中揃え(vertical alignment)
      )
      # Y軸のラベル位置を調整(グラフ外(左)の中央へ)
      ax.yaxis.set_label_coords(-0.06, 0.5)

      # Y軸の目盛(数値)サイズ
      ax.tick_params(
        axis = "y",    # Y軸を指定
        labelsize = 7  # 目盛の文字サイズ
      )

      # グリッド線の表示
      ax.grid(
        True,              # グリッドを表示
        linestyle = "--",  # 破線
        alpha = 0.3        # 不透明度 30%
      )

      # 波形の下のラベル(X軸)を省略(重ならないように)
      ax.tick_params(labelbottom=False)

    # 一番下の波形にだけ下のラベル(X軸)を表示
    axes[-1].tick_params(labelbottom=True)
    # ラベルの表示内容と文字サイズ
    axes[-1].set_xlabel("Time [s]", fontsize=11)

    # ストリーム全体の開始・終了時刻を取得する場合の例
    # （先頭のトレースから開始時刻、最後のトレースから終了時刻を取得）
    start_time = ordered_st[0].stats.starttime.strftime("%Y/%m/%d %H:%M")
    end_time = ordered_st[-1].stats.endtime.strftime("%Y/%m/%d %H:%M")

    # 波形の上に表示するタイトル
    title_display = f"{start_time} - {end_time}"
    if title_str:
      title_display += f" : {title_str}"
    plt.suptitle(
        f"有珠・満点地震計波形 ({title_display}) - 波形数: {num_traces}",
        fontsize=13,
    )

    # --- 描画用ウィンドウの作成 ---
    wave_win = tk.Toplevel(self.root)
    # ウィンドウのタイトル(拠点名と期間)
    title_stations = ", ".join(dict.fromkeys(tr.stats.station for tr in ordered_st))
    wave_win.title(f"波形表示 - {title_stations} ({start_time} - {end_time})")

    # Pyplotのグローバル管理からこのfigを外す（他のplt.show()に巻き込まれなくする）
    plt.close(fig)

    # 【重要】ウィンドウが閉じられたときにMatplotlibのリソースを解放して確実に閉じる
    def on_close():
      plt.close(fig)      # Matplotlibのグラフをメモリから破棄する
      wave_win.destroy()  # ウィンドウを破棄する

      # 戻り先があれば前面に出す。戻り先(Matplotlibのウィンドウ)はメインとは別の Tk なので、
      # 閉じた直後に Mac がメインウィンドウを前面にする。その後にもう一度前面に出し直す
      if return_to is not None:
        def bring_back():
          try:
            if return_to.winfo_exists():
              return_to.lift()
              return_to.focus_force()
          except tk.TclError:
            pass  # 戻り先のウィンドウが既に閉じられている
        bring_back()
        self.root.after(100, bring_back)

    wave_win.protocol("WM_DELETE_WINDOW", on_close)

    # 1. ウィンドウ内のキャンバスを取得
    canvas = FigureCanvasTkAgg(fig, master=wave_win)
    canvas.draw()

    # 2. 下部にコントロール用フレームを配置する
    control_frame = ttk.Frame(wave_win, padding=10)
    control_frame.pack(side=tk.BOTTOM, fill=tk.X)

    # 【追加】もともとあった虫めがねや保存などのツールバーをここに追加！
    toolbar_frame = ttk.Frame(wave_win)
    toolbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
    toolbar = NavigationToolbar2Tk(canvas, toolbar_frame)
    toolbar.update()

    # 3. 残り部分にグラフを埋め込む
    canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # 読み込まれたデータから重複のない拠点名リストを作成
    stations = sorted(list(set(tr.stats.station for tr in ordered_st)))

    # 拠点名のドロップダウン（コンボボックス）
    ttk.Label(control_frame, text="拠点名:").pack(side=tk.LEFT, padx=(0, 5))
    station_combobox = ttk.Combobox(control_frame, values=stations, state="readonly", width=15)
    if stations:
        station_combobox.set(stations[0])  # 初期選択
    station_combobox.pack(side=tk.LEFT, padx=(0, 10))

    # 「震度推定」ボタンが押されたときの処理
    def on_jma_scale():
      selected_station = station_combobox.get()
      if not selected_station:
        messagebox.showwarning("警告", "拠点名が選択されていません。")
        return

      station_st = ordered_st.select(station=selected_station)

      # 該当拠点のデータが空でないかチェック
      if len(station_st) == 0:
        messagebox.showerror("エラー", f"拠点 [{selected_station}] の波形データが見つかりません。")
        return

      # 震度推定メソッドを呼ぶ
      analyze_jma_scale(station_st)

    # 「震度推定」ボタン
    btn_measure_jma_scale = ttk.Button(control_frame, text="震度推定", command=on_jma_scale)
    btn_measure_jma_scale.pack(side=tk.LEFT, padx=(0, 10))

    # 「P波・S波検出」ボタンが押されたときの処理
    def on_ps_analyze():
      selected_station = station_combobox.get()
      if not selected_station:
        messagebox.showwarning("警告", "拠点名が選択されていません。")
        return

      station_st = ordered_st.select(station=selected_station)

      # 該当拠点のデータが空でないかチェック
      if len(station_st) == 0:
        messagebox.showerror("エラー", f"拠点 [{selected_station}] の波形データが見つかりません。")
        return

      # P波・S波検出メソッドを呼ぶ
      plot_ps_arrival_time(station_st)

    # 「P波・S波検出」ボタン
    btn_analyze_ps_arrival = ttk.Button(control_frame, text="P波・S波検出", command=on_ps_analyze)
    btn_analyze_ps_arrival.pack(side=tk.LEFT, padx=(0, 10))

  # Googleドライブから指定されたZIPファイルをダウンロードする
  def download_zip_from_drive(self, zip_filename):
    # キーファイルのパス
    creds_path = self.service_account_dir / "manten_usu_downloader.json"

    if not creds_path.exists():
      log_message(f"サービスアカウントのキーファイルが見つかりません: {creds_path}")
      return False

    # 進捗表示用の変数
    progress_var = tk.StringVar(value=f"Googleドライブからダウンロード中...\n\n{zip_filename}\n(0%)")

    # --- 処理中ポップアップの表示 ---
    loading_win = tk.Toplevel(self.root)
    loading_win.title("通信中")
    loading_win.geometry("300x120")
    loading_win.resizable(False, False)
    # 親ウインドウの前面に固定し、操作を一時的にロックする
    loading_win.transient(self.root)
    loading_win.grab_set()

    tk.Label(
        loading_win,
        textvariable=progress_var,
        font=("sans-serif", 10),
        justify=tk.CENTER
    ).pack(expand=True, padx=10, pady=10)

    # 描画を確実に反映させる
    loading_win.update()

    # 進捗率をポップアップに反映する(モジュールから呼ばれる)
    def on_progress(pct):
      progress_var.set(f"Googleドライブからのダウンロード中...\n\n{zip_filename}\n({pct}%)")
      loading_win.update()

    try:
      # フォルダIDが未取得なら、設定ファイルから読み直す
      if not self.target_folder_id:
        self.target_folder_id = load_folder_id(self.folder_config_file)

      download_file_from_drive(
        creds_path, self.target_folder_id, zip_filename,
        self.zips_root / zip_filename, on_progress)
      return True
    except DriveFileNotFoundError as e:
      log_message(str(e))
      return False
    except Exception as e:
      log_message(f"Googleドライブからのダウンロード中にエラーが発生しました: {e}")
      return False
    finally:
      loading_win.destroy()   # 成功・失敗どちらでも必ずポップアップを閉じる

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

  # --- 【新機能】バッチ処理実行メソッド（UIなしでの一括処理） ---
  def run_batch_processing(self, channel, start_date=None, end_date=None, overwrite=False):
    log_message(f"=== 1時間スタック波形バッチ処理を開始します ===")
    log_message(f"  - チャンネル: {channel}")
    log_message(f"  - 開始日: {start_date or '指定なし (全対象)'}")
    log_message(f"  - 終了日: {end_date or '指定なし (全対象)'}")

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

    batch_output_dir = self.base_dir.parent / "batch_plots"
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
        out_img_path = batch_output_dir / f"stack_{date_str}_{hour_str}_{station_name}_{comp_name}.png"

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
          fig = stack.fig

          # 画像を保存(150dpi)
          fig.savefig(out_img_path, dpi=150)

          plt.close(fig)
          log_message(f"     [保存完了] {out_img_path.name}")

        except Exception as e:
          log_message(f"     [エラー] プロット生成中にエラー: {e}")
          traceback.print_exc()

    log_message("\n=== バッチ処理がすべて完了しました ===")

# メインルーチン
def main():
  # 日本語フォント設定
  setup_japanese_font()

  # コマンドラインオプションの取得
  parser = argparse.ArgumentParser(description="有珠満点地震計プロジェクト 波形ビューワ / バッチ処理ツール")
  parser.add_argument("--batch", action="store_true", help="UIを起動せず、バッチ処理モードで実行する")
  parser.add_argument("--channel", "-c", type=str, default="Hokudai.U", help="対象チャンネル名 (例: Hokudai.U または U)")
  parser.add_argument("--start-date", "-s", type=str, help="テスト用開始日 (YYYYMMDD形式)")
  parser.add_argument("--end-date", "-e", type=str, help="テスト用終了日 (YYYYMMDD形式)")
  parser.add_argument("--overwrite", action="store_true", help="既存の画像を上書きする (デフォルトはスキップ)")
  parser.add_argument("--verbose", "-v", action="store_true", help="画面のログをコンソールにも表示する (バッチ処理モードでは常に表示)")

  args = parser.parse_args()

  # コンソールへのログ表示(バッチ処理モードは画面が無いので常に表示)
  set_console(args.verbose or args.batch)

  # GUIのウィンドウを生成
  root = tk.Tk()
  # MantenUsuSeismographApp クラスのインスタンスを生成
  app = MantenUsuSeismographApp(root)

  if args.batch:
    root.withdraw() # GUIウィンドウを非表示にする
    app.run_batch_processing(
        channel=args.channel,
        start_date=args.start_date,
        end_date=args.end_date,
        overwrite=args.overwrite
    )
    root.destroy()
  else:
    root.mainloop()  # イベントループ処理

# コマンドを実行されたときは main() を呼び出す
if __name__ == "__main__":
  main()