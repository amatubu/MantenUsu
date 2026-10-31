# gsi_tiles.py
# 地理院タイル(国土地理院の地図画像)を取得して、地図の背景に使う
#   https://maps.gsi.go.jp/development/ichiran.html
#   利用するときは「出典: 地理院タイル」の表示が必要(ATTRIBUTION)
#   取得したタイルはユーザーのキャッシュフォルダに保存し、2回目からはそれを使う
import io
import math
import urllib.request

import matplotlib.image as mpimg

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
  path = app_paths.MAP_TILES_DIR / layer / str(z) / str(x) / f"{y}.png"
  data = None
  if path.exists():
    data = path.read_bytes()
  else:
    url = LAYERS[layer][1].format(z=z, x=x, y=y)
    try:
      req = urllib.request.Request(url, headers={"User-Agent": "UsuMantenViewer"})
      with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        data = res.read()
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(data)
    except OSError as e:
      # 範囲外(海など)のタイルは 404 になる。通信できないときもここに来る
      if getattr(e, "code", None) != 404:
        log_message(f"地図のタイルを取得できませんでした: {url} ({e})")
  img = mpimg.imread(io.BytesIO(data), format="png") if data else None
  _memory[key] = img
  return img

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
