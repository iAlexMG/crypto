@echo off
REM ====================================================================
REM  Lanceur du dashboard Crypto Orderflow (double-clic).
REM  Utilise directement le python de l'env conda dédié "crypto-agg"
REM  (pas besoin de "conda activate"). La console reste ouverte et
REM  affiche les logs ; elle se ferme quand tu fermes l'application.
REM ====================================================================
title Crypto Orderflow
cd /d "%~dp0"

set "PYEXE=C:\Users\Moi\miniconda3\envs\crypto-agg\python.exe"
if not exist "%PYEXE%" (
    echo Interpreteur introuvable : %PYEXE%
    echo Verifie le chemin de l'env conda "crypto-agg".
    pause
    exit /b 1
)

"%PYEXE%" run_gui.py

REM Garde la fenetre ouverte UNIQUEMENT en cas d'erreur (pour lire le message).
if errorlevel 1 (
    echo.
    echo *** L'application s'est arretee avec une erreur (voir logs\crypto.log). ***
    pause
)
