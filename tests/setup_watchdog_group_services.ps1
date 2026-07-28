#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Cria e inicia os servicos dummy usados para testar a acao RESTART_SERVICE_GROUP
    do WatchDog DBAccess (regra "dbaccess_connection_lost" no config.ini).

.DESCRIPTION
    Reaproveita o mesmo dummy_service.exe do schedule-rebooter, registrando um
    servico Windows fake para cada nome listado na chave "services" da regra
    dbaccess_connection_lost. Ao terminar os testes, execute
    teardown_watchdog_group_services.ps1.

.NOTES
    Deve ser executado como Administrador.
    Requer tests\dist\dummy_service.exe (ja compilado neste repositorio).
#>

Set-StrictMode -Version Latest

$dummyExe = [System.IO.Path]::GetFullPath("$PSScriptRoot\dist\dummy_service.exe")

if (-not (Test-Path $dummyExe)) {
    Write-Error "dummy_service.exe nao encontrado em: $dummyExe`nCompile primeiro com PyInstaller (veja o README).`n  python -m PyInstaller --onefile --name dummy_service --uac-admin --hidden-import win32timezone --hidden-import servicemanager --distpath tests\dist --workpath tests\build tests\dummy_service.py"
    exit 1
}

# Mesma ordem/nomes da chave "services" em [rule:dbaccess_connection_lost] do
# watchdog-dbaccess\config.ini.
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

# ── Criar servicos ────────────────────────────────────────────────────────────
Write-Host "`nCriando servicos dummy do grupo RESTART_SERVICE_GROUP..."
foreach ($svc in $services) {
    $binPath = "`"$dummyExe`" --service-host `"$svc`""
    try {
        $existing = Get-Service -Name $svc -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Warning "[AVISO] Servico ja existe, pulando criacao: $svc"
        } else {
            New-Service -Name $svc -DisplayName $svc -BinaryPathName $binPath -StartupType Manual | Out-Null
            Write-Host "[OK] Criado:  $svc"
        }
    } catch {
        Write-Warning "[ERRO] Nao foi possivel criar '$svc': $_"
    }
}

# ── Iniciar servicos ──────────────────────────────────────────────────────────
Write-Host "`nIniciando servicos..."
foreach ($svc in $services) {
    try {
        Start-Service -Name $svc -ErrorAction Stop
        Write-Host "[OK] Iniciado: $svc"
    } catch {
        Write-Warning "[ERRO] Nao foi possivel iniciar '$svc': $_"
    }
}

Write-Host ""
Write-Host "Ambiente de teste do RESTART_SERVICE_GROUP pronto."
Write-Host "Execute o teste (em um PowerShell como Administrador):"
Write-Host "  python watchdog-dbaccess\monitor.py --test-rule dbaccess_connection_lost --execute"
Write-Host ""
Write-Host "Ao finalizar, limpe o ambiente:"
Write-Host "  .\tests\teardown_watchdog_group_services.ps1"
