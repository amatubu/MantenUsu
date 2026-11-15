# make_cf_image.py(ロガーの CF カードの初期化イメージを作る)のテスト
#   ツールが保存した設定値のファイル(/Volumes/home/_満天地震計/set)と同じバイト列になることを確かめる
import hashlib

import pytest

import make_cf_image as mc

# ツールの「馬場邸(2枚)」の設定値ファイルの先頭 0x40 バイト(残りは 0)
BAMBA2 = bytes.fromhex(
  "31303730343332303033000000000201027501ba01720001343233322e36"
  "3030302c4e2c31343034372e313030302c453030303032302e3003e88d10"
  "8d118d12")


def test_make_setting_matches_tool_file():
  h = mc.make_setting("1070432003", (42, 32.6), (140, 47.1), 20, ["8D10", "8D11", "8D12"], cards=2, sensor=1000)
  assert len(h) == 1024 and h[:0x40] == BAMBA2 and h[0x40:] == bytes(1024 - 0x40)


def test_position_text():
  assert mc.position_text(42, 32.4437, 140, 51.4218, 190) == "4232.4437,N,14051.4218,E000190.0"
  with pytest.raises(mc.SettingError):
    mc.position_text(42, 61, 140, 0, 0)


@pytest.mark.parametrize("kw", [dict(id_="123"), dict(channels=["8D10", "8D11"]), dict(cards=0), dict(sensor=70000)])
def test_make_setting_rejects_bad_values(kw):
  args = dict(id_="1070432003", lat=(42, 32.6), lon=(140, 47.1), alt=20, channels=["8D10", "8D11", "8D12"])
  args.update(kw)
  with pytest.raises(mc.SettingError):
    mc.make_setting(**args)


# 前に記録したカードから読んだ設定値ファイル(0x40 から後ろに校正値などが残り、カードの番号が 01)は、
# 後ろを 0 にし、カードの番号を 00 にして使う
def test_read_setting_cleans_leftovers(tmp_path):
  raw = bytearray(BAMBA2 + bytes(1024 - 0x40))
  raw[0x16] = 1
  raw[0x40:0x48] = b"\x02\x00\x01\x02\x00\x00\x03\xc2"
  raw[0x3f5:0x3fe] = b"-\x8fRS1865-"
  (tmp_path / "set").write_bytes(bytes(raw))
  assert mc.read_setting(tmp_path / "set") == BAMBA2 + bytes(1024 - 0x40)
  (tmp_path / "short").write_bytes(bytes(100))
  with pytest.raises(mc.SettingError):
    mc.read_setting(tmp_path / "short")


def test_describe():
  info = mc.describe(BAMBA2 + bytes(1024 - 0x40))
  assert info["ID"] == "1070432003" and info["標高"] == "20.0 m" and info["カードの枚数"] == 2
  assert info["チャンネル"] == "8D10 8D11 8D12" and info["Sensor check level"] == 1000 and info["知らない値"] is None


def test_write_image_and_main(tmp_path):
  setting = BAMBA2 + bytes(1024 - 0x40)
  size = 1024 * 20
  digest = mc.write_image(tmp_path / "a.img", setting, size)
  data = (tmp_path / "a.img").read_bytes()
  assert data == setting + b"\xff" * (size - 1024) and digest == hashlib.sha256(data).hexdigest()
  args = ["-o", str(tmp_path / "b.img"), "--size", str(size), "--id", "1070432003", "--lat", "42", "32.6",
          "--lon", "140", "47.1", "--alt", "20", "--channels", "8D10", "8D11", "8D12", "--sensor", "1000"]
  assert mc.main(args) == 0
  assert (tmp_path / "b.img").read_bytes() == data
  assert (tmp_path / "b.img.sha256").read_text().split()[0] == digest
  # もうあるときは上書きしない
  assert mc.main(args) == 1
  # 設定値のファイルだけ
  assert mc.main(["--setting-only", "-o", str(tmp_path / "s"), "--setting", str(tmp_path / "a.img")]) == 1
  (tmp_path / "set").write_bytes(setting)
  assert mc.main(["--setting-only", "-o", str(tmp_path / "s"), "--setting", str(tmp_path / "set")]) == 0
  assert (tmp_path / "s").read_bytes() == setting


# -o - で標準出力に流す(ファイルを作らずに dd でカードへ書く)
def test_stream_to_stdout(tmp_path):
  import subprocess
  import sys
  from pathlib import Path
  script = Path(__file__).resolve().parent.parent / "scripts" / "make_cf_image.py"
  setting = BAMBA2 + bytes(1024 - 0x40)
  (tmp_path / "set").write_bytes(setting)
  r = subprocess.run([sys.executable, str(script), "-o", "-", "--size", "8192", "--setting", str(tmp_path / "set")],
                     capture_output=True)
  assert r.returncode == 0
  assert r.stdout == setting + b"\xff" * (8192 - 1024)
  assert hashlib.sha256(r.stdout).hexdigest() in r.stderr.decode()
