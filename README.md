# FactoryDispatcher

MVP local e mecânico do dispatcher da PROJECT FACTORY. Este projeto vive em uma pasta
independente de `ProjectHub` e `Projeto-F-brica`; nenhum arquivo desses repositórios é
importado ou alterado.

O processo executa um único tick curto e encerra. A ordem é:

1. adquire um mutex local;
2. autentica no Google com OAuth Desktop;
3. lê `CONFIG` e `QUEUE` pelos cabeçalhos reais;
4. reconcilia receipts e leases expirados;
5. libera dependências e retries mecanicamente;
6. escolhe no máximo um item `READY` cujo `notBefore` venceu;
7. aplica dedupe, `maxAttempts`, capacidade e limite por `rootRunId`;
8. grava e relê o claim com lease secreto;
9. cria `STAGING/<dispatchId>/<attemptId>` e envia o bootstrap;
10. chama o launcher autorizado, grava `RUNNING` e encerra.

Não há API de LLM/OpenAI, interpretação semântica, scraping ou publicação canônica.
O executor CHATGPT automático é opt-in: usa apenas controles de autenticação,
novo chat, composer e envio, sem ler respostas. Testes não abrem browser nem executam Codex.

## Segurança por padrão

`config.example.json` vem com cinco chaves desligadas:

- `writesEnabled`: impede qualquer mutação em Sheets/Drive;
- `manualChatGptLaunchEnabled`: impede clipboard/browser;
- `browserChatGptLaunchEnabled`: impede o worker CHATGPT via CDP;
- `chatGptExtensionBridgeEnabled`: impede o worker/bridge CHATGPT via extensão;
- `codexLaunchEnabled`: impede processos `codex exec`.

O argumento `--dry-run` prevalece sobre a configuração e nunca grava nem lança. Tokens,
prompts, segredos OAuth e clipboard são redigidos dos logs JSONL. `config.json`,
`secrets/` e `state/` são ignorados pelo Git.

O mutex protege ticks concorrentes nesta máquina. A releitura posterior ao claim detecta
perda de lease e falha de forma fechada. O Google Sheets direto, porém, não oferece uma
operação compare-and-swap atômica entre máquinas: mantenha apenas um dispatcher ativo.
Antes de múltiplos workers, implemente o Queue Service previsto com Apps Script e
`LockService`.

## Instalação

Python 3.11 ou mais novo é necessário. No PowerShell, dentro desta pasta:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item -LiteralPath config.example.json -Destination config.json
```

Nesta máquina, o ambiente `.venv` e as dependências básicas já foram instalados.
Playwright é opcional e não é necessário para CODEX ou launcher manual.
O modo EXTENSION_BRIDGE usa `requirements-extension.txt`, sem Playwright no runtime.
Os testes da extensão precisam de Node no PATH ou do Node incluído no Playwright local,
sem abrir Chrome. Nesta revisão foi instalado websockets 16.1.1 na `.venv`.
Na revisão ATTACH_CDP foi encontrado Playwright 1.62.0 instalado; nenhuma instalação
foi executada pela revisão.

## OAuth Google

1. Em um projeto do Google Cloud, habilite Google Sheets API e Google Drive API.
2. Configure a tela de consentimento e crie um OAuth Client do tipo **Desktop app**.
3. Baixe o JSON para `secrets/credentials.json`.
4. Confirme que a conta autorizada possui acesso à planilha e às pastas da Factory.
5. Faça a primeira leitura manual:

```powershell
.\scripts\run-tick.ps1 -DryRun
```

O navegador abrirá apenas para o consentimento OAuth. O token local será salvo em
`secrets/token.json`. A planilha configurada é
`PROJECT_FACTORY_DISPATCH` (`1yUNfSW0jkCb-O9JpGpv8w8cYq5Uk6ngMlrOBlv7Pzww`).

## Configuração e execução

Edite somente o `config.json` local. Para uma validação segura, mantenha todas as flags
em `false` e execute:

```powershell
.\scripts\run-tick.ps1 -DryRun
```

Para o launcher manual do ChatGPT, use:

```json
{
  "writesEnabled": true,
  "manualChatGptLaunchEnabled": true,
  "chatGptBrowserMode": "DISABLED",
  "codexLaunchEnabled": false
}
```

Preserve as demais propriedades do exemplo. Um request lançável precisa declarar
explicitamente `executorType`; o dispatcher não o infere pelo agente ou pela tarefa.
Para `CHATGPT`, o bootstrap é copiado para o clipboard e `https://chatgpt.com/` é aberto.
O operador cola e envia o texto. Não há interação automática com a página.

