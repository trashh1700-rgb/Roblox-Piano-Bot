@echo off
cd /d "%~dp0"
py -3 piano.py
if errorlevel 1 (
 echo Python 3 kurulu oldugundan emin ol. Hata mesajini paylasabilirsin.
 pause
)
