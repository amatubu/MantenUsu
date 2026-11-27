# haptic_export.py
# 地震の揺れの強さの曲線を、iPhone の振動のファイル(AHAP)と音(WAV)に書き出す(TODO 58)
#   App Store の HapticSync(無料)で AHAP を読み込むと、UsuMantenFeel(テスト用のアプリ)と同じような振動で再生できる。
#   HapticSync は AHAP の強さの曲線(ParameterCurve)を読まないので、短い連続した振動(既定 0.04 秒)をすき間なく並べ、
#   1 つずつの強さをその区間の曲線の平均にする(強さが階段状に変わる)
#   強さの曲線は、波形のページ(quake_wave_view.py)と同じ計算(haptic_page.haptic_curve。3 成分・速度。基準は地震ごと、--common で拠点ごとの全地震の最大)。
#   波形は、波形のページ batch_plots/quakes/locate/usu_quakes_waves.html の中のものを使う(先に quake_wave_view.py で書き出しておく)
#   1 つの地震・拠点につき、P波の --lead 秒前から --length 秒を書き出す(終わりの 0.5 秒で弱めて消す)
#     <日時>_M<規模>_<拠点>_yawaraka.ahap・_kukkiri.ahap(鋭さ 0.3・0.7。ページの「やわらか」「くっきり」)
#     --wav のとき <日時>_M<規模>_<拠点>.wav(150 Hz の音の大きさを強さの曲線で変えたもの。HapticSync で音と一緒に鳴らせる)
#   同じフォルダに index.html(地震ごとに、拠点ごとのファイルへのリンクと、HapticSync での鳴らし方)も書く。
#     フォルダごとサーバに置けば、iPhone でページを開いてファイルを保存し、HapticSync で読み込んでもらえる
#     (一覧はその回に書き出した分。--events・--stations でしぼったときは、しぼった分だけになる)
# 使い方: ../venv/bin/python3 scripts/haptic_export.py [--events 17 20] [--stations Mimatsu] [--wav] [-o batch_plots/haptic]
import argparse
import html
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

from haptic_page import HAPTIC, SAMPLE_RATE, haptic_curve
from quake_wave_view import browser_filtered, decode_samples

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "batch_plots/quakes/locate/usu_quakes_waves.html"
OUT_DIR = ROOT / "batch_plots/haptic"
STEP = 0.04          # 1 つの振動の長さ [秒]
MIN_LEVEL = 0.02     # これより弱い区間には振動を置かない
FADE = 0.5           # 終わりの何秒で弱めて消すか
WAV_RATE, CARRIER = 44100, 150.0
SHARPNESS = {"yawaraka": HAPTIC["sharpness"][0], "kukkiri": HAPTIC["sharpness"][1]}
HAPTICSYNC_URL = "https://apps.apple.com/jp/app/hapticsync-vibration-studio/id6743813963"


# 波形のページから、設定(meta)と地震ごとの波形の JSON の文字列を読む
def load_page(path):
  text = Path(path).read_text(encoding="utf-8")
  meta = json.loads(re.search(r'id="meta"[^>]*>(.*?)</script>', text, re.S).group(1))
  blocks = dict(re.findall(r'<script type="application/json" id="w(\d+)">(.*?)</script>', text, re.S))
  return meta, blocks

# 拠点の 3 成分を、ページと同じフィルタをかけた [(値, 1カウントの nm/s), ...] にする
def station_traces(comps):
  return [(browser_filtered(decode_samples(item["d"])), item["s"]) for item in comps.values()]

# 強さの曲線を、書き出す区間(start 秒から length 秒。時刻は発生からの秒)の (区間の先頭からの時刻, 強さ) にする
#   levels ... コマごとの強さ、t0 ... 最初の点の発生からの秒
def window_levels(levels, t0, start, length, rate=SAMPLE_RATE, frame=HAPTIC["frame"]):
  times = t0 + (np.arange(levels.size) + 0.5) * frame / rate - start
  keep = (times >= 0) & (times <= length)
  times = times[keep]
  vals = np.clip(levels[keep] * np.clip((length - times) / FADE, 0, 1), 0, 1)
  return times, vals

# 短い連続した振動の並び(AHAP の Pattern)。区間ごとの強さの平均を、その振動の強さにする
def step_events(times, vals, length, step=STEP, sharpness=0.7):
  events = []
  for k in range(int(np.ceil(length / step - 1e-9))):
    t = k * step
    sel = (times >= t) & (times < t + step)
    v = float(vals[sel].mean()) if sel.any() else 0.0
    if v < MIN_LEVEL:
      continue
    events.append({"Event": {"Time": round(t, 3), "EventType": "HapticContinuous", "EventDuration": step,
                             "EventParameters": [{"ParameterID": "HapticIntensity", "ParameterValue": round(v, 3)},
                                                 {"ParameterID": "HapticSharpness", "ParameterValue": sharpness},
                                                 {"ParameterID": "AttackTime", "ParameterValue": 0},
                                                 {"ParameterID": "DecayTime", "ParameterValue": 0},
                                                 {"ParameterID": "ReleaseTime", "ParameterValue": 0},
                                                 {"ParameterID": "Sustained", "ParameterValue": 1}]}})
  return events

