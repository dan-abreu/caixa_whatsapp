param(
    [string]$DatabaseUrl,
    [string]$SchemaPath = ".\sql\schema.sql"
)

$ErrorActionPreference = "Stop"

function Import-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return
    }

    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $parts = $trimmed -split '=', 2
        if ($parts.Count -ne 2) {
            continue
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Get-DatabaseUrl {
    param([string]$ExplicitUrl)

    if ($ExplicitUrl) {
        return $ExplicitUrl
    }

    if ($env:SUPABASE_DB_URL) {
        return $env:SUPABASE_DB_URL
    }

    if ($env:SUPABASE_DB_PASSWORD -and $env:SUPABASE_PROJECT_REF) {
        return "postgresql://postgres:$($env:SUPABASE_DB_PASSWORD)@db.$($env:SUPABASE_PROJECT_REF).supabase.co:5432/postgres"
    }

    throw "Informe -DatabaseUrl ou configure SUPABASE_DB_URL. Alternativamente, configure SUPABASE_PROJECT_REF e SUPABASE_DB_PASSWORD."
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot
Import-DotEnv -Path (Join-Path $RepoRoot ".env")

if (-not (Test-Path $SchemaPath)) {
    throw "Arquivo schema não encontrado: $SchemaPath"
}

$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) {
    throw "psql não encontrado no PATH. Instale PostgreSQL client tools ou use o SQL Editor do Supabase com o conteúdo de schema.sql."
}

$resolvedDbUrl = Get-DatabaseUrl -ExplicitUrl $DatabaseUrl

Write-Host "Aplicando schema em $SchemaPath..." -ForegroundColor Yellow
& $psql.Source $resolvedDbUrl -v ON_ERROR_STOP=1 -f $SchemaPath
Write-Host "Schema aplicado com sucesso." -ForegroundColor Green