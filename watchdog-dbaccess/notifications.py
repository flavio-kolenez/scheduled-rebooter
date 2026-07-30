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

from utils import SmtpConfig, TeamsConfig, TelegramConfig


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
        return f"[WatchDog DBAccess] {self.result.upper()} - {self.environment} - {self.rule_description}"

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
    """Envia notificacoes para um canal do Microsoft Teams via um fluxo do
    Power Automate (URL de trigger HTTP), usando um Adaptive Card.
    """

    def __init__(self, teams_config: TeamsConfig, logger) -> None:
        self.teams_config = teams_config
        self.logger = logger

    @staticmethod
    def _build_card(payload: NotificationPayload) -> dict:
        result_upper = payload.result.upper()
        is_alert = result_upper == "ALERTA"
        is_success = result_upper == "SUCESSO"

        if is_alert:
            emoji, color = "🔔", "Warning"
        elif is_success:
            emoji, color = "✅", "Good"
        else:
            emoji, color = "⚠️", "Attention"

        if is_alert:
            detail_text = (
                f"**Ambiente:** {payload.environment}  \n"
                f"**Erro identificado:** {payload.error_line}  \n"
                f"**Observacao:** nenhuma acao automatica foi executada para esta regra "
                f"(acao=NOTIFICAR). Verificacao manual recomendada."
            )
            facts = [
                {"title": "Servidor", "value": payload.server},
                {"title": "Data/Hora", "value": payload.timestamp},
            ]
        else:
            detail_text = (
                f"**Ambiente:** {payload.environment}  \n"
                f"**Erro identificado:** {payload.error_line}  \n"
                f"**Acao executada:** {payload.action}  \n"
                f"**Resultado:** {payload.result}"
            )
            facts = [
                {"title": "Servidor", "value": payload.server},
                {"title": "Data/Hora", "value": payload.timestamp},
                {"title": "Tempo de recuperacao", "value": f"{payload.recovery_seconds:.1f}s"},
            ]

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "version": "1.2",
                        "body": [
                            {
                                "type": "TextBlock",
                                "text": f"{emoji} WatchDog DBAccess - {payload.rule_description}",
                                "size": "Large",
                                "weight": "Bolder",
                                "color": color,
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": detail_text,
                                "wrap": True,
                                "size": "Medium",
                            },
                            {
                                "type": "FactSet",
                                "separator": True,
                                "spacing": "Medium",
                                "facts": facts,
                            },
                        ],
                    },
                }
            ],
        }

    def send(self, payload: NotificationPayload) -> None:
        """Envia o cartao de notificacao ao Teams, se o fluxo estiver habilitado."""
        if not self.teams_config.enabled or not self.teams_config.webhook_url:
            return

        card = self._build_card(payload)

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


class TelegramNotifier:
    """Envia notificacoes para um grupo/canal do Telegram via a Bot API
    (metodo ``sendMessage``), usando formatacao Markdown.
    """

    def __init__(self, telegram_config: TelegramConfig, logger) -> None:
        self.telegram_config = telegram_config
        self.logger = logger

    @staticmethod
    def _build_text(payload: NotificationPayload) -> str:
        result_upper = payload.result.upper()
        if result_upper == "ALERTA":
            emoji = "🔔"
            detail = (
                f"*Erro identificado:* {payload.error_line}\n"
                f"*Observacao:* nenhuma acao automatica foi executada. "
                f"Verificacao manual recomendada."
            )
        elif result_upper == "SUCESSO":
            emoji = "✅"
            detail = (
                f"*Erro identificado:* {payload.error_line}\n"
                f"*Acao executada:* {payload.action}\n"
                f"*Resultado:* {payload.result}\n"
                f"*Tempo de recuperacao:* {payload.recovery_seconds:.1f}s"
            )
        else:
            emoji = "⚠️"
            detail = (
                f"*Erro identificado:* {payload.error_line}\n"
                f"*Acao executada:* {payload.action}\n"
                f"*Resultado:* {payload.result}\n"
                f"*Tempo de recuperacao:* {payload.recovery_seconds:.1f}s"
            )

        return (
            f"{emoji} *WatchDog DBAccess - {payload.rule_description}*\n"
            f"*Ambiente:* {payload.environment}\n"
            f"{detail}\n"
            f"*Servidor:* {payload.server}\n"
            f"*Data/Hora:* {payload.timestamp}"
        )

    def send(self, payload: NotificationPayload) -> None:
        """Envia a mensagem de notificacao ao Telegram, se o bot estiver habilitado."""
        if not self.telegram_config.enabled or not self.telegram_config.bot_token or not self.telegram_config.chat_id:
            return

        data = {
            "chat_id": self.telegram_config.chat_id,
            "text": self._build_text(payload),
            "parse_mode": "Markdown",
        }
        url = f"https://api.telegram.org/bot{self.telegram_config.bot_token}/sendMessage"
        request = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
            self.logger.info("Notificacao enviada ao Telegram.")
        except (urllib.error.URLError, TimeoutError) as exc:
            self.logger.error("Falha ao enviar notificacao ao Telegram: %s", exc)
            raise NotificationError(str(exc)) from exc


class NotificationService:
    """Fachada que dispara notificacoes conforme a configuracao de cada regra."""

    def __init__(self, config, logger) -> None:
        self.email_notifier = EmailNotifier(config.smtp, logger)
        self.teams_notifier = TeamsNotifier(config.teams, logger)
        self.telegram_notifier = TelegramNotifier(config.telegram, logger)
        self.logger = logger

    def notify(
        self,
        payload: NotificationPayload,
        send_email: bool,
        send_teams: bool,
        send_telegram: bool = False,
    ) -> None:
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
        if send_telegram:
            try:
                self.telegram_notifier.send(payload)
            except NotificationError:
                pass
