# shake_compare.py
# 揺れを並べて比べるページの HTML(ファイル1つ)を書き出す(TODO 41・44。「この揺れは何?」クイズとは別のページ)
#   テーマごとに、いくつかの揺れ(または 1 つの揺れの 4 拠点)の上下動を縦に並べ、同じフィルタ・同じ時間の幅・同じ音の速さで見比べ、聴き比べる
#   テーマの一覧は etc/shake_compare.csv(1行が1段。テーマの設定は、そのテーマの最初の行に書く)
#     テーマ(番号), 題, 説明, 見どころ, 基準(0 秒にする時刻の名前), 表示の始まり[秒], 表示の終わり[秒](基準からの秒),
#     P波でそろえる(○ なら、段ごとに P波の到着の見込みを 0 秒にする。気象庁の震源リスト・USGS の地震から計算。見つからない段は基準の時刻のまま),
#     はじめの拠点(段の「拠点」が空のテーマで、最初に見せる拠点。画面で 4 拠点を切り替えられる),
#     振幅(段ごと・共通・基準に合わせる), フィルタ(最初のフィルタ。空ならフィルタなし), 音の速さ(空白で区切った倍率。最初が既定),
#     紙のフィルタ(紙の教材 shake_print.py で横に並べるフィルタ。空白で区切り、元はフィルタなし。空なら「フィルタ」と同じ 1 列。
#                 「なし」なら紙の教材に入れない(画面だけ)),
#     見方(波形・周波数。空なら波形。周波数のテーマは、段ごとの「どの高さの揺れが入っているか」(振幅スペクトル)を 1 つの図に重ねる),
#     周波数の範囲[秒](スペクトルを求める範囲。「始まり 終わり」で基準からの秒。空なら表示の全部),
#     目印の帯域(周波数の図に色を付ける帯域。「下限-上限」を空白で区切る。例: 1.8-3.9 4-10),
#     0秒の線の名前(書いてあれば、0 秒の紫の破線の上にその名前を出す。例: 外核を通り内核で反射した P波),
#     目印の線(0 秒のほかに引く紫の点線。「秒 名前」を「;」で区切る。例: 90 PP(地表で 1 回はね返った P波)),
#     振幅の基準(「始まり 終わり 名前」。書いてあれば、振幅のボタンに「名前に合わせる」が増え、その時間 [基準からの秒] の揺れで
#                 縦の大きさを段ごとに決める(ほかの大きな揺れは枠の外に切れ、何倍大きいかを出す)。例: -60 400 ベネズエラ),
#     段の名前(空なら拠点の名前), 種類(クイズと同じ種類の名前。遠くの地震の音の処理と P波・S波の線に使う), 基準の時刻,
#     拠点(書いてあればその拠点だけ。空なら 4 拠点), 2つめの地震(同じ画面に入っているもう1つの地震の気象庁の発震時刻),
#     グループ(周波数の図で同じ色にする組の名前。空なら種類の名前)
#   一覧の画面では、波形のテーマ(波形の形や S-P 時間で見分ける)と、周波数のテーマ(含まれている周波数で見分ける)に分けて並べる
#   画面でできること
#   - 波形: ピンチで拡大・縮小、1本指で移動、ダブルタップで全体に戻す(全部の段がいっしょに動く)。フィルタ A〜D・L(0.1〜0.5 Hz)、振幅を段ごと・共通
#   - 音: 「♪」の段の音、または「上から順に聴く」。速さはテーマごとに選べる。音の大きさは、それぞれ最大にそろえるか、本当の大きさの比のまま
#   - P波・S波の到着の見込みの線(気象庁の震源リストと USGS の地震)
#   波形の読み込み・符号化・描画・音にする処理は shake_quiz.py と同じ。入れるのは満点の拠点の波形だけ
#
#     python3 scripts/shake_compare.py [-o 出力の HTML] [--themes テーマの一覧の CSV]
#   省略すると batch_plots/quiz/compare.html に書き出す
import argparse
import csv
import html
from datetime import datetime, timedelta
from pathlib import Path

import app_paths
import station_names
import shake_quiz as sq
from quake_wave_view import count_steps, filter_sections, load_stream, script_json, trace_samples

THEME_FILE = "shake_compare.csv"
OUTPUT_DIR = sq.OUTPUT_DIR
TITLE = "揺れを比べてみよう"
THEME_COLUMNS = ["題", "説明", "見どころ", "基準", "表示の始まり[秒]", "表示の終わり[秒]", "P波でそろえる", "はじめの拠点", "振幅", "フィルタ", "音の速さ", "紙のフィルタ", "見方", "周波数の範囲[秒]", "目印の帯域", "振幅の基準", "0秒の線の名前", "目印の線"]
VIEWS = {"": "wave", "波形": "wave", "周波数": "freq"}
GROUP_TITLES = {"wave": "波形の形や S-P 時間で見分ける", "freq": "含まれている周波数で見分ける"}
SPEC_MIN = 0.05          # スペクトルの下の端 [Hz]
SPEC_BINS = 120          # スペクトルの帯の数(対数の間隔)
AMP_MODES = ("段ごと", "共通", "基準に合わせる")   # 基準に合わせる ... 「振幅の基準」の時間で決める(その列が要る)


def parse_time(text):
  return datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f" if "." in text else "%Y-%m-%d %H:%M:%S")