def ahap(events, description):
  return {"Version": 1.0, "Metadata": {"Project": "UsuMantenFeel", "Description": description}, "Pattern": events}

# 150 Hz の音の大きさを強さの曲線で変えた音(-0.9〜0.9)
def carrier_wave(times, vals, length, rate=WAV_RATE, carrier=CARRIER):
  t = np.arange(int(round(length * rate))) / rate
  return 0.9 * np.interp(t, times, vals, left=0, right=0) * np.sin(2 * np.pi * carrier * t)

def write_wav(path, x, rate=WAV_RATE):
  pcm = np.round(np.clip(x, -1, 1) * 32767).astype("<i2")
  with wave.open(str(path), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(pcm.tobytes())

# ファイル名の先頭(2026-05-20 23:51:57.7 M1.2 → 20260520_235157_M1.2)
def file_stem(ev, label):
  stamp = re.sub(r"[-: ]", "", ev["time"].split(".")[0])
  stamp = stamp[:8] + "_" + stamp[8:]
  mag = f"_M{ev['m']}" if ev.get("m") else ""
  return f"{stamp}{mag}_{label}"


# ファイルへのリンクのページ
#   events ... [(地震の情報, [(拠点の表示名, ファイル名の先頭, P から S までの秒 か None), ...]), ...]
def index_html(events, common, wav):
  esc = html.escape
  cards = []
  for ev, rows in events:
    kind = f"・{esc(ev['type'])}" if ev.get("type") else ""
    mag = f"M{esc(ev['m'])}" + (f"({esc(ev['mSource'])})" if ev.get("mSource") else "") if ev.get("m") else "M 不明"
    items = []
    for label, stem, sp in rows:
      links = [f'<a href="{esc(stem)}_{kind_}.ahap" download>{name}</a>' for kind_, name in (("kukkiri", "くっきり"), ("yawaraka", "やわらか"))]
      if wav:
        links.append(f'<a href="{esc(stem)}.wav" download>音</a>')
      sp_text = f'<span class="sp">S−P {sp:.1f} 秒</span>' if sp is not None else ""
      items.append(f'<li><span class="st">{esc(label)}</span>{sp_text}<span class="links">{"".join(links)}</span></li>')
    cards.append(f'<section><h2>No.{ev["no"]} {esc(ev["time"][:19])}</h2>'
                 f'<p class="meta">{mag}・深さ {esc(ev.get("depth", "?"))}{kind}</p><ul>{"".join(items)}</ul></section>')
  basis = "拠点ごとに、記録したすべての地震の中でいちばん大きい揺れを最大" if common else "地震ごとに、いちばん大きい揺れを最大"
  wav_note = "「音」は、振動と同じ強さで鳴る低い音(WAV)です。HapticSync で振動と一緒に鳴らせます。" if wav else ""
  return f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>有珠山の地震を振動で感じる</title>
<style>
:root {{ color-scheme: light dark; --bg: #fff; --ink: #1c2126; --muted: #68727d; --panel: #f4f5f7; --line: #dde1e5; --accent: #1565c0; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #14171a; --ink: #e8ecef; --muted: #9aa5af; --panel: #1f2428; --line: #2e353b; --accent: #6cb0f5; }} }}
body {{ margin: 0 auto; max-width: 720px; padding: 16px; background: var(--bg); color: var(--ink); font: 16px/1.6 -apple-system, "Hiragino Sans", "Noto Sans JP", sans-serif; }}
h1 {{ font-size: 22px; margin: 0 0 8px; }}
h2 {{ font-size: 17px; margin: 0; }}
a {{ color: var(--accent); }}
.how {{ background: var(--panel); border-radius: 12px; padding: 12px 16px; margin: 12px 0 20px; }}
.how ol {{ margin: 6px 0; padding-left: 1.4em; }}
.note, .meta {{ color: var(--muted); font-size: 14px; margin: 4px 0; }}
section {{ border-top: 1px solid var(--line); padding: 12px 0; }}
ul {{ list-style: none; margin: 6px 0 0; padding: 0; }}
section li {{ display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; padding: 6px 0; }}
.st {{ font-weight: 600; min-width: 6em; }}
.sp {{ color: var(--muted); font-size: 14px; min-width: 7em; }}
.links {{ display: flex; gap: 8px; margin-left: auto; }}
.links a {{ display: inline-block; padding: 6px 12px; border: 1px solid var(--accent); border-radius: 8px; text-decoration: none; font-size: 15px; }}
</style>
</head>
<body>
<h1>有珠山の地震を振動で感じる</h1>
<p>有珠山のまわりの満点地震計で記録した地震の揺れを、iPhone の振動で感じるためのファイルです。揺れの強さの移り変わり(P波の弱い揺れから S波の強い揺れへ)を、もとの速さで再生します。</p>
<div class="how">
<b>鳴らしかた(iPhone)</b>
<ol>
<li>App Store で <a href="{HAPTICSYNC_URL}">HapticSync - Vibration Studio</a>(無料)を入れる</li>
<li>下の一覧で、地震と拠点の「くっきり」か「やわらか」を押し、ファイルを「ファイル」App に保存する</li>
<li>HapticSync を開き、読み込み(インポート)で保存したファイルを選んで ▶ を押す</li>
</ol>
<p class="note">「くっきり」は鋭い振動、「やわらか」は鈍い振動です。{wav_note}振動はどのファイルも P波の 2 秒前から始まり、強さは{basis}にしています。Android の端末では鳴らせません。HapticSync は、この教材とは関係のない方が作ったアプリです。</p>
</div>
{"".join(cards)}
</body>
</html>
'''


def main(argv=None):
  ap = argparse.ArgumentParser(description="地震の揺れの強さを、iPhone の振動のファイル(AHAP)と音(WAV)に書き出す")
  ap.add_argument("--page", default=str(PAGE), help="波形のページ(quake_wave_view.py が書いたもの)")
  ap.add_argument("--events", type=int, nargs="*", help="地震の番号(ページの一覧の No.。省くと全部)")
  ap.add_argument("--stations", nargs="*", help="拠点(Bamba・Hokudai・Mimatsu・Nizaemn。省くと全部)")
  ap.add_argument("--lead", type=float, default=2.0, help="P波の何秒前から(既定 2)")
  ap.add_argument("--length", type=float, default=12.0, help="何秒分(既定 12)")
  ap.add_argument("--step", type=float, default=STEP, help=f"1 つの振動の長さ [秒](既定 {STEP})")
  ap.add_argument("--common", action="store_true", help="強さの基準を、その拠点の全地震でいちばん大きい揺れにする(既定は地震ごと)")
  ap.add_argument("--wav", action="store_true", help="150 Hz の音(WAV)も書く")
  ap.add_argument("-o", "--out", default=str(OUT_DIR), help=f"書き出し先(既定 {OUT_DIR.relative_to(ROOT)})")
  args = ap.parse_args(argv)

  meta, blocks = load_page(args.page)
  rate, frame = meta["rate"], meta["haptic"]["frame"]
  labels = {s["id"]: s["label"] for s in meta["stations"]}
  events = [e for e in meta["events"] if not args.events or e["no"] in args.events]
  if args.events and len(events) < len(set(args.events)):
    missing = sorted(set(args.events) - {e["no"] for e in events})
    sys.exit(f"ページに無い地震の番号: {missing}")
  common = {}
  if args.common:   # 拠点ごとの、全地震の揺れの大きさ(速度)の最大
    for e in meta["events"]:
      for name, peak in e["peaks"].items():
        common[name] = max(common.get(name, 0.0), peak[0])
  out = Path(args.out)
  out.mkdir(parents=True, exist_ok=True)
  count = 0
  listed = []
  for ev in events:
    rows = []
    waves = json.loads(blocks[str(ev["no"])])
    for name in sorted(waves):
      if args.stations and name not in args.stations:
        continue
      pick = ev["picks"].get(name)
      if not pick or pick.get("p") is None:
        print(f"No.{ev['no']} {name}: P波の時刻が無いので飛ばします")
        continue
      start = pick["p"] - args.lead
      levels = haptic_curve(station_traces(waves[name]), ev["n"], ev["t0"], rate, ref=common.get(name))
      times, vals = window_levels(levels, ev["t0"], start, args.length, rate, frame)
      label = labels.get(name, name)
      s_text = f"、S は {pick['s'] - start:.1f} 秒" if pick.get("s") is not None else ""
      desc = (f"{ev['time']} M{ev['m'] or '?'} {label} の揺れの強さ(速度・3成分・"
              f"{'全地震で共通の基準' if args.common else '地震ごとの基準'})。P は {args.lead:.1f} 秒{s_text}")
      stem = file_stem(ev, label)
      for kind, sharp in SHARPNESS.items():
        pattern = step_events(times, vals, args.length, args.step, sharp)
        (out / f"{stem}_{kind}.ahap").write_text(json.dumps(ahap(pattern, desc), ensure_ascii=False, indent=1), encoding="utf-8")
      if args.wav:
        write_wav(out / f"{stem}.wav", carrier_wave(times, vals, args.length))
      rows.append((label, stem, pick["s"] - pick["p"] if pick.get("s") is not None else None))
      print(f"{stem}: 振動 {len(pattern)} 個、強さの最大 {vals.max(initial=0):.2f}")
      count += 1
    if rows:
      listed.append((ev, rows))
  (out / "index.html").write_text(index_html(listed, args.common, args.wav), encoding="utf-8")
  print(f"{count} 件と一覧(index.html)を {out} に書きました")


if __name__ == "__main__":
  main()
