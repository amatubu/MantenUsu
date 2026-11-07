# quake_web_map.py
# 地震の候補の震源を、ブラウザで見る地図(HTML)に書き出す
#   地震の候補の震源の画面(quake_locate_window.py)の「ブラウザで地図」で使う
#   背景は地理院タイル、地図の表示は Leaflet(どちらもインターネットから読む。登録や鍵は要らない)
#   震源などのデータは HTML の中に書き込むので、ファイル1つで開ける(サーバーは要らない)
#   近い地震として震源が決まったものだけを描く(画面の地図と同じ。番号も画面の一覧の「番号」と同じ)
import html
import json
from datetime import timedelta

from matplotlib import colormaps
from matplotlib.colors import to_hex

from hypocenter import LOCAL_KINDS, KIND_LOCAL, depth_for_plot, depth_short, depth_text
from quake_features import USU_SUMMIT
from quake_locate import GOOD_RMS   # 残差がこれ未満なら塗りつぶし、以上(と要確認の判定)なら白抜き(画面の地図と同じ)

LEAFLET_VERSION = "1.9.4"
# 震源の色: 深さで濃くする(赤の1色の濃淡)。この深さ [km] より深いものは一番濃い色
DEPTH_COLOR_MAX = 8.0
DEPTH_STEPS = (0, 2, 4, 6, 8)


# 深さ [km] の色(浅いほど薄い赤、深いほど濃い赤)
def depth_color(depth):
  frac = min(max(depth, 0.0), DEPTH_COLOR_MAX) / DEPTH_COLOR_MAX
  return to_hex(colormaps["Reds"](0.35 + 0.6 * frac))


# 地図に描くデータ(JSON にして HTML に書き込む)
#   items    ... [(候補, 結果), ...](quake_locate.locate_all の結果)
#   stations ... 拠点の位置 {名前: (緯度, 経度, 高度[m])}
def map_data(items, stations):
  quakes = []
  local = [(cand, r) for cand, r in items if r["kind"] in LOCAL_KINDS and r.get("lat") is not None]
  for i, (cand, r) in enumerate(local, 1):
    loc, q = r["loc"], cand["jma"]
    origin = r["ref"] + timedelta(seconds=loc["origin"]) if r.get("ref") is not None else cand["start"]
    depth = depth_for_plot(loc)
    good = r["kind"] == KIND_LOCAL and loc["rms"] < GOOD_RMS
    quakes.append({
      "no": i, "time": f"{origin:%Y-%m-%d %H:%M:%S.%f}"[:-5], "start": f"{cand['start']:%Y-%m-%d %H:%M:%S}",
      "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
      "depth": depth_short(loc), "depth_text": depth_text(loc), "color": depth_color(depth),
      "rms": f"{loc['rms']:.3f}", "kind": r["kind"], "good": good, "manual": bool(r.get("manual")),
      "memo": (cand.get("row") or {}).get("メモ", ""),
      "jma": {"lat": q["緯度"], "lon": q["経度"], "depth": f"{q['深さ[km]']:g}",
              "M": f"{q['M']:g}" if q.get("M") is not None else "", "name": q.get("震央地名", ""),
              "time": f"{q['日時']:%Y-%m-%d %H:%M:%S.%f}"[:-5]} if q else None})
  return {
    "quakes": quakes,
    "stations": [{"name": nm, "lat": la, "lon": lo} for nm, (la, lo, _el) in sorted(stations.items())],
    "summit": {"lat": USU_SUMMIT[0], "lon": USU_SUMMIT[1]},
    "others": len(items) - len(local), "good_rms": GOOD_RMS,
    "legend": [{"depth": d, "color": depth_color(d)} for d in DEPTH_STEPS],
  }