# テーマの一覧を読む。戻り値 ... [{"no", "title", ..., "rows": [{"label", "kind", "t", "station", "second"}, ...]}, ...](番号の順)
def read_themes(path=None):
  path = Path(path) if path else app_paths.ETC_DIR / THEME_FILE
  themes = {}
  with open(path, encoding="utf-8-sig", newline="") as f:
    for line, r in enumerate(csv.DictReader(f), start=2):
      r = {k: (v or "").strip() for k, v in r.items()}
      where = f"{path.name} の {line} 行目"
      no = int(r["テーマ"])
      if no not in themes:
        if not r["題"]:
          raise ValueError(f"{where}: テーマ {no} の最初の行に題がありません")
        if r["振幅"] and r["振幅"] not in AMP_MODES:
          raise ValueError(f"{where}: 振幅は {'・'.join(AMP_MODES)} のどれかです: {r['振幅']}")
        if r["フィルタ"] and r["フィルタ"] not in [key for key, *_ in sq.ALL_FILTERS]:
          raise ValueError(f"{where}: フィルタは A〜D・L か空です: {r['フィルタ']}")
        if r["はじめの拠点"] and r["はじめの拠点"] not in sq.STATIONS:
          raise ValueError(f"{where}: 満点の拠点ではありません: {r['はじめの拠点']}")
        show = (float(r["表示の始まり[秒]"]), float(r["表示の終わり[秒]"]))
        if not show[0] < show[1]:
          raise ValueError(f"{where}: 表示の始まりが終わりより後です")
        speeds = [int(v) for v in r["音の速さ"].split()] or [sq.COMMON_SPEED]
        paper = ["" if v == "元" else v for v in r["紙のフィルタ"].split()] or [r["フィルタ"]]
        if r["紙のフィルタ"] == "なし":       # 紙の教材には入れない(画面だけ)
          paper = []
        if any(v not in ["", *(key for key, *_ in sq.ALL_FILTERS)] for v in paper):
          raise ValueError(f"{where}: 紙のフィルタは 元・A〜D・L です: {r['紙のフィルタ']}")
        view = r.get("見方", "")
        if view not in VIEWS:
          raise ValueError(f"{where}: 見方は 波形・周波数 のどちらかです: {view}")
        spec_window = None
        if r.get("周波数の範囲[秒]"):
          spec_window = tuple(float(v) for v in r["周波数の範囲[秒]"].split())
          if len(spec_window) != 2 or not show[0] <= spec_window[0] < spec_window[1] <= show[1]:
            raise ValueError(f"{where}: 周波数の範囲は「始まり 終わり」で、表示の中に書きます: {r['周波数の範囲[秒]']}")
        marks = []
        for part in r.get("目印の帯域", "").split():
          try:
            lo, hi = (float(v) for v in part.split("-"))
          except ValueError:
            lo = hi = 0
          if not 0 < lo < hi:
            raise ValueError(f"{where}: 目印の帯域は「下限-上限」です: {part}")
          marks.append([lo, hi])
        amp_window = None
        if r.get("振幅の基準"):
          parts = r["振幅の基準"].split()
          try:
            amp_window = [float(parts[0]), float(parts[1]), " ".join(parts[2:]) or "前半"]
          except (ValueError, IndexError):
            amp_window = None
          if not amp_window or not show[0] <= amp_window[0] < amp_window[1] <= show[1]:
            raise ValueError(f"{where}: 振幅の基準は「始まり 終わり 名前」で、表示の中に書きます: {r['振幅の基準']}")
        if r["振幅"] == "基準に合わせる" and not amp_window:
          raise ValueError(f"{where}: 振幅を「基準に合わせる」にするときは、振幅の基準を書きます")
        lines = []
        for part in filter(None, (v.strip() for v in r.get("目印の線", "").split(";"))):
          sec, _, name = part.partition(" ")
          try:
            lines.append([float(sec), name.strip()])
          except ValueError:
            raise ValueError(f"{where}: 目印の線は「秒 名前」を「;」で区切ります: {part}")
          if not show[0] <= lines[-1][0] <= show[1]:
            raise ValueError(f"{where}: 目印の線が表示の外です: {part}")
        themes[no] = {"no": no, "title": r["題"], "text": r["説明"], "look": r["見どころ"], "ref": r["基準"], "show": show,
                      "align_p": bool(r["P波でそろえる"]), "first": r["はじめの拠点"], "common": {"共通": True, "基準に合わせる": "win"}.get(r["振幅"], False),
                      "filter": r["フィルタ"], "speeds": speeds, "paper": paper, "view": VIEWS[view], "spec_window": spec_window,
                      "marks": marks, "amp_window": amp_window,
                      "zero_label": r.get("0秒の線の名前", ""), "lines": lines, "rows": []}
      elif any(r[c] for c in THEME_COLUMNS):
        raise ValueError(f"{where}: テーマの設定({'・'.join(c for c in THEME_COLUMNS if r[c])})は、テーマの最初の行にだけ書きます")
      if r["種類"] and r["種類"] not in sq.KINDS:
        raise ValueError(f"{where}: 種類が違います: {r['種類']}")
      if r["拠点"] and r["拠点"] not in sq.STATIONS:
        raise ValueError(f"{where}: 満点の拠点ではありません: {r['拠点']}")
      themes[no]["rows"].append({"label": r["段の名前"], "kind": r["種類"], "t": parse_time(r["基準の時刻"]),
                                 "station": r["拠点"], "second": r["2つめの地震"], "group": r.get("グループ", "")})
  for th in themes.values():
    fixed = [bool(row["station"]) for row in th["rows"]]
    if any(fixed) and not all(fixed):
      raise ValueError(f"{path.name}: テーマ {th['no']} は、拠点を全部の段に書くか、全部空にします")
    th["pick"] = not any(fixed)
    if th["pick"] and not th["first"]:
      th["first"] = sq.FIRST_SOUND[0]
    if not th["pick"] and any(not row["label"] for row in th["rows"]) and len({row["t"] for row in th["rows"]}) > 1:
      raise ValueError(f"{path.name}: テーマ {th['no']} の段の名前が空です(時刻の違う段には名前を付けます)")
  # 一覧と前後の移動は、波形のテーマのあとに周波数のテーマ(それぞれ番号の順)
  return sorted(themes.values(), key=lambda th: (th["view"] != "wave", th["no"]))


# 段の P波・S波の到着の見込み(拠点ごと。段の基準の時刻からの秒。2つめの地震は p2・s2)
def row_phases(row, stations):
  q = {"kind": row["kind"], "t": row["t"], "second": row["second"]}
  out = {nm: dict(v) for nm, v in sq.phase_lines(q, stations).items()}
  for nm, v in sq.second_phases(q, stations).items():
    out.setdefault(nm, {}).update({k + "2": t for k, t in v.items()})
  return out


