# maintenance_window.py
# メンテナンス画面(メイン画面の「メンテナンス」ボタンで開く)
#   気象庁の震源リスト ... 取得の状況と、不足分の取得・期間を指定した取り直し(jma_hypocenter.py と同じ処理)
#   地震の候補の検出   ... 揺れの大きさ(RMS)の保存の状況と、地震の候補を探す処理(--detect-quakes と同じ処理)
#   キャッシュ         ... output フォルダ(cache_dialog.py)と daily_zips フォルダの整理
#   ほかの機関の観測点 ... v-net フォルダの ZIP の一覧と、変換済みの SAC(output/vnet)の変換・削除(external_stations.py)
# 取得・変換は時間がかかるので別スレッドで行い、画面は0.1秒ごとに進み具合を表示する
# 別スレッドからは画面に触れないので、メッセージはいったん溜め、画面側で実行ログに書く
#   (log_message を別スレッドから呼んだときも、log_common が溜めておき、画面側の flush_pending で書く)
import gc
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

import app_paths
import cache_manager
import external_stations
import jma_hypocenter
import quake_detect
from cache_dialog import OutputCacheDialog
from log_common import log_message, flush_pending, add_log_widget, remove_log_widget

# 取得していない日を、状況の欄に並べる上限
MAX_MISSING_SHOWN = 5
# 別スレッドの処理中に、画面側で循環参照のごみを片付ける間隔 [回](進み具合の表示 0.1秒ごとの回数)
GC_EVERY = 10


# フォルダやファイルを OS の標準のアプリで開く
def open_path(path):
  if sys.platform == "darwin":
    subprocess.run(["open", str(path)])
  elif sys.platform == "win32":
    os.startfile(str(path))
  else:
    subprocess.run(["xdg-open", str(path)])

# 日付の範囲の文字列(続いている日はまとめる)。例: 2026-11-01〜2026-11-02、2026-08-05
def format_days(days):
  spans = []
  for d in days:
    if spans and (d - spans[-1][1]).days == 1:
      spans[-1][1] = d
    else:
      spans.append([d, d])
  texts = [f"{a:%Y-%m-%d}" if a == b else f"{a:%Y-%m-%d}〜{b:%Y-%m-%d}" for a, b in spans]
  if len(texts) > MAX_MISSING_SHOWN:
    texts = texts[:MAX_MISSING_SHOWN] + [f"ほか {len(texts) - MAX_MISSING_SHOWN}か所"]
  return "、".join(texts)


