cd scripts
rem アプリに付く物(tools と etc の初期値)をアプリの中(_internal)に入れる(app_paths.py の BUNDLE_DIR)
rem etc は Git で管理している設定ファイルだけ(時刻表や画面の設定は、各自のユーザーの設定に置く)
if exist build_res rmdir /S /Q build_res
mkdir build_res\etc
copy /Y ..\etc\*.ch build_res\etc\
copy /Y ..\etc\*.tbl build_res\etc\
copy /Y ..\etc\win.prm build_res\etc\
copy /Y ..\etc\external_stations.txt build_res\etc\
copy /Y ..\etc\train_timetable_template.xlsx build_res\etc\
pyinstaller --onedir --collect-all obspy --collect-all matplotlib --collect-all google.oauth2 --collect-all googleapiclient --noconfirm --debug noarchive --add-data "build_res\etc;etc" --add-binary "..\tools\win2sac_32.exe;tools" --add-binary "..\tools\wchch_32.exe;tools" UsuMantenViewer.py
rem Googleドライブの鍵は、アプリの横の service_account にも置ける(メンテナンス画面で取り込むと、ユーザーの設定に入る)
if exist ..\service_account xcopy /E /I /Y ..\service_account dist\UsuMantenViewer\service_account
cd dist
tar -caf ..\..\UsuMantenViewer.zip UsuMantenViewer
cd ..\..


