# Ações de recuperação (`action`)

Este documento explica como funcionam as ações de recuperação do WatchDog
DBAccess: onde estão implementadas no código, o que cada uma faz de fato,
como configurá-las no `config.ini` e como adicionar uma ação nova.

## Onde vivem no código

| Peça | Arquivo | Responsabilidade |
|---|---|---|
| `ActionType` (Enum) | [`monitor_log.py`](../monitor_log.py) | Define quais valores de `action` são **aceitos** dentro de uma seção `[rule:*]` do `config.ini`. Se o valor não bater com nenhum membro do enum, `load_rules()` lança `RuleError` e a aplicação nem inicializa. |
| `Rule` (dataclass) | [`monitor_log.py`](../monitor_log.py) | Representa uma regra carregada do `config.ini`, incluindo o campo `action: ActionType`. |
| `ServiceController` | [`services.py`](../services.py) | Encapsula chamadas ao `sc.exe` (start/stop/query de um serviço Windows). |
| `RecoveryOrchestrator.run()` | [`services.py`](../services.py) | **Onde a ação é de fato executada.** Recebe o nome da ação (string) e despacha para o método correspondente (`if/elif` por valor de string, não pelo enum). |
| `WatchdogApp._execute_recovery()` | [`monitor.py`](../monitor.py) | Chama `RecoveryOrchestrator.run(rule.action.value, ...)` quando uma regra é confirmada durante o `--check`. |
| `WatchdogApp.force_restart()` | [`monitor.py`](../monitor.py) | Usado pela flag `--restart` da CLI. Chama `RecoveryOrchestrator.run("RESTART_COMPLETO", ...)` **diretamente, com uma string fixa**, sem passar pelo motor de regras nem pelo `ActionType`. Por isso `RESTART_COMPLETO` continua funcionando via `--restart` mesmo não estando mais disponível como opção de `action` dentro de uma regra (ver observação abaixo).

Importante: `ActionType` (o que uma **regra** pode declarar em `config.ini`) e o
`if/elif` de `RecoveryOrchestrator.run()` (o que o **código** sabe executar)
são duas coisas independentes. Hoje o código em `services.py` ainda sabe
executar `RESTART_COMPLETO` e `RESTART_DBACCESS`, mas esses dois valores
foram removidos do `ActionType` — ou seja, **nenhuma regra pode mais usá-los**,
eles só são alcançáveis via `--restart` (manual, na linha de comando).

## Ações disponíveis para regras (`[rule:*].action`)

| Ação | O que faz | Serviços afetados | Requer chave extra? |
|---|---|---|---|
| `RESTART_SERVICE_GROUP` | Para, na ordem declarada, e depois inicia na ordem inversa, um grupo de serviços **customizado por regra**. Ao final, valida (`validate_services`) que todos voltaram para o estado `RUNNING`. | A lista definida na própria regra, chave `services` | Sim — `services` (obrigatória; lista separada por vírgula, ordem de **parada**) |
| `RESTART_SCHEDULE` | Para todos os serviços Schedule (ordem inversa da lista) e depois inicia na ordem original. | A lista **global** `[services].schedule_services` (compartilhada por qualquer regra que use essa ação) | Não |
| `SOMENTE_LOG` | Não executa nenhuma ação de recuperação. Apenas registra no log que a regra foi detectada (a linha `"Acao configurada como SOMENTE_LOG..."`). Respeita `only_log`/`auto_execute` normalmente — ou seja, só chega a notificar se essas flags permitirem. | Nenhum | Não |
| `NOTIFICAR` | Ação dedicada **apenas a notificações** (e-mail/Teams). Nunca tenta nenhuma recuperação e **ignora `only_log`/`auto_execute`** — sempre executa e dispara os canais habilitados na regra (`send_email`/`send_teams`), respeitando somente o intervalo mínimo `min_recovery_interval_seconds` (para não gerar spam). Ideal para erros que não fazem sentido "recuperar" via restart de serviço (ex.: erro de dado, como chave duplicada). O resultado exibido nas notificações é `ALERTA` (não `SUCESSO`/`FALHA`), e o card do Teams usa uma cor/texto neutros em vez do tom de "recuperação concluída". | Nenhum | Não |