class MaintenanceWindow:
  # parent    ... 親ウィンドウ
  # app       ... メイン画面(MantenUsuSeismographApp。地震の候補を探す処理と、履歴への追加に使う)
  # on_change ... 震源リストを取得したあとに呼ぶ関数(メイン画面の表示を更新する)
  def __init__(self, parent, app=None, on_change=None):
    self.app = app
    self.on_change = on_change
    # 別スレッドとの受け渡し(スレッド側は書き込むだけ、画面側が読んで表示する)
    self.messages = []     # 実行ログに書くメッセージ
    self.progress = 0      # 済んだ件数
    self.total = 0         # 全体の件数
    self.result = None     # 処理の戻り値
    self.error = None      # 別スレッドで起きたエラー
    self.cancel = False    # 中止の指示
    self.thread = None

    win = tk.Toplevel(parent)
    self.win = win
    win.title("メンテナンス")
    win.resizable(False, False)
    win.transient(parent)
    win.protocol("WM_DELETE_WINDOW", self.close)

    frame = ttk.Frame(win, padding=15)
    frame.pack(fill=tk.BOTH, expand=True)
    self.busy_widgets = []   # 処理中は押せなくするボタン

    # 項目ごとのタブ(画面の小さいパソコンでも、下の実行ログと進み具合が隠れないように)
    notebook = ttk.Notebook(frame)
    notebook.pack(fill=tk.X)
    def page(title):
      tab = ttk.Frame(notebook, padding=12)
      notebook.add(tab, text=title)
      return tab

    # --- 気象庁の震源リスト ---
    jma = page("震源リスト")
    ttk.Label(jma, text="気象庁の震源リスト", font=("sans-serif", 11, "bold")).pack(anchor="w", pady=(0, 6))
    self.lbl_jma = ttk.Label(jma, text="", justify=tk.LEFT)
    self.lbl_jma.pack(anchor="w")
    row = ttk.Frame(jma)
    row.pack(fill=tk.X, pady=(8, 0))
    self.add_button(row, "不足分を取得", self.fetch_missing).pack(side=tk.LEFT)
    ttk.Label(row, text="　期間を指定(YYYYMMDD):").pack(side=tk.LEFT)
    self.entry_from = ttk.Entry(row, width=10)
    self.entry_from.pack(side=tk.LEFT)
    ttk.Label(row, text="〜").pack(side=tk.LEFT)
    self.entry_to = ttk.Entry(row, width=10)
    self.entry_to.pack(side=tk.LEFT)
    self.add_button(row, "取り直す", self.fetch_range).pack(side=tk.LEFT, padx=(5, 0))
    ttk.Label(jma, foreground="#666666", justify=tk.LEFT,
              text="不足分の取得では、取得済みの最後の日も、暫定値の更新を確かめるため取り直します。\n"
                   "期間の終わりを空けると、始めの1日だけ取り直します。前回との違いは実行ログに表示します。\n"
                   f"気象庁のサーバーに負担をかけないよう、1日ごとに {jma_hypocenter.REQUEST_INTERVAL:g}秒あけて取得します。"
              ).pack(anchor="w", pady=(8, 0))

    # --- 地震の候補の検出 ---
    det = page("地震の候補の検出")
    ttk.Label(det, text="地震の候補の検出(--detect-quakes と同じ処理)", font=("sans-serif", 11, "bold")).pack(anchor="w", pady=(0, 6))
    self.lbl_detect = ttk.Label(det, text="", justify=tk.LEFT)
    self.lbl_detect.pack(anchor="w")
    row = ttk.Frame(det)
    row.pack(fill=tk.X, pady=(8, 0))
    ttk.Label(row, text="期間(YYYYMMDD):").pack(side=tk.LEFT)
    self.entry_det_from = ttk.Entry(row, width=10)
    self.entry_det_from.pack(side=tk.LEFT)
    ttk.Label(row, text="〜").pack(side=tk.LEFT)
    self.entry_det_to = ttk.Entry(row, width=10)
    self.entry_det_to.pack(side=tk.LEFT)
    self.add_button(row, "地震の候補を探す", self.detect_quakes).pack(side=tk.LEFT, padx=(5, 0))
    row = ttk.Frame(det)
    row.pack(fill=tk.X, pady=(6, 0))
    self.var_images = tk.BooleanVar(value=False)
    self.var_history = tk.BooleanVar(value=False)
    self.var_overwrite = tk.BooleanVar(value=False)
    for text, var in (("確認用の画像・PDF も保存", self.var_images), ("候補を履歴に追加", self.var_history),
                      ("画像を上書き", self.var_overwrite)):
      chk = ttk.Checkbutton(row, text=text, variable=var)
      chk.pack(side=tk.LEFT, padx=(0, 10))
      self.busy_widgets.append(chk)
    # 外部の観測点で確かめる60分の一覧(quake_loose.py)
    self.var_vnet = tk.BooleanVar(value=False)
    chk = ttk.Checkbutton(det, variable=self.var_vnet,
                          text="外部の観測点で確かめるため、v-net からダウンロードする60分の一覧も作る(列車の判定に波形を読みます)")
    chk.pack(anchor="w", pady=(4, 0))
    self.busy_widgets.append(chk)
    ttk.Label(det, foreground="#666666", justify=tk.LEFT,
              text="期間を空けると daily_zips の最初の日から最後の日までです。RMS を保存済みの日はそれを使い、\n"
                   "まだの日だけ波形から計算します(1日あたり数十秒〜数分)。daily_zips に ZIP が無い日は飛ばします。\n"
                   "結果(候補の一覧・見逃し?の一覧)は batch_plots/quakes に保存し、1時間スタック画面の「地震」でも使います。\n"
                   "ダウンロードする60分の一覧は vnet_download_<期間>.csv(v-net フォルダに取得済みの時間は「済」)、\n"
                   "取得済みの候補は vnet_locate_<期間>.csv(「候補の一覧(CSV)を選んで震源を表示」で開けます)です。"
              ).pack(anchor="w", pady=(8, 0))
    if app is None:
      for w in self.busy_widgets[-5:]:
        w.config(state=tk.DISABLED)

    # --- キャッシュ ---
    cache = page("キャッシュ")
    ttk.Label(cache, text="キャッシュの整理", font=("sans-serif", 11, "bold")).pack(anchor="w", pady=(0, 6))
    row = ttk.Frame(cache)
    row.pack(fill=tk.X)
    self.add_button(row, "出力データキャッシュ整理", self.clean_output_cache).pack(side=tk.LEFT, fill=tk.X, expand=True)
    self.add_button(row, "波形Zipファイル整理", self.clean_daily_zips).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
    ttk.Label(cache, foreground="#666666", justify=tk.LEFT,
              text="出力データキャッシュ整理では、保存した到着時刻(picks・そのバックアップ)、地震の候補の RMS(quake_rms)、\n"
                   "ほかの機関の観測点の SAC(vnet)は消しません。"
              ).pack(anchor="w", pady=(8, 0))

    # --- ほかの機関の観測点 ---
    ext = page("ほかの機関の観測点")
    ttk.Label(ext, text="ほかの機関の観測点(v-net フォルダの ZIP)", font=("sans-serif", 11, "bold")).pack(anchor="w", pady=(0, 6))
    self.lbl_stations = ttk.Label(ext, text="", justify=tk.LEFT)
    self.lbl_stations.pack(anchor="w")
    columns = (("zip", "ZIP", 230), ("start", "開始", 120), ("minutes", "分", 40),
               ("size", "大きさ", 70), ("stations", "変換済みの観測点", 160))
    tree_frame = ttk.Frame(ext)
    tree_frame.pack(fill=tk.X, pady=(6, 0))
    self.tree = ttk.Treeview(tree_frame, columns=[c[0] for c in columns], show="headings", height=8)
    for key, label, width in columns:
      self.tree.heading(key, text=label)
      self.tree.column(key, width=width, anchor="w")
    scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
    self.tree.configure(yscrollcommand=scroll.set)
    self.tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
    scroll.pack(side=tk.LEFT, fill=tk.Y)
    self.lbl_cache = ttk.Label(ext, text="")
    self.lbl_cache.pack(anchor="w", pady=(6, 0))
    row = ttk.Frame(ext)
    row.pack(fill=tk.X, pady=(6, 0))
    self.add_button(row, "未変換の ZIP を変換", self.convert_zips).pack(side=tk.LEFT)
    self.add_button(row, "変換済みの SAC を消す", self.clear_vnet_cache).pack(side=tk.LEFT, padx=(5, 0))
    ttk.Button(row, text="観測点の設定を開く",
               command=lambda: open_path(external_stations.CONFIG_FILE)).pack(side=tk.RIGHT)
    ttk.Button(row, text="v-net フォルダを開く", command=self.open_data_dir).pack(side=tk.RIGHT, padx=(0, 5))
    ttk.Label(ext, foreground="#666666", justify=tk.LEFT,
              text="ZIP は防災科研のページなどからダウンロードし、v-net フォルダにそのまま置きます。\n"
                   "変換は、波形を表示するときにも自動で行います(観測点の設定を変えたときは、変換済みの SAC を消してください)。"
              ).pack(anchor="w", pady=(8, 0))

    # --- 実行ログ(メイン画面の実行ログと同じもの。メイン画面が隠れていても見えるように) ---
    ttk.Label(frame, text="実行ログ:").pack(anchor="w", pady=(10, 2))
    log_frame = ttk.Frame(frame)
    log_frame.pack(fill=tk.BOTH, expand=True)
    self.log_text = tk.Text(log_frame, height=8, width=90, font=("sans-serif", 9), bg="#f9f9f9", fg="#333333",
                            relief="solid", bd=1, state=tk.DISABLED)
    log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
    self.log_text.configure(yscrollcommand=log_scroll.set)
    self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    log_scroll.pack(side=tk.LEFT, fill=tk.Y)
    add_log_widget(self.log_text)

    # --- 進み具合・閉じる ---
    bottom = ttk.Frame(frame)
    bottom.pack(fill=tk.X, pady=(12, 0))
    self.lbl_status = ttk.Label(bottom, text="", foreground="#0066cc")
    self.lbl_status.pack(side=tk.LEFT)
    self.btn_close = ttk.Button(bottom, text="閉じる", command=self.close)
    self.btn_close.pack(side=tk.RIGHT)

    self.refresh()

  # 処理中は押せなくするボタンを作る
  def add_button(self, parent, text, command):
    btn = ttk.Button(parent, text=text, command=command)
    self.busy_widgets.append(btn)
    return btn

  # 状況の表示を更新する
  def refresh(self):
    s = jma_hypocenter.summary()
    if s["last"] is None:
      text = "取得済みの日はありません"
    else:
      text = f"取得済み: {s['saved']:,}日({s['first']:%Y-%m-%d}〜{s['last']:%Y-%m-%d})"
    if s["missing"]:
      text += f"\n取得していない日: {len(s['missing']):,}日({format_days(s['missing'])})"
    else:
      text += "\n昨日まで取得済みです"
    self.lbl_jma.config(text=text)

    zip_days = set(jma_hypocenter.zip_days())
    rms_days = quake_detect.rms_cache_days()
    todo = sorted(zip_days - set(rms_days))
    if rms_days:
      text = f"RMS を保存済み: {len(rms_days):,}日({rms_days[0]:%Y-%m-%d}〜{rms_days[-1]:%Y-%m-%d})"
    else:
      text = "RMS を保存済みの日はありません(1時間スタック画面の「地震」で候補の帯が出ません)"
    if todo:
      text += f"\ndaily_zips にあって、まだ計算していない日: {len(todo):,}日({format_days(todo)})"
    else:
      text += "\ndaily_zips の日はすべて計算済みです"
    self.lbl_detect.config(text=text)

    config = external_stations.load_config()
    names = "・".join(f"{n}({st['code']})" for n, st in config.items()) or "なし"
    self.lbl_stations.config(text=f"設定の観測点: {names}")
    self.tree.delete(*self.tree.get_children())
    for r in external_stations.zip_status():
      stations = "・".join(r["stations"]) if r["converted"] else "未変換"
      self.tree.insert("", tk.END, values=(
        r["path"].name, f"{r['start']:%Y-%m-%d %H:%M}", int((r["end"] - r["start"]).total_seconds() // 60),
        cache_manager.format_size(r["size"]), stations))
    count, size = external_stations.cache_size()
    self.lbl_cache.config(text=f"変換済みの SAC など(output/vnet): {count:,} 件 / {cache_manager.format_size(size)}")

  # --- 別スレッドの処理 ---
  # work(log, cancelled, on_progress) を別スレッドで実行し、終わったら画面側で on_done(戻り値) を呼ぶ
  def run_in_thread(self, label, total, work, on_done):
    def target():
      try:
        self.result = work(self.messages.append, lambda: self.cancel, self.set_progress)
      except Exception as e:
        self.error = e
    self.label, self.total, self.progress, self.stage = label, total, 0, None
    self.result, self.error, self.cancel = None, None, False
    for w in self.busy_widgets:
      w.config(state=tk.DISABLED)
    self.btn_close.config(text="中止")
    # 別スレッドの処理中は、自動のごみ集め(循環参照の片付け)を止め、画面側のスレッドで GC_EVERY 回ごとに行う
    #   止めないと、閉じた画面の部品(matplotlib のツールバーの画像や変数など)が別スレッドで片付けられ、
    #   そこから Tcl を呼んでしまう。Tcl は別スレッドから呼べないので、画面が消えたり落ちたりする
    #   (「main thread is not in main loop」「Tcl interpreter is leaked」が出る)
    gc.collect()
    gc.disable()
    self.polls = 0
    self.thread = threading.Thread(target=target, daemon=True)
    self.thread.start()
    self.poll(on_done)

  # 進み具合(済んだ数。段階のある処理は (何番目, 全体の数, 今していること)。数の無い段階は total=0)
  def set_progress(self, done, total=None, stage=None):
    self.progress = done
    if total is not None:
      self.total = total
    self.stage = stage

  # 進み具合の表示。段階の文字の最後の「(日)」などは、数の単位として数の後ろに付ける
  #   例: 地震の候補を探す処理: 候補ごとの波形を解析しています 12 / 215 件目
  def status_text(self):
    if self.stage is None:
      return f"{self.label}... {self.progress} / {self.total}"
    stage, unit = self.stage, ""
    if stage.endswith(")") and "(" in stage:
      stage, unit = stage[:-1].rsplit("(", 1)
    count = f" {self.progress} / {self.total} {unit}".rstrip() if self.total else "..."
    return f"{self.label}: {stage}{count}"

  # 溜まったメッセージを実行ログに書く
  def flush_messages(self):
    flush_pending()
    while self.messages:
      log_message(self.messages.pop(0))

  # 別スレッドの進み具合を0.1秒ごとに表示し、終わったら on_done を呼ぶ
  def poll(self, on_done):
    self.flush_messages()
    if not self.win.winfo_exists():
      gc.enable()
      return
    if self.thread.is_alive():
      self.lbl_status.config(text=self.status_text() + ("(中止しています)" if self.cancel else ""))
      self.polls += 1
      if self.polls % GC_EVERY == 0:
        gc.collect()
      self.win.after(100, self.poll, on_done)
      return
    gc.enable()
    for w in self.busy_widgets:
      w.config(state=tk.NORMAL)
    self.btn_close.config(text="閉じる")
    self.lbl_status.config(text="")
    if self.error is not None:
      log_message(f"{self.label}でエラーが発生しました: {self.error}")
      messagebox.showerror("エラー", f"{self.label}でエラーが発生しました:\n{self.error}", parent=self.win)
    else:
      on_done(self.result)
    self.refresh()

  # 閉じる(処理中なら中止を指示する。区切りのよいところで止まる)
  def close(self):
    if self.thread is not None and self.thread.is_alive():
      self.cancel = True
      return
    remove_log_widget(self.log_text)
    self.win.destroy()

  # --- 気象庁の震源リスト ---
  def fetch_missing(self):
    try:
      days, todo, last_saved = jma_hypocenter.plan_days()
    except ValueError as e:
      messagebox.showinfo("情報", str(e), parent=self.win)
      return
    self.start_fetch(days, todo, last_saved)

  def fetch_range(self):
    try:
      start = datetime.strptime(self.entry_from.get().strip(), "%Y%m%d")
      end_text = self.entry_to.get().strip()
      end = datetime.strptime(end_text, "%Y%m%d") if end_text else None
    except ValueError:
      messagebox.showerror("入力エラー", "期間は YYYYMMDD の形で入力してください(例: 20260820)", parent=self.win)
      return
    if end is not None and end < start:
      messagebox.showerror("入力エラー", "期間の終わりが始めより前になっています", parent=self.win)
      return
    if end is not None and end > jma_hypocenter.yesterday():
      messagebox.showerror("入力エラー", "震源リストは昨日の分までです", parent=self.win)
      return
    days, todo, last_saved = jma_hypocenter.plan_days(start, end, overwrite=True)
    self.start_fetch(days, todo, last_saved)

  def start_fetch(self, days, todo, last_saved):
    if not todo:
      messagebox.showinfo("情報", "取得する日はありません", parent=self.win)
      return
    log_message("気象庁の震源リスト: " + jma_hypocenter.describe_plan(days, todo, last_saved))
    self.run_in_thread("震源リストの取得", len(todo),
                       lambda log, cancelled, progress: jma_hypocenter.fetch_days(todo, log, cancelled, progress),
                       self.on_fetched)

  def on_fetched(self, result):
    log_message(f"気象庁の震源リスト: 保存 {result['done']}日、取得できなかった日 {result['failed']}日、"
                f"前回と違った日 {result['changed']}日" + ("(中止しました)" if self.cancel else ""))
    if self.on_change:
      self.on_change()

  # --- 地震の候補の検出 ---
  def detect_quakes(self):
    texts = [self.entry_det_from.get().strip(), self.entry_det_to.get().strip()]
    try:
      start, end = [datetime.strptime(t, "%Y%m%d") if t else None for t in texts]
    except ValueError:
      messagebox.showerror("入力エラー", "期間は YYYYMMDD の形で入力してください(例: 20260820)", parent=self.win)
      return
    if start and end and end < start:
      messagebox.showerror("入力エラー", "期間の終わりが始めより前になっています", parent=self.win)
      return
    if not jma_hypocenter.zip_days() and not (start and end):
      messagebox.showinfo("情報", "daily_zips に ZIP が無いため、期間を指定してください", parent=self.win)
      return
    add_history = self.var_history.get()
    args = dict(start_date=texts[0] or None, end_date=texts[1] or None, save_images=self.var_images.get(),
                overwrite=self.var_overwrite.get(), loose=self.var_vnet.get())

    def work(log, cancelled, progress):
      # 別スレッドではダウンロードの画面を出せないので、手元の ZIP だけを使う
      self.app.allow_download = False
      try:
        return self.app.run_quake_detection(cancelled=cancelled, on_progress=progress, **args)
      finally:
        self.app.allow_download = True

    def on_done(candidates):
      if candidates is not None and add_history:
        self.app.add_candidates_to_history(candidates)
    self.run_in_thread("地震の候補を探す処理", 0, work, on_done)

  # --- キャッシュ ---
  # output フォルダの整理(削除するファイルの種類を選ぶ画面を開く。履歴ファイルは残す)
  def clean_output_cache(self):
    if not app_paths.OUTPUT_DIR.exists():
      messagebox.showinfo("情報", "outputディレクトリが存在しません。", parent=self.win)
      return
    OutputCacheDialog(self.win, app_paths.OUTPUT_DIR)

  # daily_zips フォルダの整理
  def clean_daily_zips(self):
    zips_root = app_paths.ZIPS_DIR
    if not zips_root.exists():
      messagebox.showinfo("情報", "daily_zipsディレクトリが存在しません。", parent=self.win)
      return
    # zipファイルのみを対象にする
    zip_files = cache_manager.collect_files(zips_root, "*.zip")
    if not zip_files:
      messagebox.showinfo("情報", "対象のZIPファイルが見つかりません。", parent=self.win)
      return
    msg = (
      f"daily_zipsディレクトリ内のZIPファイルを削除します。\n\n"
      f"・ZIPファイル数: {len(zip_files):,} 件\n"
      f"・総容量: {cache_manager.format_size(cache_manager.total_size(zip_files))}\n\n"
      f"本当によろしいですか？"
    )
    if messagebox.askyesno("確認 - daily_zips整理", msg, parent=self.win):
      try:
        cache_manager.delete_files(zip_files)
        messagebox.showinfo("完了", "ZIPファイルの整理が完了しました。", parent=self.win)
      except Exception as e:
        messagebox.showerror("エラー", f"削除中にエラーが発生しました:\n{e}", parent=self.win)

  # --- ほかの機関の観測点 ---
  def open_data_dir(self):
    external_stations.DATA_DIR.mkdir(parents=True, exist_ok=True)
    open_path(external_stations.DATA_DIR)

  def convert_zips(self):
    todo = [r for r in external_stations.zip_status() if not r["converted"]]
    if not todo:
      messagebox.showinfo("情報", "未変換の ZIP はありません", parent=self.win)
      return
    log_message(f"ほかの機関の観測点: 未変換の ZIP {len(todo)}件を変換します")
    self.run_in_thread("ZIP の変換", len(todo), external_stations.convert_all,
                       lambda n: log_message(f"ほかの機関の観測点: {n}件を変換しました"
                                             + ("(中止しました)" if self.cancel else "")))

  def clear_vnet_cache(self):
    count, size = external_stations.cache_size()
    if not count:
      messagebox.showinfo("情報", "変換済みの SAC はありません", parent=self.win)
      return
    if not messagebox.askyesno(
      "確認", f"変換済みの SAC など(output/vnet)を削除します。\n\n合計: {count:,} 件 / {cache_manager.format_size(size)}\n\n"
              "ZIP は残るので、波形を表示するときに変換し直します。よろしいですか？", parent=self.win):
      return
    external_stations.clear_cache()
    log_message(f"ほかの機関の観測点: 変換済みの SAC など {count:,} 件を削除しました")
    self.refresh()