# HTML を書き出す
#   title ... ページの題名(画面の題名と同じ。例: 地震リスト_有珠山付近の地震)
#   戻り値 ... 描いた震源の数
def write_web_map(path, items, stations, title):
  data = map_data(items, stations)
  page = (TEMPLATE.replace("__TITLE__", html.escape(title))
          .replace("__LEAFLET__", LEAFLET_VERSION)
          .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(page, encoding="utf-8")
  return len(data["quakes"])


TEMPLATE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@__LEAFLET__/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@__LEAFLET__/dist/leaflet.js"></script>
<style>
  html, body { height: 100%; margin: 0; background: #fff; color: #222;
               font-family: -apple-system, "Hiragino Sans", "Yu Gothic UI", "Meiryo", sans-serif; }
  #map { position: absolute; inset: 0; }
  .panel { background: rgba(255,255,255,0.92); padding: 6px 10px; border-radius: 6px;
           box-shadow: 0 1px 4px rgba(0,0,0,0.3); font-size: 12px; line-height: 1.5; }
  .panel h1 { font-size: 14px; margin: 0 0 2px; }
  .panel .note { color: #555; }
  .legend-row { display: flex; align-items: center; gap: 6px; }
  .swatch { width: 12px; height: 12px; border-radius: 50%; display: inline-block; box-sizing: border-box; }
  .sym { width: 14px; text-align: center; display: inline-block; font-weight: bold; }
  .ramp { display: flex; gap: 2px; margin: 2px 0 0 20px; }
  .ramp span { display: inline-block; text-align: center; font-size: 10px; color: #444; }
  .ramp i { display: block; width: 22px; height: 8px; border-radius: 2px; }
  .mark-station { color: #000; font-size: 16px; line-height: 16px; text-align: center; text-shadow: 0 0 2px #fff, 0 0 2px #fff; }
  .mark-summit { color: #8b4513; font-size: 18px; line-height: 18px; text-align: center; font-weight: bold;
                 text-shadow: 0 0 2px #fff, 0 0 2px #fff; }
  .mark-jma { color: #008080; font-size: 18px; line-height: 18px; text-align: center; font-weight: bold;
              text-shadow: 0 0 2px #fff, 0 0 2px #fff; }
  .station-label { background: none; border: none; box-shadow: none; font-size: 11px; font-weight: bold;
                   color: #000; text-shadow: 0 0 2px #fff, 0 0 2px #fff, 0 0 2px #fff; }
  .station-label::before { display: none; }
  .popup td { padding: 0 6px 0 0; vertical-align: top; }
  .popup td:first-child { color: #666; white-space: nowrap; }
</style>
</head>
<body>
<div id="map"></div>
<script>
const DATA = __DATA__;
const ATTR = '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a>';
const tile = (id, ext, maxNative) => L.tileLayer(`https://cyberjapandata.gsi.go.jp/xyz/${id}/{z}/{x}/{y}.${ext}`,
  { attribution: ATTR, maxZoom: 18, maxNativeZoom: maxNative });
const bases = {
  "標準地図": tile("std", "png", 18),
  "淡色地図": tile("pale", "png", 18),
  "色別標高図": tile("relief", "png", 15),
  "写真": tile("seamlessphoto", "jpg", 18),
};
const map = L.map("map", { layers: [bases["淡色地図"]] });
L.control.layers(bases, null, { position: "topright" }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const icon = (cls, text, size) => L.divIcon({ className: "", html: `<div class="${cls}">${text}</div>`,
                                              iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
const bounds = [];

// 拠点と山頂
for (const s of DATA.stations) {
  L.marker([s.lat, s.lon], { icon: icon("mark-station", "▲", 16), keyboard: false })
    .bindTooltip(s.name, { permanent: true, direction: "right", offset: [6, 0], className: "station-label" })
    .addTo(map);
  bounds.push([s.lat, s.lon]);
}
L.marker([DATA.summit.lat, DATA.summit.lon], { icon: icon("mark-summit", "✚", 18), keyboard: false })
  .bindTooltip("有珠山 山頂(大有珠)").addTo(map);

// 震源(深い順に描いて、浅いものを上にする)と気象庁の震央
const rows = (pairs) => `<table class="popup">${pairs.filter(p => p[1] !== "" && p[1] != null)
  .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}</table>`;
const quakes = [...DATA.quakes].sort((a, b) => parseFloat(b.depth.replace("<", "")) - parseFloat(a.depth.replace("<", "")));
for (const q of quakes) {
  const marker = L.circleMarker([q.lat, q.lon], {
    radius: 7, color: q.good ? "#ffffff" : q.color, weight: q.good ? 1.5 : 2.5,
    fillColor: q.color, fillOpacity: q.good ? 0.95 : 0.0,
  }).addTo(map);
  const pairs = [["番号", q.no], ["発生時刻", q.time], ["深さ", q.depth_text], ["残差", q.rms + " 秒"],
                 ["判定", q.kind + (q.manual ? "(手直し)" : "")], ["緯度・経度", `${q.lat}, ${q.lon}`], ["メモ", q.memo]];
  if (q.jma) pairs.push(["気象庁", `${q.jma.time} ${q.jma.name} M${q.jma.M} 深さ ${q.jma.depth} km`]);
  marker.bindPopup(`<b>${esc(q.no)}. ${esc(q.time)}</b>` + rows(pairs));
  marker.bindTooltip(`${q.no}. ${q.time.slice(0, 16)}  深さ ${q.depth} km`);
  bounds.push([q.lat, q.lon]);
  if (q.jma) {
    L.polyline([[q.lat, q.lon], [q.jma.lat, q.jma.lon]], { color: "#008080", weight: 1, dashArray: "3 4" }).addTo(map);
    L.marker([q.jma.lat, q.jma.lon], { icon: icon("mark-jma", "×", 18), keyboard: false })
      .bindPopup(`<b>気象庁の震央(${esc(q.no)} の地震)</b>` +
                 rows([["発震時刻", q.jma.time], ["震央地名", q.jma.name], ["M", q.jma.M], ["深さ", q.jma.depth + " km"]]))
      .addTo(map);
  }
}
map.fitBounds(L.latLngBounds(bounds).pad(0.15));

// 題名と凡例
const info = L.control({ position: "topleft" });
info.onAdd = () => {
  const div = L.DomUtil.create("div", "panel");
  div.innerHTML = `<h1>${esc(document.title)}</h1>`
    + `<div class="note">近い地震として震源が決まった ${DATA.quakes.length}件`
    + (DATA.others ? `(遠い地震・決まらないもの ${DATA.others}件は除く)` : "") + `。点を押すと詳しく表示</div>`;
  L.DomEvent.disableClickPropagation(div);
  return div;
};
info.addTo(map);
const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "panel");
  div.innerHTML =
    `<div class="legend-row"><span class="swatch" style="background:${DATA.legend[2].color};border:1.5px solid #fff;box-shadow:0 0 0 1px #ccc"></span>推定した震源(残差 ${DATA.good_rms}秒未満)</div>`
    + `<div class="legend-row"><span class="swatch" style="border:2.5px solid ${DATA.legend[2].color}"></span>推定した震源(残差が大きい・要確認)</div>`
    + `<div class="legend-row" style="margin-top:2px">深さ [km](濃いほど深い)</div>`
    + `<div class="ramp">${DATA.legend.map(l => `<span><i style="background:${l.color}"></i>${l.depth}${l === DATA.legend[DATA.legend.length - 1] ? "+" : ""}</span>`).join("")}</div>`
    + `<div class="legend-row" style="margin-top:4px"><span class="sym" style="color:#008080">×</span>気象庁の震央(点線で結ぶ)</div>`
    + `<div class="legend-row"><span class="sym">▲</span>拠点</div>`
    + `<div class="legend-row"><span class="sym" style="color:#8b4513">✚</span>有珠山 山頂(大有珠)</div>`;
  return div;
};
legend.addTo(map);
</script>
</body>
</html>
"""
