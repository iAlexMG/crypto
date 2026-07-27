@echo off
rem Double-clic = suit le journal NDJSON de H1 (SMA Bracket / sma_bracket).
rem Au lancement, le script demande : afficher l'historique existant, ou repartir a zero.
setlocal
start "Journal NDJSON H1" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0suivre-journal.ps1" H1
endlocal
