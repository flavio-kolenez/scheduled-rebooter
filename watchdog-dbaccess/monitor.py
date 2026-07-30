"""Ponto de entrada do WatchDog DBAccess.

Monitora o ``console.log`` do DBAccess, aplica o motor de regras
configurado em ``config.ini`` e executa acoes automaticas de recuperacao
quando um erro conhecido e detectado e confirmado.

Uso:
    monitor.py                     Executa um ciclo de verificacao (padrao).
    monitor.py --check             Executa um ciclo de verificacao.
    monitor.py --restart           Forca uma recuperacao completa manual.
    monitor.py --status            Exibe o status atual do WatchDog.
    monitor.py --test-rule ID      Simula a deteccao de uma regra especifica.

Este script foi projetado para ser executado preferencialmente pelo
Agendador de Tarefas do Windows, em intervalos curtos e regulares
(configurados externamente na tarefa, em linha com
``monitor.check_interval_seconds`` do config.ini).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import Optional

from monitor_log import ActionType, LogMonitor, RuleMatch, get_rule_by_id, load_rules
from notifications import NotificationPayload, NotificationService
from services import HealthChecker, RecoveryOrchestrator, ServiceController, is_admin
from utils import (
    AppConfig,
    RecoveryHistory,
    RecoveryTracker,
    StatusLock,
    backup_console_log,
    cleanup_old_backups,
    load_config,
    setup_logging,
)


class WatchdogApp:
    """Orquestra o ciclo completo de monitoramento e recuperacao."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.logger = setup_logging(config)
        self.rules = load_rules(config)
        self.log_monitor = LogMonitor(config, self.rules)
        self.controller = ServiceController()
        self.health_checker = HealthChecker(config)
        self.orchestrator = RecoveryOrchestrator(config, self.controller, self.health_checker, self.logger)
        self.notifications = NotificationService(config, self.logger)
        self.history = RecoveryHistory(config.history_csv_path)
        self.tracker = RecoveryTracker(config.last_recovery_file)
        self.lock = StatusLock(config.lock_file_path)

    def run_check_cycle(self) -> int:
        """Executa um unico ciclo: le o log, confirma o erro e recupera se necessario."""
        self.logger.info("Iniciando ciclo de verificacao (ambiente=%s).", self.config.environment)

        if self.lock.is_locked():
            self.logger.warning("Lock ja existente em '%s'. Ciclo ignorado.", self.config.lock_file_path)
            return 0

        match = self.log_monitor.find_match()
        if match is None:
            self.logger.info("Nenhum erro identificado nas ultimas %d linhas.", self.config.lines_to_analyze)
            return 0

        self.logger.warning(
            "Possivel erro detectado. Regra='%s' Linha='%s'", match.rule.rule_id, match.matched_line
        )

        if not self.log_monitor.confirm_match(match):
            self.logger.info("Erro nao confirmado apos nova leitura do log. Ocorrencia ignorada.")
            return 0

        return self._handle_match(match)

    def _handle_match(self, match: RuleMatch) -> int:
        rule = match.rule

        if rule.action is not ActionType.NOTIFICAR:
            if rule.only_log:
                self.logger.info("Regra '%s' configurada apenas para log. Nenhuma acao executada.", rule.rule_id)
                return 0

            if not rule.auto_execute:
                self.logger.info("Regra '%s' nao possui execucao automatica habilitada.", rule.rule_id)
                return 0

        if not self.tracker.can_recover(rule.rule_id, self.config.min_recovery_interval_seconds):
            self.logger.warning(
                "Intervalo minimo entre recuperacoes ainda nao atingido para a regra '%s'.", rule.rule_id
            )
            return 0

        try:
            with self.lock:
                return self._execute_recovery(match)
        except RuntimeError as exc:
            self.logger.warning("Nao foi possivel obter o lock: %s", exc)
            return 0

    def _execute_recovery(self, match: RuleMatch) -> int:
        rule = match.rule
        self.logger.info("Iniciando recuperacao para a regra '%s' (acao=%s).", rule.rule_id, rule.action.value)

        backup_path = backup_console_log(self.config.console_log_path, self.config.backup_dir)
        if backup_path:
            self.logger.info("Backup do console.log criado em: %s", backup_path)
        cleanup_old_backups(self.config.backup_dir, self.config.backup_retention_days)

        result = self.orchestrator.run(rule.action.value, simulate=self.config.simulate_mode, services=rule.services)
        self.tracker.register_recovery(rule.rule_id)

        if rule.action is ActionType.NOTIFICAR:
            result_text = "ALERTA"
        else:
            result_text = "SUCESSO" if result.success else "FALHA"
        self.history.append(
            environment=self.config.environment,
            server=self.config.server_name,
            rule_id=rule.rule_id,
            error_line=match.matched_line,
            action=rule.action.value,
            recovery_seconds=result.duration_seconds,
            result=result_text,
        )

        payload = NotificationPayload(
            environment=self.config.environment,
            server=self.config.server_name,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            rule_description=rule.description,
            error_line=match.matched_line,
            action=rule.action.value,
            recovery_seconds=result.duration_seconds,
            result=result_text,
        )
        self.notifications.notify(payload, rule.send_email, rule.send_teams, rule.send_telegram)

        self.logger.info("Recuperacao finalizada. Resultado=%s Duracao=%.1fs", result_text, result.duration_seconds)
        return 0 if result.success else 2

    def force_restart(self) -> int:
        """Executa uma recuperacao completa manual, ignorando o motor de regras."""
        self.logger.info("Recuperacao manual (RESTART_COMPLETO) solicitada via linha de comando.")
        try:
            with self.lock:
                backup_path = backup_console_log(self.config.console_log_path, self.config.backup_dir)
                if backup_path:
                    self.logger.info("Backup do console.log criado em: %s", backup_path)

                result = self.orchestrator.run("RESTART_COMPLETO", simulate=self.config.simulate_mode)
                result_text = "SUCESSO" if result.success else "FALHA"
                self.history.append(
                    environment=self.config.environment,
                    server=self.config.server_name,
                    rule_id="MANUAL",
                    error_line="Execucao manual via --restart",
                    action="RESTART_COMPLETO",
                    recovery_seconds=result.duration_seconds,
                    result=result_text,
                )
                self.logger.info("Recuperacao manual finalizada. Resultado=%s", result_text)
                return 0 if result.success else 2
        except RuntimeError as exc:
            self.logger.warning("Nao foi possivel obter o lock: %s", exc)
            return 1

    def print_status(self) -> int:
        """Exibe o status atual do WatchDog: lock, ultima recuperacao e servicos."""
        print(f"Ambiente: {self.config.environment}")
        print(f"Servidor: {self.config.server_name}")
        print(f"Modo simulacao: {'SIM' if self.config.simulate_mode else 'NAO'}")

        if self.lock.is_locked():
            print(f"Lock ativo: {self.lock.read_info()}")
        else:
            print("Lock: livre.")

        print("\nServicos:")
        services = [self.config.dbaccess_service, *self.config.schedule_services_shutdown_order]
        for service in services:
            try:
                state = self.controller.get_state(service)
            except Exception as exc:  # noqa: BLE001 - reportar qualquer falha de consulta ao usuario
                state = f"ERRO ({exc})"
            print(f"  - {service}: {state}")

        print("\nUltimas recuperacoes:")
        for rule in self.rules:
            elapsed = self.tracker.seconds_since_last(rule.rule_id)
            elapsed_text = f"{elapsed:.0f}s atras" if elapsed is not None else "nunca"
            print(f"  - {rule.rule_id}: {elapsed_text}")

        return 0

    def test_rule(self, rule_id: str, force_execute: bool) -> int:
        """Simula a deteccao de uma regra especifica para validar todo o pipeline."""
        rule = get_rule_by_id(self.rules, rule_id)
        fake_match = RuleMatch(
            rule=rule,
            matched_line=f"[TESTE] Linha simulada para a regra '{rule.rule_id}'.",
            matched_at=datetime.now(),
        )
        self.logger.info("Executando teste da regra '%s' (execucao real=%s).", rule_id, force_execute)

        original_simulate = self.config.simulate_mode
        original_auto_execute = rule.auto_execute
        original_only_log = rule.only_log
        original_min_interval = self.config.min_recovery_interval_seconds
        try:
            self.config.simulate_mode = not force_execute
            self.config.min_recovery_interval_seconds = 0
            rule.auto_execute = True
            rule.only_log = False
            return self._handle_match(fake_match)
        finally:
            self.config.simulate_mode = original_simulate
            self.config.min_recovery_interval_seconds = original_min_interval
            rule.auto_execute = original_auto_execute
            rule.only_log = original_only_log


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Interpreta os argumentos de linha de comando do WatchDog DBAccess."""
    parser = argparse.ArgumentParser(
        description="WatchDog DBAccess - monitoramento e recuperacao automatica do TOTVS Protheus."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Executa um ciclo de verificacao (padrao).")
    group.add_argument("--restart", action="store_true", help="Forca uma recuperacao completa manual.")
    group.add_argument("--status", action="store_true", help="Exibe o status atual do WatchDog.")
    group.add_argument("--test-rule", metavar="RULE_ID", help="Simula a deteccao de uma regra especifica.")
    parser.add_argument("--config", metavar="PATH", help="Caminho customizado para o config.ini.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Usado com --test-rule para executar a acao de fato (fora do modo simulacao).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Ponto de entrada principal do WatchDog DBAccess."""
    args = parse_args(argv)

    if not is_admin():
        print("Este aplicativo precisa ser executado com privilegios administrativos.", file=sys.stderr)
        return 1

    try:
        config = load_config(args.config)
        app = WatchdogApp(config)
    except Exception as exc:  # noqa: BLE001 - falha de inicializacao deve ser reportada e encerrar
        print(f"Falha ao inicializar o WatchDog DBAccess: {exc}", file=sys.stderr)
        return 1

    try:
        if args.restart:
            return app.force_restart()
        if args.status:
            return app.print_status()
        if args.test_rule:
            return app.test_rule(args.test_rule, force_execute=args.execute)
        return app.run_check_cycle()
    except Exception as exc:  # noqa: BLE001 - garantir que a tarefa agendada nunca finalize sem log
        app.logger.exception("Erro nao tratado durante a execucao: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
