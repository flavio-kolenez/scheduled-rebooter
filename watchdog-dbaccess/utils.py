"""Utilitarios compartilhados do WatchDog DBAccess.

Este modulo concentra funcoes e classes auxiliares utilizadas pelos demais
modulos da aplicacao: leitura de configuracao (``config.ini``), configuracao
de logging com rotacao, controle de execucao concorrente via arquivo de
lock, registro de historico em CSV e rotinas de backup/limpeza do
``console.log``.
"""
from __future__ import annotations

import configparser
import csv
import json
import logging
import logging.handlers
import os
import shutil
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_CONFIG_FILENAME = "config.ini"


class ConfigError(Exception):
    """Erro de configuracao invalida ou ausente."""


@dataclass
class SmtpConfig:
    """Configuracao de envio de e-mail via SMTP."""

    enabled: bool = False
    host: str = ""
    port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)


@dataclass
class TeamsConfig:
    """Configuracao de notificacao via Webhook do Microsoft Teams."""

    enabled: bool = False
    webhook_url: str = ""


@dataclass
class TelegramConfig:
    """Configuracao de notificacao via Bot da API do Telegram."""

    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


@dataclass
class HealthCheckConfig:
    """Configuracao do Health Check executado apos uma recuperacao."""

    enabled: bool = False
    method: str = "NONE"  # TCP | HTTP | NONE
    host: str = "127.0.0.1"
    port: int = 0
    http_url: str = ""
    timeout_seconds: int = 10


@dataclass
class AppConfig:
    """Configuracao completa da aplicacao, carregada a partir do config.ini."""

    config_path: Path
    environment: str
    server_name: str
    simulate_mode: bool

    console_log_path: Path
    lines_to_analyze: int
    check_interval_seconds: int
    error_confirmation_seconds: int
    min_recovery_interval_seconds: int

    service_stop_wait_seconds: int
    dbaccess_service: str
    schedule_services_shutdown_order: list[str]

    healthcheck: HealthCheckConfig
    smtp: SmtpConfig
    teams: TeamsConfig
    telegram: TelegramConfig

    log_dir: Path
    log_level: str
    log_max_bytes: int
    log_backup_count: int

    backup_dir: Path
    backup_retention_days: int

    status_dir: Path
    lock_file_name: str

    raw: configparser.ConfigParser

    @property
    def lock_file_path(self) -> Path:
        return self.status_dir / self.lock_file_name

    @property
    def last_recovery_file(self) -> Path:
        return self.status_dir / "last_recovery.json"

    @property
    def history_csv_path(self) -> Path:
        return self.status_dir / "history.csv"


