# maintenance_window.py
# メンテナンス画面(メイン画面の「メンテナンス」ボタンで開く)
#   気象庁の震源リスト ... 取得の状況と、不足分の取得・期間を指定した取り直し(jma_hypocenter.py と同じ処理)
#   キャッシュ         ... output フォルダ(cache_dialog.py)と daily_zips フォルダの整理
#   ほかの機関の観測点 ... v-net フォルダの ZIP の一覧と、変換済みの SAC(output/vnet)の変換・削除(external_stations.py)
# 取得・変換は時間がかかるので別スレッドで行い、画面は0.1秒ごとに進み具合を表示する
# 別スレッドからは画面に触れないので、メッセージはいったん溜め、画面側で実行ログに書く
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
from cache_dialog import OutputCacheDialog
from log_common import log_message

# 取得していない日を、状況の欄に並べる上限
MAX_MISSING_SHOWN = 5


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
  # on_change ... 震源リストを取得したあとに呼ぶ関数(メイン画面の表示を更新する)
  def __init__(self, parent, on_change=None):
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

    # --- 気象庁の震源リスト ---
    jma = ttk.LabelFrame(frame, text="気象庁の震源リスト", padding=10)
    jma.pack(fill=tk.X)
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

    # --- キャッシュ ---
    cache = ttk.LabelFrame(frame, text="キャッシュ", padding=10)
    cache.pack(fill=tk.X, pady=(10, 0))
    row = ttk.Frame(cache)
    row.pack(fill=tk.X)
    self.add_button(row, "出力データキャッシュ整理", self.clean_output_cache).pack(side=tk.LEFT, fill=tk.X, expand=True)
    self.add_button(row, "波形Zipファイル整理", self.clean_daily_zips).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
    ttk.Label(cache, foreground="#666666", justify=tk.LEFT,
              text="出力データキャッシュ整理では、保存した到着時刻(picks・そのバックアップ)、地震の候補の RMS(quake_rms)、\n"
                   "ほかの機関の観測点の SAC(vnet)は消しません。"
              ).pack(anchor="w", pady=(8, 0))

    # --- ほかの機関の観測点 ---
    ext = ttk.LabelFrame(frame, text="ほかの機関の観測点(v-net フォルダの ZIP)", padding=10)
    ext.pack(fill=tk.X, pady=(10, 0))
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
        self.result = work(self.messages.append, lambda: self.cancel,
                           lambda n: setattr(self, "progress", n))
      except Exception as e:
        self.error = e
    self.label, self.total, self.progress = label, total, 0
    self.result, self.error, self.cancel = None, None, False
    for w in self.busy_widgets:
      w.config(state=tk.DISABLED)
    self.btn_close.config(text="中止")
    self.thread = threading.Thread(target=target, daemon=True)
    self.thread.start()
    self.poll(on_done)

  # 溜まったメッセージを実行ログに書く
  def flush_messages(self):
    while self.messages:
      log_message(self.messages.pop(0))

  # 別スレッドの進み具合を0.1秒ごとに表示し、終わったら on_done を呼ぶ
  def poll(self, on_done):
    self.flush_messages()
    if not self.win.winfo_exists():
      return
    if self.thread.is_alive():
      self.lbl_status.config(text=f"{self.label}... {self.progress} / {self.total}"
                                  + ("(中止しています)" if self.cancel else ""))
      self.win.after(100, self.poll, on_done)
      return
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
