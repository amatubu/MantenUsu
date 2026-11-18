# gsi_tiles.py
# 地理院タイル(国土地理院の地図画像)を取得して、地図の背景に使う
#   https://maps.gsi.go.jp/development/ichiran.html
#   利用するときは「出典: 地理院タイル」の表示が必要(ATTRIBUTION)
#   取得したタイルはユーザーのキャッシュフォルダに保存し、2回目からはそれを使う
import io
import math
import urllib.request

import matplotlib.image as mpimg
import numpy as np

import app_paths
from log_common import log_message

# 地図の種類: キー -> (表示名, タイルのURL)
LAYERS = {
  "std":    ("標準地図",   "https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png"),
  "relief": ("色別標高図", "https://cyberjapandata.gsi.go.jp/xyz/relief/{z}/{x}/{y}.png"),
}
ATTRIBUTION = "出典: 地理院タイル"

# ズームレベルの範囲(色別標高図は15まで)と、取得の待ち時間 [秒]
MIN_ZOOM, MAX_ZOOM = 5, 15
TIMEOUT = 5

# 取得したタイル(このアプリの実行中だけ)。取得できなかったタイルは None を入れ、何度も待たないようにする
_memory = {}

# 地理院地図(ブラウザで開くページ)の URL。中心に十字を出す(vs の c1)
#   zoom ... ズームレベル(15 で約 1.5 km 四方、10 で約 50 km 四方が1画面)
def gsi_map_url(lat, lon, zoom=15):
  return (f"https://maps.gsi.go.jp/#{zoom}/{lat:.6f}/{lon:.6f}/"
          "&base=std&ls=std&disp=1&vs=c1g1j0h0k0l0u0t0z0r0s0m0f1")

# 緯度・経度を、ズームレベル z のタイル番号(小数)にする(Webメルカトル)
def lonlat_to_tile(lon, lat, z):
  n = 2 ** z
  x = (lon + 180.0) / 360.0 * n
  y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
  return x, y

# タイル番号(左上の角)を緯度・経度にする
def tile_to_lonlat(x, y, z):
  n = 2 ** z
  lon = x / n * 360.0 - 180.0
  lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
  return lon, lat

# 表示する範囲の横幅に、タイルが max_tiles 枚ほど並ぶズームレベル
def choose_zoom(lon_min, lon_max, max_tiles=5):
  for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
    x0, _ = lonlat_to_tile(lon_min, 0, z)
    x1, _ = lonlat_to_tile(lon_max, 0, z)
    if x1 - x0 <= max_tiles:
      return z
  return MIN_ZOOM

# 1枚のタイルの画像(配列)。キャッシュに無ければ取得して保存する。取得できなければ None
def get_tile(layer, z, x, y):
  key = (layer, z, x, y)
  if key in _memory:
    return _memory[key]
  data = _tile_bytes(layer, LAYERS[layer][1], z, x, y)
  img = mpimg.imread(io.BytesIO(data), format="png") if data else None
  _memory[key] = img
  return img

# タイルの PNG のバイト列。キャッシュ(MAP_TILES_DIR/<layer>/z/x/y.png)に無ければ取得して保存する。取得できなければ None
def _tile_bytes(layer, url_format, z, x, y, ext="png"):
  path = app_paths.MAP_TILES_DIR / layer / str(z) / str(x) / f"{y}.{ext}"
  if path.exists():
    return path.read_bytes() or None
  url = url_format.format(z=z, x=x, y=y)
  try:
    req = urllib.request.Request(url, headers={"User-Agent": "UsuMantenViewer"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
      data = res.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data
  except OSError as e:
    # 範囲外(海など)のタイルは 404 になる。通信できないときもここに来る
    if getattr(e, "code", None) != 404:
      log_message(f"地図のタイルを取得できませんでした: {url} ({e})")
      return None
    # 何も無いタイル(404)も保存しておき、次は取りに行かない
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return None

# 緯度・経度の範囲を覆うタイルの一覧
# 戻り値: [(画像, (西端の経度, 東端の経度, 南端の緯度, 北端の緯度)), ...]
def tiles_for_bounds(layer, lat_min, lat_max, lon_min, lon_max):
  z = choose_zoom(lon_min, lon_max)
  x0, y0 = lonlat_to_tile(lon_min, lat_max, z)   # 北西の角
  x1, y1 = lonlat_to_tile(lon_max, lat_min, z)   # 南東の角
  tiles = []
  for x in range(int(x0), int(x1) + 1):
    for y in range(int(y0), int(y1) + 1):
      img = get_tile(layer, z, x, y)
      if img is None:
        continue
      west, north = tile_to_lonlat(x, y, z)
      east, south = tile_to_lonlat(x + 1, y + 1, z)
      tiles.append((img, (west, east, south, north)))
  return tiles


# --- 標高タイル(山体の3D表示に使う) ---
#   標高PNG(DEM10B。ズーム14、1画素が約 7 m)。色の値から標高を求める
#   https://maps.gsi.go.jp/development/demtile.html
#   x = 2^16 R + 2^8 G + B が 2^23 未満なら x × 0.01 m、2^23 より大きければ (x − 2^24) × 0.01 m、2^23 はデータなし
#   湖は水面の高さ(洞爺湖は約 84 m)で、湖底の形は入っていない。海などのデータなしは NaN
DEM_LAYER = "dem_png"
DEM_URL = "https://cyberjapandata.gsi.go.jp/xyz/dem_png/{z}/{x}/{y}.png"
DEM_ZOOM = 14
DEM_ATTRIBUTION = "出典: 地理院タイル(標高タイル)"
_dem_memory = {}

# 標高PNG の画素(0〜255 の R・G・B の配列 (..., 3))を標高 [m] にする
def decode_dem_png(rgb):
  rgb = np.asarray(rgb, dtype=np.int64)
  x = (rgb[..., 0] << 16) + (rgb[..., 1] << 8) + rgb[..., 2]
  h = np.where(x < 2 ** 23, x, x - 2 ** 24) * 0.01
  return np.where(x == 2 ** 23, np.nan, h)

# 1枚の標高タイル(256 × 256 の標高 [m] の配列)。取得できないとき(海など)はすべて NaN
def get_dem_tile(z, x, y):
  key = (z, x, y)
  if key not in _dem_memory:
    data = _tile_bytes(DEM_LAYER, DEM_URL, z, x, y)
    if data:
      img = mpimg.imread(io.BytesIO(data), format="png")
      _dem_memory[key] = decode_dem_png(np.rint(img[..., :3] * 255))
    else:
      _dem_memory[key] = np.full((256, 256), np.nan)
  return _dem_memory[key]

# 緯度・経度の点ごとの標高 [m](いちばん近い画素の値)。データなしは NaN
#   lats, lons ... 同じ形の配列
def elevations(lats, lons, z=DEM_ZOOM):
  lats, lons = np.asarray(lats, float), np.asarray(lons, float)
  n = 2 ** z
  tx = (lons + 180.0) / 360.0 * n
  ty = (1 - np.arcsinh(np.tan(np.radians(lats))) / np.pi) / 2 * n
  out = np.full(lats.shape, np.nan)
  ix, iy = np.floor(tx).astype(int), np.floor(ty).astype(int)
  px = np.clip(((tx - ix) * 256).astype(int), 0, 255)
  py = np.clip(((ty - iy) * 256).astype(int), 0, 255)
  for x, y in set(zip(ix.ravel().tolist(), iy.ravel().tolist())):
    sel = (ix == x) & (iy == y)
    out[sel] = get_dem_tile(z, x, y)[py[sel], px[sel]]
  return out
