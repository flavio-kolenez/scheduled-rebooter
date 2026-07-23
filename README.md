# Schedule Rebooter

Ferramenta de linha de comando para reinicializar os serviços **TOTVS Protheus Schedule** em ordem controlada e sequencial, sem necessidade de Python instalado no servidor.

---

## Como funciona

O executável realiza um ciclo completo de reinício em duas fases:

```
DESLIGAMENTO  8 → 7 → 6 → 5 → 4 → 3 → 2 → 1 → 0 Broker
INICIALIZAÇÃO 0 Broker → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
```

**Serviços alvo (em ordem de desligamento):**

| # | Nome do serviço                        |
|---|----------------------------------------|
| 1 | 02 - Totvs Protheus Schedule 8         |
| 2 | 02 - Totvs Protheus Schedule 7         |
| 3 | 02 - Totvs Protheus Schedule 6         |
| 4 | 02 - Totvs Protheus Schedule 5         |
| 5 | 02 - Totvs Protheus Schedule 4         |
| 6 | 02 - Totvs Protheus Schedule 3         |
| 7 | 02 - Totvs Protheus Schedule 2         |
| 8 | 02 - Totvs Protheus Schedule 1         |
| 9 | 02 - TOTVS Protheus Schedule 0 Broker  |

**Garantias de execução:**

- Um serviço por vez, sem paralelismo.
- Só avança para o próximo após validar o estado esperado (`STOPPED` ou `RUNNING`).
- Interrompe imediatamente na primeira falha, informando fase e serviço exatos.
- Log detalhado por passo e resumo final.

---

## Requisitos

- Windows Server com os serviços TOTVS Protheus Schedule instalados.
- Conta com permissão de **administrador local** (o executável solicita elevação UAC automaticamente).
- Python **não precisa** estar instalado no servidor.

---

## Execução

Copie `schedule-rebooter.exe` para o servidor e execute via PowerShell ou Prompt de Comando.

**Uso básico:**

```powershell
.\schedule-rebooter.exe
```

**Com timeout customizado** (padrão: 60 segundos por serviço):

```powershell
.\schedule-rebooter.exe --timeout 120
```

**Exemplo de saída esperada:**

```
Inicio do reinicio controlado do TOTVS Protheus Schedule.

=== Fase: DESLIGAMENTO ===
[DESLIGAMENTO] Passo 1/9 | Servico: '02 - Totvs Protheus Schedule 8'
[DESLIGAMENTO] Acao: STOP
[DESLIGAMENTO] Validacao: OK | Estado atual: STOPPED (esperado: STOPPED)
...

=== Fase: INICIALIZACAO ===
[INICIALIZACAO] Passo 1/9 | Servico: '02 - TOTVS Protheus Schedule 0 Broker'
[INICIALIZACAO] Acao: START
[INICIALIZACAO] Validacao: OK | Estado atual: RUNNING (esperado: RUNNING)
...

Resumo final: SUCESSO TOTAL.
```

**Em caso de falha:**

```
[DESLIGAMENTO] Validacao: FALHA | Timeout esperando servico '...' ficar em STOPPED.

Resumo final: FALHA INTERROMPIDA. Fase: DESLIGAMENTO | Servico: '...' | Motivo: ...
```

---

## Códigos de saída

| Código | Significado                                              |
|--------|----------------------------------------------------------|
| `0`    | Sucesso total — todos os serviços reiniciados            |
| `1`    | Não executado como administrador                         |
| `2`    | Falha geral de serviço ou comando                        |
| `3`    | Falha interrompida com fase e serviço identificados      |

---

## Gerando um novo executável

Necessário quando houver alterações em `index.py` ou `names.py`.

**Pré-requisito:** Python 3.10+ e o ambiente virtual configurado localmente.

### Passo a passo

1. Ative o ambiente virtual:

```powershell
.\.venv\Scripts\Activate.ps1
```

2. Instale ou atualize o PyInstaller:

```powershell
python -m pip install --upgrade pyinstaller
```

3. Limpe artefatos anteriores:

```powershell
Remove-Item -Recurse -Force .\build, .\dist -ErrorAction SilentlyContinue
```

4. Gere o novo executável:

```powershell
python -m PyInstaller --onefile --name schedule-rebooter --uac-admin index.py
```

O executável será gerado em `dist\schedule-rebooter.exe`.

### Build reproduzível com .spec

O arquivo `schedule-rebooter.spec` está versionado e reflete as opções de build atuais.
Para reconstruir sem precisar passar os parâmetros manualmente:

```powershell
python -m PyInstaller schedule-rebooter.spec
```

> As pastas `build/` e `dist/` estão no `.gitignore` e não devem ser commitadas.
> Apenas o `.spec` e o código-fonte são versionados.

---

## Testes locais

Para testar o script sem os serviços reais do TOTVS, existe um ambiente de teste que cria 9 serviços dummy com os mesmos nomes.

### Pré-requisitos

- Python instalado localmente com o ambiente virtual configurado (`.venv`).
- PowerShell rodando **como Administrador**.
- `dummy_service.exe` compilado (instruções abaixo).

### 1. Compilar o serviço dummy

```powershell
.\.venv\Scripts\Activate.ps1
python -m PyInstaller --onefile --name dummy_service --uac-admin `
  --hidden-import win32timezone --hidden-import servicemanager `
  --distpath tests\dist --workpath tests\build tests\dummy_service.py
```

O executável será gerado em `tests\dist\dummy_service.exe`.

### 2. Criar e iniciar os serviços dummy

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\tests\setup_services.ps1
```

Confirme que todos estão em execução:

```powershell
Get-Service "02 - Totvs Protheus Schedule*", "02 - TOTVS Protheus Schedule 0 Broker" | Select-Object Name, Status
```

### 3. Executar o script

```powershell
.\dist\schedule-rebooter.exe
```

### 4. Limpar o ambiente após os testes

```powershell
.\tests\teardown_services.ps1
```

> **Observação:** Os serviços dummy requerem que o `dummy_service.exe` seja um executável **standalone compilado com PyInstaller** — não é possível usar `python.exe` diretamente como binário de serviço porque o Windows Services (LocalSystem) não tem acesso ao Python instalado em `AppData` do usuário.

---

## TODO

- Documentar quais erros indicam travamento dos serviços (padrões de mensagem, códigos, contexto).
- Implementar leitura do arquivo `error.log` para identificar erros de travamento automaticamente.
- Executar a reinicialização de forma condicional, acionada pelos erros encontrados no `error.log`.

---

## Estrutura do projeto

```
schedule-rebooter/
├── index.py                  # Lógica principal de reinício
├── names.py                  # Listas de serviços em ordem de desligamento e inicialização
├── schedule-rebooter.spec    # Configuração de build do PyInstaller
├── .gitignore
└── README.md
```
