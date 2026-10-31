cd scripts
pyinstaller --onedir --collect-all obspy --collect-all matplotlib --collect-all google.oauth2 --collect-all googleapiclient --noconfirm --debug noarchive UsuMantenViewer.py
xcopy /E /I /Y ..\etc dist\UsuMantenViewer\etc
xcopy /E /I /Y ..\service_account dist\UsuMantenViewer\service_account
xcopy /E /I /Y ..\tools dist\UsuMantenViewer\tools
xcopy /I /Y ..\output\history.json dist\UsuMantenViewer\output\
cd dist
tar -caf ..\..\UsuMantenViewer.zip UsuMantenViewer
cd ..\..


