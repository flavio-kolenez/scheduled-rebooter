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
from dataclasses import dataclass, field
from email.message import EmailMessage

from services import ServiceStepResult
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
    steps: list[ServiceStepResult] = field(default_factory=list)

    def subject(self) -> str:
        return f"[WatchDog DBAccess] {self.result.upper()} - {self.environment} - {self.rule_description}"

    def status_final(self) -> str:
        """Frase de status final da operacao, para exibir ao time de plantao."""
        result_upper = self.result.upper()
        if result_upper == "SUCESSO":
            return "Recuperacao concluida com sucesso."
        if result_upper == "FALHA":
            return "Recuperacao concluida com falhas."
        return "Alerta: nenhuma recuperacao automatica foi executada."

    def format_steps(self, line_break: str = "\n") -> str:
        """Formata a lista de servicos parados/iniciados, na ordem em que ocorreram."""
        if not self.steps:
            return ""
        lines = []
        for step in self.steps:
            icon = "✅" if step.success else "❌"
            operacao = "Parar" if step.operation == "parar" else "Iniciar"
            line = f"{icon} {operacao}: {step.service}"
            if not step.success and step.error:
                line += f" — {step.error}"
            lines.append(line)
        return line_break.join(lines)

    def body_text(self) -> str:
        lines = [
            f"Ambiente: {self.environment}",
            f"Servidor: {self.server}",
            f"Data/Hora: {self.timestamp}",
            f"Regra acionada: {self.rule_description}",
            f"Erro identificado: {self.error_line}",
            f"Acao executada: {self.action}",
        ]
        if self.steps:
            lines.append("Servicos afetados (na ordem em que ocorreram):")
            lines.append(self.format_steps())
        lines.append(f"Tempo de recuperacao: {self.recovery_seconds:.1f}s")
        lines.append(f"Resultado: {self.result}")
        lines.append(f"Status final: {self.status_final()}")
        return "\n".join(lines) + "\n"


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
            emoji, color = "❌", "Attention"

        body_blocks: list[dict] = [
            {
                "type": "TextBlock",
                "text": f"{emoji} WatchDog DBAccess - {payload.rule_description}",
                "size": "Large",
                "weight": "Bolder",
                "color": color,
                "wrap": True,
            }
        ]

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
                f"**Status final:** {payload.status_final()}"
            )
            facts = [
                {"title": "Servidor", "value": payload.server},
                {"title": "Data/Hora", "value": payload.timestamp},
                {"title": "Tempo de recuperacao", "value": f"{payload.recovery_seconds:.1f}s"},
            ]

        body_blocks.append(
            {
                "type": "TextBlock",
                "text": detail_text,
                "wrap": True,
                "size": "Medium",
            }
        )

        if payload.steps:
            body_blocks.append(
                {
                    "type": "TextBlock",
                    "text": (
                        "**Servicos afetados (na ordem em que ocorreram):**  \n"
                        f"{payload.format_steps(line_break='  \n')}"
                    ),
                    "wrap": True,
                    "size": "Small",
                    "spacing": "Medium",
                }
            )

        body_blocks.append(
            {
                "type": "FactSet",
                "separator": True,
                "spacing": "Medium",
                "facts": facts,
            }
        )

        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "version": "1.2",
                        "body": body_blocks,
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
        else:
            emoji = "✅" if result_upper == "SUCESSO" else "❌"
            detail = (
                f"*Erro identificado:* {payload.error_line}\n"
                f"*Acao executada:* {payload.action}\n"
            )
            if payload.steps:
                detail += (
                    f"*Servicos afetados (na ordem em que ocorreram):*\n{payload.format_steps()}\n"
                )
            detail += (
                f"*Tempo de recuperacao:* {payload.recovery_seconds:.1f}s\n"
                f"*Status final:* {payload.status_final()}"
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
