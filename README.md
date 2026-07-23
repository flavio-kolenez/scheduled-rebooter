# Schedule Rebooter

Ferramenta de linha de comando para reinicializar os servicos **TOTVS Protheus Schedule** em ordem controlada e sequencial, sem necessidade de Python instalado no servidor.

---

## Como funciona

O executavel realiza um ciclo completo de reinicio em duas fases:

```
DESLIGAMENTO  8 → 7 → 6 → 5 → 4 → 3 → 2 → 1 → 0 Broker
INICIALIZACAO 0 Broker → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8
```

**Servicos alvo (em ordem de desligamento):**

| # | Nome do servico                        |
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

**Garantias de execucao:**

- Um servico por vez, sem paralelismo.
- So avanca para o proximo apos validar o estado esperado (`STOPPED` ou `RUNNING`).
- Interrompe imediatamente na primeira falha, informando fase e servico exatos.
- Log detalhado por passo e resumo final.

---

## Requisitos

- Windows Server com os servicos TOTVS Protheus Schedule instalados.
- Conta com permissao de **administrador local** (o executavel solicita elevacao UAC automaticamente).
- Python **nao precisa** estar instalado no servidor.

---

## Execucao

Copie `schedule-rebooter.exe` para o servidor e execute via PowerShell ou Prompt de Comando.

**Uso basico:**

```powershell
.\schedule-rebooter.exe
```

**Com timeout customizado** (padrao: 60 segundos por servico):

```powershell
.\schedule-rebooter.exe --timeout 120
```

**Exemplo de saida esperada:**

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

## Codigos de saida

| Codigo | Significado                                              |
|--------|----------------------------------------------------------|
| `0`    | Sucesso total — todos os servicos reiniciados            |
| `1`    | Nao executado como administrador                         |
| `2`    | Falha geral de servico ou comando                        |
| `3`    | Falha interrompida com fase e servico identificados      |

---

## Gerando um novo executavel

Necessario quando houver alteracoes em `index.py` ou `names.py`.

**Prerequisito:** Python 3.10+ e o ambiente virtual configurado localmente.

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

4. Gere o novo executavel:

```powershell
python -m PyInstaller --onefile --name schedule-rebooter --uac-admin index.py
```

O executavel sera gerado em `dist\schedule-rebooter.exe`.

### Build reproduzivel com .spec

O arquivo `schedule-rebooter.spec` esta versionado e reflete as opcoes de build atuais.
Para reconstruir sem precisar passar os parametros manualmente:

```powershell
python -m PyInstaller schedule-rebooter.spec
```

> As pastas `build/` e `dist/` estao no `.gitignore` e nao devem ser commitadas.
> Apenas o `.spec` e o codigo-fonte sao versionados.

---

## Estrutura do projeto

```
schedule-rebooter/
├── index.py                  # Logica principal de reinicio
├── names.py                  # Listas de servicos em ordem de desligamento e inicializacao
├── schedule-rebooter.spec    # Configuracao de build do PyInstaller
├── .gitignore
└── README.md
```
