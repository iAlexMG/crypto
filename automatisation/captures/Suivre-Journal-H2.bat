@echo off
rem Double-clic = suit le journal NDJSON de H2 (SMA Suiveur / sma_suiveur).
rem Au lancement, le script demande : afficher l'historique existant, ou repartir a zero.
setlocal
start "Journal NDJSON H2" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0suivre-journal.ps1" H2
endlocal
