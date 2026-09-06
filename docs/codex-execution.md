# Execução CODEX e transporte de receipt

O dispatcher gera o prompt específico de CODEX, enquanto o worker cuida de finalizar a
execução no protocolo da Factory. O modelo retorna dados; o código local valida e transporta.

```text
Dispatcher → prompt CODEX + contexto local da tentativa
           → worker → codex exec → JSON final
                    → validação → execution-receipt.json local
                    → validação do lease → upload via OAuth → EVENT
Próximo tick → receipt no Drive → RESULT_STAGED
```

## Escopo de escrita

`allowedWrites.stagingOnly=false` é obrigatório para este launcher `workspace-write`.
Ele permite somente as mutações explicitamente autorizadas pelo request em
`codexExecution.repositoryPath`. Alterações de código/arquivos do repositório são efeitos
da execução, e não artefatos administrativos da Factory.

Evidências e resultados estruturados da Factory continuam destinados a staging. Este
milestone não publica arquivos administrativos ou canônicos a partir de referências
produzidas pelo modelo; o único upload automático do worker é o receipt.

Para `stagingOnly=true`, o launcher recusa iniciar: não é seguro conceder escrita no
workspace e depender apenas de uma instrução textual para proibi-la. Um eventual executor
restrito a staging precisará de outro sandbox explícito. Campos ausentes ou tipos como
`"false"` também são recusados.

O prompt `FACTORY_CODEX_EXECUTION_V1` contém o snapshot do request e as instruções de
finalização próprias deste executor. Ele não reutiliza o bootstrap de chat. A regra do
bootstrap genérico sobre staging é delimitada aos artefatos administrativos; o contrato
CODEX explicita a autorização de mutação no repositório e a proibição de publicação canônica.

As verificações de elegibilidade, agente, dispatch, changeId, top-level, SHA e branch
permanecem. `LOCAL/<nome>` exige o nome exato do diretório e dispensa `origin`; os demais
repositórios continuam exigindo `origin` correspondente. Nenhuma pasta adicional recebe
permissão de escrita. Os arquivos de controle do worker ficam fora do repositório.

## Comando e versão