# 振幅スペクトル(対数の間隔の帯ごとの平均。いちばん大きい帯を 1)。v は物理量(記録の無い所は NaN)
#   なめらかにするため、範囲を 4 つ以上(1 つは 16 秒まで)に分けて重ねて平均する(Welch の方法)。戻り値 ... (帯の中心の周波数, 大きさ)
def amplitude_spectrum(v, rate, fmin=SPEC_MIN, bins=SPEC_BINS):
  import numpy as np
  from scipy import signal
  x = np.asarray(v, dtype=float)
  x = np.where(np.isnan(x), 0.0, x - (np.nanmean(x) if np.isfinite(x).any() else 0.0))
  f, power = signal.welch(x, rate, nperseg=max(256, min(len(x) // 4, int(16 * rate))))   # 16 秒より長くは分けない(0.1 Hz まで見える)
  amp = np.sqrt(power)
  edges = np.geomspace(fmin, rate / 2, bins + 1)
  centers = np.sqrt(edges[:-1] * edges[1:])
  out = np.full(bins, np.nan)
  for i in range(bins):
    m = (f >= edges[i]) & (f < edges[i + 1])
    if m.any():
      out[i] = amp[m].mean()
  ok = np.isfinite(out)
  out = np.interp(np.arange(bins), np.flatnonzero(ok), out[ok])   # 線の無い低い帯は埋める
  return [round(float(c), 4) for c in centers], [round(float(a), 4) for a in out / (out.max() or 1)]

def row_group(row):
  if row.get("group"):
    return row["group"]
  return sq.KINDS[row["kind"]][0] if row["kind"] else row["label"]


# 1つのテーマのデータ。load(t0, t1) で波形を読む
#   戻り値 ... (ページに入れる情報, [{内部の拠点名: {"U": 波形}}, ...](段ごと))
def theme_data(th, load, steps, stations):
  th = {"view": "wave", "spec_window": None, "marks": [], **th}
  before, after = th["show"]
  rate = 125 if after - before > sq.LONG_SECONDS else 250
  n = int(round((after - before) * rate))
  rows, waves = [], []
  for row in th["rows"]:
    names = [row["station"]] if row["station"] else sq.STATIONS
    phases = row_phases(row, {nm: stations[nm] for nm in names if nm in stations})
    shift = {nm: phases.get(nm, {}).get("p", 0.0) if th["align_p"] else 0.0 for nm in names}
    first = row["t"] + timedelta(seconds=before + min(shift.values()))
    st = load(first, first + timedelta(seconds=after - before + max(shift.values()) - min(shift.values())))
    data = {}
    for nm in names:
      item = trace_samples(st, nm, "U", row["t"] + timedelta(seconds=before + shift[nm]), n, steps.get((nm, "U"), 1.0), rate)
      if item:
        data[nm] = {"U": item}
    lines = {nm: {k: round(t - shift[nm], 2) for k, t in phases[nm].items()} for nm in names if nm in phases}
    label = sq.display_names(row["label"]) if row["label"] else station_names.full(row["station"])
    spec = {}
    if th["view"] == "freq":
      from quake_wave_view import decode_samples
      w0, w1 = th["spec_window"] or (before, after)
      a, b = int(round((w0 - before) * rate)), int(round((w1 - before) * rate))
      for nm, item in data.items():
        v = decode_samples(item["U"]["d"]).astype(float) * item["U"]["s"]
        for g0, g1 in item["U"]["g"]:
          v[g0:g1] = float("nan")
        freqs, spec[nm] = amplitude_spectrum(v[a:b], rate)
    rows.append({"label": label, "time": f"{row['t']:%Y-%m-%d %H:%M:%S}", "station": row["station"], "have": list(data),
                 "phases": lines, "far": row["kind"] in sq.FAR_KINDS, "group": sq.display_names(row_group(row)),
                 "aligned": th["align_p"] and any(shift[nm] for nm in data), "spec": spec})
    waves.append(data)
  info = {"no": th["no"], "title": th["title"], "text": sq.display_names(th["text"]), "look": sq.display_names(th["look"]),
          "ref": sq.display_names(th["ref"]), "rate": rate, "n": n, "t0": before, "pick": th["pick"], "first": th["first"],
          "common": th["common"], "filter": th["filter"], "speeds": th["speeds"], "paper": th["paper"], "view": th["view"],
          "spec_window": th["spec_window"], "marks": th["marks"], "amp_window": th.get("amp_window"),
          "zero_label": sq.display_names(th.get("zero_label", "")),
          "lines": [[t, sq.display_names(name)] for t, name in th.get("lines", [])], "rows": rows}
  if th["view"] == "freq":
    info["spec_f"] = amplitude_spectrum([0.0] * n, rate)[0]
  return info, waves


def write_page(path, themes):
  path = Path(path)
  rates = sorted({info["rate"] for info, _ in themes})
  meta = {
    "title": TITLE,
    "stations": [{"id": nm, "label": station_names.full(nm)} for nm in sq.STATIONS],
    "filters": [{"key": key, "low": lo, "high": hi, "sound": sound,
                 "sections": {str(rate): filter_sections((lo, hi), rate) for rate in rates}} for key, lo, hi, sound in sq.ALL_FILTERS],
    "themes": [info for info, _ in themes],
    "groupTitles": GROUP_TITLES,
    "audio": {"sections": {str(rate): {"base": sq.audio_sections(rate, False), "far": sq.audio_sections(rate, True)} for rate in rates}},
  }
  blocks = "\n".join(f'<script type="application/json" id="w{info["no"]}_{k}">{script_json(data)}</script>'
                     for info, waves in themes for k, data in enumerate(waves))
  page = (PAGE.replace("__TITLE__", html.escape(TITLE))
          .replace("__COMMON_JS__", sq.COMMON_JS)
          .replace("__META__", script_json(meta))
          .replace("__WAVES__", blocks))
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(page, encoding="utf-8")
  return path.stat().st_size


PAGE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: light dark;
  --bg: #ffffff; --panel: #f4f5f7; --ink: #1c2126; --muted: #68727d; --line: #dfe3e8; --grid: #eceff2;
  --accent: #1565c0; --ref: #7b4fb5; --p: #1565c0; --s: #d84e00; --cursor: #1b7f3b; --gap: #e4e7eb; --wave: #1c2126; --sel: #e8f3ea;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14171a; --panel: #1f2428; --ink: #e8ecef; --muted: #9aa5af; --line: #333a41; --grid: #24292e;
    --accent: #6cb0f5; --ref: #c39cf0; --p: #6cb0f5; --s: #ff9a57; --cursor: #5cc27e; --gap: #2b3137; --wave: #e8ecef; --sel: #1d2b22;
  }
}
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; height: 100%; background: var(--bg); color: var(--ink);
  font: 16px/1.45 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", sans-serif;
  -webkit-text-size-adjust: 100%; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
[hidden] { display: none !important; }
#list { max-width: 640px; margin: 0 auto; padding: 0 16px calc(24px + env(safe-area-inset-bottom)); }
#list h1 { font-size: 22px; margin: 0; padding: calc(18px + env(safe-area-inset-top)) 0 4px; }
.note { color: var(--muted); font-size: 13px; }
#themes { list-style: none; margin: 14px 0; padding: 0; display: grid; gap: 8px; }
.groupTitle { font-size: 16px; margin: 18px 0 2px; list-style: none; }
.groupTitle small { display: block; font-weight: normal; color: var(--muted); font-size: 12px; }
#themes button { width: 100%; text-align: left; padding: 10px 12px; border-radius: 10px; background: var(--panel); }
#themes b { display: block; font-size: 15px; }
#themes small { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
#view { position: fixed; inset: 0; display: flex; flex-direction: column; height: 100vh; height: 100dvh;
  padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left); }
header { display: flex; align-items: center; gap: 2px; min-height: 48px; padding: 0 4px; border-bottom: 1px solid var(--line); }
header button { min-width: 44px; height: 44px; color: var(--accent); font-size: 17px; }
header button:disabled { color: var(--line); cursor: default; }
#back { padding: 0 6px; white-space: nowrap; }
#heading { flex: 1; min-width: 0; text-align: center; font-weight: 600; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tools { display: flex; gap: 6px; padding: 6px 8px 0; align-items: center; }
.tools:last-of-type { padding-bottom: 6px; }
.seg { flex: 1 1 auto; display: flex; padding: 2px; border-radius: 8px; background: var(--panel); }
.seg button { flex: 1 1 0; height: 36px; padding: 0 4px; border-radius: 6px; font-size: 13px; color: var(--muted); white-space: nowrap; }
.seg button[aria-pressed="true"] { background: var(--bg); color: var(--ink); font-weight: 600; box-shadow: 0 0 0 1px var(--line); }
.seg button:disabled { opacity: 0.35; cursor: default; }
#amp, #viewSeg { flex: 0 0 auto; }
#stage { position: relative; flex: 1; min-height: 200px; }
#wave { position: absolute; inset: 0; width: 100%; height: 100%; display: block; touch-action: none;
  -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
#status { position: absolute; left: 16px; right: 16px; top: 40%; text-align: center; color: var(--muted); font-size: 14px; pointer-events: none; }
#bottom { border-top: 1px solid var(--line); padding: 8px; max-height: 42vh; overflow-y: auto; }
#bottom p { margin: 4px 0; font-size: 14px; }
.listenRow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 0 0 6px; }
.listen { height: 40px; padding: 0 14px; border-radius: 20px; color: var(--bg); background: var(--accent); font-size: 14px; font-weight: 600; white-space: nowrap; }
.listen.alt { color: var(--accent); background: none; box-shadow: inset 0 0 0 1.5px var(--accent); }
#speedSeg, #loudSeg { flex: 0 0 auto; }
#speedSeg button, #loudSeg button { flex: 0 0 auto; padding: 0 10px; }
.look { padding: 8px 10px; border-radius: 8px; background: var(--panel); }
.look b { color: var(--accent); }
.warn { margin: 16px 0; padding: 12px; border-radius: 8px; background: var(--panel); font-size: 14px; }
</style>
</head>
<body>
<main id="list">
  <h1>__TITLE__</h1>
  <p class="note">有珠山のまわりの満点地震計 4 か所の記録を、テーマごとに並べました。同じフィルタ・同じ時間の幅・同じ音の速さで、見比べたり聴き比べたりできます。</p>
  <noscript><p class="warn">プレビューの画面では動きません。Safari などのブラウザで開いてください。</p></noscript>
  <p class="warn" id="unsupported" hidden>このブラウザは圧縮の展開(DecompressionStream)に対応していません。iOS 16.4 以降の Safari で開いてください。</p>
  <ul id="themes"></ul>
  <p class="note">波形はピンチで拡大・縮小、1本指で左右に移動、ダブルタップで全体に戻ります(全部の段がいっしょに動きます)。フィルタは A(とても低い音)・B(低い音)・C(高い音)・D(とても高い音)です。</p>
  <p class="note">「聴く」を押すと、波形を速く再生した音が鳴り、波形の上を緑の線が動きます。音は「♪」の付いた段の記録で、段をタップするとその段の音に変わります。「上から順に聴く」で、全部の段を続けて聴けます。iPhone では、消音モード(マナーモード)でも鳴ります。</p>
