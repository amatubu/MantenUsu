# gsi_tiles.gsi_map_url(震源推定の配置図をクリックしたときに開く地理院地図の URL)のテスト
import gsi_tiles


def test_gsi_map_url_centers_with_crosshair():
  url = gsi_tiles.gsi_map_url(42.544, 140.839)
  assert url.startswith("https://maps.gsi.go.jp/#15/42.544000/140.839000/")
  assert "vs=c1" in url   # 中心の十字を出す


def test_gsi_map_url_zoom():
  assert gsi_tiles.gsi_map_url(43.0, 141.0, zoom=10).startswith("https://maps.gsi.go.jp/#10/")
