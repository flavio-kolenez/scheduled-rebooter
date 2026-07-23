#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Para e remove os 9 servicos dummy do TOTVS Protheus Schedule criados para testes.

.NOTES
    Deve ser executado como Administrador.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$services = @(
    "02 - Totvs Protheus Schedule 8",
    "02 - Totvs Protheus Schedule 7",
    "02 - Totvs Protheus Schedule 6",
    "02 - Totvs Protheus Schedule 5",
    "02 - Totvs Protheus Schedule 4",
    "02 - Totvs Protheus Schedule 3",
    "02 - Totvs Protheus Schedule 2",
    "02 - Totvs Protheus Schedule 1",
    "02 - TOTVS Protheus Schedule 0 Broker"
)

# ── Parar servicos ────────────────────────────────────────────────────────────
Write-Host "Parando servicos..."
foreach ($svc in $services) {
    Stop-Service -Name $svc -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Parado (ou ja estava parado): $svc"
}

# ── Remover servicos ──────────────────────────────────────────────────────────
Write-Host "`nRemovendo servicos..."
foreach ($svc in $services) {
    try {
        $existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
        if (-not $existing) {
            Write-Warning "[AVISO] Servico nao encontrado (pode ja ter sido removido): $svc"
        } else {
            sc.exe delete "$svc" | Out-Null
            Write-Host "[OK] Removido: $svc"
        }
    } catch {
        Write-Warning "[ERRO] Nao foi possivel remover '$svc': $_"
    }
}

Write-Host "`nAmbiente de teste limpo."
