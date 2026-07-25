<#
.SYNOPSIS
  Suiveur lisible du journal NDJSON des hybrides crypto — pour les captures / la vidéo Bitget.

.DESCRIPTION
  Lit EN DIRECT le journal de décisions d'une stratégie hybride (H1/H2/H3) et affiche des
  lignes propres et colorées, au lieu du mur de JSON brut. Le fichier reste un vrai .ndjson
  lu tel quel (Get-Content -Wait) : c'est le MÊME format que le jumeau (jumeau_hybrides.py),
  donc la preuve de parité tient — on n'ajoute qu'une mise en forme.

  Pendant crypto de l'outil des indices. Deux différences avec la version indices :
    1. le journal crypto ne contient AUCUN numéro de compte (contrairement à Apex) — rien à
       masquer côté terminal ; l'UID Bitget n'apparaît QUE dans l'interface web (à masquer à
       la prise, côté navigateur) ;
    2. le fichier .ndjson reste en UTC (données Binance + exécution Bitget en UTC, comme le runner
       et le nom du fichier), mais l'AFFICHAGE est converti en heure LOCALE pour coller aux graphes.

  Force l'UTF-8 (sinon PowerShell 5.1 casse tous les accents à l'écran). Ctrl+C pour arrêter.

.PARAMETER Strategie
  H1 | H2 | H3 (alias) ou un slug direct (sma_bracket / sma_suiveur / sma_annule).

.EXAMPLE
  .\suivre-journal.ps1 H2
  .\suivre-journal.ps1 -Strategie sma_suiveur -Tail 20
  .\suivre-journal.ps1 -Fichier '..\journaux\sma_bracket\2026-07-23.ndjson' -Instantane