</main>
<section id="view" hidden>
  <header>
    <button id="back" type="button" aria-label="一覧に戻る">‹ 一覧</button>
    <div id="heading"></div>
    <button id="prev" type="button" aria-label="前のテーマ">◀</button>
    <button id="next" type="button" aria-label="次のテーマ">▶</button>
  </header>
  <div class="tools">
    <div class="seg" id="filters" role="group" aria-label="フィルタ"></div>
    <div class="seg" id="viewSeg" role="group" aria-label="見方">
      <button id="viewFreq" type="button">周波数</button><button id="viewWave" type="button">波形</button>
    </div>
    <div class="seg" id="amp" role="group" aria-label="振幅">
      <button id="ampEach" type="button">段ごと</button><button id="ampCommon" type="button">共通</button><button id="ampWin" type="button" hidden></button>
    </div>
  </div>
  <div class="tools" id="stationBox"><div class="seg" id="stationSeg" role="group" aria-label="拠点"></div></div>
  <div id="stage"><canvas id="wave" aria-label="波形。ピンチで拡大・縮小、ドラッグで移動、ダブルタップで全体に戻す、タップで音の段を選ぶ"></canvas><div id="status"></div></div>
  <div id="bottom">
    <div class="listenRow">
      <button class="listen" id="listenOne" type="button"></button>
      <button class="listen alt" id="listenAll" type="button"></button>
    </div>
    <div class="listenRow">
      <div class="seg" id="speedSeg" role="group" aria-label="速さ"></div>
      <div class="seg" id="loudSeg" role="group" aria-label="音の大きさ">
        <button id="loudEach" type="button">それぞれ最大</button><button id="loudReal" type="button">本当の大きさの比</button>
      </div>
    </div>
    <p id="text"></p>
    <p class="look" id="look"></p>
    <p class="note" id="more"></p>
  </div>
</section>
<script type="application/json" id="meta">__META__</script>
__WAVES__
<script>
"use strict";
const META = JSON.parse(document.getElementById("meta").textContent);
const MIN_SPAN_SECONDS = 0.1;
const STORE = "manten-compare-v1";
const $ = id => document.getElementById(id);
const canvas = $("wave"), ctx = canvas.getContext("2d");
const S = { idx: -1, data: null, filter: "", common: false, real: false, station: "", sel: 0, speed: 0, mode: "wave",
            v0: 0, span: 1, dpr: 1, token: 0, listY: 0 };
let colors = {}, drawQueued = false;
__COMMON_JS__

function theme() { return META.themes[S.idx]; }
function stationOf(row) { return row.station || S.station; }
function stationLabel(id) { const st = META.stations.find(s => s.id === id); return st ? st.label : id; }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]); }

// 段 k の今の拠点の波形(フィルタを選んでいれば、フィルタをかけたもの)
function traceOf(k) {
  const th = theme(), id = stationOf(th.rows[k]);
  const tr = S.data && S.data[k][id] && S.data[k][id].U;
  if (!tr || !S.filter) return tr;
  if (!tr.f[S.filter]) {
    const f = META.filters.find(f => f.key === S.filter);
    const v = filterSamples(tr.v, f.sections[String(th.rate)]);
    tr.f[S.filter] = { v, levels: buildLevels(v), step: tr.step / FINE, gaps: tr.gaps };
  }
  return tr.f[S.filter];
}
async function loadTheme(th) {
  const out = [];
  for (let k = 0; k < th.rows.length; k++) {
    const raw = JSON.parse($(`w${th.no}_${k}`).textContent), jobs = [], data = {};
    for (const [name, comps] of Object.entries(raw)) {
      data[name] = {};
      for (const [comp, item] of Object.entries(comps)) {
        jobs.push(gunzipBase64(item.d).then(bytes => {
          const v = decodeSamples(bytes);
          data[name][comp] = { v, levels: buildLevels(v), step: item.s, gaps: item.g, f: {} };
        }));
      }
    }
    await Promise.all(jobs);
    out.push(data);
  }
  return out;
}

