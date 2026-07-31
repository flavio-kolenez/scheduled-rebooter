@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "LOG_DIR=%SCRIPT_DIR%logs"
set "WAIT_TIMEOUT_SECONDS=120"
set "WAIT_INTERVAL_SECONDS=2"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "RUN_ID=%%I"
if not defined RUN_ID set "RUN_ID=fallback"
set "LOG_FILE=%LOG_DIR%\rebooter_%RUN_ID%.log"

call :Log "============================================"
call :Log "Inicio da execucao do rebooter"
call :Log "Arquivo de log: %LOG_FILE%"

net session >nul 2>&1
if errorlevel 1 (
    call :Log "ERRO: execute este script como Administrador."
    exit /b 1
)

call :Log "Reiniciando TOTVS Protheus Schedule"

call :StopService "02 - Totvs Protheus Schedule 8"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 7"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 6"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 5"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 4"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 3"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 2"
if errorlevel 1 goto :Fail
call :StopService "02 - Totvs Protheus Schedule 1"
if errorlevel 1 goto :Fail
call :StopService "02 - TOTVS Protheus Schedule 0 Broker"
if errorlevel 1 goto :Fail

call :Log "Todos os servicos foram parados."

call :StartService "02 - TOTVS Protheus Schedule 0 Broker"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 1"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 2"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 3"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 4"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 5"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 6"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 7"
if errorlevel 1 goto :Fail
call :StartService "02 - Totvs Protheus Schedule 8"
if errorlevel 1 goto :Fail

call :Log "Reinicializacao concluida com sucesso."
call :Log "Fim da execucao."

exit /b 0

:Fail
call :Log "FALHA: execucao interrompida. Consulte o log para detalhes."
call :Log "Fim da execucao com erro."
exit /b 1


:StopService
call :Log "[STOP] %~1"

net stop "%~1" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :Log "ERRO ao parar %~1"
    exit /b 1
)

set /a elapsed=0

:WaitStop
powershell -NoProfile -Command ^
    "if((Get-Service -Name '%~1').Status -eq 'Stopped'){exit 0}else{exit 1}" >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    if %elapsed% GEQ %WAIT_TIMEOUT_SECONDS% (
        call :Log "ERRO: timeout aguardando %~1 parar (%WAIT_TIMEOUT_SECONDS%s)."
        exit /b 1
    )
    timeout /t %WAIT_INTERVAL_SECONDS% /nobreak >nul
    set /a elapsed+=WAIT_INTERVAL_SECONDS
    goto WaitStop
)

call :Log "[OK] %~1 parado."
exit /b 0


:StartService
call :Log "[START] %~1"

net start "%~1" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    call :Log "ERRO ao iniciar %~1"
    exit /b 1
)

set /a elapsed=0

:WaitStart
powershell -NoProfile -Command ^
    "if((Get-Service -Name '%~1').Status -eq 'Running'){exit 0}else{exit 1}" >> "%LOG_FILE%" 2>&1

if errorlevel 1 (
    if %elapsed% GEQ %WAIT_TIMEOUT_SECONDS% (
        call :Log "ERRO: timeout aguardando %~1 iniciar (%WAIT_TIMEOUT_SECONDS%s)."
        exit /b 1
    )
    timeout /t %WAIT_INTERVAL_SECONDS% /nobreak >nul
    set /a elapsed+=WAIT_INTERVAL_SECONDS
    goto WaitStart
)

call :Log "[OK] %~1 iniciado."
exit /b 0


:Log
set "MESSAGE=%~1"
echo [%date% %time%] %MESSAGE%
>> "%LOG_FILE%" echo [%date% %time%] %MESSAGE%
exit /b 0