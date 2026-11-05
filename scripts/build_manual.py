# build_manual.py
# 操作マニュアル(docs/manual/manual.md)を PDF にする。ビルド(build_mac.sh・build_win.bat)から呼び、アプリと一緒に zip に入れる
#   1. Markdown を HTML にする(Python の markdown パッケージ。pip install markdown)
#   2. HTML を、Chrome か Edge の画面を出さない印刷(--print-to-pdf)で PDF にする
#      (Windows には Edge が必ずあり、Mac は Chrome を使う。どちらも無ければ HTML だけ作る)
#   画像(docs/manual/images)が無いところは、「画面イメージ(ファイル名)」の枠を入れる(スクリーンショットの撮り忘れが分かるように)
#
# 使い方(プロジェクトのフォルダで):
#   python3 scripts/build_manual.py                     ... docs/manual/_build/UsuMantenViewer_manual.pdf を作る
#   python3 scripts/build_manual.py --out <PDF のパス>  ... 作る場所を指定する
#   python3 scripts/build_manual.py --html              ... HTML だけ作る(ブラウザで見た目を確かめるとき)
#   使うブラウザを指定するとき: 環境変数 MANUAL_BROWSER にブラウザの実行ファイルのパス
import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
MANUAL_DIR = PROJECT_DIR / "docs" / "manual"
SOURCE = MANUAL_DIR / "manual.md"
BUILD_DIR = MANUAL_DIR / "_build"
PDF_NAME = "UsuMantenViewer_manual.pdf"
# PDF にするのを待つ長さの上限 [秒]
PRINT_TIMEOUT = 180

# ブラウザの場所(上から順に探す)
BROWSERS = {
  "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
             "/Applications/Chromium.app/Contents/MacOS/Chromium"],
  "win32": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe"],
}

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm 15mm;
        @bottom-center { content: counter(page) " / " counter(pages); font-size: 8pt; color: #888; } }
body { font-family: "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", "YuGothic", "Meiryo", sans-serif;
       font-size: 10pt; line-height: 1.65; color: #222; }
h1 { font-size: 20pt; border-bottom: 3px solid #2c5f8a; padding-bottom: 6px; margin-top: 0; }
h2 { font-size: 15pt; color: #2c5f8a; border-bottom: 1px solid #c8d6e2; padding-bottom: 3px;
     margin-top: 0; break-before: page; }
h3 { font-size: 12pt; margin-top: 1.4em; border-left: 4px solid #2c5f8a; padding-left: 8px; }
h2, h3 { break-after: avoid; }
p, li { orphans: 2; widows: 2; }
img { max-width: 100%; max-height: 120mm; display: block; margin: 8px auto; border: 1px solid #ddd; }
.figure { break-inside: avoid; text-align: center; margin: 10px 0; }
.caption { font-size: 8.5pt; color: #666; margin-top: 2px; }
.missing { border: 2px dashed #b0b8c0; background: #f3f5f7; color: #6b7680; padding: 26px 10px;
           text-align: center; font-size: 9pt; margin: 8px 0; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt; break-inside: auto; }
th, td { border: 1px solid #c8d0d8; padding: 4px 6px; vertical-align: top; }
th { background: #eef3f7; }
tr { break-inside: avoid; }
code { font-family: "Menlo", "Consolas", monospace; font-size: 8.5pt; background: #f2f4f6; padding: 0 3px; border-radius: 3px; }
pre code { display: block; padding: 6px 8px; white-space: pre-wrap; }
hr { display: none; }
.toc { break-after: page; }
.toc ul { list-style: none; padding-left: 1.2em; }
.toc > ul { padding-left: 0; }
.toc a { color: #222; text-decoration: none; }
a { color: #2c5f8a; }
"""

# Markdown を HTML(1ページ)にする
def to_html(md_text, base_dir):
  import markdown
  from markdown.extensions.toc import slugify_unicode
  body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "attr_list", "sane_lists", "toc"],
                           extension_configs={"toc": {"slugify": slugify_unicode, "toc_depth": "2-3"}})
  missing = []
  # 画像は図(キャプション付き)にし、無い画像は枠にする
  def figure(m):
    src, alt = html.unescape(m.group("src")), html.unescape(m.group("alt"))
    caption = f'<div class="caption">{html.escape(alt)}</div>' if alt else ""
    if not (base_dir / src).exists():
      missing.append(src)
      return f'<div class="missing">画面イメージ: {html.escape(alt)}<br>({html.escape(src)})</div>'
    return f'<div class="figure"><img src="{html.escape(src)}" alt="{html.escape(alt)}">{caption}</div>'
  img = r'<img alt="(?P<alt>[^"]*)" src="(?P<src>[^"]*)"\s*/?>'
  body = re.sub(r"<p>\s*" + img + r"\s*</p>", figure, body)
  body = re.sub(img, figure, body)
  title = re.search(r"^#\s+(.+)$", md_text, re.M)
  doc = (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
         f'<base href="{base_dir.as_uri()}/"><title>{html.escape(title.group(1) if title else "manual")}</title>'
         f"<style>{CSS}</style></head><body>{body}</body></html>")
  return doc, missing

def find_browser():
  env = os.environ.get("MANUAL_BROWSER")
  if env:
    return env if Path(env).exists() else None
  for path in BROWSERS.get(sys.platform, []):
    path = os.path.expandvars(path)
    if Path(path).exists():
      return path
  for name in ("google-chrome", "chromium", "chromium-browser", "msedge"):
    found = shutil.which(name)
    if found:
      return found
  return None

# HTML をブラウザで PDF にする(ふだん使っているブラウザの設定とぶつからないよう、仮のプロファイルで動かす)
def print_pdf(browser, html_path, pdf_path):
  pdf_path.parent.mkdir(parents=True, exist_ok=True)
  pdf_path.unlink(missing_ok=True)
  with tempfile.TemporaryDirectory() as profile:
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={profile}", "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
           html_path.as_uri()]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=PRINT_TIMEOUT)
  return pdf_path.exists() and pdf_path.stat().st_size > 0

def main():
  parser = argparse.ArgumentParser(description="操作マニュアル(docs/manual/manual.md)を PDF にする")
  parser.add_argument("--out", type=Path, default=BUILD_DIR / PDF_NAME, help="作る PDF のパス")
  parser.add_argument("--html", action="store_true", help="HTML だけ作る")
  args = parser.parse_args()

  try:
    import markdown  # noqa: F401
  except ImportError:
    print("注意: markdown パッケージが無いため、マニュアルの PDF を作りません(pip install markdown)")
    return 1
  doc, missing = to_html(SOURCE.read_text(encoding="utf-8"), MANUAL_DIR)
  BUILD_DIR.mkdir(parents=True, exist_ok=True)
  html_path = BUILD_DIR / "manual.html"
  html_path.write_text(doc, encoding="utf-8")
  if missing:
    print(f"注意: 画像が {len(missing)}件ありません(枠だけ入れます): {', '.join(missing)}")
  if args.html:
    print(f"HTML を作りました: {html_path}")
    return 0
  browser = find_browser()
  if browser is None:
    print(f"注意: Chrome・Edge が見つからないため、PDF を作りません(HTML: {html_path})。"
          "MANUAL_BROWSER でブラウザを指定できます")
    return 1
  try:
    ok = print_pdf(browser, html_path, args.out.resolve())
  except subprocess.TimeoutExpired:
    ok = False
  if not ok:
    print(f"注意: PDF を作れませんでした(ブラウザ: {browser})")
    return 1
  print(f"マニュアルの PDF を作りました: {args.out}")
  return 0

if __name__ == "__main__":
  sys.exit(main())
