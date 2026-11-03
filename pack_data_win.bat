@echo off
rem pack_data_win.bat - pack the data to move to another machine into one zip (see scripts\pack_data.py)
rem Run after start_env.bat (obspy_env).
rem   --private    also include files that must not be redistributed (timetable, v-net ZIPs). For your own use only.
rem   --with-zips  also include daily_zips (very large).
cd /d "%~dp0"
python scripts\pack_data.py %*
