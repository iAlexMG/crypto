@echo off
REM ====================================================================
REM  Lanceur SILENCIEUX (aucune fenetre de console) du dashboard.
REM  Utilise pythonw.exe de l'env conda "crypto-agg". Les logs vont
REM  uniquement dans logs\crypto.log (ecrase a chaque lancement).
REM  Pratique au quotidien ; en cas de souci, utilise "Lancer.bat"
REM  (avec console) pour voir les erreurs.
REM ====================================================================
cd /d "%~dp0"
start "" "C:\Users\Moi\miniconda3\envs\crypto-agg\pythonw.exe" run_gui.py
