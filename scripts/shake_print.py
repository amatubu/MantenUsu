# shake_print.py
# 「この揺れは何?」クイズと、揺れを並べて比べるページを、紙の教材(A4 の PDF)にする(TODO 41・44)
#   - クイズの問題用紙 ... 1問1ページ。4拠点の上下動(フィルタなし。答える前の画面と同じ)、選択肢、音を聴く QR コード
#     折り線の下に「ヒント」(その揺れが見やすいフィルタをかけた波形。どのフィルタかは書かない。折ると隠せる)
#   - クイズの答え ... 1ページに2問。答え・説明・注意、見やすいフィルタの波形と基準の時刻・P波・S波の線、答えの音の QR コード
#   - 比べる ... 1テーマ1ページ。題・説明・見どころ、段を縦に並べた図(紙のフィルタの数だけ横に並べる)、音の QR コード
#   紙では音が出ないので、問題・答え・テーマごとに「聴くページ」(小さな HTML)と音(WAV)を作り、QR コードはその聴くページを開く
#   聴くページと音は 出力のフォルダ/listen にまとめて書くので、そのフォルダの中身をサーバーに置く
#   置き場所の URL は etc/shake_print.txt の「音の置き場所 = https://...」(--url でも指定できる)。決まっていなければ QR の所に枠だけ出す
#   問題・テーマ・波形・音にする処理は、画面(shake_quiz.py・shake_compare.py)と同じ。入れるのは満点の拠点の波形だけ
#   QR コードには segno(requirements-dev.txt)を使う
#
#     python3 scripts/shake_print.py [-o 出力のフォルダ] [--url 音の置き場所] [--only quiz,answers,compare] [--png]
#   省略すると batch_plots/quiz/print に quiz.pdf・quiz_answers.pdf・compare.pdf と listen/ を書く
#   3 つとも作るときは、前に作った listen/ のページと音(*.html・*.wav)を消してから書く
import argparse
import html
import io
import re
import warnings
import wave
from datetime import timedelta
from pathlib import Path

import numpy as np

import app_paths
import station_names
import shake_compare as sc
import shake_quiz as sq
from quake_wave_view import count_steps, decode_samples, filter_sections, load_stream

SETTINGS_FILE = "shake_print.txt"
URL_KEY = "音の置き場所"
OUTPUT_DIR = sq.OUTPUT_DIR / "print"
A4 = (8.27, 11.69)          # インチ
DPI = 200                   # 波形は画像にして入れる(PDF を小さくする)
PLOT_COLUMNS = 1600         # 波形を描く横の点の数(これより多いサンプルは、幅ごとの最小・最大にまとめる)
INK, MUTED, LINE = "#1c2126", "#68727d", "#c9ced4"
REF, P_COLOR, S_COLOR, FOCUS_BG = "#7b4fb5", "#1565c0", "#d84e00", "#f3eefa"
FILTER_NAMES = {key: f"フィルタ {key}({lo:g}〜{hi:g} Hz、{sound})" for key, lo, hi, sound in sq.ALL_FILTERS}
FILTER_NAMES[""] = "フィルタなし"


# --- 設定 ---

# etc/shake_print.txt を読む(「名前 = 値」。# から後は注)。ファイルが無ければ空
def read_settings(path=None):
  path = Path(path) if path else app_paths.ETC_DIR / SETTINGS_FILE
  out = {}
  if not path.exists():
    return out
  for line in path.read_text(encoding="utf-8-sig").splitlines():
    line = line.split("#", 1)[0].strip()
    if "=" in line:
      key, value = (v.strip() for v in line.split("=", 1))
      out[key] = value
  return out

def page_url(base, name):
  return f"{base.rstrip('/')}/{name}" if base else ""


# --- 波形(画面に入れるのと同じデータから戻す) ---

# trace_samples の結果 → 物理量(nm/s。記録の無い所は NaN)
def samples(item):
  v = decode_samples(item["d"]).astype(float) * item["s"]
  for a, b in item["g"]:
    v[a:b] = np.nan
  return v

def sos_of(sections):
  return np.array([[b0, b1, b2, 1.0, a1, a2] for b0, b1, b2, a1, a2 in sections])

