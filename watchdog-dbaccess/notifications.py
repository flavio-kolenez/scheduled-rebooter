"""Envio de notificacoes por e-mail (SMTP) e Microsoft Teams (Webhook).

Cada regra do config.ini pode habilitar, independentemente, o envio de
e-mail e/ou Teams. Este modulo centraliza a montagem e o disparo dessas
notificacoes, sem interromper o fluxo principal em caso de falha de envio.
"""
from __future__ import annotations

import json
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage

from utils import SmtpConfig, TeamsConfig


class NotificationError(Exception):
    """Erro ao enviar uma notificacao."""


@dataclass
class NotificationPayload:
    """Dados exibidos em qualquer canal de notificacao."""

    environment: str
    server: str
    timestamp: str
    rule_description: str
    error_line: str
    action: str
    recovery_seconds: float
    result: str

    def subject(self) -> str:
        status = "SUCESSO" if self.result.upper() == "SUCESSO" else "FALHA"
        return f"[WatchDog DBAccess] {status} - {self.environment} - {self.rule_description}"

    def body_text(self) -> str:
        return (
            f"Ambiente: {self.environment}\n"
            f"Servidor: {self.server}\n"
            f"Data/Hora: {self.timestamp}\n"
            f"Regra acionada: {self.rule_description}\n"
            f"Erro identificado: {self.error_line}\n"
            f"Acao executada: {self.action}\n"
            f"Tempo de recuperacao: {self.recovery_seconds:.1f}s\n"
            f"Resultado: {self.result}\n"
        )


class EmailNotifier:
    """Envia notificacoes por e-mail via SMTP."""

    def __init__(self, smtp_config: SmtpConfig, logger) -> None:
        self.smtp_config = smtp_config
        self.logger = logger

    def send(self, payload: NotificationPayload) -> None:
        """Envia o e-mail de notificacao, se o SMTP estiver habilitado."""
        if not self.smtp_config.enabled:
            return
        if not self.smtp_config.to_addrs:
            self.logger.warning("SMTP habilitado, mas nenhum destinatario configurado.")
            return

        message = EmailMessage()
        message["Subject"] = payload.subject()
        message["From"] = self.smtp_config.from_addr
        message["To"] = ", ".join(self.smtp_config.to_addrs)
        message.set_content(payload.body_text())

        try:
            with smtplib.SMTP(self.smtp_config.host, self.smtp_config.port, timeout=15) as server:
                if self.smtp_config.use_tls:
                    server.starttls()
                if self.smtp_config.username:
                    server.login(self.smtp_config.username, self.smtp_config.password)
                server.send_message(message)
            self.logger.info("Notificacao por e-mail enviada para: %s", self.smtp_config.to_addrs)
        except (smtplib.SMTPException, OSError) as exc:
            self.logger.error("Falha ao enviar e-mail: %s", exc)
            raise NotificationError(str(exc)) from exc


class TeamsNotifier:
    """Envia notificacoes para um canal do Microsoft Teams via Webhook."""

    def __init__(self, teams_config: TeamsConfig, logger) -> None:
        self.teams_config = teams_config
        self.logger = logger

    def send(self, payload: NotificationPayload) -> None:
        """Envia o cartao de notificacao ao Teams, se o Webhook estiver habilitado."""
        if not self.teams_config.enabled or not self.teams_config.webhook_url:
            return

        card = {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": "00A651" if payload.result.upper() == "SUCESSO" else "FF0000",
            "summary": payload.subject(),
            "sections": [
                {
                    "activityTitle": payload.subject(),
                    "facts": [
                        {"name": "Ambiente", "value": payload.environment},
                        {"name": "Servidor", "value": payload.server},
                        {"name": "Data/Hora", "value": payload.timestamp},
                        {"name": "Regra", "value": payload.rule_description},
                        {"name": "Erro", "value": payload.error_line},
                        {"name": "Acao", "value": payload.action},
                        {"name": "Tempo de recuperacao", "value": f"{payload.recovery_seconds:.1f}s"},
                        {"name": "Resultado", "value": payload.result},
                    ],
                }
            ],
        }

        request = urllib.request.Request(
            self.teams_config.webhook_url,
            data=json.dumps(card).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
            self.logger.info("Notificacao enviada ao Microsoft Teams.")
        except (urllib.error.URLError, TimeoutError) as exc:
            self.logger.error("Falha ao enviar notificacao ao Teams: %s", exc)
            raise NotificationError(str(exc)) from exc


class NotificationService:
    """Fachada que dispara notificacoes conforme a configuracao de cada regra."""

    def __init__(self, config, logger) -> None:
        self.email_notifier = EmailNotifier(config.smtp, logger)
        self.teams_notifier = TeamsNotifier(config.teams, logger)
        self.logger = logger

    def notify(self, payload: NotificationPayload, send_email: bool, send_teams: bool) -> None:
        """Dispara os canais habilitados para esta regra, sem interromper em caso de falha."""
        if send_email:
            try:
                self.email_notifier.send(payload)
            except NotificationError:
                pass
        if send_teams:
            try:
                self.teams_notifier.send(payload)
            except NotificationError:
                pass
