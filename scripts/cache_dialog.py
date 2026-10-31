# cache_dialog.py
# output キャッシュの整理画面(削除するファイルの種類を選ぶ)
# 集計・削除は時間がかかるので別スレッドで行い、画面は0.1秒ごとに進み具合を表示する
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import cache_manager
from log_common import log_message

# 最初にチェックを入れておく種類(ZIPから作り直せるWINファイル。SACは表示に使うので残す)
DEFAULT_KINDS = {"raw_win", "merged_win"}


class OutputCacheDialog:
  # parent      ... 親ウィンドウ
  # output_base ... output フォルダ
  def __init__(self, parent, output_base):
    self.output_base = output_base
    # 別スレッドとの受け渡し(スレッド側は書き込むだけ、画面側が読んで表示する)
    self.progress = 0          # 集計・削除したファイル数
    self.stats = None          # 集計結果 {種類: [ファイル数, 合計サイズ]}
    self.result = None         # 削除したファイル数
    self.error = None          # 別スレッドで起きたエラー
    self.cancel = False        # 中止の指示
    self.thread = None

    win = tk.Toplevel(parent)
    self.win = win
    win.title("出力データキャッシュ整理")
    win.resizable(False, False)
    win.transient(parent)
    win.protocol("WM_DELETE_WINDOW", self.close)

    frame = ttk.Frame(win, padding=15)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(frame, text="削除するファイルの種類を選んでください（履歴は残ります）").pack(anchor="w")

    # 種類ごとのチェックボックス(集計が終わるまでは押せない)
    self.vars = {}
    self.checks = {}
    for key, label in cache_manager.OUTPUT_KINDS:
      var = tk.BooleanVar(value=key in DEFAULT_KINDS)
      chk = ttk.Checkbutton(frame, text=label, variable=var, state=tk.DISABLED,
                            command=self.update_total)
      chk.pack(anchor="w", pady=(8, 0))
      self.vars[key] = var
      self.checks[key] = chk

    ttk.Label(frame, text="WINファイルもSACファイルも無くなったフォルダは、フォルダごと削除します。",
              foreground="#666666").pack(anchor="w", pady=(10, 0))

    # 進み具合・選択中の合計
    self.status = ttk.Label(frame, text="集計中...", foreground="#0066cc")
    self.status.pack(anchor="w", pady=(10, 0))

    buttons = ttk.Frame(frame)
    buttons.pack(fill=tk.X, pady=(12, 0))
    self.btn_close = ttk.Button(buttons, text="閉じる", command=self.close)
    self.btn_close.pack(side=tk.RIGHT)
    self.btn_delete = ttk.Button(buttons, text="削除", command=self.start_delete, state=tk.DISABLED)
    self.btn_delete.pack(side=tk.RIGHT, padx=(0, 8))

    self.run_in_thread(self.scan, self.on_scanned)

  # 処理を別スレッドで実行し、終わったら画面側で on_done を呼ぶ
  def run_in_thread(self, work, on_done):
    def target():
      try:
        work()
      except Exception as e:
        self.error = e
    self.error = None
    self.thread = threading.Thread(target=target, daemon=True)
    self.thread.start()
    self.poll(on_done)

  # 別スレッドの進み具合を0.1秒ごとに表示し、終わったら on_done を呼ぶ
  def poll(self, on_done):
    if not self.win.winfo_exists():
      return
    if self.thread.is_alive():
      verb = "削除中" if self.stats is not None else "集計中"
      self.status.config(text=f"{verb}... {self.progress:,} 件")
      self.win.after(100, self.poll, on_done)
      return
    if self.error is not None:
      messagebox.showerror("エラー", f"処理中にエラーが発生しました:\n{self.error}", parent=self.win)
      self.win.destroy()
      return
    on_done()

  # --- 集計 ---
  def scan(self):
    self.stats = cache_manager.scan_output(
      self.output_base,
      on_progress=lambda n: setattr(self, "progress", n),
      cancelled=lambda: self.cancel)

  def on_scanned(self):
    if self.stats is None:   # 中止された
      return
    # チェックボックスに件数と容量を表示し、ファイルがある種類だけ選べるようにする
    for key, label in cache_manager.OUTPUT_KINDS:
      count, size = self.stats[key]
      self.checks[key].config(
        text=f"{label}　{count:,} 件 / {cache_manager.format_size(size)}",
        state=tk.NORMAL if count else tk.DISABLED)
      if not count:
        self.vars[key].set(False)
    self.update_total()

  # 選択中の種類の合計を表示し、削除ボタンの状態を合わせる
  def update_total(self):
    count, size = self.selected_total()
    self.status.config(text=f"選択中: {count:,} 件 / {cache_manager.format_size(size)}")
    self.btn_delete.config(state=tk.NORMAL if count else tk.DISABLED)

  # 選択中の種類のキーの集合
  def selected_kinds(self):
    return {key for key, var in self.vars.items() if var.get()}

  # 選択中の種類の合計 (ファイル数, 合計サイズ)
  def selected_total(self):
    kinds = self.selected_kinds()
    return (sum(self.stats[k][0] for k in kinds), sum(self.stats[k][1] for k in kinds))

  # --- 削除 ---
  def start_delete(self):
    kinds = self.selected_kinds()
    count, size = self.selected_total()
    labels = "\n".join(f"・{label}" for key, label in cache_manager.OUTPUT_KINDS if key in kinds)
    if not messagebox.askyesno(
      "確認 - 出力データキャッシュ整理",
      f"次のファイルを削除します。\n\n{labels}\n\n"
      f"合計: {count:,} 件 / {cache_manager.format_size(size)}\n\n本当によろしいですか？",
      parent=self.win):
      return
    for chk in self.checks.values():
      chk.config(state=tk.DISABLED)
    self.btn_delete.config(state=tk.DISABLED)
    self.btn_close.config(text="中止")
    self.progress = 0
    log_message(f"出力データキャッシュの削除を開始します: {', '.join(sorted(kinds))}")
    self.run_in_thread(lambda: self.delete(kinds), self.on_deleted)

  def delete(self, kinds):
    self.result = cache_manager.delete_output_kinds(
      self.output_base, kinds,
      on_progress=lambda n: setattr(self, "progress", n),
      cancelled=lambda: self.cancel)

  def on_deleted(self):
    log_message(f"出力データキャッシュの削除が完了しました（{self.result:,} 件）")
    messagebox.showinfo("完了", f"出力データキャッシュの整理が完了しました。\n削除: {self.result:,} 件",
                        parent=self.win)
    self.win.destroy()

  # 閉じる(処理中なら中止を指示する。スレッドは区切りのよいところで止まる)
  def close(self):
    if self.thread is not None and self.thread.is_alive():
      deleting = self.stats is not None
      if deleting and not messagebox.askyesno(
        "確認", "削除を中止しますか？（削除済みのファイルは戻りません）", parent=self.win):
        return
      self.cancel = True
      if deleting:
        log_message(f"出力データキャッシュの削除を中止しました（約 {self.progress:,} 件削除済み）")
    self.win.destroy()
