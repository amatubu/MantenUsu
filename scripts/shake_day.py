# shake_day.py
# 1日の記録から揺れを探す教材(TODO 51)の、紙の記録を作る
#   アプリの「1時間スタック波形」と同じ形: 1 ページ = 1 時間、1 段 = 1 分(60 段)。A4 縦。拠点ごとに 24 ページの PDF
#   波形は、帯域(既定 0.5〜20 Hz)のフィルタをかけた上下動を 50 Hz に間引いて、細い線で描く
#   縦の大きさはページ(1 時間)ごとに共通: その 1 時間のふだんの揺れ(|速度| の中央値)の GAIN 倍を、段の間隔の半分にする。
#   段の間隔の CLIP 倍をこえる揺れは切る(となりの段と重なって黒く塗りつぶされないように)。
#   --no-clip では切らずにはみ出させる(V-net の連続波形や、昔の機械式の地震計の記録と同じ見え方。ファイル名に _noclip を付ける)
#   1 日の記録を読むのに時間がかかるので、間引いた波形を output/shake_day/<日>_<帯域>.npz に保存し、次からはそれを使う
#   入れるのは満点の拠点の波形だけ
#
#     python3 scripts/shake_day.py 2026-07-08 [--band 0.5 20] [--stations Mimatsu ...] [--hours 0-23] [--no-clip] [--answers] [-o 出力のフォルダ] [--png]
#   --answers ... 答えのページ(answers_<拠点>.pdf)も書く。1 ページめに揺れの一覧(etc/shake_day_<日>.csv)と列車の確かめ方
#                 (時刻表は転載できないので載せず、JR北海道の Web サイトで調べてもらう)、
#                 そのあとに揺れのある時間のページだけを、揺れの時間に色を付けて番号を入れたもの
#   答えの一覧 etc/shake_day_<日>.csv: 番号, 種類, 始まり, 終わり(時:分:秒), 内容, 見え方(はっきり・見える・弱い), 説明
#   省略すると batch_plots/quiz/day/<日>/ に day_<拠点>.pdf を書く(拠点はページの題と同じ発表用の名前。Hokudai → Kawaminami など)
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import app_paths
import station_names
import shake_quiz as sq

RATE = 50                # 保存・描画する波形のサンプリング [Hz](20 Hz までの揺れが描ける)
CACHE_DIR = app_paths.OUTPUT_DIR / "shake_day"
OUTPUT_DIR = sq.OUTPUT_DIR / "day"
GAIN = 30.0              # 段の間隔の半分 = その 1 時間のふだんの揺れ × GAIN
CLIP = 0.6               # 段の間隔の 0.6 倍(上下それぞれ)をこえる揺れは切る
MAX_ROWS = 3.0           # 切らない版(--no-clip): その 1 時間の最大の揺れが、段の間隔の MAX_ROWS 倍になるようにする
                         # (ふだんの揺れの倍で決めると、大きな地震がページの外まではみ出すため)
INK, MUTED, LINE = "#1c2126", "#68727d", "#c9ced4"
WAVE = "#1f5f99"


