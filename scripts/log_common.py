# log_common.py
# メッセージ出力の共通処理(コンソールと画面のログ欄へ出す)
import threading
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

# ほかの画面のログ欄(メンテナンス画面など。メイン画面のログ欄と同じメッセージを書く)
_extra_texts = []

def add_log_widget(text_widget):
  """メイン画面のログ欄に加えて、メッセージを書くログ欄を登録する"""
  _extra_texts.append(text_widget)

def remove_log_widget(text_widget):
  """add_log_widget で登録したログ欄を外す(画面を閉じるとき)"""
  if text_widget in _extra_texts:
    _extra_texts.remove(text_widget)

# 別スレッドから出したメッセージ(画面の部品は別スレッドから触れないので、画面側で flush_pending を呼んで書く)
_pending = []

def log_message(message):
  """メッセージをコンソール(設定時のみ)と画面のログ欄(登録時のみ)に出す"""
  if _console:
    print(message)
  if _log_text is None and not _extra_texts:
    return
  if threading.current_thread() is not threading.main_thread():
    _pending.append(message)
    return
  flush_pending()
  _write(message)

def flush_pending():
  """別スレッドから出したメッセージを、画面のログ欄に書く(画面側から呼ぶ)"""
  while _pending:
    _write(_pending.pop(0))

# 画面のログテキストボックスに追記
def _write(message):
  for text in ([_log_text] if _log_text is not None else []) + _extra_texts:
    text.config(state=tk.NORMAL)   # 一時的に書き込み許可
    text.insert(tk.END, message + "\n")
    text.see(tk.END)               # 自動で一番下にスクロール
    text.config(state=tk.DISABLED) # 再び書き込み禁止に戻す
