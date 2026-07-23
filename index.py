import argparse
import ctypes
import re
import subprocess
import sys
import time


class ServiceError(Exception):
	pass


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
	if current_state == "STOPPED":
		print(f"Servico '{service_name}' ja esta parado.")
		return

	result = run_sc_command("stop", service_name)
	if result.returncode != 0:
		output = f"{result.stdout}\n{result.stderr}".strip()
		raise ServiceError(f"Falha ao parar servico '{service_name}'.\n{output}")

	wait_for_state(service_name, "STOPPED", timeout_seconds)
	print(f"Servico '{service_name}' parado com sucesso.")


def start_service(service_name: str, timeout_seconds: int) -> None:
	current_state = get_service_state(service_name)
	if current_state == "RUNNING":
		print(f"Servico '{service_name}' ja esta em execucao.")
		return

	result = run_sc_command("start", service_name)
	if result.returncode != 0:
		output = f"{result.stdout}\n{result.stderr}".strip()
		raise ServiceError(f"Falha ao iniciar servico '{service_name}'.\n{output}")

	wait_for_state(service_name, "RUNNING", timeout_seconds)
	print(f"Servico '{service_name}' iniciado com sucesso.")


def restart_service(service_name: str, timeout_seconds: int) -> None:
	stop_service(service_name, timeout_seconds)
	start_service(service_name, timeout_seconds)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Reinicia um servico do Windows com validacao de estado."
	)
	parser.add_argument("service_name", help="Nome do servico no Windows (Service Name)")
	parser.add_argument(
		"--timeout",
		type=int,
		default=60,
		help="Tempo maximo (em segundos) para parada/inicio do servico.",
	)
	return parser.parse_args()


def main() -> int:
	args = parse_args()

	if not is_admin():
		print("Este script precisa ser executado como administrador.")
		return 1

	try:
		restart_service(args.service_name, args.timeout)
		print(f"Reinicio do servico '{args.service_name}' concluido.")
		return 0
	except ServiceError as exc:
		print(f"Erro: {exc}")
		return 2

if __name__ == "__main__":
	sys.exit(main())
