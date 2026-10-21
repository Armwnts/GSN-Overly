# Genshin Overlay - première installation sous Windows.
# Installe une copie privée de Python (version "embarquable", sans toucher au reste
# du PC) dans le dossier runtime\, puis les bibliothèques nécessaires.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$App = Split-Path -Parent $PSCommandPath
$Root = Split-Path -Parent $App
$Rt = Join-Path $Root 'runtime'
$Sup = Join-Path $env:LOCALAPPDATA 'GenshinOverlay'
New-Item -ItemType Directory -Force (Join-Path $Sup 'data') | Out-Null
Start-Transcript -Path (Join-Path $Sup 'data\setup.log') -Force | Out-Null
$Host.UI.RawUI.WindowTitle = 'Genshin Overlay - installation'

try {
    Write-Host ''
    Write-Host '  Genshin Overlay - premiere installation (5 a 10 minutes)' -ForegroundColor Cyan
    Write-Host '  Ne ferme pas cette fenetre.' -ForegroundColor Cyan
    Write-Host ''

    $ver = '3.12.10'
    if (-not (Test-Path (Join-Path $Rt 'python.exe'))) {
        Write-Host "[1/3] Telechargement de Python $ver..."
        $zip = Join-Path $env:TEMP "python-$ver-embed-amd64.zip"
        Invoke-WebRequest "https://www.python.org/ftp/python/$ver/python-$ver-embed-amd64.zip" -OutFile $zip -UseBasicParsing
        New-Item -ItemType Directory -Force $Rt | Out-Null
        Expand-Archive $zip -DestinationPath $Rt -Force
        Remove-Item $zip -Force

        # Active les paquets installés avec pip (désactivés par défaut dans cette version de Python)
        $pth = Get-ChildItem $Rt -Filter 'python3*._pth' | Select-Object -First 1
        Set-Content -Path $pth.FullName -Encoding ASCII -Value @(
            ($pth.BaseName + '.zip'), '.', 'Lib\site-packages', 'import site')

        Write-Host '[2/3] Installation de pip...'
        $gp = Join-Path $Rt 'get-pip.py'
        Invoke-WebRequest 'https://bootstrap.pypa.io/get-pip.py' -OutFile $gp -UseBasicParsing
        & (Join-Path $Rt 'python.exe') $gp --no-warn-script-location --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { throw "Echec de l'installation de pip" }
        Remove-Item $gp -Force
    }

    Write-Host '[3/3] Installation des bibliotheques (PyQt6, OpenCV, ...) - environ 150 Mo...'
    & (Join-Path $Rt 'python.exe') -m pip install --no-warn-script-location --disable-pip-version-check `
        -r (Join-Path $App 'code\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Echec de pip install' }

    $h = (Get-FileHash -Algorithm SHA256 (Join-Path $App 'code\requirements.txt')).Hash.ToLower().Substring(0, 16)
    Set-Content -Path (Join-Path $Rt '.reqhash') -Value $h -NoNewline -Encoding ASCII
    New-Item -ItemType File -Force (Join-Path $Rt '.ready-v21') | Out-Null
    Write-Host ''
    Write-Host '  Installation terminee ! Genshin Overlay va demarrer.' -ForegroundColor Green
    Start-Sleep -Seconds 2
    Stop-Transcript | Out-Null
    exit 0
}
catch {
    Write-Host ''
    Write-Host "  ERREUR : $_" -ForegroundColor Red
    Write-Host '  Appuie sur Entree pour fermer.'
    Stop-Transcript | Out-Null
    Read-Host | Out-Null
    exit 1
}
