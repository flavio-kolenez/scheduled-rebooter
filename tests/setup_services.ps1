#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Cria e inicia os 9 servicos dummy do TOTVS Protheus Schedule para testes locais.

.DESCRIPTION
    Instala pywin32 no ambiente virtual, registra cada servico via sc.exe apontando
    para dummy_service.py, e os inicia. Ao terminar os testes, execute teardown_services.ps1.

.NOTES
    Deve ser executado como Administrador.
    Requer o ambiente virtual em ..\.venv\
#>

Set-StrictMode -Version Latest

$dummyExe = [System.IO.Path]::GetFullPath("$PSScriptRoot\dist\dummy_service.exe")

if (-not (Test-Path $dummyExe)) {
    Write-Error "dummy_service.exe nao encontrado em: $dummyExe`nCompile primeiro com PyInstaller (veja o README).`n  python -m PyInstaller --onefile --name dummy_service --uac-admin --hidden-import win32timezone --hidden-import servicemanager --distpath tests\dist --workpath tests\build tests\dummy_service.py"
    exit 1
}

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

# ── Criar servicos ────────────────────────────────────────────────────────────
Write-Host "`nCriando servicos dummy..."
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
Write-Host "Ambiente de teste pronto."
Write-Host "Execute o schedule-rebooter para testar:"
Write-Host "  .\dist\schedule-rebooter.exe"
Write-Host ""
Write-Host "Ao finalizar, limpe o ambiente:"
Write-Host "  .\tests\teardown_services.ps1"
