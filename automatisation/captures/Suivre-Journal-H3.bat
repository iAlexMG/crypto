@echo off
rem Double-clic = suit le journal NDJSON de H3 (SMA Annulation / sma_annule).
rem Au lancement, le script demande : afficher l'historique existant, ou repartir a zero.
setlocal
start "Journal NDJSON H3" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0suivre-journal.ps1" H3
endlocal
