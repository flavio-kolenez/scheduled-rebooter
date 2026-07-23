"""
Servico dummy para testes locais do schedule-rebooter.

Compilado com PyInstaller em um exe standalone que nao depende de Python externo.
O SCM invoca: dummy_service.exe --service-host "Nome do Servico"
"""
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil


def _parse_args() -> tuple[str | None, list[str]]:
    args = list(sys.argv)
    for i, arg in enumerate(args):
        if arg == "--service-host" and i + 1 < len(args):
            return args[i + 1], args[:i] + args[i + 2:]
    return None, args


_SERVICE_NAME, _CLEAN_ARGV = _parse_args()


class DummyScheduleService(win32serviceutil.ServiceFramework):
    _svc_name_ = _SERVICE_NAME or "DummyScheduleService"
    _svc_display_name_ = _SERVICE_NAME or "Dummy Schedule Service"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)


if __name__ == "__main__":
    if _SERVICE_NAME is None:
        print("Erro: --service-host nao encontrado.")
        print('Uso: dummy_service.exe --service-host "Nome do Servico"')
        sys.exit(1)

    if len(_CLEAN_ARGV) <= 1:
        # Sem subcomandos apos remover --service-host: iniciado pelo SCM.
        # Usa a API direta do servicemanager (correto para exes compilados).
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(DummyScheduleService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # Subcomando explicito (install/remove/start/stop): modo CLI.
        win32serviceutil.HandleCommandLine(DummyScheduleService, argv=_CLEAN_ARGV[1:])

