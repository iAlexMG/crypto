@echo off
rem Double-clic = lance H1 d'un seul geste, pour filmer :
rem   Fenetre 1 (Runner) : la strategie qui EXECUTE sur la demo Bitget (heartbeat + evenements).
rem   Fenetre 2 (Journal): le suiveur en LECTURE SEULE, version coloree epuree du meme journal.
rem Les deux restent SEPAREES : deux flux live ne se melangent pas proprement dans une seule
rem console. Ctrl-C dans la fenetre Runner = flat + kill switch.
setlocal
start "Runner H1 (execute)" cmd /k "cd /d %~dp0.. && python runner_sma.py --strategie h1 --go"
rem laisse le runner creer le journal du jour avant d'attacher le suiveur
timeout /t 3 /nobreak >nul
start "Journal H1 (lecture seule)" powershell -NoExit -ExecutionPolicy Bypass -File "%~dp0suivre-journal.ps1" H1 -Neuf
endlocal
