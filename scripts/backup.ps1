param(
  [Parameter(Mandatory = $true)]
  [string]$Target,
  [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$Dest = Join-Path $Target "karta-backup-$Stamp"

if ($Target -match '^[Cc]:') {
  Write-Warning "Цель на диске C:. Это защита от случайного удаления, но не от смерти диска. Для реального бэкапа подключи внешний диск."
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null

function Copy-Tree([string]$Source, [string]$Destination) {
  if (-not (Test-Path $Source)) {
    Write-Warning "Нет папки: $Source"
    return
  }
  New-Item -ItemType Directory -Force -Path $Destination | Out-Null
  robocopy $Source $Destination /E /Z /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
  if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed for $Source (exit $LASTEXITCODE)"
  }
}

Write-Host "Бэкап в $Dest"

Copy-Item (Join-Path $Repo "config\scoring.json") (Join-Path $Dest "scoring.json") -Force
Copy-Item (Join-Path $Repo "data\reference_set.md") (Join-Path $Dest "reference_set.md") -Force
Copy-Item (Join-Path $Repo "docs\SECURITY.md") (Join-Path $Dest "SECURITY.md") -Force

Write-Host "git bundle..."
Push-Location $Repo
git bundle create (Join-Path $Dest "repo.bundle") --all | Out-Null
$Commit = git rev-parse --short HEAD
Pop-Location

Write-Host "data/raw..."
Copy-Tree (Join-Path $Repo "data\raw") (Join-Path $Dest "data\raw")
Write-Host "data/processed (включая Valhalla-тайлы)..."
Copy-Tree (Join-Path $Repo "data\processed") (Join-Path $Dest "data\processed")

if (-not $SkipDocker) {
  # Docker пишет статус в stderr. На Windows PowerShell 5.1 при
  # $ErrorActionPreference = "Stop" это превращается в terminating error
  # (ловили: дамп пропускался на «Container ... Running»). Поэтому внутри
  # блока ошибки проверяем явно по $LASTEXITCODE.
  $prevEap = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  $pushed = $false
  try {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Docker daemon не запущен" }
    Push-Location $Repo
    $pushed = $true
    docker compose up -d postgis 2>&1 | Out-Null
    $healthy = $false
    for ($i = 0; $i -lt 30; $i++) {
      $health = docker inspect --format "{{.State.Health.Status}}" okrug-postgis 2>$null
      if ($health -eq "healthy") { $healthy = $true; break }
      Start-Sleep -Seconds 2
    }
    if (-not $healthy) { throw "PostGIS не стал healthy за 60 секунд" }
    docker exec okrug-postgis pg_dump -U okrug -d okrug -Fc -f /tmp/okrug.dump 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_dump exit $LASTEXITCODE" }
    docker cp okrug-postgis:/tmp/okrug.dump (Join-Path $Dest "okrug.dump") 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker cp exit $LASTEXITCODE" }
    Write-Host "PostGIS dump: ok"
  } catch {
    Write-Warning "PostGIS dump пропущен: $_"
  } finally {
    if ($pushed) { Pop-Location }
    $ErrorActionPreference = $prevEap
  }
}

$Size = (Get-ChildItem $Dest -Recurse -File | Measure-Object Length -Sum).Sum
$manifest = "date=" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + "`n" +
  "commit=$Commit`n" +
  "target=$Dest`n" +
  "size_gb=$([math]::Round($Size / 1GB, 2))`n"
$manifest | Set-Content (Join-Path $Dest "manifest.txt") -Encoding UTF8

Write-Host ("Готово: {0} ({1:N2} ГБ)" -f $Dest, ($Size / 1GB))
