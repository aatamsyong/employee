@echo off
title PT Amsyong AI - Karyawan Virtual
cd /d "%~dp0"
echo ========================================================
echo   PT AMSYONG AI (AIMPLOYEE) - SISTEM KARYAWAN VIRTUAL
echo ========================================================
echo.
echo Menjalankan aplikasi web Amsyong AI LAB...
echo URL Lokal: http://localhost:8509
echo.
python -m streamlit run app.py --server.port 8509
pause
