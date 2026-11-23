# plane_dem.py
# 平面直角座標系の GeoTIFF の標高(数値標高モデル)を、緯度・経度で読む
#   3Dの図(quake_3d_view.py)で、洞爺湖の湖底の形に使う(地理院のベクトルタイルの湖の等深線から作り、陸の数値標高モデルと合わせたもの)
#   GeoTIFF は、1バンドの浮動小数点、圧縮なし、北が上で、ModelPixelScale と ModelTiepoint を持つもの(Pillow で読める形)
#   座標系は JGD2011 の平面直角座標系の北海道の3つの系(XI・XII・XIII)だけ(EPSG で見分ける)
#   緯度・経度から平面直角座標への変換は、国土地理院の「平面直角座標への換算」と同じガウス・クリューゲル図法の式(河瀬 2011)
from pathlib import Path

import numpy as np

GRS80_A = 6378137.0
GRS80_F = 1 / 298.257222101
SCALE = 0.9999
# EPSG → (原点の緯度, 原点の経度)。JGD2011 / Japan Plane Rectangular CS XI・XII・XIII
ZONES = {6679: (44.0, 140.25), 6680: (44.0, 142.25), 6681: (44.0, 144.25)}

TAG_PIXEL_SCALE = 33550
TAG_TIEPOINT = 33922
TAG_GEOKEYS = 34735
KEY_PROJECTED_CRS = 3072


# 緯度・経度(度) → 平面直角座標 (X: 北向き, Y: 東向き) [m]
def to_plane(lat, lon, origin):
  lat0, lon0 = origin
  n = GRS80_F / (2 - GRS80_F)
  big_a = GRS80_A / (1 + n) * (1 + n ** 2 / 4 + n ** 4 / 64)
  alpha = [n / 2 - 2 * n ** 2 / 3 + 5 * n ** 3 / 16, 13 * n ** 2 / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240]
  c = 2 * np.sqrt(n) / (1 + n)

  def xi_eta(lat, lon):
    phi = np.radians(lat)
    lam = np.radians(np.asarray(lon, float) - lon0)
    t = np.sinh(np.arctanh(np.sin(phi)) - c * np.arctanh(c * np.sin(phi)))
    return np.arctan(t / np.cos(lam)), np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))

  def series(xi, eta):
    x = xi + sum(a * np.sin(2 * j * xi) * np.cosh(2 * j * eta) for j, a in enumerate(alpha, 1))
    y = eta + sum(a * np.cos(2 * j * xi) * np.sinh(2 * j * eta) for j, a in enumerate(alpha, 1))
    return x, y

  x, y = series(*xi_eta(lat, lon))
  x0, _ = series(*xi_eta(lat0, lon0))
  return SCALE * big_a * (x - x0), SCALE * big_a * y


class PlaneDem:
  # path ... GeoTIFF のファイル
  def __init__(self, path):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    with Image.open(path) as im:
      tags = im.tag_v2
      self.values = np.array(im, dtype=float)
      scale, tie, keys = tags.get(TAG_PIXEL_SCALE), tags.get(TAG_TIEPOINT), tags.get(TAG_GEOKEYS)
    if not scale or not tie or not keys:
      raise ValueError(f"位置の情報(GeoTIFF のタグ)がありません: {path}")
    crs = next((keys[k + 3] for k in range(4, len(keys), 4) if keys[k] == KEY_PROJECTED_CRS), None)
    if crs not in ZONES:
      raise ValueError(f"対応していない座標系です(EPSG {crs}): {path}")
    if tie[0] != 0 or tie[1] != 0:
      raise ValueError(f"左上の画素で位置を決めていない GeoTIFF には対応していません: {path}")
    self.path = Path(path)
    self.origin = ZONES[crs]
    self.step_e, self.step_n = float(scale[0]), float(scale[1])
    self.left, self.top = float(tie[3]), float(tie[4])   # 左上の画素の外側の角(東, 北)[m]

  # 緯度・経度の配列 → 標高 [m](となりの4点から直線で補う。範囲の外は NaN)
  def elevations(self, lat, lon):
    north, east = to_plane(np.asarray(lat, float), np.asarray(lon, float), self.origin)
    col = (east - self.left) / self.step_e - 0.5    # 画素の中心の位置で読む
    row = (self.top - north) / self.step_n - 0.5
    ny, nx = self.values.shape
    inside = (col >= 0) & (col <= nx - 1) & (row >= 0) & (row <= ny - 1)
    c = np.clip(col, 0, nx - 1)
    r = np.clip(row, 0, ny - 1)
    c0 = np.minimum(np.floor(c).astype(int), nx - 2)
    r0 = np.minimum(np.floor(r).astype(int), ny - 2)
    fc, fr = c - c0, r - r0
    v = self.values
    out = ((v[r0, c0] * (1 - fc) + v[r0, c0 + 1] * fc) * (1 - fr)
           + (v[r0 + 1, c0] * (1 - fc) + v[r0 + 1, c0 + 1] * fc) * fr)
    return np.where(inside, out, np.nan)