// --- 描く ---
function readColors() {
  const css = getComputedStyle(document.documentElement);
  for (const k of ["bg", "ink", "muted", "line", "grid", "ref", "p", "s", "gap", "wave", "cursor", "sel"]) colors[k] = css.getPropertyValue("--" + k).trim();
}
function requestDraw() {
  if (drawQueued) return;
  drawQueued = true;
  requestAnimationFrame(() => { drawQueued = false; draw(); drawCursor(); });
}
function resize() {
  const r = canvas.getBoundingClientRect();
  if (!r.width || !r.height) return;
  S.dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(r.width * S.dpr);
  canvas.height = Math.round(r.height * S.dpr);
  draw();
}
function formatAmp(nm) {
  if (nm >= 1e6) return (nm / 1e6).toPrecision(3) + " mm/s";
  if (nm >= 1e3) return (nm / 1e3).toPrecision(3) + " µm/s";
  return nm.toPrecision(3) + " nm/s";
}
function tickStep(seconds) {
  for (const m of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200]) if (m >= seconds) return m;
  return 500;
}
function labelBox(text, x, y, align, color) {
  const d = S.dpr, w = ctx.measureText(text).width, left = align === "right" ? x - w : x;
  ctx.fillStyle = colors.bg; ctx.globalAlpha = 0.78;
  ctx.fillRect(left - 2 * d, y - 1 * d, w + 4 * d, 14 * d);
  ctx.globalAlpha = 1; ctx.fillStyle = color; ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(text, left, y);
}
const FONT = `-apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif`;
function layout() {
  const d = S.dpr, axisH = Math.round(22 * d), plotH = canvas.height - axisH;
  return { axisH, plotH, blockH: plotH / theme().rows.length };
}
function draw() {
  const W = canvas.width, H = canvas.height, d = S.dpr;
  if (!W || !H || !colors.bg) return;
  ctx.fillStyle = colors.bg; ctx.fillRect(0, 0, W, H);
  const th = theme();
  if (!th || !S.data) return;
  if (freqMode()) return drawSpectra();
  const { axisH, plotH, blockH } = layout(), rate = th.rate, n = th.n;
  const timeOf = i => th.t0 + i / rate, xOfTime = t => ((t - th.t0) * rate - S.v0) / S.span * W;
  ctx.font = `${11 * d}px ${FONT}`;

  // 音の段の背景
  if (th.rows.length > 1) {
    ctx.fillStyle = colors.sel;
    ctx.fillRect(0, S.sel * blockH, W, blockH);
  }
  const step = tickStep(S.span / rate / (W / d / 64));
  ctx.lineWidth = 1; ctx.textBaseline = "middle"; ctx.textAlign = "center";
  const digits = step < 0.1 ? 2 : step < 1 ? 1 : 0;
  for (let k = Math.ceil(timeOf(S.v0) / step); k * step <= timeOf(S.v0 + S.span); k++) {
    const t = k * step, x = Math.round(xOfTime(t)) + 0.5;
    ctx.strokeStyle = colors.grid;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke();
    ctx.fillStyle = colors.muted;
    ctx.fillText(t.toFixed(digits) + " 秒", Math.min(W - 20 * d, Math.max(20 * d, x)), plotH + axisH / 2);
  }
  // 0 秒(基準の時刻)と目印の線
  const x0 = Math.round(xOfTime(0)) + 0.5;
  for (const t of [0, ...(th.lines || []).map(l => l[0])]) {
    const x = Math.round(xOfTime(t)) + 0.5;
    if (x < 0 || x > W) continue;
    ctx.strokeStyle = colors.ref; ctx.lineWidth = Math.max(1, d); ctx.setLineDash(t === 0 ? [4 * d, 3 * d] : [2 * d, 3 * d]);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, plotH); ctx.stroke(); ctx.setLineDash([]);
  }

  const a = Math.max(0, Math.floor(S.v0)), b = Math.min(n, Math.ceil(S.v0 + S.span));
  const out = { lo: 0, hi: 0 }, centers = [], halves = [];
  let allHalf = 0;
  // 「名前に合わせる」では、決めた時間の揺れで縦の大きさを決める(見えているほかの大きな揺れは枠の外)
  const win = S.common === "win" && th.amp_window;
  const wa = win ? Math.max(0, Math.round((th.amp_window[0] - th.t0) * rate)) : a;
  const wb = win ? Math.min(n, Math.round((th.amp_window[1] - th.t0) * rate)) : b;
  const over = [];
  th.rows.forEach((row, k) => {
    const tr = traceOf(k);
    if (!tr) return;
    rangeOf(tr, wa, wb, out);
    const lo = out.lo * tr.step, hi = out.hi * tr.step;
    centers[k] = (lo + hi) / 2; halves[k] = (hi - lo) / 2;
    allHalf = Math.max(allHalf, halves[k]);
    if (win) { rangeOf(tr, a, b, out); over[k] = (out.hi - out.lo) * tr.step / 2 / (halves[k] || 1); }
  });
  th.rows.forEach((row, k) => {
    const top = k * blockH, tr = traceOf(k), id = stationOf(row);
    const half = S.common === true ? allHalf : halves[k];
    if (tr) drawTrace(tr, top, blockH, centers[k], half, n);
    const sound = k === S.sel;
    ctx.font = `${sound ? "600 " : ""}${11 * d}px ${FONT}`;
    labelBox((sound ? "♪ " : "") + row.label, 6 * d, top + 2 * d, "left", sound ? colors.cursor : colors.ink);
    ctx.font = `${11 * d}px ${FONT}`;
    if (tr) labelBox("±" + formatAmp(half) + (win && over[k] > 1.5 ? `(最大はこの ${over[k] >= 100 ? Math.round(over[k]).toLocaleString() : over[k].toFixed(1)} 倍)` : ""),
                     W - 6 * d, top + 2 * d, "right", colors.muted);
    else {
      ctx.fillStyle = colors.muted; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(`記録なし(${stationLabel(id)})`, W / 2, top + blockH / 2);
    }
    // P波・S波の到着の見込み(震源からの計算)
    const ph = row.phases[id] || {};
    for (const key of ["p", "s", "p2", "s2"]) {
      if (ph[key] === undefined) continue;
      const x = Math.round(xOfTime(ph[key])) + 0.5, color = key[0] === "p" ? colors.p : colors.s;
      if (x < 0 || x > W) continue;
      ctx.strokeStyle = color; ctx.lineWidth = Math.max(1, 1.5 * d); ctx.setLineDash([4 * d, 3 * d]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + blockH); ctx.stroke(); ctx.setLineDash([]);
      ctx.font = `600 ${11 * d}px ${FONT}`;
      const text = key.toUpperCase();
      if (key[0] === "p") labelBox(text, x - 3 * d, top + blockH - 15 * d, "right", color);
      else labelBox(text, x + 3 * d, top + blockH - 15 * d, "left", color);
      ctx.font = `${11 * d}px ${FONT}`;
    }
    ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
    const yLine = Math.round(top + blockH) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, yLine); ctx.lineTo(W, yLine); ctx.stroke();
  });
  // 名前の付いた線(0 秒の線・目印の線)には「▲※1」などの印だけを書き、名前は図の下の説明に書く(スマートフォンでも重ならないように)
  const names = lineNames(th);
  ctx.font = `600 ${11 * d}px ${FONT}`;
  names.forEach(([t, _name, mark]) => {
    const x = Math.round(xOfTime(t)) + 0.5;
    if (x < 0 || x > W) return;
    const right = x > W * 0.6;
    labelBox("▲" + mark, right ? x - 4 * d : x + 4 * d, plotH - 16 * d, right ? "right" : "left", colors.ref);
  });
  ctx.font = `${11 * d}px ${FONT}`;
}
// 名前の付いた線: [[秒, 名前, 印], ...](印は 1 本なら「※」、2 本以上なら「※1」「※2」…)
function lineNames(th) {
  const names = [[0, th.zero_label], ...(th.lines || [])].filter(l => l[1]);
  return names.map(([t, name], i) => [t, name, names.length > 1 ? `※${i + 1}` : "※"]);
}
// --- 周波数の図(段ごとの振幅スペクトルを重ねる。横は対数の周波数、縦はいちばん大きいところを 1) ---
const PALETTE = ["#1565c0", "#d84e00", "#2e7d32", "#7b4fb5", "#6d4c41", "#00838f", "#c2185b"];
const DASHES = [[], [6, 4], [2, 3]];
function freqMode() { const th = theme(); return th && th.view === "freq" && S.mode === "freq"; }
function rowStyles(th) {
  const groups = [], count = {};
  return th.rows.map(row => {
    if (!groups.includes(row.group)) groups.push(row.group);
    const k = count[row.group] = (count[row.group] || 0) + 1;
    return { color: PALETTE[groups.indexOf(row.group) % PALETTE.length], dash: DASHES[(k - 1) % DASHES.length] };
  });
}
const LEGEND = { top: 6, line: 17 };
function drawSpectra() {
  const W = canvas.width, H = canvas.height, d = S.dpr, th = theme(), f = th.spec_f;
  const axisH = Math.round(30 * d), legendH = Math.round((LEGEND.top + LEGEND.line * th.rows.length + 4) * d), top = legendH, plotH = H - axisH - top;
  const fLo = 0.1, fHi = th.rate / 2, xOf = v => Math.log(v / fLo) / Math.log(fHi / fLo) * W;
  const yOf = a => top + plotH - a * (plotH - 4 * d);
  ctx.font = `${11 * d}px ${FONT}`;
  // 目印の帯域・選んでいるフィルタ
  th.marks.forEach(([lo, hi], k) => {
    ctx.fillStyle = colors.grid; ctx.fillRect(xOf(lo), top, xOf(hi) - xOf(lo), plotH);
    ctx.fillStyle = colors.muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText(`${lo}〜${hi} Hz`, (xOf(lo) + xOf(hi)) / 2, top + (2 + 14 * (k % 2)) * d);   // となりの帯の文字と重ならないよう、交互に下げる
  });
  const fl = META.filters.find(x => x.key === S.filter);
  if (fl) {
    ctx.strokeStyle = colors.ref; ctx.lineWidth = d; ctx.setLineDash([3 * d, 3 * d]);
    for (const v of [fl.low, fl.high]) { ctx.beginPath(); ctx.moveTo(xOf(v), top); ctx.lineTo(xOf(v), top + plotH); ctx.stroke(); }
    ctx.setLineDash([]);
  }
  // 目盛り
  ctx.strokeStyle = colors.line; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, top + plotH + 0.5); ctx.lineTo(W, top + plotH + 0.5); ctx.stroke();
  ctx.fillStyle = colors.muted; ctx.textBaseline = "top"; ctx.textAlign = "center";
  for (const v of [0.2, 0.5, 1, 2, 5, 10, 20, 50]) {
    if (v > fHi) continue;
    const x = xOf(v);
    ctx.strokeStyle = colors.grid; ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + plotH); ctx.stroke();
    ctx.fillText(`${v} Hz`, Math.min(W - 18 * d, Math.max(18 * d, x)), top + plotH + 3 * d);
  }
  ctx.textAlign = "left"; ctx.fillText("← 低い音(ゆっくりした揺れ)", 4 * d, top + plotH + 16 * d);
  ctx.textAlign = "right"; ctx.fillText("高い音(細かい揺れ) →", W - 4 * d, top + plotH + 16 * d);
  // 線と凡例
  const styles = rowStyles(th);
  th.rows.forEach((row, k) => {
    const a = row.spec[stationOf(row)], st = styles[k], y = (LEGEND.top + LEGEND.line * k) * d, sel = k === S.sel;
    ctx.strokeStyle = st.color; ctx.setLineDash(st.dash.map(v => v * d)); ctx.lineWidth = (sel ? 2.6 : 1.4) * d;
    if (a) {
      ctx.beginPath();
      f.forEach((v, i) => { if (v >= fLo) { const x = xOf(v), yy = yOf(a[i]); if (i === 0 || f[i - 1] < fLo) ctx.moveTo(x, yy); else ctx.lineTo(x, yy); } });
      ctx.stroke();
    }
    ctx.beginPath(); ctx.moveTo(8 * d, y + 7 * d); ctx.lineTo(30 * d, y + 7 * d); ctx.stroke(); ctx.setLineDash([]);
    ctx.font = `${sel ? "600 " : ""}${11 * d}px ${FONT}`;
    ctx.fillStyle = sel ? colors.cursor : colors.ink; ctx.textAlign = "left"; ctx.textBaseline = "top";
    ctx.fillText((sel ? "♪ " : "") + row.label + (a ? "" : `(${stationLabel(stationOf(row))} は記録なし)`), 36 * d, y);
  });
  ctx.font = `${11 * d}px ${FONT}`;
}
// 再生位置の線(鳴っている段だけ)
function drawCursor() {
  const th = theme();
  if (!th || freqMode() || AUD.theme !== S.idx || AUD.el.paused || AUD.row < 0) return;
  const W = canvas.width, d = S.dpr, { blockH } = layout();
  const i = AUD.off + AUD.el.currentTime * AUD.speed * th.rate, x = Math.round((i - S.v0) / S.span * W) + 0.5;
  if (x < 0 || x > W) return;
  ctx.strokeStyle = colors.cursor; ctx.lineWidth = Math.max(2, 2 * d);
  ctx.beginPath(); ctx.moveTo(x, AUD.row * blockH); ctx.lineTo(x, (AUD.row + 1) * blockH); ctx.stroke();
}

