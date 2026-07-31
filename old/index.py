import argparse
import ctypes
import re
import subprocess
import sys
import time

from names import SERVICES_SHUTDOWN_ORDER, SERVICES_STARTUP_ORDER


class ServiceError(Exception):
	pass


class RestartSequenceError(Exception):
	def __init__(self, phase: str, service_name: str, message: str):
		super().__init__(message)
		self.phase = phase
		self.service_name = service_name
		self.message = message


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
	try:
		return bool(ctypes.windll.shell32.IsUserAnAdmin())
	except Exception:
		return False


def run_sc_command(*args: str) -> subprocess.CompletedProcess:
	return subprocess.run(
		["sc", *args],
		capture_output=True,
		text=True,
		check=False,
		encoding="cp1252",
		errors="replace",
	)


def get_service_state(service_name: str) -> str:
	result = run_sc_command("query", service_name)
	output = f"{result.stdout}\n{result.stderr}"

	if "FAILED 1060" in output:
		raise ServiceError(f"Servico '{service_name}' nao existe.")

	if result.returncode != 0:
		raise ServiceError(
			f"Falha ao consultar servico '{service_name}'.\n{output.strip()}"
		)

	for line in result.stdout.splitlines():
		# Funciona em qualquer idioma do Windows porque extrai o codigo numerico
		# Exemplo EN: STATE  : 4  RUNNING
		# Exemplo PT: ESTADO : 4  RUNNING
		match = re.search(r":\s*(\d+)\s+([A-Z_]+)", line)
		if match:
			state_code = match.group(1)
			if state_code in STATE_BY_CODE:
				return STATE_BY_CODE[state_code]

	raise ServiceError(
		f"Nao foi possivel identificar o estado do servico '{service_name}'."
	)


def wait_for_state(service_name: str, target_state: str, timeout_seconds: int) -> None:
	deadline = time.time() + timeout_seconds

	while time.time() < deadline:
		current_state = get_service_state(service_name)
		if current_state == target_state:
			return
		time.sleep(2)

	raise ServiceError(
		f"Timeout esperando servico '{service_name}' ficar em {target_state}."
	)


def stop_service(service_name: str, timeout_seconds: int) -> None:
	current_state = get_service_state(service_name)
	if current_state != "STOPPED":
		result = run_sc_command("stop", service_name)
		if result.returncode != 0:
			output = f"{result.stdout}\n{result.stderr}".strip()
			raise ServiceError(f"Falha ao parar servico '{service_name}'.\n{output}")

	wait_for_state(service_name, "STOPPED", timeout_seconds)


def start_service(service_name: str, timeout_seconds: int) -> None:
	current_state = get_service_state(service_name)
	if current_state != "RUNNING":
		result = run_sc_command("start", service_name)
		if result.returncode != 0:
			output = f"{result.stdout}\n{result.stderr}".strip()
			raise ServiceError(f"Falha ao iniciar servico '{service_name}'.\n{output}")

	wait_for_state(service_name, "RUNNING", timeout_seconds)


def run_phase(
	phase_name: str,
	services: list[str],
	action: str,
	expected_state: str,
	timeout_seconds: int,
) -> None:
	print(f"\n=== Fase: {phase_name} ===")
	for index, service_name in enumerate(services, start=1):
		print(f"[{phase_name}] Passo {index}/{len(services)} | Servico: '{service_name}'")
		print(f"[{phase_name}] Acao: {action}")

		try:
			if action == "STOP":
				stop_service(service_name, timeout_seconds)
			else:
				start_service(service_name, timeout_seconds)

			current_state = get_service_state(service_name)
			if current_state != expected_state:
				raise ServiceError(
					f"Validacao falhou para '{service_name}'. Estado atual: {current_state}, esperado: {expected_state}."
				)

			print(
				f"[{phase_name}] Validacao: OK | Estado atual: {current_state} (esperado: {expected_state})"
			)
		except ServiceError as exc:
			print(f"[{phase_name}] Validacao: FALHA | {exc}")
			raise RestartSequenceError(phase_name, service_name, str(exc)) from exc


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Reinicia o conjunto de servicos TOTVS Protheus Schedule em ordem controlada."
	)
	parser.add_argument(
		"--timeout",
		type=int,
		default=60,
		help="Tempo maximo (em segundos) para cada validacao de parada/inicio.",
	)
	return parser.parse_args()


def main() -> int:
	args = parse_args()

	if not is_admin():
		print("Este script precisa ser executado como administrador.")
		return 1

	try:
		print("Inicio do reinicio controlado do TOTVS Protheus Schedule.")
		run_phase(
			phase_name="DESLIGAMENTO",
			services=SERVICES_SHUTDOWN_ORDER,
			action="STOP",
			expected_state="STOPPED",
			timeout_seconds=args.timeout,
		)
		run_phase(
			phase_name="INICIALIZACAO",
			services=SERVICES_STARTUP_ORDER,
			action="START",
			expected_state="RUNNING",
			timeout_seconds=args.timeout,
		)
		print("\nResumo final: SUCESSO TOTAL.")
		return 0
	except RestartSequenceError as exc:
		print(
			"\nResumo final: FALHA INTERROMPIDA. "
			f"Fase: {exc.phase} | Servico: '{exc.service_name}' | Motivo: {exc.message}"
		)
		return 3
	except ServiceError as exc:
		print(f"\nResumo final: FALHA INTERROMPIDA. Motivo: {exc}")
		return 2

if __name__ == "__main__":
	sys.exit(main())
