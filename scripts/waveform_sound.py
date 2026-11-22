# waveform_sound.py
# 波形表示の画面で、波形を音にして聴く(TODO 48)
#   音にする処理は、クイズ・比べるページ(shake_quiz.py)と同じ:
#   表示のフィルタ(A〜D・L。なしも選べる)→ 0.2 Hz の高域通過と電気の 50・100 Hz のノッチ → 最大でそろえる
#   フィルタはどれも往復(位相のずれなし)でかける
#   音は WAV にして、Mac は afplay、Windows は winsound(Python に入っている)で鳴らす。新しいライブラリは使わない
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np
from scipy import signal

from quake_wave_view import filter_sections
from shake_quiz import ALL_FILTERS, audio_sections

# 選べる速さ(倍率)と、はじめの速さ
SPEEDS = (5, 10, 20, 40, 80)
DEFAULT_SPEED = 40
# 表示と音のフィルタ: [(画面に出す名前, (下限, 上限) [Hz] または None)]。クイズと同じ A〜D と L
FILTER_CHOICES = [("なし", None)] + [(f"{key} {lo:g}〜{hi:g} Hz", (lo, hi)) for key, lo, hi, _sound in ALL_FILTERS]
# 音のサンプリング [Hz](波形を速めた周波数から、帯域を制限して直す)
SOUND_RATE = 16000
# 音の長さの上限 [秒](長すぎる範囲を遅い速さで聴こうとしたとき)
MAX_SOUND_SECONDS = 600
# 音の始めと終わりを絞る長さ [秒](プツッという音を防ぐ)
FADE_SECONDS = 0.03
# 最大でそろえるときの大きさ(1 が上限)
PEAK_LEVEL = 0.9


# 係数の表 [[b0, b1, b2, a1, a2], ...] を scipy の sos の形にする
def to_sos(rows):
  return np.array([[b0, b1, b2, 1.0, a1, a2] for b0, b1, b2, a1, a2 in rows])

# 欠けたところ(マスク)を 0 にした、平均を引いた float の配列
def plain_samples(data):
  x = np.ma.filled(np.ma.asarray(data, dtype=float), np.nan)
  if not np.isfinite(x).any():
    return np.zeros(len(x))
  return np.nan_to_num(x - np.nanmean(x))

# 波形に表示のフィルタをかける(band が None ならそのまま)。戻り値 ... float の配列
#   rate ... サンプリング [Hz] / band ... (下限, 上限) [Hz]。上限がナイキストに近すぎるときは下げる
def filter_samples(data, rate, band):
  x = plain_samples(data)
  if band is None or len(x) < 30:
    return x
  lo, hi = band
  hi = min(hi, rate / 2 * 0.9)
  if lo >= hi:
    return x
  return signal.sosfiltfilt(to_sos(filter_sections((lo, hi), rate)), x)

# 表示のフィルタをかけた Stream の写し(band が None なら元の Stream をそのまま返す)
def filtered_stream(st, band):
  if band is None:
    return st
  out = st.copy()
  for tr in out:
    tr.data = filter_samples(tr.data, tr.stats.sampling_rate, band)
  return out

# 波形の一部を音のサンプルにする
#   data  ... 波形(表示のフィルタをかける前) / rate ... そのサンプリング [Hz]
#   i0, i1 ... 音にする範囲(サンプルの番号) / band ... 表示のフィルタ / speed ... 速さ(倍率)
#   戻り値 ... (音のサンプル(-1〜1 の float), 音のサンプリング [Hz])。音にならないときは (空の配列, SOUND_RATE)
def sound_samples(data, rate, i0, i1, band, speed):
  # フィルタは範囲より少し広くかけてから切り出す(端で揺れが立ち上がって聞こえないように)
  pad = int(rate * 5)
  a, b = max(0, i0 - pad), min(len(data), i1 + pad)
  x = filter_samples(data[a:b], rate, band)
  if len(x) >= 30:
    x = signal.sosfiltfilt(to_sos(audio_sections(rate, False)), x)
  x = x[i0 - a:i1 - a]
  if len(x) < 2:
    return np.zeros(0), SOUND_RATE
  # 速めた周波数(rate × speed)から SOUND_RATE に直す(帯域を制限した間引き・補間)
  fin = int(round(rate * speed))
  g = np.gcd(SOUND_RATE, fin)
  y = signal.resample_poly(x, SOUND_RATE // g, fin // g)
  peak = np.max(np.abs(y)) if len(y) else 0.0
  if peak > 0:
    y = y / peak * PEAK_LEVEL
  fade = min(int(FADE_SECONDS * SOUND_RATE), len(y) // 2)
  if fade > 0:
    ramp = np.linspace(0.0, 1.0, fade)
    y[:fade] *= ramp
    y[-fade:] *= ramp[::-1]
  return y, SOUND_RATE

# 音のサンプルを WAV(16 bit、1 チャンネル)に書く
def write_wav(path, y, rate):
  pcm = np.round(np.clip(y, -1.0, 1.0) * 32767).astype("<i2")
  with wave.open(str(path), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(int(rate))
    w.writeframes(pcm.tobytes())


# --- 鳴らす ---

# この OS で音を鳴らす方法。戻り値 ... "afplay"・"winsound"・Linux のコマンドのパス、無ければ None
def player_kind():
  if sys.platform == "darwin" and shutil.which("afplay"):
    return "afplay"
  if sys.platform.startswith("win"):
    return "winsound"
  for name in ("paplay", "aplay"):
    path = shutil.which(name)
    if path:
      return path
  return None


# WAV を鳴らし、止める(1 つの画面に 1 つ)
class SoundPlayer:
  def __init__(self):
    self.proc = None
    self.path = None

  # 音のサンプルを一時ファイルにして鳴らし始める。戻り値 ... 鳴らせたら True
  def play(self, y, rate):
    self.stop()
    kind = player_kind()
    if kind is None:
      return False
    fd, path = tempfile.mkstemp(prefix="manten_sound_", suffix=".wav")
    os.close(fd)
    write_wav(path, y, rate)
    self.path = path
    if kind == "winsound":
      import winsound
      winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    else:
      self.proc = subprocess.Popen([kind, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True

  # 止めて、一時ファイルを消す
  def stop(self):
    if self.proc is not None:
      if self.proc.poll() is None:
        self.proc.terminate()
        try:
          self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
          self.proc.kill()
      self.proc = None
    elif self.path is not None and sys.platform.startswith("win"):
      import winsound
      winsound.PlaySound(None, 0)   # 鳴っている音を止める
    if self.path is not None:
      try:
        os.remove(self.path)
      except OSError:
        pass   # Windows で鳴らし終わる前など。一時フォルダなので残っても OS が消す
      self.path = None
