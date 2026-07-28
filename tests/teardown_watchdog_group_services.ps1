#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Para e remove os servicos dummy criados por setup_watchdog_group_services.ps1
    (grupo da regra "dbaccess_connection_lost" do WatchDog DBAccess).

.NOTES
    Deve ser executado como Administrador.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$services = @(
    "TOTVS_Schedule_8",
    "TOTVS_Schedule_7",
    "TOTVS_Schedule_6",
    "TOTVS_Schedule_5",
    "TOTVS_Schedule_4",
    "TOTVS_Schedule_3",
    "TOTVS_Schedule_2",
    "TOTVS_Schedule_1",
    "TOTVS_Protheus_Rest_SSL_SmartView",
    "TOTVS_Protheus_Rest_SSL_2",
    "TOTVS_Protheus_Rest_SSL_1",
    "TOTVS_Protheus_Rest_1",
    "TOTVS_Protheus_Schedule_Broker",
    "TOTVS_Protheus_Rest_WS_Broker_SSL",
    "TOTVS_Protheus_Rest_WS_Broker",
    "00_TOTVS_DBAccess_Slave"
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

Write-Host "`nAmbiente de teste do RESTART_SERVICE_GROUP limpo."
