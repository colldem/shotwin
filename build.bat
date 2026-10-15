@echo off
python -m pip install -r requirements.txt || exit /b 1
pyinstaller --noconfirm --onefile --windowed --name ShotWin shotwin.py || exit /b 1
echo Done: dist\ShotWin.exe
