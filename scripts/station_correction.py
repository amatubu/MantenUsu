# station_correction.py
# 拠点ごとの補正(観測点・波ごとの決まったずれ)を読み、震源を求めるときに到着時刻から差し引く
#   補正値は etc/station_corrections.txt に書く(ユーザーの設定の etc に同じ名前があれば、そちらを使う)
#   保存した到着時刻(pick_store)は書き換えない。震源を求める計算のときだけ、読んだ時刻から補正値を差し引く
#   補正値は「観測 − 計算」の秒。正なら、その観測点には計算より遅れて届く(差し引くと早くなる)
#   使うかどうかは、震源推定の画面の「拠点の補正」で切り替え、保存した到着時刻のパラメータ station_correction に持つ
#
#   地震が増えたら求め直す:
#     python3 scripts/station_correction.py            ... 有珠山リストの地震から求めて表示する(ファイルは変えない)
#     python3 scripts/station_correction.py --write    ... 求めた値をユーザーの設定の etc/station_corrections.txt に書く
import sys
from pathlib import Path

import numpy as np

import app_paths

FILE_NAME = "station_corrections.txt"
PHASES = ("P", "S")
# 使うかどうかの既定(保存した到着時刻にパラメータが無いとき)
DEFAULT_ON = True
# 補正値を求めるのに要る地震の数(これより少ない観測点・波は補正しない)
MIN_EVENTS = 5
# 求めるときのくり返しの回数
ITERATIONS = 6

_cache = {"key": None, "values": {}}


def path():
  return app_paths.ETC_DIR / FILE_NAME

# 補正値を読む。戻り値 ... {(観測点の名前, "P" か "S"): 秒}(ファイルが無ければ空)
def load(file=None):
  file = Path(file) if file is not None else path()
  try:
    st = file.stat()
  except OSError:
    return {}
  key = (str(file), st.st_mtime_ns, st.st_size)
  if _cache["key"] == key:
    return dict(_cache["values"])
  values = {}
  for line in file.read_text(encoding="utf-8").splitlines():
    line = line.split("#", 1)[0].strip()
    if not line:
      continue
    name, phase, sec = line.split()[:3]
    if phase not in PHASES:
      raise ValueError(f"{file}: 波は P か S にしてください: {line}")
    values[(name, phase)] = float(sec)
  _cache.update(key=key, values=values)
  return dict(values)

# 結果の保存(地震の候補の震源)を見分けるための文字列。補正値のファイルが変わったら作り直すため
def signature():
  return repr(sorted(load().items()))

# 保存したパラメータから、補正を使うかを読む(パラメータ station_correction: 1 なら使う、0 なら使わない)
def read_enabled(params, default=DEFAULT_ON):
  val = (params or {}).get("station_correction")
  return bool(val) if isinstance(val, (int, float)) else default

# 到着時刻から補正値を差し引く。picks・s_picks は {観測点: 時刻(秒)}。補正値の無い観測点はそのまま
def apply(picks, s_picks, values=None):
  values = load() if values is None else values
  return ({nm: t - values.get((nm, "P"), 0.0) for nm, t in picks.items()},
          {nm: t - values.get((nm, "S"), 0.0) for nm, t in s_picks.items()})

# 説明の文字列(結果の欄・図の注意書き)。例: 「拠点の補正: あり(9拠点)」
def label(enabled):
  if not enabled:
    return "拠点の補正: なし"
  n = len({nm for nm, _ph in load()})
  return f"拠点の補正: あり({n}拠点)" if n else "拠点の補正: あり(補正値のファイルが無い)"


# --- 補正値を求める ---

# events ... [(P波 {観測点: 秒}, S波 {観測点: 秒})]。計算に使う到着時刻だけ
# locate ... (P波, S波) から (震源, {(観測点, 波): 残差}) を返す関数
# 補正後に残った残差の中央値を足していき、P波・S波それぞれ平均を 0 にする(発生時刻・速度とのかね合いを避ける)
#   戻り値 ... ({(観測点, 波): 秒}, {(観測点, 波): 使った地震の数})
def estimate(events, locate, min_events=MIN_EVENTS, iterations=ITERATIONS):
  values, counts = {}, {}
  for _ in range(iterations):
    resids = [locate(*apply(p, s, values))[1] for p, s in events]
    new, counts = {}, {}
    for key in {k for res in resids for k in res}:
      vals = [res[key] for res in resids if key in res]
      counts[key] = len(vals)
      if len(vals) >= min_events:
        new[key] = values.get(key, 0.0) + float(np.median(vals))
    for ph in PHASES:
      keys = [k for k in new if k[1] == ph]
      if keys:
        mean = float(np.mean([new[k] for k in keys]))
        for k in keys:
          new[k] -= mean
    values = new
  return values, counts

