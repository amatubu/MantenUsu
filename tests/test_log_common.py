import os
import sys
import tkinter as tk
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import log_common


class TestDestroyedLogWidget(unittest.TestCase):
  def setUp(self):
    try:
      self.root = tk.Tk()
    except tk.TclError:
      self.skipTest("画面が使えない")
    self.root.withdraw()
    log_common.set_console(False)

  def tearDown(self):
    log_common.set_log_widget(None)
    log_common._extra_texts.clear()
    log_common.set_console(True)
    self.root.destroy()

  def test_main_log_destroyed(self):
    """メイン画面のログ欄が先に閉じられても、メッセージを出すと落ちずに外れる"""
    frame = tk.Frame(self.root)
    text = tk.Text(frame)
    log_common.set_log_widget(text)
    frame.destroy()
    log_common.log_message("保存しました")
    self.assertIsNone(log_common._log_text)

  def test_extra_log_destroyed_others_still_written(self):
    """閉じられたログ欄は外し、残っているログ欄には書く"""
    gone = tk.Text(self.root)
    alive = tk.Text(self.root)
    log_common.add_log_widget(gone)
    log_common.add_log_widget(alive)
    gone.destroy()
    log_common.log_message("保存しました")
    self.assertEqual(log_common._extra_texts, [alive])
    self.assertIn("保存しました", alive.get("1.0", tk.END))


if __name__ == "__main__":
  unittest.main()