// --- 音 ---
//   段の波形を speed 倍の速さで再生する音(shake_quiz と同じ処理)。音の大きさは、それぞれ最大(0.9)にそろえるか、
//   テーマの全部の段で同じ倍率にする(本当の大きさの比。いちばん大きい段が 0.9)
const AUD = { el: new Audio(), url: "", theme: -1, row: -1, speed: 0, key: null, queue: [], raf: 0, off: 0 };
AUD.el.addEventListener("ended", () => {
  if (AUD.queue.length) {
    const next = AUD.queue.shift();
    setTimeout(() => { if (AUD.theme === S.idx && AUD.queue !== null) startRow(next); }, 500);
    AUD.el.pause();
    AUD.row = -1; requestDraw();
  } else stopAudio();
});
// 段 k の音にする前の波形(前処理とフィルタのあと。音のサンプリングのまま)
function soundSamples(k, speed) {
  const th = theme(), row = th.rows[k], id = stationOf(row);
  const tr = S.data && S.data[k][id] && S.data[k][id].U;
  if (!tr) return null;
  const n = tr.v.length, x = new Float64Array(n);
  let mean = 0;
  for (let i = 0; i < n; i++) mean += tr.v[i];
  mean /= n;
  for (let i = 0; i < n; i++) x[i] = (tr.v[i] - mean) * tr.step;
  for (const [a, b] of tr.gaps) x.fill(0, a, b);
  zeroPhase(x, META.audio.sections[String(th.rate)][row.far ? "far" : "base"]);
  if (S.filter) zeroPhase(x, META.filters.find(f => f.key === S.filter).sections[String(th.rate)]);
  // 「名前に合わせる」のときは、その時間だけを音にする(ほかの大きな揺れに合わせて小さくなり、聞こえなくなるのを防ぐ)
  const [a, b] = soundRange(th);
  return resampleForSound(x.subarray(a, b), th.rate * speed);
}
// 音にする範囲 [始まり, 終わり](サンプルの番号)
function soundRange(th) {
  if (S.common !== "win" || !th.amp_window) return [0, th.n];
  return [Math.max(0, Math.round((th.amp_window[0] - th.t0) * th.rate)), Math.min(th.n, Math.round((th.amp_window[1] - th.t0) * th.rate))];
}
function peakOf(y) { let p = 0; for (let i = 0; i < y.length; i++) if (Math.abs(y[i]) > p) p = Math.abs(y[i]); return p; }
function makeSound(k, speed) {
  const s = soundSamples(k, speed);
  if (!s) return null;
  let peak = peakOf(s.y);
  if (S.real) {
    peak = 0;
    for (let j = 0; j < theme().rows.length; j++) {
      const o = j === k ? s : soundSamples(j, speed);
      if (o) peak = Math.max(peak, peakOf(o.y));
    }
  }
  return wavBlob(finishSound(s.y, s.rate, peak > 0 ? 0.9 / peak : 0), s.rate);
}
async function startRow(k) {
  const blob = makeSound(k, S.speed);
  if (!blob) { if (AUD.queue.length) startRow(AUD.queue.shift()); else stopAudio(); return; }
  if (AUD.url) URL.revokeObjectURL(AUD.url);
  AUD.url = URL.createObjectURL(blob); AUD.el.src = AUD.url; AUD.theme = S.idx; AUD.row = k; AUD.speed = S.speed; AUD.key = S.filter;
  AUD.off = soundRange(theme())[0];
  S.sel = k;
  const play = AUD.el.play();
  updateListenButtons();
  try { await play; } catch (err) { stopAudio(); $("status").textContent = "音を鳴らせませんでした: " + err.message; return; }
  cancelAnimationFrame(AUD.raf);
  const tick = () => { requestDraw(); if (!AUD.el.paused) AUD.raf = requestAnimationFrame(tick); };
  tick();
}
function playing() { return AUD.theme === S.idx && AUD.theme >= 0; }
function play(all) {
  const was = playing() && AUD.all === all;
  stopAudio();
  if (was || !S.data) return;              // 鳴っている同じボタンをもう一度押したら止める
  const rows = theme().rows.map((_, k) => k);
  AUD.all = all;
  AUD.queue = all ? rows.slice(1) : [];
  startRow(all ? 0 : S.sel);
}
function stopAudio() {
  AUD.el.pause();
  cancelAnimationFrame(AUD.raf);
  if (AUD.url) URL.revokeObjectURL(AUD.url);
  AUD.url = ""; AUD.theme = -1; AUD.row = -1; AUD.speed = 0; AUD.key = null; AUD.queue = []; AUD.all = null;
  updateListenButtons();
  requestDraw();
}
function filterName() { return S.filter ? `フィルタ ${S.filter}` : "フィルタなし"; }
function updateListenButtons() {
  const th = theme();
  if (!th) return;
  $("listenOne").textContent = playing() && AUD.all === false ? "■ 止める" : `▶ ♪の段を聴く(${filterName()})`;
  $("listenAll").textContent = playing() && AUD.all === true ? "■ 止める" : "▶ 上から順に聴く";
  $("listenAll").hidden = th.rows.length < 2;
  for (const b of $("speedSeg").children) b.setAttribute("aria-pressed", Number(b.dataset.speed) === S.speed);
  $("loudEach").setAttribute("aria-pressed", !S.real); $("loudReal").setAttribute("aria-pressed", S.real);
}
$("listenOne").addEventListener("click", () => play(false));
$("listenAll").addEventListener("click", () => play(true));
$("loudEach").addEventListener("click", () => { stopAudio(); S.real = false; updateListenButtons(); });
$("loudReal").addEventListener("click", () => { stopAudio(); S.real = true; updateListenButtons(); });