## Ações "escondidas" (só via `--restart` manual, não via regra)

| Ação (string interna) | O que faz | Como é acionada |
|---|---|---|
| `RESTART_COMPLETO` | Para todos os Schedules → reinicia o DBAccess → inicia todos os Schedules → valida que **tudo** (DBAccess + Schedules) está `RUNNING`. | Só via `WatchDogProtheus.exe --restart` / `python monitor.py --restart`. Não pode ser usada em `[rule:*].action` (não existe mais no `ActionType`). |
| `RESTART_DBACCESS` | Para e reinicia somente o serviço do DBAccess (`[services].dbaccess_service`). | Código presente em `services.py`, mas **não é acionável hoje** por nenhum caminho (nem regra, nem flag de CLI). Existe como base de implementação caso um dia se queira expor essa opção. |

## Como configurar no `config.ini`

```ini
[rule:minha_regra]
description = Texto livre exibido em logs e notificacoes
pattern = (?i)algum regex aqui
severity = ALTA
action = RESTART_SCHEDULE
send_email = true
send_teams = false
only_log = false
auto_execute = true

; Obrigatorio apenas quando action = RESTART_SERVICE_GROUP
services =
    Servico_A,
    Servico_B,
    Servico_C
```

Campos que interagem com a ação escolhida:

- `only_log = true` força a regra a **apenas logar**, independente do valor de `action` (nunca chega a chamar `RecoveryOrchestrator.run()`) — **exceto** quando `action = NOTIFICAR`, que ignora essa flag (ver acima).
- `auto_execute = false` faz a regra ser detectada e logada, mas a ação **não é executada de fato** (fica só sugerida no log) — **exceto** quando `action = NOTIFICAR`, que sempre executa (só a notificação, nunca uma recuperação real).
- `general.simulate_mode = true` (seção `[general]`) faz **qualquer** ação apenas simular (loga "[SIMULACAO] Acao 'X' nao sera executada de fato") sem mexer em nenhum serviço — vale para todas as regras, é um interruptor global.

## Como criar uma ação nova

1. **Adicionar o valor ao enum** em [`monitor_log.py`](../monitor_log.py), dentro de `class ActionType`:
   ```python
   class ActionType(Enum):
       ...
       MINHA_ACAO_NOVA = "MINHA_ACAO_NOVA"
   ```
2. **Implementar o comportamento** em `RecoveryOrchestrator.run()` (`services.py`), adicionando um novo `elif`:
   ```python
   elif action == "MINHA_ACAO_NOVA":
       ...  # sua logica aqui, usando self.controller / self.config
   ```
3. **Documentar no comentário do `config.ini`** (bloco acima de `[rule:dbaccess_connection_lost]`) a nova opção válida para `action`.
4. **Atualizar a tabela acima** neste documento.
5. **Testar** com `python monitor.py --test-rule <id> --execute` (ou o `.exe` equivalente) antes de usar em produção.

## Referência rápida — fluxo de execução de uma regra

```mermaid
flowchart LR
    A["config.ini: [rule:x].action = 'NOME'"] --> B["load_rules() valida contra ActionType"]
    B -- valor invalido --> B1["RuleError na inicializacao"]
    B -- valido --> C["Rule.action guarda o ActionType"]
    C --> D["_execute_recovery() chama orchestrator.run(rule.action.value, ...)"]
    D --> E["RecoveryOrchestrator.run(): if/elif por string"]
    E --> F["Metodo especifico em ServiceController/RecoveryOrchestrator"]
    F --> G["Health Check (se habilitado)"]
    G --> H["RecoveryResult (sucesso/falha)"]
```