def format_file(values, counts, note=""):
  lines = ["# 拠点ごとの補正(観測点・波ごとの決まったずれ)。station_correction.py が読む",
           "#   1行に1つ: 観測点の名前  波(P か S)  補正[秒](観測 − 計算。正は計算より遅れて届く)  # 使った地震の数",
           "#   震源を求めるときに、読んだ到着時刻からこの値を差し引く(保存した到着時刻は変えない)",
           "#   深さで速度が変わる構造(velocity_model の勾配の構造)で求めた値"]
  if note:
    lines.append(f"#   {note}")
  for (nm, ph), v in sorted(values.items(), key=lambda kv: (kv[0][1], kv[0][0])):
    lines.append(f"{nm:8s} {ph}  {v:+.3f}  # {counts.get((nm, ph), 0)}件")
  return "\n".join(lines) + "\n"

# 有珠山リストの地震(種別が「地震」で、到着時刻を保存したもの)から求める
def estimate_from_usu_list():
  from datetime import datetime
  import external_stations
  import pick_store
  import quake_list
  import quake_locate
  import velocity_model
  from hypocenter import LocalProjection, load_stations, locate_event
  stations = load_stations(app_paths.ETC_DIR)
  proj = LocalProjection(list(stations.values()))
  coords = {**stations, **external_stations.coords()}
  model = velocity_model.get(velocity_model.GRADIENT)
  events = []
  for e in quake_list.load()[quake_list.USU]:
    if quake_list.entry_type(e) != quake_list.TYPE_QUAKE:
      continue
    cand = {"start": datetime.strptime(e["start"], quake_list.TIME_FORMAT),
            "end": datetime.strptime(e["end"], quake_list.TIME_FORMAT), "jma": None}
    t0, t1 = quake_locate.candidate_window(cand)
    _path, rec = pick_store.find_record(t0, t1)
    if rec is None:
      continue
    p, s, _pm, _sm = pick_store.record_seconds(rec, t0)
    excluded, s_excluded = set(rec.get("excluded", [])), set(rec.get("s_excluded", []))
    p = {nm: t for nm, t in p.items() if nm in coords and nm not in excluded}
    s = {nm: t for nm, t in s.items() if nm in p and nm not in s_excluded}
    events.append((p, s))

  def xyz(names):
    return {nm: np.array([*proj.to_xy(*coords[nm][:2]), coords[nm][2] / 1000]) for nm in names}

  def predict(loc, names, phase):
    if not names:
      return {}
    pos = np.array([xyz([nm])[nm] for nm in names])
    tt = velocity_model.travel_function(model, pos, [phase] * len(names))(
      np.array([loc["x"]]), np.array([loc["y"]]), np.array([loc["depth"]]))
    return {nm: loc["origin"] + float(tt[0][i]) for i, nm in enumerate(names)}

  def locate(p, s):
    r = locate_event(xyz(p), p, s, proj, model=model)
    loc = r["loc"]
    if loc is None:
      return r, {}
    pp, ps = predict(loc, list(p), "P"), predict(loc, list(s), "S")
    res = {(nm, "P"): p[nm] - pp[nm] for nm in p}
    res.update({(nm, "S"): s[nm] - ps[nm] for nm in s})
    return r, res

  values, counts = estimate(events, locate)
  return values, counts, len(events)


def main(argv):
  from datetime import date
  values, counts, n = estimate_from_usu_list()
  text = format_file(values, counts, f"{date.today():%Y-%m-%d} に有珠山リストの地震 {n}件から求めた")
  if "--write" in argv:
    out = app_paths.USER_ETC_DIR / FILE_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"書き出しました: {out}")
  else:
    print(text, end="")


if __name__ == "__main__":
  main(sys.argv[1:])
