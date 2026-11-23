# 教材のページで同じ JS の部品(page_common.py)のテスト
import re

import page_common
import shake_compare
import shake_day_web
import shake_filter_lab
import shake_quiz


def functions(js):
  return re.findall(r"^(?:async )?function (\w+)\(", js, flags=re.M)


def test_parts_are_in_page_js():
  for part in (page_common.COMMON_JS, page_common.VIEW_JS, page_common.AUDIO_JS, page_common.GESTURE_JS):
    assert part in page_common.PAGE_JS
  names = functions(page_common.PAGE_JS)
  assert {"attachGestures", "audioPlay", "audioStop", "formatAmp", "tickStep", "labelBox", "drawTrace", "wavBlob"} <= set(names)
  assert len(names) == len(set(names))
  assert shake_quiz.COMMON_JS is page_common.COMMON_JS                       # ほかのスクリプトのために残してある名前


def test_pages_do_not_redefine_common_functions():
  # 部品と同じ名前の関数をページの側で作ると、あとから読んだほうが勝ってしまい、部品を直しても効かなくなる
  common = set(functions(page_common.PAGE_JS))
  for mod in (shake_quiz, shake_compare, shake_filter_lab, shake_day_web):
    page = mod.PAGE
    assert "__COMMON_JS__" in page
    assert not common & set(functions(page)), mod.__name__
    assert "attachGestures(canvas" in page, mod.__name__