def _default_base_dir() -> Path:
    """Retorna a pasta base da aplicacao, mesmo quando empacotada com PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def split_csv_list(value: str) -> list[str]:
    """Divide uma string separada por virgulas (com quebras de linha) em uma lista limpa."""
    return [item.strip() for item in value.split(",") if item.strip()]


def load_dotenv(env_path: Path) -> None:
    """Carrega variaveis de um arquivo ``.env`` (formato ``CHAVE=VALOR``) para
    o ambiente do processo atual, sem sobrescrever variaveis ja definidas
    (ex.: definidas pelo sistema operacional ou pelo Agendador de Tarefas).

    Usado para manter segredos (tokens, senhas, URLs de webhook) fora do
    ``config.ini`` e, portanto, fora do controle de versao.
    """
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, fallback: str = "") -> str:
    """Le uma variavel de ambiente (ex.: carregada do ``.env``), com fallback."""
    return os.environ.get(name, "").strip() or fallback


def _get(section, key: str, fallback: str) -> str:
    if hasattr(section, "get"):
        return section.get(key, fallback)
    return fallback


def _getboolean(section, key: str, fallback: bool) -> bool:
    value = _get(section, key, str(fallback))
    return str(value).strip().lower() in {"1", "true", "yes", "sim", "on"}


def load_config(config_path: Optional[str | Path] = None) -> AppConfig:
    """Carrega e valida o ``config.ini``, aplicando overrides de ambiente.

    Args:
        config_path: Caminho customizado para o arquivo de configuracao.
            Quando omitido, procura ``config.ini`` ao lado do executavel
            (ou do script, em modo desenvolvimento).

    Raises:
        ConfigError: Quando o arquivo nao existe ou uma chave obrigatoria
            esta ausente/invalida.
    """
    path = Path(config_path) if config_path else _default_base_dir() / DEFAULT_CONFIG_FILENAME
    if not path.exists():
        raise ConfigError(f"Arquivo de configuracao nao encontrado: {path}")

    load_dotenv(path.parent / ".env")

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")

    try:
        general = parser["general"]
        monitor = parser["monitor"]
        services = parser["services"]
        logging_section = parser["logging"]
        backup_section = parser["backup"]
        lock_section = parser["lock"]
    except KeyError as exc:
        raise ConfigError(f"Secao obrigatoria ausente no config.ini: {exc}") from exc

    environment = general.get("environment", "PRODUCTION").strip()

    # Overrides especificos de ambiente: secao opcional [environment:NOME]
    env_section_name = f"environment:{environment}"
    env_overrides = parser[env_section_name] if parser.has_section(env_section_name) else {}

    def env_get(section: configparser.SectionProxy, key: str, fallback: str) -> str:
        return _get(env_overrides, key, section.get(key, fallback))

    healthcheck_section = parser["healthcheck"] if parser.has_section("healthcheck") else {}
    smtp_section = parser["smtp"] if parser.has_section("smtp") else {}
    teams_section = parser["teams"] if parser.has_section("teams") else {}
    telegram_section = parser["telegram"] if parser.has_section("telegram") else {}

    config = AppConfig(
        config_path=path,
        environment=environment,
        server_name=general.get("server_name", "").strip() or socket.gethostname(),
        simulate_mode=general.getboolean("simulate_mode", fallback=False),
        console_log_path=Path(env_get(monitor, "console_log_path", "")),
        lines_to_analyze=int(env_get(monitor, "lines_to_analyze", "200")),
        check_interval_seconds=monitor.getint("check_interval_seconds", fallback=30),
        error_confirmation_seconds=monitor.getint("error_confirmation_seconds", fallback=10),
        min_recovery_interval_seconds=monitor.getint("min_recovery_interval_seconds", fallback=600),
        service_stop_wait_seconds=services.getint("service_stop_wait_seconds", fallback=5),
        dbaccess_service=env_get(services, "dbaccess_service", ""),
        schedule_services_shutdown_order=split_csv_list(env_get(services, "schedule_services", "")),
        healthcheck=HealthCheckConfig(
            enabled=_getboolean(healthcheck_section, "enabled", False),
            method=_get(healthcheck_section, "method", "NONE").strip().upper(),
            host=_get(healthcheck_section, "host", "127.0.0.1"),
            port=int(_get(healthcheck_section, "port", "0") or 0),
            http_url=_get(healthcheck_section, "http_url", ""),
            timeout_seconds=int(_get(healthcheck_section, "timeout_seconds", "10") or 10),
        ),
        smtp=SmtpConfig(
            enabled=_getboolean(smtp_section, "enabled", False),
            host=_get(smtp_section, "host", ""),
            port=int(_get(smtp_section, "port", "587") or 587),
            use_tls=_getboolean(smtp_section, "use_tls", True),
            username=_env("WATCHDOG_SMTP_USERNAME", _get(smtp_section, "username", "")),
            password=_env("WATCHDOG_SMTP_PASSWORD", _get(smtp_section, "password", "")),
            from_addr=_get(smtp_section, "from_addr", ""),
            to_addrs=split_csv_list(_get(smtp_section, "to_addrs", "")),
        ),
        teams=TeamsConfig(
            enabled=_getboolean(teams_section, "enabled", False),
            webhook_url=_env("WATCHDOG_TEAMS_WEBHOOK_URL", _get(teams_section, "webhook_url", "")),
        ),
        telegram=TelegramConfig(
            enabled=_getboolean(telegram_section, "enabled", False),
            bot_token=_env("WATCHDOG_TELEGRAM_BOT_TOKEN", _get(telegram_section, "bot_token", "")),
            chat_id=_env("WATCHDOG_TELEGRAM_CHAT_ID", _get(telegram_section, "chat_id", "")),
        ),
        log_dir=Path(logging_section.get("log_dir", "logs")),
        log_level=logging_section.get("log_level", "INFO").strip().upper(),
        log_max_bytes=logging_section.getint("max_bytes", fallback=5_242_880),
        log_backup_count=logging_section.getint("backup_count", fallback=10),
        backup_dir=Path(backup_section.get("backup_dir", "backup")),
        backup_retention_days=backup_section.getint("retention_days", fallback=30),
        status_dir=Path(lock_section.get("status_dir", "status")),
        lock_file_name=lock_section.get("lock_file", "status.lock"),
        raw=parser,
    )

    if not str(config.console_log_path):
        raise ConfigError("A chave 'monitor.console_log_path' nao esta configurada.")
    if not config.dbaccess_service:
        raise ConfigError("A chave 'services.dbaccess_service' nao esta configurada.")
    if not config.schedule_services_shutdown_order:
        raise ConfigError("A chave 'services.schedule_services' nao esta configurada.")

    base_dir = path.parent
    if not config.log_dir.is_absolute():
        config.log_dir = base_dir / config.log_dir
    if not config.backup_dir.is_absolute():
        config.backup_dir = base_dir / config.backup_dir
    if not config.status_dir.is_absolute():
        config.status_dir = base_dir / config.status_dir

    for directory in (config.log_dir, config.backup_dir, config.status_dir):
        directory.mkdir(parents=True, exist_ok=True)

    return config


def setup_logging(config: AppConfig) -> logging.Logger:
    """Configura logging com rotacao de arquivos e saida no console."""
    logger = logging.getLogger("watchdog_dbaccess")
    logger.setLevel(getattr(logging, config.log_level, logging.INFO))
    logger.handlers.clear()

    log_file = config.log_dir / "watchdog.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


class StatusLock:
    """Lock exclusivo baseado em arquivo, para evitar execucoes concorrentes."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._acquired = False

    def acquire(self) -> bool:
        """Tenta criar o arquivo de lock de forma atomica. Retorna False se ja existir."""
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as handle:
            handle.write(f"pid={os.getpid()}\nstarted_at={datetime.now().isoformat()}\n")
        self._acquired = True
        return True

    def release(self) -> None:
        """Remove o arquivo de lock, se este processo o possuir."""
        if self._acquired and self.lock_path.exists():
            self.lock_path.unlink(missing_ok=True)
        self._acquired = False

    def is_locked(self) -> bool:
        return self.lock_path.exists()

    def read_info(self) -> dict[str, str]:
        if not self.lock_path.exists():
            return {}
        info: dict[str, str] = {}
        for line in self.lock_path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip()
        return info

    def __enter__(self) -> "StatusLock":
        if not self.acquire():
            raise RuntimeError(f"Nao foi possivel obter o lock: {self.lock_path}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


class RecoveryTracker:
    """Controla o intervalo minimo entre recuperacoes, por regra."""

    def __init__(self, tracker_path: Path) -> None:
        self.tracker_path = tracker_path

    def _load(self) -> dict[str, str]:
        if not self.tracker_path.exists():
            return {}
        try:
            return json.loads(self.tracker_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def seconds_since_last(self, rule_id: str) -> Optional[float]:
        """Retorna quantos segundos se passaram desde a ultima recuperacao da regra."""
        data = self._load()
        last = data.get(rule_id)
        if not last:
            return None
        last_dt = datetime.fromisoformat(last)
        return (datetime.now() - last_dt).total_seconds()

    def can_recover(self, rule_id: str, min_interval_seconds: int) -> bool:
        elapsed = self.seconds_since_last(rule_id)
        if elapsed is None:
            return True
        return elapsed >= min_interval_seconds

    def register_recovery(self, rule_id: str) -> None:
        data = self._load()
        data[rule_id] = datetime.now().isoformat()
        self.tracker_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class RecoveryHistory:
    """Registra o historico de recuperacoes em um arquivo CSV."""

    FIELDNAMES = [
        "data", "hora", "ambiente", "servidor", "regra",
        "erro", "acao", "tempo_recuperacao_s", "resultado",
    ]

    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path

    def append(
        self,
        environment: str,
        server: str,
        rule_id: str,
        error_line: str,
        action: str,
        recovery_seconds: float,
        result: str,
    ) -> None:
        """Adiciona uma linha ao historico, criando o cabecalho se necessario."""
        is_new = not self.csv_path.exists()
        with open(self.csv_path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
            if is_new:
                writer.writeheader()
            now = datetime.now()
            writer.writerow({
                "data": now.strftime("%Y-%m-%d"),
                "hora": now.strftime("%H:%M:%S"),
                "ambiente": environment,
                "servidor": server,
                "regra": rule_id,
                "erro": error_line[:300],
                "acao": action,
                "tempo_recuperacao_s": f"{recovery_seconds:.1f}",
                "resultado": result,
            })


def backup_console_log(source: Path, backup_dir: Path) -> Optional[Path]:
    """Copia o console.log atual para a pasta de backup com timestamp."""
    if not source.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = backup_dir / f"console_{timestamp}.log"
    shutil.copy2(source, destination)
    return destination


def cleanup_old_backups(backup_dir: Path, retention_days: int) -> int:
    """Remove backups mais antigos que o periodo de retencao configurado."""
    if retention_days <= 0:
        return 0
    cutoff = time.time() - (retention_days * 86400)
    removed = 0
    for item in backup_dir.glob("console_*.log"):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                removed += 1
        except OSError:
            continue
    return removed