Para a implementação automática, o modo recomendado é
[`EXTENSION_BRIDGE`](docs/chatgpt-extension-bridge.md): Chrome normal, extensão Manifest V3
carregada pelo operador, pairing explícito e WebSocket apenas em 127.0.0.1. A extensão
executa somente PRECHECK, NEW_CHAT, INSERT_BOOTSTRAP e SEND na aba dedicada registrada
explicitamente como PROJECT FACTORY TAB nas Opções. Outras abas ChatGPT do operador
são ignoradas. Completion continua vindo exclusivamente do receipt no Drive. O modo é opt-in,
não faz fallback para CDP/manual e não foi ativado no config real.

Reservas antigas comprovadamente PRE-SEND têm uma
[CLI de recuperação explícita](docs/chatgpt-extension-bridge.md#recuperação-explícita-de-uma-reserva-pre-send),
`python -m factory_dispatcher.chatgpt_extension_recover`, com IDs obrigatórios e
validação fail-closed. Não há force, retry, tick ou alteração de Drive/QUEUE.
A recuperação real não foi executada nesta implementação.

O caminho [`ATTACH_CDP`](docs/chatgpt-browser-execution.md) permanece disponível e seu
código foi preservado, com endpoint local explícito. Quando habilitado, não cai no
manual em caso de falha.
O humano inicia a instância dedicada uma vez, faz login e deixa uma única aba ChatGPT
funcional aberta. O worker reconecta a essa mesma instância/aba, clica New Chat,
envia o bootstrap exato e aguarda receipt no Drive. Ao terminar, apenas desconecta:
não fecha browser/aba nem abre outro perfil por dispatch.

O helper explícito de inicialização é:

```powershell
.\.venv\Scripts\python.exe -m factory_dispatcher.chatgpt_browser_start --config .\config.json
```

Se CDP válido já estiver na porta, não inicia outro browser. O antigo
`chatgpt_profile_setup` está desabilitado. Preflight bloqueia antes do claim em caso
de CDP/aba/auth/seletores/lock inválidos. Timeout ou envio incerto exige reconciliação
antes de retry e impede reutilizar a aba automaticamente. O config real não foi
habilitado nem alterado; nenhum smoke de browser foi executado nesta revisão.

## Receipt e reconciliação

O executor deve gravar exatamente um arquivo com o nome:

```text
EXECUTION_RECEIPT__<dispatchId>__<attemptId>.json
```

O dispatcher valida `artifactType`, `dispatchId`, `attemptId`, `leaseToken`, `agentId`,
`requestDriveId`, status terminal e validade do lease. Um receipt válido move a fila
somente para `RESULT_STAGED`; este MVP não publica artefatos canônicos. Receipt incorreto,
duplicado ou atrasado não promove a execução.

`attemptId` é mecânico e estável para cada contador da fila:
`<dispatchId>-A001`, `<dispatchId>-A002`, etc.

## Launcher Codex

O launcher exige `codexLaunchEnabled=true` na configuração local. No contrato
`DISPATCH_REQUEST` v2, `executorType`, `dispatchId`, `agentId`, `changeId`,
`maxExecutionMinutes` e `instructions` continuam na raiz do request. Os campos específicos
ficam obrigatoriamente no objeto `codexExecution`:

- `implementationEligible`: somente o booleano JSON `true`; `"true"` e `1` falham;
- `repository`, `repositoryPath`, `baseBranch`, `baseSha` e `specArtifact`: textos não vazios;
- `specVersion` ou `specIdentity`: ao menos um texto não vazio;
- `causalDecision`: referência não vazia, como texto ou objeto.

Não há fallback para campos Codex na raiz. Requests legados devem ser migrados para o
formato aninhado antes de uma nova execução autorizada. O exemplo
`examples/codex-request.example.json` usa o contrato v2.

`executorType` deve ser `CODEX` e `agentId` deve ser `CODEX_IMPLEMENTER`, com
`dispatchId`, `agentId` e `changeId` correspondentes à fila. A comparação de `changeId`
aceita `null` e célula vazia como ausência; qualquer valor presente de um só lado ou
diferente do outro é rejeitado. Isso permite smokes técnicos sem CR e mantém a identidade
exata para pedidos vinculados a CR. Outros tipos JSON não são convertidos para ausência.

Git confirma que `repositoryPath` é absoluto, existe e é exatamente o top-level, com
`HEAD == baseSha` e branch igual a `baseBranch`. Para `repository=LOCAL/<nome>`, o nome
final do diretório deve coincidir com `<nome>` (sem diferenciar maiúsculas no Windows);
esse caso não exige nem consulta `origin`. O prefixo é literal: `local/` e `Local/` não
dispensam o remote. Para todos os demais repositórios, `origin` continua obrigatório e
deve corresponder à identidade declarada.

`maxExecutionMinutes` deve ser um número inteiro JSON entre 1 e 240. Booleanos, frações
e strings numéricas são rejeitados. O prompt é
passado por stdin para `codex exec -`. O comando usa `--sandbox workspace-write`,
`--ephemeral`, `--cd <repositoryPath>`, `--output-schema` e `--output-last-message`. Ele nunca usa
`--dangerously-bypass-approvals-and-sandbox`, `--yolo`, `--full-auto` ou `--add-dir`.
A referência oficial dos argumentos é a documentação de
[modo não interativo do Codex](https://developers.openai.com/codex/non-interactive-mode).

Veja `examples/codex-request.example.json`. O caminho de repositório é sempre o diretório
de trabalho exclusivo do processo; nenhuma pasta extra recebe permissão de escrita.
Um worker local separado preserva o caráter one-shot do dispatcher, aplica
`maxExecutionMinutes` (entre 1 e 240), apaga o arquivo temporário do prompt assim que o
entrega por stdin e grava `codexPid`, `exitCode`, `timedOut`, stdout, stderr e mensagem
final em `state/codex-runs/<dispatchId>/<attemptId>/status.json`.

CODEX recebe um prompt próprio, `FACTORY_CODEX_EXECUTION_V1`. O request precisa declarar
`allowedWrites.stagingOnly=false` para permitir as mutações autorizadas exclusivamente em
`repositoryPath`. Essas mutações são efeitos do trabalho; os artefatos administrativos da
Factory continuam destinados a staging, e publicação canônica continua proibida.
`stagingOnly=true`, ausente ou com tipo inválido bloqueia o launcher antes do processo:
este executor `workspace-write` não oferece um modo de execução restrito apenas a staging.

O modelo entrega JSON estruturado na resposta final. O worker valida esse JSON, persiste
`execution-receipt.json` e envia o receipt com o mesmo cliente/OAuth Google do dispatcher.
O modelo não recebe o leaseToken nem deve criar/enviar receipts pelo MCP. Falha de transporte
mantém a cópia local para recuperação explícita. O próximo tick reconcilia o receipt para
`RESULT_STAGED`, inclusive quando o resultado declarado é `BLOCKED`.

O loader de `config.json` aceita UTF-8 com e sem BOM, sem regravar o arquivo.
O contrato completo, o comando inspecionado e as limitações estão em
[Execução CODEX e recuperação de receipts](docs/codex-execution.md).

Para transportar posteriormente um receipt local, sem executar outro job:

```powershell
.\.venv\Scripts\python.exe -m factory_dispatcher.receipt_transport --file "C:\caminho\execution-receipt.json" --config .\config.json
```

Esse comando exige OAuth já disponível, `writesEnabled=true` e a tentativa ainda
`CLAIMED`/`RUNNING`, com lease vigente e identidade correspondente. Não renova leases nem
altera a QUEUE. A execução desse comando não faz parte dos testes locais.

## Testes locais

Os testes são inteiramente locais e usam doubles para Google, clipboard, navegador e
Codex:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
```

Eles cobrem claim/lease, staging/bootstrap, dry-run, receipt válido e inválido, expiração,
backoff, dedupe, limite por root run, redaction e guardas/flags do Codex.
Os testes do launcher também cobrem o contrato v2 aninhado, rejeição do formato legado,
comparação de `changeId` com `null`, identidades Git locais/remotas e executável ausente.
Os testes do worker verificam stdin, sandbox, saída, PID, timeout e códigos de término
usando processos simulados.
Também cobrem resultado JSON válido/inválido, `BLOCKED` com exit code zero, escopo de escrita,
receipt persistido antes do upload, rejeição de leases obsoletos, duplicatas, recuperação,
reconciliação no próximo tick e configurações com/sem BOM.

O dispatch histórico `D-SMOKE-CODEX-0001` informado como `FAILED_FINAL` não deve ser
reutilizado nem alterado. A correção e os testes locais não acionam esse dispatch.

## Smoke manual, não executado

O registro existente `D-SMOKE-0001` permanece `WAITING_HUMAN`, tentativa `0`. O código não
contém rotina que o ative. Quando um operador decidir executar o smoke:

1. mantenha o Task Scheduler ainda desativado;
2. confirme que o request imutável correspondente declara `executorType=CHATGPT` e tem
   `dispatchId`/`agentId` idênticos à fila;
3. ajuste `config.json` para habilitar writes e o launcher manual, mantendo Codex falso;
4. altere conscientemente o smoke para `READY` na planilha;
5. execute uma única vez `.\scripts\run-tick.ps1`;
6. confirme `CLAIMED`, staging, bootstrap, evento `LAUNCHED` e estado `RUNNING`;
7. cole o bootstrap no ChatGPT e envie manualmente;
8. coloque o receipt exato em `DISPATCH/RECEIPTS`;
9. execute outro tick e confirme apenas `RESULT_STAGED`.

Se qualquer identidade não corresponder, não corrija por inferência: devolva o item a
`WAITING_HUMAN` e corrija o request antes de uma nova tentativa autorizada.

## Windows Task Scheduler

Registre a tarefa somente depois do smoke manual:

```powershell
.\scripts\install-task-scheduler.ps1
```

A tarefa roda a cada minuto, apenas com o usuário logado, nível limitado, diretório de
trabalho explícito e política `IgnoreNew` para instâncias sobrepostas. Se uma tarefa com o
mesmo nome já existir, o script para; use `-Replace` apenas de forma consciente.

Para desativar sem apagar histórico:

```powershell
Disable-ScheduledTask -TaskName 'ProjectFactory Dispatcher Tick'
```

Logs ficam em `state/logs/dispatcher-AAAA-MM-DD.jsonl`. Saídas de um Codex autorizado
ficam em `state/codex-runs/<dispatchId>/<attemptId>/`.

## Diagnóstico rápido

- `OAuth Desktop credentials not found`: coloque o arquivo no caminho configurado.
- `SKIPPED_MUTEX_BUSY`: outro tick local ainda está ativo; nenhuma ação foi tomada.
- `INVALID_REQUEST_CONTRACT`: falta identidade explícita ou existe divergência com a fila.
- `capacity_full`: já existe execução ChatGPT ativa no limite configurado.
- `LEASE_EXPIRED`: o receipt válido não apareceu dentro do lease.
- `ClaimLostError`: outro escritor alterou a linha; não relance manualmente sem reconciliar.
- Erro 403 do Google: confirme APIs, consentimento, scopes e compartilhamento dos assets.
