"""Leitura e analise do console.log do DBAccess, com motor de regras.

Este modulo e responsavel por ler apenas o final do arquivo de log (via
``collections.deque``), carregar as regras de deteccao definidas no
``config.ini`` e aplica-las sobre as linhas mais recentes para identificar
erros conhecidos.
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from utils import AppConfig, split_csv_list


class Severity(Enum):
    """Niveis de severidade suportados por uma regra."""

    BAIXA = "BAIXA"
    MEDIA = "MEDIA"
    ALTA = "ALTA"
    CRITICA = "CRITICA"


class ActionType(Enum):
    """Acoes de recuperacao suportadas por uma regra."""
    RESTART_COMPLETO = "RESTART_COMPLETO"
    RESTART_DBACCESS = "RESTART_DBACCESS"
    RESTART_SCHEDULE = "RESTART_SCHEDULE"
    RESTART_SERVICE_GROUP = "RESTART_SERVICE_GROUP"
    SOMENTE_LOG = "SOMENTE_LOG"
    EXECUTAR_SCRIPT = "EXECUTAR_SCRIPT"


class RuleError(Exception):
    """Erro ao carregar ou aplicar uma regra de deteccao."""

@dataclass
class Rule:
    """Representa uma regra de deteccao de erro configurada no config.ini."""

    rule_id: str
    description: str
    pattern: re.Pattern[str]
    severity: Severity
    action: ActionType
    send_email: bool
    send_teams: bool
    only_log: bool
    auto_execute: bool
    script_path: str = ""
    services: list[str] = field(default_factory=list)

    def matches(self, line: str) -> bool:
        """Retorna True se a linha corresponder ao padrao da regra."""
        return bool(self.pattern.search(line))


@dataclass
class RuleMatch:
    """Resultado de uma deteccao de regra em uma linha do log."""

    rule: Rule
    matched_line: str
    matched_at: datetime


def load_rules(config: AppConfig) -> list[Rule]:
    """Carrega todas as secoes ``[rule:*]`` do config.ini como objetos Rule."""
    rules: list[Rule] = []
    for section_name in config.raw.sections():
        if not section_name.startswith("rule:"):
            continue
        rule_id = section_name.split(":", 1)[1].strip()
        section = config.raw[section_name]

        try:
            pattern_text = section["pattern"]
        except KeyError as exc:
            raise RuleError(f"Regra '{rule_id}' sem a chave 'pattern' definida.") from exc

        try:
            severity = Severity(section.get("severity", "MEDIA").strip().upper())
            action = ActionType(section.get("action", "SOMENTE_LOG").strip().upper())
        except ValueError as exc:
            raise RuleError(f"Regra '{rule_id}' com severidade ou acao invalida: {exc}") from exc

        try:
            compiled = re.compile(pattern_text)
        except re.error as exc:
            raise RuleError(f"Regra '{rule_id}' com expressao regular invalida: {exc}") from exc

        rule_services = split_csv_list(section.get("services", ""))
        if action is ActionType.RESTART_SERVICE_GROUP and not rule_services:
            raise RuleError(
                f"Regra '{rule_id}' usa acao RESTART_SERVICE_GROUP mas nao define a chave 'services'."
            )

        rules.append(
            Rule(
                rule_id=rule_id,
                description=section.get("description", rule_id),
                pattern=compiled,
                severity=severity,
                action=action,
                send_email=section.getboolean("send_email", fallback=False),
                send_teams=section.getboolean("send_teams", fallback=False),
                only_log=section.getboolean("only_log", fallback=False),
                auto_execute=section.getboolean("auto_execute", fallback=False),
                script_path=section.get("script_path", ""),
                services=rule_services,
            )
        )

    if not rules:
        raise RuleError("Nenhuma regra '[rule:*]' encontrada no config.ini.")
    return rules


def get_rule_by_id(rules: list[Rule], rule_id: str) -> Rule:
    """Busca uma regra pelo identificador. Lanca RuleError se nao encontrada."""
    for rule in rules:
        if rule.rule_id == rule_id:
            return rule
    raise RuleError(f"Regra '{rule_id}' nao encontrada no config.ini.")


def tail_lines(path: Path, max_lines: int) -> list[str]:
    """Le apenas as ultimas ``max_lines`` linhas de um arquivo de texto grande."""
    if not path.exists():
        return []
    buffer: deque[str] = deque(maxlen=max_lines)
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            buffer.append(line.rstrip("\n"))
    return list(buffer)


class LogMonitor:
    """Le o console.log e aplica o motor de regras para detectar erros."""

    def __init__(self, config: AppConfig, rules: list[Rule]) -> None:
        self.config = config
        self.rules = rules

    def read_recent_lines(self) -> list[str]:
        """Le as ultimas linhas configuradas do console.log."""
        return tail_lines(self.config.console_log_path, self.config.lines_to_analyze)

    def find_match(self, lines: Optional[list[str]] = None) -> Optional[RuleMatch]:
        """Retorna a primeira ocorrencia de regra encontrada, da linha mais recente para a mais antiga."""
        lines = lines if lines is not None else self.read_recent_lines()
        for line in reversed(lines):
            for rule in self.rules:
                if rule.matches(line):
                    return RuleMatch(rule=rule, matched_line=line, matched_at=datetime.now())
        return None

    def confirm_match(self, match: RuleMatch) -> bool:
        """Aguarda o tempo configurado e confirma se o erro ainda esta presente no log."""
        wait_seconds = self.config.error_confirmation_seconds
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        lines = self.read_recent_lines()
        return any(match.rule.matches(line) for line in lines)
