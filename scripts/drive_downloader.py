import json
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# 読み取り専用のスコープ
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Googleドライブ上に対象ファイルが無かったときの例外
class DriveFileNotFoundError(Exception):
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
    print(f"Googleドライブのフォルダ設定ファイルの読み込みに失敗しました: {e}")
    return None

# Googleドライブの指定フォルダからファイルをダウンロードする
#   creds_path  ... サービスアカウントのキーファイル(JSON)
#   folder_id   ... 検索対象のフォルダID
#   filename    ... ダウンロードするファイル名
#   dest_path   ... 保存先のパス
#   on_progress ... 進捗率(0〜100の整数)を受け取る関数。省略可
#   例外         ... 対象が無いと DriveFileNotFoundError、その他の失敗はそのまま送出
def download_file_from_drive(creds_path, folder_id, filename, dest_path, on_progress=None):
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
    fields="files(id, name)",
    supportsAllDrives=True,          # マイドライブと共有ドライブをサポート
    includeItemsFromAllDrives=True   # 共有ドライブをレスポンスに含める
  ).execute()
  files = results.get("files", [])
  if not files:
    raise DriveFileNotFoundError(f"Googleドライブ上にファイルが見つかりませんでした: {filename}")

  # 見つかった最初のファイルを対象にする
  file_id = files[0]["id"]

  # 保存先フォルダを作成してダウンロード
  dest_path = Path(dest_path)
  dest_path.parent.mkdir(parents=True, exist_ok=True)
  request = service.files().get_media(fileId=file_id)
  with open(dest_path, "wb") as f:
    downloader = MediaIoBaseDownload(f, request)
    done = False
    while not done:
      status, done = downloader.next_chunk()
      if status and on_progress:
        on_progress(int(status.progress() * 100))