// --- 拡大・移動(全部の段がいっしょ) ---
function setView(v0, span) {
  const th = theme();
  if (!th) return;
  S.span = Math.min(th.n, Math.max(MIN_SPAN_SECONDS * th.rate, span));
  S.v0 = Math.min(th.n - S.span, Math.max(0, v0));
  requestDraw();
}
function resetView() { const th = theme(); if (th) setView(0, th.n); }
const pointers = new Map();
let gesture = null, lastTap = null;
function localX(e) { return e.clientX - canvas.getBoundingClientRect().left; }
function startGesture() {
  const p = [...pointers.values()], w = canvas.clientWidth;
  if (p.length === 1) gesture = { kind: "pan", x: p[0].x, v0: S.v0, span: S.span, w };
  else if (p.length >= 2) {
    const mid = (p[0].x + p[1].x) / 2;
    // 2 本指: 指の横の開き(縦の開きは使わない)が 1 割以上変わるまでは、拡大縮小せずに左右に動かすだけにする
    gesture = { kind: "pinch", dist: Math.max(20, Math.abs(p[0].x - p[1].x)), zooming: false,
                anchor: S.v0 + mid / w * S.span, span: S.span, w };
  } else gesture = null;
}
canvas.addEventListener("pointerdown", e => {
  e.preventDefault();
  if (e.isPrimary) pointers.clear();     // 新しいタッチの始まり: 終わりの知らせが来なかった古い指を消す(残ると 1 本指の移動が拡大縮小になる)
  try { canvas.setPointerCapture(e.pointerId); } catch (_) {}
  pointers.set(e.pointerId, { x: localX(e), y: e.clientY, x0: localX(e), y0: e.clientY, t0: e.timeStamp, moved: false });
  if (pointers.size > 1) for (const p of pointers.values()) p.moved = true;
  startGesture();
});
canvas.addEventListener("pointermove", e => {
  const me = pointers.get(e.pointerId);
  if (!me) return;
  e.preventDefault();
  me.x = localX(e); me.y = e.clientY;
  if (Math.hypot(me.x - me.x0, me.y - me.y0) > 10) me.moved = true;
  const p = [...pointers.values()], th = theme();
  if (gesture && gesture.kind === "pan") setView(gesture.v0 - (me.x - gesture.x) / gesture.w * gesture.span, gesture.span);
  else if (gesture && gesture.kind === "pinch" && p.length >= 2) {
    const dist = Math.max(20, Math.abs(p[0].x - p[1].x)), mid = (p[0].x + p[1].x) / 2;
    if (!gesture.zooming && Math.abs(dist / gesture.dist - 1) > 0.1) gesture.zooming = true;
    const span = gesture.zooming ? Math.min(th.n, Math.max(MIN_SPAN_SECONDS * th.rate, gesture.span * gesture.dist / dist)) : gesture.span;
    setView(gesture.anchor - mid / gesture.w * span, span);
  }
});
// タップした段を、音にする段にする
function selectRow(y) {
  const th = theme(), plotH = canvas.clientHeight - 22;
  const k = freqMode() ? Math.floor((y - LEGEND.top) / LEGEND.line) : Math.floor(y / (plotH / th.rows.length));
  if (k < 0 || k >= th.rows.length || k === S.sel) return;
  stopAudio();
  S.sel = k;
  requestDraw();
}
function endPointer(e) {
  const me = pointers.get(e.pointerId);
  if (!me) return;
  pointers.delete(e.pointerId);
  if (e.type === "pointerup" && !me.moved && pointers.size === 0 && e.timeStamp - me.t0 < 300) {
    selectRow(e.clientY - canvas.getBoundingClientRect().top);
    if (lastTap && e.timeStamp - lastTap.t < 350 && Math.hypot(me.x - lastTap.x, me.y - lastTap.y) < 30) {
      resetView(); lastTap = null;
    } else lastTap = { t: e.timeStamp, x: me.x, y: me.y };
  }
  startGesture();
}
canvas.addEventListener("pointerup", endPointer);
canvas.addEventListener("pointercancel", endPointer);
canvas.addEventListener("lostpointercapture", endPointer);
canvas.addEventListener("wheel", e => {
  e.preventDefault();
  const x = localX(e), w = canvas.clientWidth, th = theme();
  if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) return setView(S.v0 + e.deltaX / w * S.span, S.span);
  const anchor = S.v0 + x / w * S.span, span = S.span * Math.exp(e.deltaY * 0.002);
  const next = Math.min(th.n, Math.max(MIN_SPAN_SECONDS * th.rate, span));
  setView(anchor - x / w * next, next);
}, { passive: false });
canvas.addEventListener("touchmove", e => e.preventDefault(), { passive: false });
for (const type of ["gesturestart", "gesturechange"]) canvas.addEventListener(type, e => e.preventDefault(), { passive: false });

