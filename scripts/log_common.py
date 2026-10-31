# log_common.py
# メッセージ出力の共通処理(コンソールと画面のログ欄へ出す)
import tkinter as tk

# コンソールに表示するか(単体実行のスクリプトでも表示されるよう、既定は表示)
_console = True
# 画面のログ欄(tk.Text)。登録されていなければ None
_log_text = None

def set_console(enabled):
  """コンソールに表示するかどうかを設定する"""
  global _console
  _console = enabled

def set_log_widget(text_widget):
  """メッセージを追記する画面のログ欄(tk.Text)を登録する。None で解除"""
  global _log_text
  _log_text = text_widget

def log_message(message):
  """メッセージをコンソール(設定時のみ)と画面のログ欄(登録時のみ)に出す"""
  if _console:
    print(message)

  # 画面のログテキストボックスに追記
  if _log_text is not None:
    _log_text.config(state=tk.NORMAL)   # 一時的に書き込み許可
    _log_text.insert(tk.END, message + "\n")
    _log_text.see(tk.END)               # 自動で一番下にスクロール
    _log_text.config(state=tk.DISABLED) # 再び書き込み禁止に戻す
