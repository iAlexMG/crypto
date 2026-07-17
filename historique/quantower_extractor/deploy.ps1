# Compile l'extracteur crypto et le déploie dans le dossier Scripts de Quantower.
# Résout dynamiquement le bin v* le plus récent (jamais de chemin en dur).
$ErrorActionPreference = "Stop"
$root = "C:\Quantower\TradingPlatform"
$latest = Get-ChildItem $root -Directory -Filter "v*" |
    Sort-Object { [version]($_.Name.TrimStart('v')) } -Descending |
    Select-Object -First 1
if ($null -eq $latest) { throw "Aucun dossier v* sous $root" }
$bin = Join-Path $latest.FullName "bin"

dotnet build "$PSScriptRoot\CryptoTickExtractor.csproj" -c Release -p:QuantowerBin="$bin"

$out  = Join-Path $PSScriptRoot "bin\Release\net10.0"
# Settings est un frère de TradingPlatform (C:\Quantower\Settings) — le script NQ archivé
# remontait d'un niveau de trop.
$dest = Join-Path (Split-Path $root -Parent) "Settings\Scripts\Strategies\CryptoTickExtractor"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
foreach ($f in "CryptoTickExtractor.dll","CryptoTickExtractor.deps.json","CryptoTickExtractor.pdb") {
    Copy-Item (Join-Path $out $f) $dest -Force
}
Write-Host "Déployé dans : $dest"
Write-Host "Dans Quantower : panneau Strategies -> Crypto History Ticks -> choisir le symbole (sa connexion fixe l'exchange) -> Start."