#>
param(
    [Parameter(Position = 0)]
    [string]$Strategie = 'H1',
    [string]$Base = '',
    [string]$Fichier = '',
    [int]$Tail = 12,
    [switch]$Instantane,
    [switch]$Neuf
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$Inv = [System.Globalization.CultureInfo]::InvariantCulture

# Base par défaut : le dossier journaux du chantier, relatif à ce script (captures/ -> ../journaux).
if (-not $Base) { $Base = Join-Path (Split-Path $PSScriptRoot -Parent) 'journaux' }

$slugs = @{ 'H1' = 'sma_bracket'; 'H2' = 'sma_suiveur'; 'H3' = 'sma_annule' }
$cle = $Strategie.ToUpper()
if ($slugs.ContainsKey($cle)) { $slug = $slugs[$cle] } else { $slug = $Strategie }

# Choix au lancement (fenêtre interactive) : garder l'historique déjà affiché, ou repartir
# à zéro. Sert à filmer une nouvelle prise sans les événements de l'essai précédent.
# -Instantane (rejeu one-shot) et -Neuf (forcé zéro) sautent la question.
if (-not $Instantane) {
    if ($Neuf) {
        $Tail = 0
    }
    else {
        Write-Host ''
        Write-Host '  Historique déjà présent dans le journal du jour ?' -ForegroundColor Cyan
        Write-Host "    [Entrée] : l'AFFICHER (les $Tail dernières lignes, puis suivre en direct)" -ForegroundColor DarkGray
        Write-Host '    [n]      : repartir à ZÉRO (écran vierge, n''afficher que les nouveaux)' -ForegroundColor DarkGray
        if ((Read-Host '  Choix') -match '^\s*[nN]') { $Tail = 0 }
    }
}

function Trouver-Fichier($dir) {
    Get-ChildItem $dir -Filter *.ndjson -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
}

if ($Fichier) {
    $cible = $Fichier
}
else {
    $dir = Join-Path $Base $slug
    if (-not (Test-Path $dir)) {
        Write-Host "Dossier introuvable : $dir" -ForegroundColor Red
        Write-Host "Slugs : sma_bracket (H1), sma_suiveur (H2), sma_annule (H3)." -ForegroundColor DarkGray
        return
    }
    $f = Trouver-Fichier $dir
    while (-not $f) {
        Write-Host "En attente du fichier du jour dans $dir  (lance la stratégie...)" -ForegroundColor DarkGray
        Start-Sleep -Seconds 1
        $f = Trouver-Fichier $dir
    }
    $cible = $f.FullName
}

Clear-Host   # écran net à chaque lancement (règle le scrollback des prises précédentes)
Write-Host ''
Write-Host "  Journal : $cible" -ForegroundColor Gray
Write-Host "  $slug   .   heure locale   .   Binance -> demo Bitget   .   Ctrl+C pour arreter" -ForegroundColor DarkGray
Write-Host ('  ' + ('-' * 84)) -ForegroundColor DarkGray

function Fmt($v) {
    if ($null -eq $v) { return '' }
    return ([double]$v).ToString('0.##', $Inv)
}

function Afficher-Ligne($ligne) {
    if ([string]::IsNullOrWhiteSpace($ligne)) { return }
    try { $o = $ligne | ConvertFrom-Json }
    catch { Write-Host "  $ligne" -ForegroundColor DarkGray; return }

    $tsUtc = [datetime]::Parse($o.ts, $Inv,
        [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal)
    # fichier en UTC (non ambigu, parité intacte) ; AFFICHAGE en heure locale, pour coller aux graphes.
    $h = $tsUtc.ToLocalTime().ToString('HH:mm:ss')

    $ev = "$($o.evenement)"
    $raison = "$($o.raison)"

    switch ($ev) {
        'signal'       { $lab = 'SIGNAL     '; $col = 'Cyan' }
        'entree'       { $lab = 'ENTRÉE     '; $col = 'White' }
        'stop_modifie' {
                         # flèche selon le sens, déduit ligne par ligne (robuste même si l'entrée est
                         # hors tampon) : stop d'un SHORT posé AU-DESSUS de l'extrême (↓ il descend) ;
                         # stop d'un LONG posé EN DESSOUS (↑ il monte). Sinon « STOP ↑ » sur un short
                         # dont le stop baisse aurait l'air d'un bug.
                         $fleche = [char]0x2191
                         if ($null -ne $o.prix -and $null -ne $o.extreme -and
                             [double]$o.prix -gt [double]$o.extreme) { $fleche = [char]0x2193 }
                         $lab = ('STOP {0}' -f $fleche).PadRight(11); $col = 'Magenta' }
        'sortie'       { if ($o.code -eq 'TP') { $lab = 'SORTIE TP  '; $col = 'Green' } else { $lab = 'SORTIE     '; $col = 'Red' } }
        'refuse'       { $lab = 'REFUSÉ     '; $col = 'DarkYellow' }
        'avert'        { $lab = 'AVERT      '; $col = 'Yellow' }
        'info'         { $lab = 'INFO       '; $col = 'DarkGray' }
        'erreur'       { $lab = 'ERREUR     '; $col = 'Red' }
        'demarrage'    { $lab = 'DÉMARRAGE  '; $col = 'Gray' }
        'arret'        { $lab = 'ARRÊT      '; $col = 'DarkGray' }
        default        { $lab = $ev.PadRight(11); $col = 'White' }
    }

    # champs de détail par événement (le journal crypto porte prix/sens/sl/tp/code + sma/atr sur le signal)
    $p = @()
    if ($null -ne $o.sens)                                      { $p += ("$($o.sens)") }
    if ($null -ne $o.code)                                      { $p += ('code ' + $o.code) }
    if ($null -ne $o.sma_rapide -and $null -ne $o.sma_lente)    { $p += ('sma {0}/{1}' -f (Fmt $o.sma_rapide), (Fmt $o.sma_lente)) }
    if ($null -ne $o.sl)                                        { $p += ('SL ' + (Fmt $o.sl)) }
    if ($null -ne $o.tp)                                        { $p += ('TP ' + (Fmt $o.tp)) }
    if ($null -ne $o.extreme)                                   { $p += ('extrême ' + (Fmt $o.extreme)) }
    if ($null -ne $o.atr)                                       { $p += ('atr ' + (Fmt $o.atr)) }
    if ($null -ne $o.net_bps)                                   { $p += ('net ' + (Fmt $o.net_bps) + ' bps') }
    $extra = ''
    if ($p.Count) { $extra = '   [ ' + ($p -join '  ') + ' ]' }

    if ($null -ne $o.prix) { $prix = '@' + (Fmt $o.prix) } else { $prix = '' }

    Write-Host ('  {0}  ' -f $h) -NoNewline -ForegroundColor DarkGray
    Write-Host ('{0}' -f $lab) -NoNewline -ForegroundColor $col
    Write-Host ('  {0} {1}{2}' -f $prix, $raison, $extra) -ForegroundColor Gray
}

if ($Instantane) {
    Get-Content -Path $cible -Tail $Tail -Encoding UTF8 | ForEach-Object { Afficher-Ligne $_ }
}
else {
    Get-Content -Path $cible -Wait -Tail $Tail -Encoding UTF8 | ForEach-Object { Afficher-Ligne $_ }
}