# 1 日の上下動(物理量 nm/s)を、帯域のフィルタをかけて RATE に間引く。戻り値 ... {拠点: 波形}(86400 × RATE 点。記録の無い所は NaN)
def day_waveforms(day, band, load=None):
  from scipy import signal
  import quake_wave_view as wv
  load = load or wv.load_stream
  steps = wv.count_steps(app_paths.ETC_DIR)
  n = 86400 * RATE
  out = {nm: np.full(n, np.nan, dtype=np.float32) for nm in sq.STATIONS}
  for h in range(24):
    t0 = day + timedelta(hours=h)
    st = load(t0 - timedelta(seconds=30), t0 + timedelta(hours=1, seconds=30))   # フィルタの端の乱れを避けるため前後に 30 秒
    for tr in st:
      nm = tr.stats.station
      if nm not in out or not tr.stats.channel.endswith("U"):
        continue
      fs = tr.stats.sampling_rate
      k = int(round(fs / RATE))
      if k < 1 or abs(fs / RATE - k) > 1e-6:
        continue
      x = np.ma.filled(np.ma.asarray(tr.data, dtype=float), np.nan) * steps.get((nm, "U"), 1.0)
      gap = np.isnan(x)
      if gap.all():
        continue
      x = np.where(gap, 0.0, x - np.nanmean(x))
      y = signal.sosfiltfilt(signal.butter(4, band, "bandpass", fs=fs, output="sos"), x)
      y[gap] = np.nan
      off = int(round((tr.stats.starttime.datetime - day).total_seconds() * fs))   # 日の始まりからのサンプル数
      first = max(-(-off // k), h * 3600 * RATE)
      last = min((off + len(y)) // k, (h + 1) * 3600 * RATE, n)
      if last > first:
        out[nm][first:last] = y[first * k - off:last * k - off:k]
    print(f"  {t0:%H}時", flush=True)
  return out

def cached_waveforms(day, band, load=None):
  path = CACHE_DIR / f"{day:%Y%m%d}_{band[0]:g}-{band[1]:g}Hz_{RATE}Hz.npz"
  if path.exists():
    with np.load(path) as z:
      return {nm: z[nm] for nm in sq.STATIONS if nm in z.files}
  wf = day_waveforms(day, band, load)
  path.parent.mkdir(parents=True, exist_ok=True)
  np.savez_compressed(path, **wf)
  return wf

# その 1 時間のふだんの揺れ(|速度| の中央値)
def hour_level(x):
  a = np.abs(x[np.isfinite(x)])
  return float(np.median(a)) if a.size else 0.0

KIND_COLORS = {"有珠山の地震": "#1565c0", "発破": "#d84e00", "遠くの地震": "#2e7d32", "少し遠い地震": "#2e7d32", "花火": "#c2185b",
               "地震ではない揺れ": "#757575", "列車": "#6d4c41"}

# 答えの一覧を読む。戻り値 ... [{"no", "kind", "start", "end"(日の始まりからの秒), "text", "vis", "note"}, ...]
def read_answers(day, path=None):
  import csv
  path = Path(path) if path else app_paths.ETC_DIR / f"shake_day_{day:%Y%m%d}.csv"
  if not path.exists():
    return []
  def sec(v):
    h, m, s_ = (int(x) for x in v.split(":"))
    return h * 3600 + m * 60 + s_
  out = []
  with open(path, encoding="utf-8-sig", newline="") as f:
    for r in csv.DictReader(f):
      start, end = sec(r["始まり"]), sec(r["終わり"])
      if end < start:
        raise ValueError(f"{path.name}: 番号 {r['番号']} の終わりが始まりより前です")
      out.append({"no": int(r["番号"]), "kind": r["種類"], "start": start, "end": end, "text": r["内容"],
                  "vis": r["見え方"], "note": r["説明"]})
  return sorted(out, key=lambda e: e["start"])

def summary_page(fig, day, nm, events):
  from shake_print import wrap
  fig.text(0.08, 0.965, f"{day:%Y年%m月%d日}({'月火水木金土日'[day.weekday()]})  1 日の記録の答え({station_names.full(nm)})",
           fontsize=14, weight="bold", color=INK, va="top")
  y = 0.925
  for e in events:
    t0, t1 = (f"{v // 3600:02d}:{v % 3600 // 60:02d}:{v % 60:02d}" for v in (e["start"], e["end"]))
    color = KIND_COLORS.get(e["kind"], INK)
    fig.text(0.08, y, f"{e['no']}.", fontsize=10, weight="bold", color=color, va="top")
    fig.text(0.12, y, f"{t0}〜{t1}  {e['kind']}({e['vis']})", fontsize=10, weight="bold", color=color, va="top")
    lines = wrap(f"{e['text']}。{e['note']}", 50)
    fig.text(0.12, y - 0.02, lines, fontsize=8, color=INK, va="top", linespacing=1.4)
    y -= 0.032 + 0.0135 * (lines.count("\n") + 1)
  y -= 0.01
  fig.text(0.08, y, "列車", fontsize=10, weight="bold", color=KIND_COLORS["列車"], va="top")
  # 時刻表は転載できないので、ページには載せず、JR北海道の Web サイトで調べてもらう
  note = ("Bamba(線路から約 120 m)の、30 秒〜1 分ほど続く大きな揺れは列車です。ほかの拠点ではほとんど揺れません。"
          "洞爺駅の時刻表(JR北海道の Web サイト https://www.jrhokkaido.co.jp/ で調べられます)と照らすと、"
          "函館方面の列車は発車の 1〜3 分前、札幌方面の列車は発車の 2〜3 分後に Bamba の前を通ります。"
          "時刻表に無い長い揺れ(1 分以上)は、貨物列車です。")
  lines = wrap(note, 50)
  fig.text(0.12, y - 0.02, lines, fontsize=8, color=INK, va="top", linespacing=1.4)
  fig.text(0.08, 0.03, wrap("揺れのある時間のページには、揺れの時間に色を付けて番号を入れています(色: 青 = 有珠山の地震、赤 = 発破、緑 = 遠くの地震、"
           "紫 = 花火、灰色 = 地震ではない揺れ)。「弱い」ものは、この拠点では見えにくいことがあります。", 62), fontsize=7, color=MUTED, va="bottom", linespacing=1.4)

def mark_events(ax, h, events):
  from matplotlib.patches import Rectangle
  for e in events:
    a, b = max(e["start"], h * 3600), min(e["end"], (h + 1) * 3600)
    if a >= b:
      continue
    color = KIND_COLORS.get(e["kind"], INK)
    first = True
    for r in range((a - h * 3600) // 60, (b - 1 - h * 3600) // 60 + 1):
      x0 = max(a - h * 3600 - r * 60, 0)
      x1 = min(b - h * 3600 - r * 60, 60)
      ax.add_patch(Rectangle((x0, r - 0.5), x1 - x0, 1, color=color, alpha=0.16, lw=0, zorder=0))
      if first:
        ax.text(x0 if x0 < 50 else x1, r - 0.55, f"{e['no']}. {e['kind']}", fontsize=6.5, color=color, weight="bold", va="bottom",
                ha="left" if x0 < 50 else "right", zorder=10,
                bbox=dict(facecolor="white", edgecolor=color, lw=0.5, alpha=0.92, pad=1.2, boxstyle="round,pad=0.25"))
        first = False

def day_pdf(path, day, nm, wave, band, hours=range(24), png_dir=None, clip=CLIP, events=None):
  import matplotlib.pyplot as plt
  from matplotlib.backends.backend_pdf import PdfPages
  per_row = 60 * RATE
  t = np.arange(per_row) / RATE
  if events is not None:
    hours = [h for h in hours if any(e["start"] < (h + 1) * 3600 and e["end"] > h * 3600 for e in events)]
  with PdfPages(path) as pdf:
    if events is not None:
      fig = plt.figure(figsize=(8.27, 11.69))
      summary_page(fig, day, nm, events)
      pdf.savefig(fig)
      if png_dir:
        fig.savefig(Path(png_dir) / f"{path.stem}_00list.png", dpi=110)
      plt.close(fig)
    for h in hours:
      x = wave[h * 3600 * RATE:(h + 1) * 3600 * RATE]
      level = hour_level(x)
      scale = level * GAIN / 0.5 if level > 0 else 1.0      # 段の間隔 1 にあたる大きさ
      if not clip:                                          # 切らない版は、その 1 時間の最大を基準にする
        top = float(np.nanmax(np.abs(x))) if np.isfinite(x).any() else 0.0
        scale = max(scale, top / MAX_ROWS) if top > 0 else scale
      fig = plt.figure(figsize=(8.27, 11.69))
      ax = fig.add_axes([0.08, 0.05, 0.88, 0.87])
      for r in range(60):
        y = x[r * per_row:(r + 1) * per_row] / scale
        if clip:
          y = np.clip(y, -clip, clip)
        ax.plot(t, r - y, color=WAVE, lw=0.35, rasterized=True)
        miss = np.isnan(x[r * per_row:(r + 1) * per_row])
        if miss.any():
          ax.fill_between(t, r - 0.5, r + 0.5, where=miss, color="#e4e7eb", lw=0)
      if events is not None:
        mark_events(ax, h, events)
      ax.set_ylim(59.8, -0.8)
      ax.set_yticks(range(60))
      ax.set_yticklabels([f"{h:02d}:{m:02d}" for m in range(60)], fontsize=5.5)
      ax.set_xlim(0, 60)
      ax.set_xticks(range(0, 61, 10))
      ax.set_xticks(range(0, 61, 2), minor=True)
      ax.set_xlabel("秒(その段の分の始まりから)", fontsize=8, color=MUTED)
      ax.grid(axis="x", which="major", color=LINE, lw=0.6)
      ax.grid(axis="x", which="minor", color="#eef0f2", lw=0.4)
      ax.tick_params(labelsize=7, colors=MUTED)
      ax.tick_params(axis="y", length=0)
      for side in ("top", "right"):
        ax.spines[side].set_visible(False)
      fig.text(0.08, 0.975, f"{day:%Y年%m月%d日}({'月火水木金土日'[day.weekday()]})  {h:02d}:00〜{h + 1:02d}:00  "
               f"{station_names.full(nm)} の上下動", fontsize=12, weight="bold", color=INK, va="top")
      fig.text(0.08, 0.955, f"{band[0]:g}〜{band[1]:g} Hz の揺れ。1 段が 1 分で、左から右へ時間が進みます。"
               + (f"段の間隔の半分 = {level * GAIN / 1000:.2g} µm/s\n(この 1 時間のふだんの揺れの {GAIN:g} 倍)。" if clip else
                f"段の間隔の半分 = {scale / 2 / 1000:.2g} µm/s\n(この 1 時間でいちばん大きな揺れが、段の間隔の {MAX_ROWS:g} 倍になる大きさ)。")
               + ("大きな揺れは途中で切っています。" if clip else "大きな揺れは、となりの段まではみ出しています。")
               + ("" if clip else "\n") + "揺れを見つけたら、時刻に印を付けて、何の揺れか考えてみましょう。",
               fontsize=7, color=MUTED, va="top", linespacing=1.4)
      fig.text(0.96, 0.975, f"{h + 1} / 24", fontsize=8, color=MUTED, ha="right", va="top")
      pdf.savefig(fig, dpi=200)
      if png_dir:
        fig.savefig(Path(png_dir) / f"{path.stem}_{h:02d}.png", dpi=110)
      plt.close(fig)


def parse_hours(text):
  out = []
  for part in text.split(","):
    a, _, b = part.partition("-")
    out += list(range(int(a), int(b or a) + 1))
  return [h for h in out if 0 <= h < 24]

def main():
  from plot_common import setup_japanese_font
  parser = argparse.ArgumentParser(description="1日の記録(1 時間 = 1 ページ、1 分ずつの段)の紙を作る(TODO 51)")
  parser.add_argument("day", help="日(例: 2026-07-08)")
  parser.add_argument("--band", nargs=2, type=float, default=[0.5, 20.0], metavar=("下限", "上限"), help="帯域 [Hz](既定 0.5 20)")
  parser.add_argument("--stations", nargs="*", default=sq.STATIONS, help="拠点(既定は 4 拠点)")
  parser.add_argument("--hours", default="0-23", help="時(例: 0-23、19、1,11,16-17)")
  parser.add_argument("-o", "--output", help="出力のフォルダ(省略すると batch_plots/quiz/day/<日>)")
  parser.add_argument("--png", action="store_true", help="ページを PNG にも書く")
  parser.add_argument("--no-clip", action="store_true", help="大きな揺れを切らずに、となりの段まではみ出させる")
  parser.add_argument("--answers", action="store_true", help="答えのページ(answers_<拠点>.pdf)も書く(etc/shake_day_<日>.csv)")
  args = parser.parse_args()
  setup_japanese_font()
  day = datetime.strptime(args.day, "%Y-%m-%d")
  band = tuple(args.band)
  wf = cached_waveforms(day, band)
  out = Path(args.output) if args.output else OUTPUT_DIR / f"{day:%Y%m%d}"
  out.mkdir(parents=True, exist_ok=True)
  for nm in args.stations:
    if nm in wf:
      path = out / f"day_{station_names.full(nm)}{'_noclip' if args.no_clip else ''}.pdf"   # ファイル名も発表用の拠点名(Kawaminami など)
      day_pdf(path, day, nm, wf[nm], band, parse_hours(args.hours), png_dir=out if args.png else None, clip=0 if args.no_clip else CLIP)
      print(path, f"{path.stat().st_size / 1e6:.1f} MB")
      if args.answers:
        events = read_answers(day)
        if not events:
          print(f"答えの一覧がありません: etc/shake_day_{day:%Y%m%d}.csv")
          continue
        apath = out / f"answers_{station_names.full(nm)}{'_noclip' if args.no_clip else ''}.pdf"
        day_pdf(apath, day, nm, wf[nm], band, parse_hours(args.hours), png_dir=out if args.png else None,
                clip=0 if args.no_clip else CLIP, events=events)
        print(apath, f"{apath.stat().st_size / 1e6:.1f} MB")

if __name__ == "__main__":
  main()
