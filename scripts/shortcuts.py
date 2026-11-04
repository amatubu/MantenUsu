# shortcuts.py
# 各画面のキーボード操作(ショートカット)の共通処理(TODO 23)
#   Mac は Command キー、Windows は Ctrl キーと組み合わせる(矢印キーなど、修飾キーなしのものもある)
import sys

IS_MAC = sys.platform == "darwin"
# Tk のイベントで使う修飾キーの名前
MODIFIER = "Command" if IS_MAC else "Control"
# 修飾キーなしのショートカットを効かせない部品(文字を入力している欄)
TEXT_INPUT_CLASSES = ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox")

# Tk のキーの名前と、ボタンなどに出す表記
KEY_NAMES = {"Return": ("↩", "Enter"), "Left": ("←", "←"), "Right": ("→", "→")}

# ボタンなどに出す表記。例: label("i") → Mac「⌘I」、Windows「Ctrl+I」 / label("Left", modifier=False) → 「←」
def label(key, modifier=True):
  mac_name, win_name = KEY_NAMES.get(key, (key.upper(), key.upper()))
  name = mac_name if IS_MAC else win_name
  if not modifier:
    return name
  return f"⌘{name}" if IS_MAC else f"Ctrl+{name}"

# ボタンの文字の後ろにショートカットの表記を付ける。例: with_label("震度推定", "i") → 「震度推定  ⌘I」
def with_label(text, key, modifier=True):
  return f"{text}  {label(key, modifier)}"

# ウィンドウにショートカットを割り当てる
#   win      ... Tk のウィンドウ(Tk・Toplevel)。中の部品にキーボードの入力があるときも効く
#   key      ... Tk のキーの名前(英字は小文字。例: "i"、"Return"、"Left")
#   command  ... 引数なしで呼ぶ関数
#   modifier ... Command(Mac)・Ctrl(Windows)と組み合わせる。False なら修飾キーなし
#                (修飾キーなしのときは、文字を入力している欄では効かない)
def bind(win, key, command, modifier=True):
  def handler(event):
    if not modifier and event.widget.winfo_class() in TEXT_INPUT_CLASSES:
      return None
    command()
    return "break"
  names = [key] if len(key) > 1 else [key.lower(), key.upper()]   # Caps Lock のときも効くように
  for name in names:
    win.bind(f"<{MODIFIER}-{name}>" if modifier else f"<{name}>", handler)

# 子の画面(Matplotlib の図)を閉じたら、開いた元の画面を前に出して、キーボードの入力を受けるようにする
#   (Matplotlib の図はメインの画面とは別の Tk なので、閉じても元の画面にキーボードの入力が戻らないことがあるため)
#   win ... 元の画面(Tk・Toplevel)。None なら何もしない
def return_focus_on_close(fig, win):
  if win is None:
    return
  def bring_back():
    try:
      if win.winfo_exists():
        win.lift()
        win.focus_force()
    except Exception:
      pass   # 元の画面が先に閉じられている
  def on_close(_event):
    # 閉じた直後は Mac がほかの画面を前に出すことがあるので、少しあとにもう一度出し直す
    win.after(50, bring_back)
    win.after(300, bring_back)
  fig.canvas.mpl_connect("close_event", on_close)

# Matplotlib の図のウィンドウ(Tk)を返し、自前のショートカットを割り当てられるようにする
#   Matplotlib の標準のキー操作(s で保存、h で表示を戻す、Windows の Ctrl+S など)は、割り当てるキーとぶつかるので外し、
#   ⌘W(Ctrl+W)で閉じる操作だけ割り当て直す
def figure_window(fig):
  import matplotlib.pyplot as plt
  manager = fig.canvas.manager
  if manager is not None and getattr(manager, "key_press_handler_id", None) is not None:
    fig.canvas.mpl_disconnect(manager.key_press_handler_id)
  win = fig.canvas.get_tk_widget().winfo_toplevel()
  bind(win, "w", lambda: plt.close(fig))
  return win
