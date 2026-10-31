import json
from pathlib import Path

from log_common import log_message

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# 読み取り専用のスコープ
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# 1回の通信でダウンロードする大きさ。この単位で進捗の表示と中止の確認を行う
# 小さくすると進捗・中止の反応は早くなるが、通信の回数が増えてダウンロード全体が遅くなるため、
# ライブラリの既定と同じ100MBにしている。中止は主に、長い期間を指定してしまったときに
# 残りの日のダウンロードをやめるためのもの(1ファイルの途中では、最大100MB分待つことになる)
CHUNK_SIZE = 100 * 1024 * 1024

# Googleドライブ上に対象ファイルが無かったときの例外
class DriveFileNotFoundError(Exception):
  pass

# ダウンロードを中止したときの例外
class DownloadCancelled(Exception):
  pass

# フォルダ設定ファイル(JSON)から、GoogleドライブのフォルダIDを読み込む
#   config_file ... 設定ファイルのパス
#   key         ... JSON内のキー名
#   戻り値       ... フォルダID。読み込めなかったときは None
def load_folder_id(config_file, key="daily_zips_id"):
  try:
    with open(config_file, "r", encoding="utf-8") as f:
      return json.load(f).get(key)
  except Exception as e:
    log_message(f"Googleドライブのフォルダ設定ファイルの読み込みに失敗しました: {e}")
    return None

# Googleドライブの指定フォルダからファイルをダウンロードする
#   creds_path  ... サービスアカウントのキーファイル(JSON)
#   folder_id   ... 検索対象のフォルダID
#   filename    ... ダウンロードするファイル名
#   dest_path   ... 保存先のパス
#   on_progress ... 進捗率(0〜100の整数)を受け取る関数。省略可
#   cancelled   ... 中止するときに True を返す関数。省略可(CHUNK_SIZE ごとに確認する)
#   例外         ... 対象が無いと DriveFileNotFoundError、中止すると DownloadCancelled、
#                   その他の失敗はそのまま送出
def download_file_from_drive(creds_path, folder_id, filename, dest_path, on_progress=None,
                             cancelled=lambda: False):
  if not folder_id:
    raise ValueError("GoogleドライブのフォルダIDが設定されていません")

  # サービスアカウントの認証情報を読み込み、Drive APIクライアントを構築
  creds = service_account.Credentials.from_service_account_file(
    str(creds_path), scopes=SCOPES)
  service = build("drive", "v3", credentials=creds)

  # フォルダ内から同名のファイルを検索
  query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
  results = service.files().list(
    q=query,
    spaces="drive",
    fields="files(id, name, size)",  # size は整合性チェック用
    supportsAllDrives=True,          # マイドライブと共有ドライブをサポート
    includeItemsFromAllDrives=True   # 共有ドライブをレスポンスに含める
  ).execute()
  files = results.get("files", [])
  if not files:
    raise DriveFileNotFoundError(f"Googleドライブ上にファイルが見つかりませんでした: {filename}")

  # 見つかった最初のファイルを対象にする
  file_id = files[0]["id"]
  expected_size = files[0].get("size")   # Drive上のファイルサイズ(バイト、文字列)

  # 保存先フォルダを作成
  dest_path = Path(dest_path)
  dest_path.parent.mkdir(parents=True, exist_ok=True)

  # ダウンロード中は仮の名前(.part)で保存し、成功したら本来の名前に変更する
  tmp_path = dest_path.with_name(dest_path.name + ".part")
  try:
    request = service.files().get_media(fileId=file_id)
    with open(tmp_path, "wb") as f:
      downloader = MediaIoBaseDownload(f, request, chunksize=CHUNK_SIZE)
      done = False
      while not done:
        status, done = downloader.next_chunk()
        if status and on_progress:
          on_progress(int(status.progress() * 100))
        if not done and cancelled():
          raise DownloadCancelled(f"ダウンロードを中止しました: {filename}")

    # サイズがDrive上のものと一致するか確認(0バイトや途中切れの検出)
    if expected_size is not None and tmp_path.stat().st_size != int(expected_size):
      raise IOError(f"ダウンロードしたファイルのサイズが一致しません: {filename}")

    # 成功したので、本来の名前に変更(既存のファイルがあれば置き換わる)
    tmp_path.replace(dest_path)
  except BaseException:
    # 失敗・中断のときは、仮のファイルを削除してから例外を再送出する
    tmp_path.unlink(missing_ok=True)
    raise