// --- ボタン ---
function buildFilterButtons() {
  for (const f of [{ key: "", name: "元" }, ...META.filters.map(f => ({ key: f.key, name: f.key }))]) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = f.name; b.dataset.key = f.key;
    b.setAttribute("aria-label", f.key ? `フィルタ ${f.key}` : "フィルタなし");
    b.addEventListener("click", () => setFilter(f.key));
    $("filters").append(b);
  }
  for (const st of META.stations) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = st.label; b.dataset.id = st.id;
    b.addEventListener("click", () => setStation(st.id));
    $("stationSeg").append(b);
  }
}
function setFilter(key) {
  if (playing()) stopAudio();
  S.filter = key;
  for (const b of $("filters").children) b.setAttribute("aria-pressed", b.dataset.key === key);
  updateListenButtons(); showMore(); requestDraw();
}
// v ... false(段ごと)・true(共通)・"win"(振幅の基準の時間に合わせる)
function setCommon(v) {
  if (playing() && (v === "win") !== (S.common === "win")) stopAudio();
  S.common = v;
  $("ampCommon").setAttribute("aria-pressed", v === true); $("ampEach").setAttribute("aria-pressed", v === false);
  $("ampWin").setAttribute("aria-pressed", v === "win");
  requestDraw();
}
function setMode(mode) {
  S.mode = mode;
  $("viewFreq").setAttribute("aria-pressed", mode === "freq"); $("viewWave").setAttribute("aria-pressed", mode === "wave");
  $("amp").hidden = freqMode();
  showMore(); requestDraw();
}
$("viewFreq").addEventListener("click", () => setMode("freq"));
$("viewWave").addEventListener("click", () => setMode("wave"));
function setStation(id) {
  if (playing()) stopAudio();
  S.station = id;
  for (const b of $("stationSeg").children) b.setAttribute("aria-pressed", b.dataset.id === id);
  requestDraw();
}
function setSpeed(sp) { if (playing()) stopAudio(); S.speed = sp; updateListenButtons(); showMore(); }
function showMore() {
  const th = theme(), f = META.filters.find(f => f.key === S.filter);
  if (freqMode()) {
    const w = th.spec_window;
    $("more").textContent = `周波数の図は、${w ? `${w[0]}〜${w[1]} 秒` : "表示の全部"}の揺れに、どの高さ(周波数)の揺れがどれくらい入っているか(振幅スペクトル)を示します。`
      + "線ごとに、いちばん大きいところを 1 にそろえています(揺れの大きさではなく、形を比べます)。凡例をタップすると、その揺れの音になります。"
      + (f ? `紫の点線は、フィルタ ${f.key}(${f.low}〜${f.high} Hz)の範囲です。` : "")
      + "「波形」に切り替えると、波形も見られます。";
    return;
  }
  $("more").textContent = `0 秒は「${th.ref}」です(紫の破線)。`
    + lineNames(th).map(([t, name, mark]) => `${mark}(${t ? `${t} 秒の紫の点線` : "0 秒の線"})は「${name}」です。`).join("")
    + (th.rows.some(r => Object.keys(r.phases).length) ? "青の P・赤の S の破線は、震源から計算した到着の見込みです(実際とは 1〜2 秒ずれることがあります)。" : "")
    + (f ? `フィルタ ${f.key} は ${f.low}〜${f.high} Hz(${f.sound})の揺れだけを通します。` : "")
    + `×${S.speed} は ${S.speed} 倍の速さで、揺れの周波数も ${S.speed} 倍の音になります(5 Hz の揺れが ${5 * S.speed} Hz の音に)。`
    + "「それぞれ最大」は段ごとに音の大きさをそろえ、「本当の大きさの比」は揺れの大きさの違いがそのまま音の大きさの違いになります。";
}

// --- 画面の切り替え ---
async function openTheme(idx) {
  stopAudio();
  const th = META.themes[idx], token = ++S.token;
  if ($("view").hidden) S.listY = window.scrollY;
  S.idx = idx; S.data = null; S.sel = 0; S.real = th.common === true;
  $("list").hidden = true; $("view").hidden = false;
  $("heading").textContent = th.title;
  $("prev").disabled = idx === 0;
  $("next").disabled = idx === META.themes.length - 1;
  $("stationBox").hidden = !th.pick;
  if (th.pick) setStation(th.first);
  $("speedSeg").textContent = "";
  for (const sp of th.speeds) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = `×${sp}`; b.dataset.speed = sp;
    b.addEventListener("click", () => setSpeed(sp));
    $("speedSeg").append(b);
  }
  S.speed = th.speeds[0];
  $("ampWin").hidden = !th.amp_window;
  if (th.amp_window) $("ampWin").textContent = `${th.amp_window[2]}に合わせる`;
  setCommon(th.common);
  $("viewSeg").hidden = th.view !== "freq";
  S.mode = th.view;
  setFilter(th.filter);
  setMode(th.view);
  $("text").textContent = th.text;
  $("look").innerHTML = th.look ? `<b>ここを見てみよう</b> ${escapeHtml(th.look)}` : "";
  $("look").hidden = !th.look;
  $("bottom").scrollTop = 0;
  $("status").textContent = "波形を読み込んでいます…";
  resize();
  try {
    const data = await loadTheme(th);
    if (token !== S.token) return;
    S.data = data;
    $("status").textContent = "";
    resetView();
  } catch (err) {
    if (token === S.token) $("status").textContent = "波形を読み込めませんでした: " + err.message;
  }
}
function showList() {
  stopAudio();
  S.token++; S.idx = -1; S.data = null;
  $("view").hidden = true; $("list").hidden = false;
  window.scrollTo(0, S.listY);
}
function route() {
  const idx = META.themes.findIndex(t => String(t.no) === location.hash.slice(1));
  if (idx >= 0 && typeof DecompressionStream !== "undefined") openTheme(idx); else showList();
}
function buildList() {
  let last = null;
  for (const th of META.themes) {
    if (th.view !== last) {
      last = th.view;
      const h = document.createElement("li");
      h.className = "groupTitle";
      h.innerHTML = `${escapeHtml(META.groupTitles[th.view])}`
        + (th.view === "freq" ? "<small>どの高さ(周波数)の揺れが入っているかを、1 つの図に重ねて比べます</small>"
                              : "<small>揺れの形・長さ・P波と S波の間(S-P 時間)・届く順などを比べます</small>");
      $("themes").append(h);
    }
    const li = document.createElement("li"), b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `<b>${th.no}. ${escapeHtml(th.title)}</b><small>${escapeHtml(th.rows.length)} 段`
      + `${th.pick ? "・拠点を切り替えられます" : ""}</small>`;
    b.addEventListener("click", () => { location.hash = String(th.no); });
    li.append(b); $("themes").append(li);
  }
}
$("back").addEventListener("click", () => { location.hash = ""; });
function step(delta) {
  const th = META.themes[S.idx + delta];
  if (th) location.replace("#" + th.no);
}
$("prev").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("ampEach").addEventListener("click", () => setCommon(false));
$("ampCommon").addEventListener("click", () => setCommon(true));
$("ampWin").addEventListener("click", () => setCommon("win"));
window.addEventListener("hashchange", route);
new ResizeObserver(resize).observe(canvas);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { readColors(); requestDraw(); });
if (typeof DecompressionStream === "undefined") $("unsupported").hidden = false;
readColors(); buildFilterButtons(); buildList(); route();
</script>
</body>
</html>
"""


def main():
  from hypocenter import load_stations
  parser = argparse.ArgumentParser(description="揺れを並べて比べるページの HTML を書き出す(テーマは etc/shake_compare.csv)")
  parser.add_argument("-o", "--output", help="出力する HTML(省略すると batch_plots/quiz/compare.html)")
  parser.add_argument("--themes", help="テーマの一覧の CSV(省略すると etc/shake_compare.csv)")
  args = parser.parse_args()
  steps = count_steps(app_paths.ETC_DIR)
  stations = {nm: c for nm, c in load_stations(app_paths.ETC_DIR).items() if nm in sq.STATIONS}
  themes = []
  for th in read_themes(args.themes):
    info, waves = theme_data(th, load_stream, steps, stations)
    themes.append((info, waves))
    missing = [f"{row['label']}: {', '.join(station_names.full(nm) for nm in ([row['station']] if row['station'] else sq.STATIONS) if nm not in row['have'])}"
               for row in info["rows"] if len(row["have"]) < (1 if row["station"] else len(sq.STATIONS))]
    print(f"  テーマ{th['no']:2d} {th['title']}  {len(info['rows'])} 段 {info['rate']} Hz"
          + (f"  記録なし: {' / '.join(missing)}" if missing else ""))
  out = Path(args.output) if args.output else OUTPUT_DIR / "compare.html"
  size = write_page(out, themes)
  print(f"書き出しました({len(themes)} テーマ、{size / 1e6:.1f} MB): {out}")

if __name__ == "__main__":
  main()
