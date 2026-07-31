"""Controle de servicos Windows e orquestracao da recuperacao automatica.

Este modulo encapsula a comunicacao com o Service Control Manager do
Windows (via ``sc.exe``), a validacao de saude do ambiente (Health Check)
e o fluxo completo de parada/inicializacao dos servicos do TOTVS Protheus.
"""
from __future__ import annotations

import ctypes
import json
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from utils import AppConfig


class ServiceError(Exception):
    """Erro ao consultar ou controlar um servico do Windows."""


STATE_BY_CODE = {
    "1": "STOPPED",
    "2": "START_PENDING",
    "3": "STOP_PENDING",
    "4": "RUNNING",
    "5": "CONTINUE_PENDING",
    "6": "PAUSE_PENDING",
    "7": "PAUSED",
}


def is_admin() -> bool:
    """Verifica se o processo atual possui privilegios administrativos."""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


class ServiceController:
    """Encapsula chamadas a ``sc.exe`` para consultar e controlar servicos."""

    def __init__(self, timeout_seconds: int = 60, poll_interval_seconds: int = 2) -> None:
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    @staticmethod
    def _run_sc(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sc", *args],
            capture_output=True,
            text=True,
            check=False,
            encoding="cp1252",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def get_state(self, service_name: str) -> str:
        """Consulta o estado atual de um servico pelo nome."""
        result = self._run_sc("query", service_name)
        output = f"{result.stdout}\n{result.stderr}"

        if "FAILED 1060" in output:
            raise ServiceError(f"Servico '{service_name}' nao existe.")
        if result.returncode != 0:
            raise ServiceError(f"Falha ao consultar servico '{service_name}'.\n{output.strip()}")

        for line in result.stdout.splitlines():
            match = re.search(r":\s*(\d+)\s+([A-Z_]+)", line)
            if match and match.group(1) in STATE_BY_CODE:
                return STATE_BY_CODE[match.group(1)]

        raise ServiceError(f"Nao foi possivel identificar o estado do servico '{service_name}'.")

    def wait_for_state(self, service_name: str, target_state: str, timeout_seconds: Optional[int] = None) -> None:
        """Aguarda ate o servico atingir o estado esperado ou estourar o timeout."""
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.get_state(service_name) == target_state:
                return
            time.sleep(self.poll_interval_seconds)
        raise ServiceError(f"Timeout esperando servico '{service_name}' ficar em {target_state}.")

    def stop(self, service_name: str) -> None:
        """Para um servico e aguarda a confirmacao do estado STOPPED."""
        if self.get_state(service_name) == "STOPPED":
            return
        result = self._run_sc("stop", service_name)
        if result.returncode != 0:
            raise ServiceError(f"Falha ao parar servico '{service_name}'.\n{result.stdout}{result.stderr}")
        self.wait_for_state(service_name, "STOPPED")

    def start(self, service_name: str) -> None:
        """Inicia um servico e aguarda a confirmacao do estado RUNNING."""
        if self.get_state(service_name) == "RUNNING":
            return
        result = self._run_sc("start", service_name)
        if result.returncode != 0:
            raise ServiceError(f"Falha ao iniciar servico '{service_name}'.\n{result.stdout}{result.stderr}")
        self.wait_for_state(service_name, "RUNNING")


class HealthChecker:
    """Executa uma validacao final de saude do ambiente apos a recuperacao."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def run(self) -> bool:
        """Executa o Health Check configurado (TCP, HTTP ou nenhum)."""
        hc = self.config.healthcheck
        if not hc.enabled or hc.method == "NONE":
            return True
        if hc.method == "TCP":
            return self._check_tcp(hc.host, hc.port, hc.timeout_seconds)
        if hc.method == "HTTP":
            return self._check_http(hc.http_url, hc.timeout_seconds)
        return True

    @staticmethod
    def _check_tcp(host: str, port: int, timeout: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    @staticmethod
    def _check_http(url: str, timeout: int) -> bool:
        if not url:
            return True
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, TimeoutError, ValueError):
            return False


class PlantaoChecker:
    """Consulta o endpoint que informa o modo de operacao atual (normal ou
    plantao) de cada grupo de atendimento.

    O endpoint e chamado uma unica vez por execucao e retorna o modo de
    operacao de TODOS os grupos de atendimento numa unica resposta, na
    chave "grupos" (dicionario ``{grupo_id: modo_operacao}``). Cada regra
    do config.ini pode ser associada a um ou mais grupos atraves da chave
    ``plantao_grupos`` (lista separada por virgula); regras sem essa chave
    (lista vazia) nunca sao "gated" por este recurso e a recuperacao
    automatica funciona sempre normalmente para elas.

    Quando QUALQUER UM dos grupos responsaveis por uma regra estiver em
    modo "normal", ha tecnicos presentes na empresa capazes de atuar
    manualmente: a recuperacao automatica da regra e pulada (apenas
    notificar). Somente quando TODOS os grupos responsaveis estiverem em
    modo "Plantao" (ninguem presencial em nenhum deles) a recuperacao
    automatica e executada normalmente.

    Resposta esperada do endpoint:
        {"status": "ok", "message": "...", "grupos": {"1": "normal", "2": "Plantao"}}
    """

    def __init__(self, config: AppConfig, logger) -> None:
        self.config = config
        self.logger = logger
        self._grupos_cache: Optional[dict[str, str]] = None

    def _fetch_grupos(self) -> dict[str, str]:
        """Consulta o endpoint uma unica vez por execucao e cacheia o resultado."""
        if self._grupos_cache is not None:
            return self._grupos_cache

        plantao = self.config.plantao
        if not plantao.enabled or not plantao.url:
            self._grupos_cache = {}
            return self._grupos_cache

        try:
            with urllib.request.urlopen(plantao.url, timeout=plantao.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            self.logger.warning(
                "Falha ao consultar o endpoint de plantao (%s). Assumindo modo 'Plantao' "
                "para todos os grupos (recuperacao automatica habilitada).", exc
            )
            self._grupos_cache = {}
            return self._grupos_cache

        grupos_raw = data.get("grupos", {})
        grupos: dict[str, str] = {}
        if isinstance(grupos_raw, dict):
            for grupo_id, modo in grupos_raw.items():
                grupos[str(grupo_id).strip()] = str(modo).strip().lower()
        elif isinstance(grupos_raw, list):
            for item in grupos_raw:
                if isinstance(item, dict) and "grupo" in item:
                    grupos[str(item["grupo"]).strip()] = str(item.get("modo_operacao", "")).strip().lower()

        self._grupos_cache = grupos
        return grupos

    def is_normal_mode(self, grupos: list[str]) -> bool:
        """Retorna True se ALGUM dos grupos de atendimento informados estiver em modo "normal".

        Regras sem grupos definidos (lista vazia) nunca sao "gated":
        retorna sempre False (recuperacao automatica funciona
        normalmente). Em caso de falha na consulta, ou se nenhum dos
        grupos aparecer na resposta do endpoint, tambem retorna False,
        priorizando a recuperacao automatica.
        """
        grupos = [g.strip() for g in grupos if g and g.strip()]
        if not grupos:
            return False

        grupos_status = self._fetch_grupos()
        return any(grupos_status.get(grupo) == "normal" for grupo in grupos)


@dataclass
class ServiceStepResult:
    """Resultado de um unico passo (parar/iniciar) de um servico durante a recuperacao."""

    service: str
    operation: str  # "parar" | "iniciar"
    success: bool
    error: str = ""


@dataclass
class RecoveryResult:
    """Resultado consolidado de uma execucao de recuperacao."""

    success: bool
    action: str
    started_at: float
    finished_at: float
    message: str
    steps: list[ServiceStepResult] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.finished_at - self.started_at


class RecoveryOrchestrator:
    """Executa o fluxo completo de parada/inicializacao dos servicos do Protheus."""

    def __init__(
        self,
        config: AppConfig,
        controller: ServiceController,
        health_checker: HealthChecker,
        logger,
        on_step: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.controller = controller
        self.health_checker = health_checker
        self.logger = logger
        self.on_step = on_step or (lambda message: None)
        self._steps: list[ServiceStepResult] = []

    def _log(self, message: str) -> None:
        self.logger.info(message)
        self.on_step(message)

    def _record_step(self, service: str, operation: str, action: Callable[[], None]) -> None:
        """Executa a acao (parar/iniciar) de um servico, registrando o resultado do passo.

        Em caso de falha, o passo e registrado como malsucedido e a excecao e
        relancada (mantendo o comportamento de interromper a recuperacao no
        primeiro erro).
        """
        try:
            action()
        except ServiceError as exc:
            self._steps.append(ServiceStepResult(service=service, operation=operation, success=False, error=str(exc)))
            raise
        self._steps.append(ServiceStepResult(service=service, operation=operation, success=True))

    def stop_schedules(self) -> None:
        """Para os servicos Schedule na ordem inversa (do maior indice para o Broker)."""
        for service in self.config.schedule_services_shutdown_order:
            self._log(f"Parando servico Schedule: {service}")
            self._record_step(service, "parar", lambda service=service: self.controller.stop(service))

    def start_schedules(self) -> None:
        """Inicia os servicos Schedule na ordem crescente (do Broker para o maior indice)."""
        for service in reversed(self.config.schedule_services_shutdown_order):
            self._log(f"Iniciando servico Schedule: {service}")
            self._record_step(service, "iniciar", lambda service=service: self.controller.start(service))

    def restart_dbaccess(self) -> None:
        """Para o DBAccess, aguarda o tempo configurado e o inicia novamente."""
        self._log(f"Parando DBAccess: {self.config.dbaccess_service}")
        self._record_step(
            self.config.dbaccess_service, "parar", lambda: self.controller.stop(self.config.dbaccess_service)
        )

        wait_seconds = self.config.service_stop_wait_seconds
        if wait_seconds > 0:
            self._log(f"Aguardando {wait_seconds}s antes de iniciar o DBAccess.")
            time.sleep(wait_seconds)

        self._log(f"Iniciando DBAccess: {self.config.dbaccess_service}")
        self._record_step(
            self.config.dbaccess_service, "iniciar", lambda: self.controller.start(self.config.dbaccess_service)
        )

    def stop_service_group(self, services: list[str]) -> None:
        """Para uma lista customizada de servicos, na ordem informada."""
        for service in services:
            self._log(f"Parando servico: {service}")
            self._record_step(service, "parar", lambda service=service: self.controller.stop(service))

    def start_service_group(self, services: list[str]) -> None:
        """Inicia uma lista customizada de servicos, na ordem inversa da informada."""
        for service in reversed(services):
            self._log(f"Iniciando servico: {service}")
            self._record_step(service, "iniciar", lambda service=service: self.controller.start(service))

    def restart_service_group(self, services: list[str]) -> None:
        """Para e reinicia um grupo customizado de servicos (usado por regras especificas)."""
        self.stop_service_group(services)

        wait_seconds = self.config.service_stop_wait_seconds
        if wait_seconds > 0:
            self._log(f"Aguardando {wait_seconds}s antes de reiniciar o grupo de servicos.")
            time.sleep(wait_seconds)

        self.start_service_group(services)

    def validate_services(self, services: list[str]) -> None:
        """Confirma que todos os servicos informados estao em execucao."""
        for service in services:
            state = self.controller.get_state(service)
            if state != "RUNNING":
                raise ServiceError(
                    f"Servico '{service}' nao esta em execucao apos a recuperacao (estado: {state})."
                )

    def validate_all_services(self) -> None:
        """Confirma que todos os servicos monitorados (DBAccess + Schedules) estao em execucao."""
        self.validate_services([self.config.dbaccess_service, *self.config.schedule_services_shutdown_order])

    def run(self, action: str, simulate: bool = False, services: Optional[list[str]] = None) -> RecoveryResult:
        """Executa a acao de recuperacao solicitada e retorna o resultado consolidado.

        Args:
            action: nome da acao (ver ``ActionType`` em ``monitor_log.py``).
            simulate: quando True, apenas loga o que seria feito, sem executar.
            services: lista customizada de servicos, obrigatoria para a acao
                ``RESTART_SERVICE_GROUP``. Ignorada pelas demais acoes.
        """
        started_at = time.time()
        self._steps = []
        try:
            if simulate:
                self._log(f"[SIMULACAO] Acao '{action}' nao sera executada de fato.")
                return RecoveryResult(True, action, started_at, time.time(), "Simulado com sucesso.")

            if action == "RESTART_COMPLETO":
                self.stop_schedules()
                self.restart_dbaccess()
                self.start_schedules()
                self.validate_all_services()
            elif action == "RESTART_DBACCESS":
                self.restart_dbaccess()
            elif action == "RESTART_SCHEDULE":
                self.stop_schedules()
                wait_seconds = self.config.service_stop_wait_seconds
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                self.start_schedules()
            elif action == "RESTART_SERVICE_GROUP":
                if not services:
                    raise ServiceError("Acao RESTART_SERVICE_GROUP exige uma lista de servicos.")
                self.restart_service_group(services)
                self.validate_services(services)
            elif action == "SOMENTE_LOG":
                self._log("Acao configurada como SOMENTE_LOG: nenhuma acao de recuperacao executada.")
                finished_at = time.time()
                return RecoveryResult(True, action, started_at, finished_at, "Nenhuma acao executada (SOMENTE_LOG).")
            elif action == "NOTIFICAR":
                self._log(
                    "Acao configurada como NOTIFICAR: nenhuma tentativa de recuperacao sera feita, "
                    "apenas notificacao (e-mail/Teams, conforme configurado na regra)."
                )
                finished_at = time.time()
                return RecoveryResult(True, action, started_at, finished_at, "Nenhuma acao executada (NOTIFICAR).")

            else:
                raise ServiceError(f"Acao de recuperacao desconhecida: {action}")

            healthy = self.health_checker.run()
            if not healthy:
                raise ServiceError("Health Check falhou apos a recuperacao.")

            finished_at = time.time()
            return RecoveryResult(
                True, action, started_at, finished_at, "Recuperacao concluida com sucesso.", list(self._steps)
            )
        except ServiceError as exc:
            finished_at = time.time()
            self.logger.error("Falha na recuperacao: %s", exc)
            return RecoveryResult(False, action, started_at, finished_at, str(exc), list(self._steps))