Inspecionados localmente: `codex-cli 0.153.0` e `codex exec --help`. Essa versão expõe
`--output-schema`. Seu uso para estruturar a resposta final também consta da
[documentação oficial](https://developers.openai.com/codex/non-interactive-mode).

O launcher produz a seguinte lista de argumentos, executada com `shell=False`:

```text
codex exec --sandbox workspace-write --ephemeral --color never
  --cd <repositoryPath>
  --output-schema <attemptDirectory>/result-schema.json
  --output-last-message <attemptDirectory>/final-message.txt -
```

O prompt entra por stdin. O worker verifica a lista completa de argumentos e o schema
antes de iniciar o processo. Não existem `--full-auto`, `--yolo`, `--add-dir`, aprovação
automática ou flags de bypass. `rg` é opcional; o prompt admite ferramentas de busca do shell.

## Contrato de resultado

`codex_result.result_schema()` gera o schema por tentativa, com `dispatchId` e `attemptId`
restritos às identidades daquela execução. Exemplo de resposta:

```json
{
  "schemaVersion": 1,
  "dispatchId": "D-EXAMPLE-0001",
  "attemptId": "D-EXAMPLE-0001-A001",
  "status": "SUCCEEDED",
  "summary": "Alteração autorizada concluída e verificada.",
  "producedArtifacts": [
    {"path": "src/example.py", "description": "Arquivo alterado no repositório autorizado."}
  ],
  "error": {"code": null, "detail": null, "retrySuggested": false}
}
```

Todos os campos são obrigatórios. Campos extras, prosa, fences Markdown, chaves JSON
duplicadas, números não finitos, status desconhecidos e IDs divergentes são rejeitados.
O JSON local tem limite de 2 MiB. `summary` e as descrições não podem estar vazios. Cada
referência de artefato contém somente `path` relativo ao repositório e `description`; não
há leitura ou upload automático do arquivo referenciado.

| Resultado/processo | Status do receipt |
| --- | --- |
| JSON `SUCCEEDED` válido, exit code 0 | `SUCCEEDED` |
| JSON `BLOCKED` válido, exit code 0 | `BLOCKED` |
| JSON `FAILED` válido, exit code 0 | `FAILED_FINAL` |
| Exit code diferente de 0 | `FAILED_FINAL`, erro `CODEX_PROCESS_FAILED` |
| Timeout | `FAILED_FINAL`, erro `CODEX_TIMEOUT` |
| JSON ausente, inválido ou incompatível | `FAILED_FINAL`, erro `CODEX_RESULT_INVALID` |

`SUCCEEDED` exige os três campos de erro no estado nulo/nulo/falso. `BLOCKED` e `FAILED`
exigem `error.code` e `error.detail` não vazios. `retrySuggested` é apenas dado: o worker
não agenda tentativas nem toma decisões sobre publicação. Exit code zero representa o
término do processo, não a conclusão bem-sucedida da tarefa.

## Receipt e transporte

O contexto privado da tentativa fica em `launch.json` (manifestVersion 2). Ele contém
dispatch, tentativa, token, agente, request e pasta de receipts. O modelo recebe somente
as identidades necessárias ao resultado; o token é aplicado mecanicamente pelo worker.

Após o processo, o worker persiste primeiro:

```text
state/codex-runs/<dispatchId>/<attemptId>/execution-receipt.json
```

O receipt v1 contém `schemaVersion`, `artifactType`, `dispatchId`, `attemptId`, `leaseToken`,
`agentId`, `requestDriveId`, `status`, `producedArtifacts`, `error`, `startedAt`, `finishedAt`
e `executorStatement`. Os tempos são medidos pelo worker. Resultado inválido produz um
receipt de falha criado pelo worker, sem transformar prosa em sucesso.

O transporte usa `build_google_services()` e `GoogleWorkspaceGateway` do dispatcher,
com a mesma configuração OAuth. O modo não interativo exige token existente/refresh token;
o worker não abre uma tela de consentimento. `codexLaunchEnabled` não é exigido para
recuperar um receipt, mas `writesEnabled` continua sendo respeitado.

Sob o mutex local, o transporte:

1. valida o receipt e encontra exatamente um dispatch na QUEUE;
2. compara dispatchId, attemptId, leaseToken, agentId e requestDriveId;
3. exige tentativa atual `CLAIMED` ou `RUNNING`, ainda dentro do lease;
4. resolve RECEIPTS em CONFIG e compara com o destino registrado pela tentativa;
5. verifica que o destino é uma pasta existente diretamente sob DISPATCH;
6. procura o nome terminal exato e rejeita conteúdo conflitante;
7. relê a linha e a expiração imediatamente antes de enviar;
8. faz upload dos bytes originais como `application/json` e registra `RECEIPT_UPLOADED`.

Nome remoto:

```text
EXECUTION_RECEIPT__<dispatchId>__<attemptId>.json
```

O transporte não altera a QUEUE. O próximo tick reconcilia normalmente para `RESULT_STAGED`.
O receipt mantém o status declarado; `RESULT_STAGED` não significa `SUCCEEDED`.

## Recuperação explícita

Com o OAuth configurado e um receipt local válido, o operador pode executar:

```powershell
.\.venv\Scripts\python.exe -m factory_dispatcher.receipt_transport --file "C:\caminho\execution-receipt.json" --config .\config.json
```

O comando só transporta o receipt. Não executa Codex, não cria uma tentativa, não renova
lease e não modifica o conteúdo original. Aceita receipts v1 com campos adicionais e
preserva seus bytes, incluindo BOM. Se um manifest novo existir para aquela tentativa,
o destino nele registrado também precisa corresponder; receipts históricos sem esse
contexto usam CONFIG e a identidade atual da QUEUE.

Um arquivo `.transport.json` ao lado do receipt registra hash, destino, ID do upload e
confirmação do evento. Repetir o comando com receipt remoto idêntico não cria outro arquivo.
Se o upload funcionar e o append de EVENT falhar, a recuperação detecta o upload já feito
e tenta registrar o evento pendente. Conteúdo conflitante nunca é sobrescrito.

`status.json` separa `exitCode`, `receiptStatus` e `transportState`. `PENDING` significa que
a cópia local deve ser recuperada explicitamente. Consulte o sidecar após a recuperação;
o status histórico do worker não é reescrito pelo comando administrativo.

Nenhuma recuperação real ou novo smoke foi executado nesta correção. Uma tentativa antiga
com lease expirado, substituído ou estado terminal será recusada, mesmo que o arquivo
local ainda exista. Não existe opção para ignorar essa validação.

## Limitações

- Mutex e detecção de duplicatas protegem esta máquina. Sheets e Drive não fornecem uma
  transação única entre validação de lease e upload. Uma alteração concorrente externa ou
  expiração durante a chamada ainda pode ocorrer; a reconciliação permanece fail-closed.
- Falha de rede durante o append de EVENT pode deixar confirmação ambígua; eventId é
  determinístico, mas o backend ainda não garante exatamente um append entre máquinas.
- Referências de evidência são metadados. A verificação semântica e a publicação canônica
  continuam fora do worker/dispatcher.
- O modelo pode ler fontes no Drive conforme as permissões existentes; leituras que
  exigirem aprovação podem resultar em `BLOCKED`. Nenhum bypass foi adicionado.
- Manifests antigos não têm o contexto/schema do novo worker e não devem ser reexecutados.
  A recuperação administrativa aceita o receipt v1 já produzido se o lease ainda for válido.

## Validação local

```powershell
.\.venv\Scripts\python.exe -m compileall .\factory_dispatcher
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Os testes usam processos, Google APIs, OAuth e filas simulados. Nenhum software adicional
é necessário para a correção; o parser estrito usa a biblioteca padrão do Python.

Validação realizada nesta entrega: `compileall` aprovado, **243 testes passaram**, Ruff lint
aprovado e formatter check aprovado (33 arquivos Python). `config.json` permaneceu idêntico
por SHA256. O comando de recuperação foi consultado apenas com `--help`.

## Arquivos desta correção

- Novos contratos e transporte: `factory_dispatcher/codex_prompt.py`,
  `factory_dispatcher/codex_result.py`, `factory_dispatcher/receipts.py`,
  `factory_dispatcher/receipt_transport.py`.
- Integração: `factory_dispatcher/codex_worker.py`, `factory_dispatcher/bootstrap.py`,
  `factory_dispatcher/engine.py`, `factory_dispatcher/cli.py`,
  `factory_dispatcher/launchers/base.py`, `factory_dispatcher/launchers/codex.py`,
  `factory_dispatcher/launchers/manual_chatgpt.py`.
- Configuração e Google: `factory_dispatcher/config.py`, `factory_dispatcher/google_auth.py`,
  `factory_dispatcher/google_gateway.py`.
- Testes atualizados: `tests/conftest.py`, `tests/test_codex_launcher.py`,
  `tests/test_codex_worker.py`, `tests/test_engine.py`.
- Testes novos: `tests/test_config.py`, `tests/test_google_receipt_gateway.py`,
  `tests/test_receipt_transport.py`.
- Documentação: `README.md`, `docs/codex-execution.md`.