# 往復のフィルタ(画面と同じ係数)。記録の無い所は 0 にしてかけ、あとで NaN に戻す
def filtered(v, key, rate):
  from scipy import signal
  if not key:
    return v
  lo, hi = next((lo, hi) for k, lo, hi, _ in sq.ALL_FILTERS if k == key)
  gap = np.isnan(v)
  x = np.where(gap, 0.0, v - np.nanmean(v))
  # 両端を絞ってからかける(長い周期のフィルタ(L)で、端の段差が大きな揺れのように残らないように)
  n = min(int(round(2 / lo * rate)), len(x) // 10)
  if n > 1:
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n))
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
  y = signal.sosfiltfilt(sos_of(filter_sections((lo, hi), rate)), x)
  y[gap] = np.nan
  return y


# --- 音(画面の「聴く」と同じ処理: 0.2 Hz の高域通過・50/100 Hz のノッチ(遠くの地震は 15 Hz の低域通過)・フィルタ・最大でそろえる) ---

# 戻り値 ... (音のサンプリングの波形, 音のサンプリング [Hz])。大きさはそろえない
def sound_wave(v, rate, speed, far, key):
  from scipy import signal
  x = np.where(np.isnan(v), 0.0, v - np.nanmean(v))
  x = signal.sosfiltfilt(sos_of(sq.audio_sections(rate, far)), x)
  x = filtered(x, key, rate)
  fin = int(round(rate * speed))
  out = 8000 if fin <= 8000 else 16000 if fin <= 16000 else 32000
  g = np.gcd(fin, out)
  return signal.resample_poly(x, out // g, fin // g), out

def wav_bytes(y, rate, gain):
  y = np.clip(y * gain, -1, 1)
  n = int(0.03 * rate)
  if len(y) > 2 * n:
    ramp = np.linspace(0, 1, n)
    y[:n] *= ramp
    y[-n:] *= ramp[::-1]
  buf = io.BytesIO()
  with wave.open(buf, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes((y * 32767).astype("<i2").tobytes())
  return buf.getvalue()

# 音のファイルを書く。sounds ... [(名前, v, rate, speed, far, key), ...]。real なら全部を同じ倍率(いちばん大きいものが 0.9)
def write_sounds(folder, sounds, real=False):
  folder.mkdir(parents=True, exist_ok=True)
  made = [(name, *sound_wave(v, rate, speed, far, key)) for name, v, rate, speed, far, key in sounds]
  top = max((np.max(np.abs(y)) for _, y, _ in made), default=0)
  for name, y, out in made:
    peak = top if real else np.max(np.abs(y))
    (folder / name).write_bytes(wav_bytes(y, out, 0.9 / peak if peak > 0 else 0))


# --- 聴くページ ---

LISTEN_PAGE = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; --bg: #fff; --ink: #1c2126; --muted: #68727d; --panel: #f4f5f7; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #14171a; --ink: #e8ecef; --muted: #9aa5af; --panel: #1f2428; }} }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif; }}
main {{ max-width: 640px; margin: 0 auto; padding: 16px; }}
h1 {{ font-size: 20px; margin: 0 0 6px; }} h2 {{ font-size: 16px; margin: 18px 0 6px; }}
.note {{ color: var(--muted); font-size: 13px; }}
.snd {{ background: var(--panel); border-radius: 10px; padding: 8px 10px; margin: 8px 0; }}
.snd b {{ display: block; font-size: 14px; }}
audio {{ width: 100%; height: 40px; margin-top: 4px; }}
</style></head><body><main>
<h1>{title}</h1>
{body}
<p class="note">地震計の記録を速く再生して音にしました。×20 なら 20 倍の速さで、揺れの周波数も 20 倍の音になります(5 Hz の揺れが 100 Hz の音に)。
電気の 50 Hz の雑音は取り除いています。イヤホンかヘッドホンで聴くと、低い音までよく聞こえます。満点地震計の記録です。</p>
</main></body></html>
"""

# items ... [(見出し, [(説明, ファイル名), ...]), ...]
def write_listen_page(path, title, lead, items):
  parts = [f'<p>{html.escape(lead)}</p>'] if lead else []
  for heading, sounds in items:
    if heading:
      parts.append(f"<h2>{html.escape(heading)}</h2>")
    for text, name in sounds:
      parts.append(f'<div class="snd"><b>{html.escape(text)}</b><audio controls preload="none" src="{html.escape(name)}"></audio></div>')
  path.write_text(LISTEN_PAGE.format(title=html.escape(title), body="\n".join(parts)), encoding="utf-8")


# 聴くページの一覧(listen/index.html。フォルダにある聴くページを全部並べる)
def write_index(folder):
  parts = []
  for heading, prefix in (("クイズの問題", "q"), ("クイズの答え", "a"), ("揺れを比べてみよう", "t")):
    pages = sorted(folder.glob(f"{prefix}[0-9]*.html"), key=lambda p: int(p.stem[1:]))
    if pages:
      titles = [re.search(r"<h1>(.*?)</h1>", p.read_text(encoding="utf-8")).group(1) for p in pages]   # エスケープ済み
      parts.append(f"<h2>{heading}</h2><ul>" + "".join(f'<li><a href="{p.name}">{t}</a></li>' for p, t in zip(pages, titles)) + "</ul>")
  (folder / "index.html").write_text(LISTEN_PAGE.format(title="揺れを音で聴く", body="\n".join(parts)), encoding="utf-8")


# --- 図 ---

# 日本語の折り返し: 全角を 1、半角を 0.5 と数えて width ごとに折る。行頭に句読点・閉じかっこを置かず、半角の語(数字・英字)は切らない
NO_HEAD = "、。,.)」』〜ー"
def wrap(text, width):
  import unicodedata
  def w(ch):
    return 1.0 if unicodedata.east_asian_width(ch) in "WFA" else 0.5
  lines = []
  for para in text.split("\n"):
    tokens = re.findall(r"[A-Za-z0-9.\-+±°/:%]+|.", para)
    line, size = "", 0.0
    for tok in tokens:
      tw = sum(w(ch) for ch in tok)
      if line and size + tw > width and tok not in NO_HEAD:
        lines.append(line.rstrip())
        line, size = "", 0.0
        if tok == " ":
          continue
      line += tok
      size += tw
    lines.append(line.rstrip())
  return "\n".join(lines)

# 横に PLOT_COLUMNS 点より多ければ、幅ごとの最小・最大にまとめる
def draw_wave(ax, sec, v, color=INK):
  if len(v) <= PLOT_COLUMNS * 2:
    ax.plot(sec, v, color=color, lw=0.5, rasterized=True)
    return
  k = -(-len(v) // PLOT_COLUMNS)                         # 切り上げ(最後の半端な幅も描く)
  cols = -(-len(v) // k)
  part = np.pad(v, (0, cols * k - len(v)), constant_values=np.nan).reshape(cols, k)
  with warnings.catch_warnings():
    warnings.simplefilter("ignore", RuntimeWarning)       # 全部 NaN の幅(記録なし)
    lo, hi = np.nanmin(part, axis=1), np.nanmax(part, axis=1)
  ax.fill_between(sec[0] + (np.arange(cols) + 0.5) * k * (sec[1] - sec[0]), lo, hi, color=color, lw=0.4, rasterized=True)

def amp_text(nm):
  if nm >= 1e6:
    return f"±{nm / 1e6:.3g} mm/s"
  if nm >= 1e3:
    return f"±{nm / 1e3:.3g} µm/s"
  return f"±{nm:.3g} nm/s"

# 段を縦に並べた図。rows ... [(名前, v または None, {p, s, p2, s2}), ...]。sec0 ... 最初の点の時刻 [秒]
def draw_rows(fig, box, rows, sec0, rate, common, title="", ref_line=None, focus=None):
  x0, y0, w, h = box
  n = len(rows)
  halves = []
  for _, v, _ in rows:
    halves.append(float(np.nanmax(np.abs(v - np.nanmedian(v)))) if v is not None and np.isfinite(v).any() else 0.0)
  top = max(halves, default=0)
  axes = []
  for k, ((name, v, phases), half) in enumerate(zip(rows, halves)):
    ax = fig.add_axes([x0, y0 + h - (k + 1) * h / n, w, h / n])
    axes.append(ax)
    for side in ("top", "right", "left"):
      ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(LINE)
    ax.set_yticks([])
    ax.tick_params(labelsize=7, colors=MUTED, length=2)
    count = len(v) if v is not None else 0
    ax.set_xlim(sec0, sec0 + (count or 1) / rate)
    if v is not None and np.isfinite(v).any():
      sec = sec0 + np.arange(len(v)) / rate
      scale = top if common else half
      mid = np.nanmedian(v)
      draw_wave(ax, sec, v - mid)
      ax.set_ylim(-scale * 1.05 or -1, scale * 1.05 or 1)
      ax.text(1.0, 0.98, amp_text(scale), transform=ax.transAxes, ha="right", va="top", fontsize=6.5, color=MUTED)
    else:
      ax.text(0.5, 0.5, "記録なし", transform=ax.transAxes, ha="center", va="center", fontsize=8, color=MUTED)
    ax.text(0.0, 0.98, name, transform=ax.transAxes, ha="left", va="top", fontsize=7.5, color=INK,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 1})
    if focus is not None:
      ax.axvline(focus, color=REF, lw=0.9, ls=(0, (1, 2)))
    if ref_line is not None:
      ax.axvline(ref_line, color=REF, lw=0.8, ls="--")
    for key, t in (phases or {}).items():
      if not ax.get_xlim()[0] <= t <= ax.get_xlim()[1]:
        continue
      color = P_COLOR if key.startswith("p") else S_COLOR
      ax.axvline(t, color=color, lw=0.8, ls="--")
      ax.text(t, 0.02, key.upper(), transform=ax.get_xaxis_transform(), fontsize=6.5, color=color, weight="bold",
              ha="right" if key.startswith("p") else "left", va="bottom")
    if k < n - 1:
      ax.tick_params(bottom=False, labelbottom=False)
  axes[-1].set_xlabel("秒", fontsize=7, color=MUTED, labelpad=1)
  if title:
    axes[0].set_title(title, fontsize=8.5, color=INK, loc="left", pad=3)
  if focus is not None:
    axes[0].annotate("この揺れ", (focus, 1.0), xycoords=("data", "axes fraction"), xytext=(4, 2), textcoords="offset points",
                     fontsize=7.5, color=REF, weight="bold", va="bottom")
  return axes

# QR コード(url が空なら枠と注だけ)
def draw_qr(fig, box, url, caption):
  ax = fig.add_axes(box)
  ax.set_xticks([]); ax.set_yticks([])
  if url:
    import segno
    m = np.array(segno.make(url, error="m").matrix, dtype=float)
    m = np.pad(m, 2)
    ax.imshow(1 - m, cmap="gray", interpolation="nearest", vmin=0, vmax=1)
    for side in ax.spines.values():
      side.set_visible(False)
  else:
    for side in ax.spines.values():
      side.set_color(LINE); side.set_linestyle("--")
    ax.text(0.5, 0.5, "QR コード\n(音の置き場所が\n決まったら入ります)", ha="center", va="center", fontsize=6.5, color=MUTED,
            transform=ax.transAxes)
  ax.set_title(caption, fontsize=7, color=INK, pad=2)

def new_page():
  import matplotlib.pyplot as plt
  return plt.figure(figsize=A4)

# PDF に 1 ページ足す(PNG_DIR があれば、同じページを PNG にも書く)
PNG = {"dir": None, "count": 0, "name": ""}
def save_page(pdf, fig):
  pdf.savefig(fig, dpi=DPI)
  if PNG["dir"]:
    PNG["count"] += 1
    fig.savefig(Path(PNG["dir"]) / f"{PNG['name']}_{PNG['count']:02d}.png", dpi=110)

def footer(fig, text):
  fig.text(0.5, 0.025, text, ha="center", fontsize=7, color=MUTED)


# --- クイズ ---

def quiz_events(quiz, stations, steps):
  out = []
  for q in quiz:
    before, after = q["show"]
    start = q["t"] + timedelta(seconds=before)
    st = load_stream(start, start + timedelta(seconds=after - before))
    info, waves = sq.event_data(q, st, steps, stations)
    out.append((q, info, {nm: samples(w["U"]) for nm, w in waves.items()}))
  return out

def first_sound(info):
  return next((nm for nm in sq.FIRST_SOUND if nm in info["have"]), info["audio"]["station"])

# 問題・答えの音と聴くページ。戻り値 ... {問題の番号: (問題の聴くページ, 答えの聴くページ)}
def quiz_listen(folder, events):
  pages = {}
  for q, info, waves in events:
    no, rate, far = info["no"], info["rate"], info["audio"]["far"]
    best, speed = info["best"], info["audio"]["speed"]
    first, ans = first_sound(info), info["audio"]["station"]
    write_sounds(folder, [(f"q{no}.wav", waves[first], rate, sq.COMMON_SPEED, far, "")])
    write_listen_page(folder / f"q{no}.html", f"問題 {no} の音", f"{station_names.full(first)} の上下動を、"
                      f"{sq.COMMON_SPEED} 倍の速さにした音です(どの問題も同じ速さです)。何の揺れでしょう?",
                      [("", [(f"×{sq.COMMON_SPEED}(フィルタなし)", f"q{no}.wav")])])
    sounds, items = [], []
    for sp in dict.fromkeys([speed, sq.COMMON_SPEED]):
      keys = ["", best] + ([sq.LOW_FILTER[0]] if info["low"] and best != sq.LOW_FILTER[0] else [])
      names = [(f"a{no}_x{sp}_{key or 'raw'}.wav", key) for key in keys]
      sounds += [(name, waves[ans], rate, sp, far, key) for name, key in names]
      items.append((f"×{sp}" + ("(答える前と同じ速さ)" if sp == sq.COMMON_SPEED and sp != speed else ""),
                    [(FILTER_NAMES[key], name) for name, key in names]))
    write_sounds(folder, sounds)
    kind = sq.KINDS[sq.CHOICES[info["kind"]]][0]
    write_listen_page(folder / f"a{no}.html", f"問題 {no} の答え: {kind}",
                      f"{info['time']}  {info['label']}。{station_names.full(ans)} の上下動の音です。"
                      f"フィルタなしの音と、この揺れが見やすいフィルタ {best} の音を聴き比べてみましょう。", items)
    pages[no] = (f"q{no}.html", f"a{no}.html")
  return pages

def quiz_pdf(path, events, pages, base):
  import matplotlib.lines
  from matplotlib.backends.backend_pdf import PdfPages
  import matplotlib.pyplot as plt
  PNG["count"], PNG["name"] = 0, Path(path).stem
  with PdfPages(path) as pdf:
    for q, info, waves in events:
      fig = new_page()
      no = info["no"]
      fig.text(0.07, 0.95, f"問題 {no}", fontsize=20, weight="bold", color=INK, va="top")
      fig.text(0.07, 0.915, ("印を付けた「この揺れ」は何の揺れでしょう?" if info["focus"] else "何の揺れでしょう?")
               + "  有珠山のまわりの満点地震計 4 か所の上下動です。", fontsize=10, color=INK, va="top")
      rows = [(station_names.full(nm), waves.get(nm), None) for nm in sq.STATIONS]
      focus = -info["t0"] if info["focus"] else None
      draw_rows(fig, (0.07, 0.545, 0.86, 0.34), rows, 0.0, info["rate"], False, focus=focus)
      fig.text(0.07, 0.505, "答え(1つに○)", fontsize=11, weight="bold", color=INK, va="top")
      for i, name in enumerate(c[0] for c in (sq.KINDS[k] for k in sq.CHOICES)):
        fig.text(0.09 + (i % 2) * 0.30, 0.475 - (i // 2) * 0.031, f"□ {name}", fontsize=10.5, color=INK, va="top")
      fig.text(0.07, 0.345, wrap(sq.CHOICE_NOTE, 44), fontsize=7.5, color=MUTED, va="top", linespacing=1.45)
      fig.text(0.07, 0.305, "縦の大きさは拠点ごとにそろえています(右上の数字が、その段の上端の速さ)。", fontsize=7, color=MUTED, va="top")
      draw_qr(fig, (0.77, 0.335, 0.15, 0.105), page_url(base, pages[no][0]), f"音で聴く(×{sq.COMMON_SPEED})")
      # ヒント: その揺れが見やすいフィルタをかけた波形(どのフィルタかは書かない)。折り線より下に置き、折って隠せるようにする
      fig.add_artist(matplotlib.lines.Line2D([0.04, 0.96], [0.285, 0.285], transform=fig.transFigure, color=MUTED, lw=0.8, ls=(0, (4, 3))))
      fig.text(0.96, 0.29, "ここで折ると、ヒントを隠せます", fontsize=6.5, color=MUTED, ha="right", va="bottom")
      fig.text(0.07, 0.268, "ヒント", fontsize=10, weight="bold", color=P_COLOR, va="top")
      fig.text(0.15, 0.266, "上と同じ記録に、ある高さ(周波数)の揺れだけを通すフィルタをかけました。どのフィルタかは、答えのページにあります。",
               fontsize=7.5, color=INK, va="top")
      hint = [(station_names.full(nm), filtered(waves[nm], info["best"], info["rate"]) if nm in waves else None, None) for nm in sq.STATIONS]
      draw_rows(fig, (0.07, 0.075, 0.86, 0.165), hint, 0.0, info["rate"], False, focus=focus)
      footer(fig, f"「この揺れは何?」 問題 {no} / {len(events)}")
      save_page(pdf, fig)
      plt.close(fig)

def answers_pdf(path, events, pages, base):
  from matplotlib.backends.backend_pdf import PdfPages
  import matplotlib.pyplot as plt
  PNG["count"], PNG["name"] = 0, Path(path).stem
  with PdfPages(path) as pdf:
    for i in range(0, len(events), 2):
      fig = new_page()
      for j, (q, info, waves) in enumerate(events[i:i + 2]):
        top = 0.96 - j * 0.47
        no, best = info["no"], info["best"]
        c = sq.KINDS[sq.CHOICES[info["kind"]]]
        fig.text(0.07, top, f"問題 {no}  答え: {c[0]}", fontsize=14, weight="bold", color=INK, va="top")
        fig.text(0.07, top - 0.03, f"{info['time']}  {info['label']}", fontsize=8.5, color=INK, va="top")
        rows = []
        for nm in sq.STATIONS:
          ph = dict(info["phases"].get(nm, {}))
          ph.update({k + "2": t for k, t in info["phases2"].get(nm, {}).items()})
          rows.append((station_names.full(nm), filtered(waves[nm], best, info["rate"]) if nm in waves else None, ph))
        draw_rows(fig, (0.07, top - 0.29, 0.86, 0.23), rows, info["t0"], info["rate"], False,
                  title=f"{FILTER_NAMES[best]}。紫の破線 = {info['ref']}(0 秒)", ref_line=0.0)
        text = c[2].format(b=station_names.full("Bamba")) + (f"\n注意: {info['note']}" if info["note"] else "")
        fig.text(0.07, top - 0.315, wrap(text, 52), fontsize=7.5, color=INK, va="top", linespacing=1.45)
        draw_qr(fig, (0.80, top - 0.43, 0.12, 0.085), page_url(base, pages[no][1]), "答えの音")
      footer(fig, f"「この揺れは何?」 答え({i // 2 + 1} / {(len(events) + 1) // 2})")
      save_page(pdf, fig)
      plt.close(fig)


# --- 比べる ---

def compare_themes(themes, stations, steps):
  out = []
  for th in themes:
    info, waves = sc.theme_data(th, load_stream, steps, stations)
    out.append((info, [{nm: samples(w["U"]) for nm, w in data.items()} for data in waves]))
  return out

def row_station(info, row):
  return row["station"] or info["first"]

def compare_listen(folder, themes):
  pages = {}
  for info, waves in themes:
    no, rate = info["no"], info["rate"]
    items = []
    for key in info["paper"]:
      for sp in info["speeds"]:
        sounds, entries = [], []
        for k, (row, data) in enumerate(zip(info["rows"], waves)):
          nm = row_station(info, row)
          if nm in data:
            name = f"t{no}_{k + 1}_x{sp}_{key or 'raw'}.wav"
            sounds.append((name, data[nm], rate, sp, row["far"], key))
            entries.append((f"{k + 1}. {row['label']}" + ("" if row["station"] else f"({station_names.full(nm)})"), name))
        write_sounds(folder, sounds, real=info["common"])
        items.append((f"{FILTER_NAMES[key]}・×{sp}", entries))
    lead = info["text"] + (" 音の大きさは、本当の揺れの大きさの比のままです。" if info["common"] else " 音の大きさは、それぞれ最大でそろえています。")
    write_listen_page(folder / f"t{no}.html", f"{no}. {info['title']}", lead, items)
    pages[no] = f"t{no}.html"
  return pages

# 周波数のテーマ: 段ごとの振幅スペクトルを 1 つの図に重ねる(画面と同じ色・線の形)
PALETTE = ["#1565c0", "#d84e00", "#2e7d32", "#7b4fb5", "#6d4c41", "#00838f", "#c2185b"]
DASHES = ["-", "--", ":"]
def draw_spectra(fig, box, info):
  ax = fig.add_axes(box)
  f = np.array(info["spec_f"])
  m = f >= 0.1
  for lo, hi in info["marks"]:
    ax.axvspan(lo, hi, color="#eceff2", zorder=0)
    ax.text(np.sqrt(lo * hi), 1.04, f"{lo:g}〜{hi:g} Hz", ha="center", fontsize=7, color=MUTED)
  groups, count = [], {}
  for row in info["rows"]:
    if row["group"] not in groups:
      groups.append(row["group"])
    count[row["group"]] = count.get(row["group"], 0) + 1
    nm = row_station(info, row)
    if nm not in row["spec"]:
      continue
    ax.plot(f[m], np.array(row["spec"][nm])[m], color=PALETTE[groups.index(row["group"]) % len(PALETTE)],
            ls=DASHES[(count[row["group"]] - 1) % len(DASHES)], lw=1.3, label=row["label"])
  ax.set_xscale("log")
  ax.set_xlim(0.1, info["rate"] / 2)
  ax.set_ylim(0, 1.1)
  ax.set_xticks([0.2, 0.5, 1, 2, 5, 10, 20, 50])
  ax.set_xticklabels(["0.2", "0.5", "1", "2", "5", "10", "20", "50"])
  ax.set_xlabel("周波数 [Hz]   ← 低い音(ゆっくりした揺れ)     高い音(細かい揺れ) →", fontsize=8, color=MUTED)
  ax.set_ylabel("大きさ(いちばん大きいところを 1)", fontsize=8, color=MUTED)
  ax.tick_params(labelsize=7, colors=MUTED)
  for side in ("top", "right"):
    ax.spines[side].set_visible(False)
  ax.grid(alpha=0.3)
  ax.legend(fontsize=7.5, loc="upper left", frameon=False)

def compare_pdf(path, themes, pages, base):
  from matplotlib.backends.backend_pdf import PdfPages
  import matplotlib.pyplot as plt
  PNG["count"], PNG["name"] = 0, Path(path).stem
  with PdfPages(path) as pdf:
    for info, waves in themes:
      fig = new_page()
      fig.text(0.07, 0.955, f"{info['no']}. {info['title']}", fontsize=16, weight="bold", color=INK, va="top")
      fig.text(0.07, 0.92, wrap(info["text"], 50), fontsize=8.5, color=INK, va="top", linespacing=1.45)
      cols = [] if info.get("view") == "freq" else info["paper"]
      if not cols:
        draw_spectra(fig, (0.10, 0.32, 0.83, 0.48), info)
      width = (0.86 - 0.03 * (len(cols) - 1)) / max(len(cols), 1)
      for c, key in enumerate(cols):
        rows = []
        for row, data in zip(info["rows"], waves):
          nm = row_station(info, row)
          v = filtered(data[nm], key, info["rate"]) if nm in data else None
          rows.append((row["label"], v, row["phases"].get(nm)))
        draw_rows(fig, (0.07 + c * (width + 0.03), 0.30, width, 0.52), rows, info["t0"], info["rate"], info["common"],
                  title=FILTER_NAMES[key], ref_line=0.0)
      where = "" if all(r["station"] for r in info["rows"]) else f"拠点は {station_names.full(info['first'])}。"
      if info.get("view") == "freq":
        w = info["spec_window"]
        fig.text(0.07, 0.265, wrap(f"{where}{'0 秒は「' + info['ref'] + '」で、' if w else ''}"
                 f"{f'{w[0]:g}〜{w[1]:g} 秒' if w else '表示の全部'}の揺れに、どの高さの揺れがどれくらい入っているか(振幅スペクトル)。"
                 "線ごとに、いちばん大きいところを 1 にそろえています。", 66), fontsize=7.5, color=MUTED, va="top", linespacing=1.4)
      else:
        fig.text(0.07, 0.265, f"0 秒(紫の破線)は「{info['ref']}」。{where}"
                 + ("縦の大きさは全部の段で共通です。" if info["common"] else "縦の大きさは段ごとにそろえています。"),
                 fontsize=7.5, color=MUTED, va="top")
      fig.text(0.07, 0.235, "ここを見てみよう", fontsize=10, weight="bold", color=P_COLOR, va="top")
      fig.text(0.07, 0.21, wrap(info["look"], 40), fontsize=8.5, color=INK, va="top", linespacing=1.5)
      draw_qr(fig, (0.75, 0.07, 0.17, 0.12), page_url(base, pages[info["no"]]), "音で聴き比べる")
      footer(fig, f"揺れを比べてみよう  {info['no']} / {len(themes)}")
      save_page(pdf, fig)
      plt.close(fig)


def main():
  from hypocenter import load_stations
  from plot_common import setup_japanese_font
  parser = argparse.ArgumentParser(description="クイズと比べるページを、紙の教材(A4 の PDF)と、QR コードで開く聴くページにする")
  parser.add_argument("-o", "--output", help="出力のフォルダ(省略すると batch_plots/quiz/print)")
  parser.add_argument("--url", help=f"音の置き場所(listen フォルダの中身を置く URL。省略すると etc/{SETTINGS_FILE})")
  parser.add_argument("--png", action="store_true", help="ページを PNG にも書く(出力のフォルダ/png。確かめ・スライド用)")
  parser.add_argument("--only", default="quiz,answers,compare", help="作るもの(quiz・answers・compare をコンマで区切る)")
  args = parser.parse_args()
  setup_japanese_font()
  base = args.url if args.url is not None else read_settings().get(URL_KEY, "")
  only = set(args.only.split(","))
  out = Path(args.output) if args.output else OUTPUT_DIR
  listen = out / "listen"
  listen.mkdir(parents=True, exist_ok=True)
  # 前に作った聴くページと音を消す(段の数が減ったテーマなどの古いファイルを残さない。作るのが一部だけのときは消さない)
  if only >= {"quiz", "answers", "compare"}:
    for old in list(listen.glob("*.wav")) + list(listen.glob("*.html")):
      old.unlink()
  if args.png:
    PNG["dir"] = out / "png"
    PNG["dir"].mkdir(exist_ok=True)
  steps = count_steps(app_paths.ETC_DIR)
  stations = {nm: c for nm, c in load_stations(app_paths.ETC_DIR).items() if nm in sq.STATIONS}
  if only & {"quiz", "answers"}:
    events = quiz_events(sq.read_quiz(), stations, steps)
    pages = quiz_listen(listen, events)
    if "quiz" in only:
      quiz_pdf(out / "quiz.pdf", events, pages, base)
      print(f"問題用紙: {out / 'quiz.pdf'}({len(events)} ページ)")
    if "answers" in only:
      answers_pdf(out / "quiz_answers.pdf", events, pages, base)
      print(f"答え: {out / 'quiz_answers.pdf'}")
  if "compare" in only:
    themes = compare_themes(sc.read_themes(), stations, steps)
    pages = compare_listen(listen, themes)
    compare_pdf(out / "compare.pdf", themes, pages, base)
    print(f"比べる: {out / 'compare.pdf'}({len(themes)} ページ)")
  write_index(listen)
  size = sum(p.stat().st_size for p in listen.iterdir())
  print(f"聴くページと音: {listen}({size / 1e6:.0f} MB。このフォルダの中身を「{URL_KEY}」の URL に置く)")
  print(f"音の置き場所: {base}" if base else f"音の置き場所が決まっていないので、QR コードは枠だけです(etc/{SETTINGS_FILE} に書くか --url)")

if __name__ == "__main__":
  main()
