# CHATGPT por EXTENSION_BRIDGE (recomendado, opt-in)

Este modo usa Chrome normal, uma extensão Manifest V3 carregada pelo operador e um
WebSocket exclusivamente local. ATTACH_CDP permanece disponível, sem alterações no seu
executor. CODEX não utiliza este bridge. Não existe fallback do modo EXTENSION_BRIDGE
para CDP, launcher manual ou outro browser.

```text
dispatcher → PRECHECK antes do claim → worker local
                                         ↓ comandos autenticados
                                bridge em 127.0.0.1
                                         ↕ WebSocket
                                extensão Manifest V3
                                         ↓ quatro operações mecânicas
                                PROJECT FACTORY TAB registrada

worker local → polling read-only do EXECUTION_RECEIPT no Drive → encerramento
```

A extensão não é agente nem orquestrador. ACK de SEND significa apenas que o handler
do clique retornou. Não significa envio confirmado pelo serviço, conclusão, qualidade
da resposta ou sucesso semântico. Somente o receipt válido encerra o worker; seu status
pode inclusive ser BLOCKED ou FAILED_FINAL. O dispatcher continua responsável pela
reconciliação mecânica posterior de RESULT_STAGED.

O perfil pode conter zero ou mais abas ChatGPT normais do operador e uma Factory Tab
explicitamente registrada. Somente a Factory Tab recebe comandos. Ter várias abas
ChatGPT não é ambiguidade; usar outra aba normalmente não altera o binding da Factory.

## Arquivos e responsabilidades

- `factory_dispatcher/chatgpt_extension_protocol.py`: schemas fechados, HMAC, limites,
  configuração segura, lock e reserva persistente do bridge.
- `chatgpt_extension_bridge.py`: servidor WebSocket, autenticação mútua e relay serial.
- `chatgpt_extension_pair.py`: geração explícita de dois segredos locais independentes.
- `chatgpt_extension_client.py`: cliente privado do worker e CLI de preflight read-only.
- `chatgpt_extension_worker.py`: um envio e polling do contrato de receipt existente.
- `launchers/chatgpt_extension.py`: preflight, binding da aba e manifesto de tentativa v3.
- `browser_extension/project_factory_bridge/manifest.json`: Manifest V3.
- `service_worker.js`: conexão, autenticação, heartbeat e reconexão de transporte.
- `controller.js`: resolução da Factory Tab registrada, reserva e sequência fechada de ações.
- `factory_tab.js`: registro explícito por ID/sessão, validação da aba e canal privado das Opções.
- `selectors.js`, `composer.js`, `content.js`: somente controles permitidos da UI.
- `options.html`, `options.js`: pairing, lista explícita de abas e status/registro/limpeza
  da Factory Tab; nenhum segredo volta a ser exibido.
- `tests/test_chatgpt_extension.py`, `tests/extension_bridge.test.mjs`: transporte local
  com extensão falsa, worker/Drive falsos, Chrome/DOM falsos e verificações estruturais.

## Manifest e permissões

Chrome mínimo 120. `host_permissions` e `content_scripts.matches` contêm exclusivamente
`https://chatgpt.com/*`. O content script roda apenas no frame principal, no mundo
isolado. Não há acesso de UI a outros sites.

Permissões: `storage` para pairing/registro/reserva e `alarms` para reconectar o transporte a cada
30 segundos quando desconectado. Não há `tabs`, `scripting`, `debugger`, cookies, history,
bookmarks, downloads, `<all_urls>`, scripts externos ou `externally_connectable`.
A permissão de host fornece os metadados da aba ChatGPT para a consulta restrita.

O CSP autoriza conexão WebSocket somente a `ws://127.0.0.1:*`; não concede acesso a
outros sites. JavaScript constrói uma URL de host fixo e porta inteira validada.
`chrome.storage.local` é restringido a `TRUSTED_CONTEXTS`: o content script não recebe
o segredo. `storage.session` mantém uma identidade da sessão do browser; a reserva e
o registro permanecem em `storage.local`. Após mudança da sessão, o registro antigo é
inválido e exige novo registro explícito, nunca remapeamento automático de Tab IDs.

O heartbeat tem intervalo de 20 segundos. A reconexão de transporte nunca reenvia uma
ação nem remove uma reserva. O suporte a WebSocket no service worker e a necessidade
de tráfego periódico estão descritos na [documentação oficial do Chrome](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets).
Veja também [storage e níveis de acesso](https://developer.chrome.com/docs/extensions/reference/api/storage)
e [metadados de tabs por permissão de host](https://developer.chrome.com/docs/extensions/reference/api/tabs).

## Configuração (não aplicada ao config real nesta entrega)

Mesclar no `config.json` existente, sem substituir credenciais ou outras propriedades:

```json
{
  "chatGptBrowserMode": "EXTENSION_BRIDGE",
  "chatGptExtensionBridgeEnabled": false,
  "chatGptExtensionBridgeHost": "127.0.0.1",
  "chatGptExtensionBridgePort": 8765,
  "chatGptExtensionId": "",
  "chatGptExtensionPairingSecretFile": "state/chatgpt-bridge/pairing-secret.json",
  "browserChatGptLaunchEnabled": false,
  "manualChatGptLaunchEnabled": false,
  "chatGptBrowserTimeoutSeconds": 2700,
  "chatGptBrowserPollSeconds": 15
}
```

Valores de modo: EXTENSION_BRIDGE, ATTACH_CDP, DISABLED. A flag
`chatGptExtensionBridgeEnabled` é específica do modo novo; a flag
`browserChatGptLaunchEnabled` continua pertencendo ao CDP. DISABLED desliga a automação;
o launcher manual legado continua exigindo sua própria autorização explícita, fora do
modo EXTENSION_BRIDGE. Falha de preflight nunca troca executor.

Ativar somente `chatGptExtensionBridgeEnabled=true` permite bridge/preflight local.
Uma execução de dispatch exige também `writesEnabled=true`, autorização operacional e
um request/lease válido. Não altere essas flags para testar automaticamente esta entrega.
`config.example.json` mantém todas as flags desligadas.

O segredo deve ficar dentro de `<pasta do config>/state/`, já ignorado pelo Git.
Outro caminho é recusado. O ID deve ter 32 letras `a` a `p`; `localhost`, `0.0.0.0`, IPv6,
outros endereços e portas fora de 1–65535 são recusados. Não exponha a porta por proxy.

## Setup manual exato (não executado nesta entrega)

No PowerShell, dentro de FactoryDispatcher:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-extension.txt
```

1. Abra `chrome://extensions` no Chrome normal do operador.
2. Ative **Developer Mode / Modo do desenvolvedor**.
3. Clique em **Load unpacked / Carregar sem compactação**.
4. Escolha `C:\Users\natan\OneDrive\Desktop\projetos\FactoryDispatcher\browser_extension\project_factory_bridge`.
5. Copie o Extension ID para `chatGptExtensionId` no config local; mantenha o mesmo
   diretório da extensão. Não instale uma segunda cópia ou habilite acesso anônimo.
6. Gere o pairing uma única vez:

   ```powershell
   .\.venv\Scripts\python.exe -m factory_dispatcher.chatgpt_extension_pair --config .\config.json
   ```

   Abra o arquivo indicado em um editor local. Copie **somente `extensionSecret`** para
   o campo da página **Opções** da extensão. Informe a porta configurada e clique em
   **Salvar pairing e conectar**. Nunca copie `controlSecret`, o arquivo inteiro, nem
   segredos para chat, issue, Git, terminal de log ou captura de tela. Feche o editor.
   Pairing existente nunca é sobrescrito; não há rotação automática.
7. Abra uma aba `https://chatgpt.com/` dedicada à Factory e faça login. Outras abas do
   operador podem continuar abertas. Sem execução/reserva pendente, recarregue a aba
   manualmente se ela já existia ao carregar/atualizar a extensão, para instalar o
   content script novo. Deixe New Chat e composer visíveis. Nas Opções, clique em
   **Atualizar lista e status**, escolha explicitamente o Tab ID da aba dedicada e
   clique em **Registrar aba selecionada como PROJECT FACTORY TAB**. A lista mostra
   janela e posição visual para identificação, sem títulos nem URLs de conversas;
   posição não é identidade nem critério de seleção automática. Confira Factory tab:
   CONFIGURED, Tab ID correto e Origin valid: yes. Nunca converse manualmente nessa
   aba enquanto a Factory a estiver usando; use as demais abas.
8. Quando autorizado, habilite a flag do bridge e inicie-o em um terminal dedicado:

   ```powershell
   .\.venv\Scripts\python.exe -m factory_dispatcher.chatgpt_extension_bridge --config .\config.json
   ```

   Aguarde `CONNECTED` nas Opções da extensão. Em outro terminal, execute somente:

   ```powershell
   .\.venv\Scripts\python.exe -m factory_dispatcher.chatgpt_extension_client --config .\config.json
   ```

   Esse comando imprime PREFLIGHT_OK ou PREFLIGHT_BLOCKED; não lê Google, não cria
   tentativa, não altera Drive/QUEUE, não cria chat nem envia texto. Não é um smoke de
   dispatch. O bridge fica ativo até Ctrl+C; pará-lo não fecha Chrome ou a aba.

Nenhuma instalação de extensão é automatizada. Não é necessário Playwright nem CDP
para o runtime deste modo. Para testes JavaScript é necessário Node; a suíte usa Node
no PATH ou o Node já incluído no Playwright local, sem abrir seu navegador.

## Registro, Opções e migração para FACTORY_TAB

As Opções mostram Bridge: CONNECTED/DISCONNECTED, Factory tab: CONFIGURED/NOT CONFIGURED,
Tab ID, Origin valid: yes/no, reserva ativa e eventual código de invalidez. CONFIGURED
significa registro presente; aba fechada, sessão antiga ou origem inválida ainda bloqueiam
o preflight. O status do bridge vem do service worker, não da presença de uma credencial.

Uma lista vazia/sem seleção nunca registra nada automaticamente, nem quando só existe
uma aba elegível. A escolha é feita por Tab ID confirmado pelo operador. Não há seleção
por aba ativa, recência, primeira ocorrência ou URL da conversa. `tabs.query` é utilizado
somente para a lista das Opções; durante dispatch, usa-se `tabs.get(factoryTabId)`.

O único registro em `chrome.storage.local.factoryTab` contém:

```json
{"factoryTabId":123,"browserInstanceId":"uuid-da-sessao"}
```

Não há URL de conversa persistida. `factoryTabId` identifica a aba dentro da sessão;
browserInstanceId impede aceitar o reaproveitamento do número após reinício. A reserva
persiste o binding correspondente com `tabId` igual ao factoryTabId e browserInstanceId.

Registrar/substituir/limpar são mensagens locais `FACTORY_TAB_OPTIONS` aceitas somente
da URL exata das Opções da própria extensão. Não são novas ações do WebSocket. Compartilham
a mesma exclusão dos comandos do dispatcher. Qualquer activeReservation, inclusive
legada/incerta, bloqueia CLEAR e REGISTER com CHATGPT_TAB_BUSY. Limpar um registro ocioso
nunca remove a reserva do bridge nem os arquivos de tentativa.

Para migrar, primeiro reconcilie qualquer tentativa/reserva antiga. Esta entrega NÃO
limpa o estado de `D-SMOKE-CHATGPT-0001`, nem tenta novamente o seu envio. Em momento
seguro, reinicie manualmente o bridge e recarregue a extensão atualizada (manifest 0.2.0),
recarregue a aba dedicada e registre-a nas Opções. Preserve os segredos e config existentes:
o pairing v1 continua válido. Não existe migração automática de uma aba antiga para Factory Tab.

O AUTH da extensão agora exige a capability `FACTORY_TAB_V1`; versões antigas que ainda
selecionavam uma aba global são recusadas com EXTENSION_VERSION_MISMATCH. Bridge e
extensão devem ser atualizados juntos. Isso não exige gerar outro segredo e não habilita
configuração real automaticamente.
O content script também deve declarar essa capability no seu PRECHECK interno: uma
aba ainda executando o script antigo não passa no preflight após atualizar a extensão.
Recarregá-la manualmente, somente sem execução/reserva pendente, instala a versão nova.

## Pairing e protocolo v1

### Recuperação explícita de uma reserva PRE-SEND

Implementada em `chatgpt_extension_recover.py` (CLI) e
`chatgpt_extension_recovery.py` (validação no bridge). **Não executada no caso real.**
Não é um reset, não modifica o status da tentativa e não retoma o dispatch.

Comando exato, no PowerShell dentro de `FactoryDispatcher`, somente quando o
operador decidir executar a recuperação:

```powershell
.\.venv\Scripts\python.exe -m factory_dispatcher.chatgpt_extension_recover `
  --config .\config.json `
  --dispatch-id D-SMOKE-CHATGPT-0001 `
  --attempt-id D-SMOKE-CHATGPT-0001-A001
```

Pré-requisitos: bridge e extensão com este código carregado, conexão autenticada
existente e credenciais Google locais válidas. A CLI não inicia bridge, Chrome,
login, tick, pairing ou registro de aba. Atualizar o código carregado é uma ação
manual separada; reinício/reload não limpa reservas. Não gere outro pairing.
Uma extensão antiga recusa o controle novo sem liberar nada. A recuperação não
consulta nem recarrega o content script e não exige que a aba antiga ainda exista.

O bridge recebe apenas os dois IDs pelo canal privado com `controlSecret`. Não
aceita `--force`, reset-all, timeout de liberação ou afirmações do chamador como
`sendAttempted=false`. Sob os locks do dispatcher, da aba e dos workers, verifica:

1. IDs exatos no diretório, status e contexto do `launch.json` v3 EXTENSION_BRIDGE.
2. Status existente, `STOPPED`, `sendAttempted` estritamente `false`,
   `operationalSuccess` estritamente `false` e `needsReconciliation=true`.
3. Código PRE-SEND comprovado: nesta versão, **somente `CHATGPT_NEW_CHAT_FAILED`**.
   Log íntegro na sequência STARTING, PREFLIGHT, PREPARING_TAB, STOPPED; hash do
   bootstrap e binding do manifesto compatíveis. Evidência desconhecida é recusada.
4. Nenhum SEND_ATTEMPTED ou indício de envio/receipt nas evidências da tentativa e
   nos registros JSON/JSONL/log de state associados aos IDs, inclusive receipts com
   nomes alternativos. Nenhum arquivo com o nome canônico do receipt em state.
   Arquivos estruturados ilegíveis, malformados ou maiores que 2 MiB bloqueiam.
   O perfil nativo do browser não é inspecionado; bootstrap não é log de execução.
5. QUEUE completa (`QUEUE!A:AN`, não limitada a 1000 linhas), em leitura, com uma
   única linha correspondente à identidade/lease da tentativa, WAITING_HUMAN e
   sem receiptDriveId. Lease expirado não autoriza liberação nem retry.
6. Pasta canônica de receipts válida e consulta ao Drive sem receipt correspondente.
   Receipt inválido/ambíguo também bloqueia; erro de autenticação, permissão, leitura
   ou rede impede concluir ausência e, portanto, impede a recuperação.
7. Reserva persistida do bridge e reserva em memória iguais, com reservationId e
   binding exatos do manifesto e fase NEW_CHAT_ATTEMPTED.
8. Nenhuma outra tentativa CHATGPT ativa, tanto na QUEUE completa quanto nos
   diretórios/locks locais. Estados locais desconhecidos ou locks ocupados bloqueiam.
9. Revalidação local após as consultas de rede e auditoria durável antes da liberação.
10. Na extensão, comparação estrita da activeReservation com o mesmo reservationId,
    binding antigo e fase NEW_CHAT_ATTEMPTED, sem outra operação concorrente.

Só então o controle interno `PRE_SEND_RECOVERY_RELEASE` remove **exclusivamente**
`activeReservation`. Esse controle não é uma ação de UI e o bridge recusa recebê-lo
diretamente de um worker. Após ACK válido, grava `{}` em reservation.json.
Pairing, extensionSecret/controlSecret, Factory Tab, arquivos da tentativa e logs
permanecem intactos. Nenhum método de escrita no Drive/QUEUE é chamado.

Auditoria privada: `state/chatgpt-bridge/recovery-audit/<uuid>.json`, com **somente**
dispatchId, attemptId, previousReservationPresent, evidenceSendAttemptedFalse e
outcome. Sem timestamps, tokens, detalhes de exceções, prompts ou segredos. O registro
da CLI distingue pedido/ACK; `previousReservationPresent=null` nele significa ainda
não verificado. O registro do bridge contém a comprovação e o resultado efetivo.
VALIDATED_RELEASE_PENDING é persistido antes do controle e substituído por
RELEASED_PRE_SEND ou uma recusa. Se a auditoria prévia não puder ser persistida,
nenhuma liberação é enviada.

Saída 0: RELEASED_PRE_SEND. Saída 2: RECOVERY_REFUSED ou RECOVERY_UNCERTAIN. Se houver
perda do ACK/desconexão após emitir o controle, a extensão pode já ter removido sua
reserva: **não se afirma atomicidade entre Chrome e disco**. O bridge mantém sua
barreira persistente, registra incerteza e não repete o controle. Falha ao gravar a
conclusão mantém a evidência prévia; não interprete PENDING como sucesso.

Mesmo após sucesso, a QUEUE continua WAITING_HUMAN e status.json continua STOPPED
com needsReconciliation=true. Esta CLI não reconcilia a QUEUE, não autoriza novo
envio e não faz retry automático. Reconciliação posterior exige decisão separada.
Os locks protegem os produtores deste workspace; não coordenam outra máquina ou
edições externas concorrentes no Google. Mantenha a operação suspensa durante a
recuperação e não use outro modo/instância para contornar uma reserva.

Validação desta implementação: 549 testes Python aprovados (83 novos casos de
recuperação), 127 testes JavaScript aprovados, compileall e Ruff check aprovados;
Ruff format --check: 54 arquivos formatados. O transporte de recuperação foi
testado em loopback com extensão e Google simulados, sem Chrome real. Sem novas
dependências, recuperação real, tick, escritas no Drive/QUEUE, commit ou push.
Hashes do config real e dos executores CODEX/ATTACH_CDP preservados.

### Transporte autenticado

Dois canais têm papéis separados:

| Canal | Origin exigido | Credencial |
| --- | --- | --- |
| `/extension` | exatamente `chrome-extension://<ID configurado>` | extensionSecret |
| `/worker` | ausente; clientes web são recusados | controlSecret, somente local |

Ambos exigem conexão de 127.0.0.1. Origin não é tratado como autenticação suficiente:
clientes locais podem forjá-lo, por isso HMAC é obrigatório. Cada conexão recebe um nonce
aleatório de 32 bytes, descartado após autenticação ou timeout de 10 segundos.

`CHALLENGE → AUTH → READY` usa HMAC-SHA256 sobre
`PROJECT_FACTORY_BRIDGE_V1\n<role>\n<nonce>\n<extensionId>`. A extensão usa role `extension`,
o worker usa `worker`; READY contém prova inversa `server-extension` ou `server-worker`.
Isso também impede que um servidor falso na porta envie comandos sem conhecer o segredo.
Cada segredo tem 32 bytes criptograficamente aleatórios. Frames incompatíveis ou sem
autenticação são recusados. O worker desliga proxy explícito/automático no cliente local.
No canal `/extension`, AUTH também deve declarar `capabilities: ["FACTORY_TAB_V1"]`;
o schema de autenticação do worker permanece inalterado.

As únicas operações de UI são PRECHECK, NEW_CHAT, INSERT_BOOTSTRAP e SEND. Exemplos:

```json
{"protocolVersion":1,"type":"COMMAND","requestId":"uuid","action":"PRECHECK"}
```

```json
{"protocolVersion":1,"type":"RESULT","requestId":"uuid","status":"ERROR","errorCode":"CHATGPT_TAB_BUSY"}
```

PRECHECK retorna apenas binding: extensionId, browserInstanceId, tabId e documentId.
O campo tabId é exatamente o factoryTabId registrado, não uma aba descoberta no perfil.
As operações seguintes exigem o mesmo binding e reservationId opaco da tentativa.
INSERT_BOOTSTRAP aceita somente texto UTF-8 até 64 KiB; SEND recebe também `expiresAt`
em milissegundos UTC e recusa clique atrasado. Frames são limitados a 512 KiB.
Schemas recusam campos extras e códigos de erro arbitrários. Não existe campo de
resposta do modelo nem ação GET_TEXT, GET_HTML, CLICK_SELECTOR ou execução de JavaScript.

PING/PONG são apenas heartbeat. `RECEIPT_CONFIRMED` é mensagem de controle autenticada
do worker, emitida somente após validação do receipt no Drive; libera as reservas sem
acessar a aba. Não é uma quinta operação de UI, não transporta conteúdo do receipt e
não permite à extensão decidir completion. O segredo da extensão não autentica esse
canal privado. Um processo com acesso a controlSecret pertence à mesma fronteira de
confiança do worker; este MVP não isola processos maliciosos do mesmo usuário do SO.

O envelope dessa liberação usa `type: "CONTROL"` e `control: "RECEIPT_CONFIRMED"`, não
`type: "COMMAND"`/`action`. O schema de COMMAND aceita estritamente as quatro ações de UI.

## Preflight, DOM e concorrência

Antes do claim: verificar bridge, pairing, versão/capability, lock e ausência de reserva;
registro da Factory Tab na sessão atual; existência e origem da aba; content script
responsivo; autenticação; New Chat e composer utilizáveis. Outras abas são ignoradas.
Erros distinguem BRIDGE_UNAVAILABLE, EXTENSION_NOT_PAIRED, EXTENSION_VERSION_MISMATCH,
FACTORY_TAB_NOT_CONFIGURED, FACTORY_TAB_NOT_FOUND, FACTORY_TAB_INVALID, AUTH_REQUIRED,
SELECTOR_UNAVAILABLE e TAB_BUSY (prefixo `CHATGPT_`). Aba descartada, congelada, anônima,
em carregamento/navegação pendente ou sem content script pronto é recusada.
Falhas resultam em PREFLIGHT_BLOCKED sem attempt++, lease, staging ou mutação da candidate.

O worker refaz PRECHECK contra o binding fixado antes do claim. Substituição da Factory Tab,
reload ou mudança do documento/rota invalida o binding antigo; não se escolhe automaticamente
uma aba diferente. Trocar a aba ativa do operador não altera o destino dos comandos.
NEW_CHAT clica o controle existente e confirma pathname `/` e composer vazio. Não cria
aba, não altera URL programaticamente e não usa endpoints internos do ChatGPT.

No content script, documentId é um identificador de documento/geração de navegação.
O listener nativo de `navigation.navigate` observa somente metadados e invalida esse
identificador em navegações, inclusive SPA, back/forward e reload; `pagehide` também
invalida. Não intercepta/cancela navegação nem lê mensagens. A única exceção é uma
transição same-document para `/`, sem query/hash, push/replace, durante o NEW_CHAT
autorizado. Outra navegação durante essa espera, inclusive voltar ao chat anterior,
retorna BINDING_CHANGED. INSERT_BOOTSTRAP revalida após seu await e SEND revalida antes
do clique. Navegar para fora e voltar não restaura um binding antigo.

A Navigation API centraliza eventos de navegação programática e iniciada pelo usuário,
conforme a [documentação oficial do Chrome](https://developer.chrome.com/docs/web-platform/navigation-api).
O preflight falha se essa proteção não estiver disponível. Depois de SEND, nenhuma
navegação autoriza outro clique ou troca de aba; o worker continua aguardando apenas o
receipt, preservando a reconciliação de efeitos já tentados. Não se acompanha resposta.

Seletores ficam em `selectors.js`, priorizando data-testid e ARIA. Não há coordenadas,
OCR, seletores de resposta, observador de mensagens nem leitura de containers da conversa.
A identidade autenticada exige a origem exata `https://chatgpt.com`, nenhum controle de
login utilizável e pelo menos um indicador de conta utilizável. Conta é evidência, não
alvo de ação: dois indicadores legítimos não bloqueiam autenticação. New Chat, composer
e SEND exigem exatamente um controle utilizável cada, na etapa em que são usados;
zero ou múltiplos controles retornam `CHATGPT_SELECTOR_UNAVAILABLE`, sem escolher o
primeiro match arbitrariamente.

A seleção isolada em `selectors.js` distingue `rawMatches` (elementos únicos encontrados
pelos seletores) de `candidates` (somente os utilizáveis). O helper `isUsable` rejeita
elementos nulos, desconectados, hidden, disabled ou aria-disabled; elementos sob
`[hidden]`, `[aria-hidden="true"]` ou `[inert]`; estilos display:none,
visibility:hidden/collapse ou pointer-events:none; e dimensões menores ou iguais a zero.
`closest` inclui o próprio elemento. Não exige offsetParent: elementos fixed continuam
elegíveis. O mesmo elemento encontrado por múltiplos seletores é deduplicado antes da
checagem de unicidade. Nenhum seletor depende do nome da conta, plano ou texto pessoal;
os data-testid existentes continuam atendendo à UI localizada em português.

Textarea usa o setter nativo de value. Contenteditable usa seleção limitada ao composer
e a operação fixa nativa insertText; ambos disparam input/change para o editor reconhecer
o texto. Só value/innerText/textContent do próprio composer é lido. A comparação exige igualdade
exata após a renderização e novamente antes do clique; não normaliza ou interpreta texto.
Depois de SEND nenhum código acompanha a resposta.

Um BR de placeholder cujo innerText é apenas uma quebra e cujo textContent é vazio é
reconhecido como composer vazio. Essa inspeção também é limitada ao próprio composer;
nenhum texto efetivamente inserido é normalizado para fazê-lo coincidir com o bootstrap.

A capacidade efetiva de CHATGPT permanece limitada a 1 pelo dispatcher. Um lock fixo
`state/chatgpt-bridge/tab.lock` cobre o worker inteiro e não é evitado mudando porta ou
stateDirectory. O bridge também serializa RPCs e persiste reservation.json. A extensão
mantém activeReservation em storage.local antes de cada operação com efeito, rejeitando
outra tentativa mesmo após suspensão/reinício do service worker.

## Incerteza, receipt e recuperação

O worker grava status SEND_ATTEMPTED e faz flush antes de solicitar SEND. Bridge e
extensão persistem a mesma intenção antes de repassar o comando/clicar. Se qualquer ACK
se perder, NÃO há reenvio: o worker continua polling de receipt até lease/deadline.
A extensão não recebe leaseToken separadamente nem decide autoridade; o bootstrap
autorizado continua sendo entregue integralmente, conforme o contrato existente.

O worker reutiliza os validadores existentes de request e receipt: dispatchId, attemptId,
leaseToken, agentId, requestDriveId, receiptFolderId, requestHash, bootstrap exato e lease
corrente. Revalida a fila após leituras de rede, inclusive em RESULT_STAGED com o mesmo
receiptDriveId. Não grava no Drive ou QUEUE. Falha, stale lease ou timeout deixa STOPPED
e needsReconciliation=true; o tick existente encaminha a tentativa para WAITING_HUMAN
e não faz retry automático. A tentativa nunca pode ser retomada para enviar novamente.

Os arquivos de evidência continuam em `state/chatgpt-runs/<dispatchId>/<attemptId>/`:
launch.json v3, bootstrap.txt, status.json e browser.log. Logs contêm somente estados e
códigos fixos, nunca segredos, tokens, bootstrap ou resposta. Logs internos de frames
WebSocket estão desabilitados.

Receipt válido encerra o worker, liberando as reservas por controle autenticado. Se a
liberação falhar, o receipt continua registrado e o worker encerra com
`reservationReleasePending=true`; a reserva remanescente bloqueia novos dispatches.
Falhas antes de SEND também podem deixar a reserva conservadoramente bloqueada.

Não há botão/CLI de reset ou liberação forçada de reserva nesta entrega. O botão
**Limpar Factory Tab** remove apenas um registro ocioso e recusa qualquer reserva ativa.
Em reserva pendente:
pare novos dispatches; preserve evidências; reconcilie externamente a tentativa e o
receipt correto; solicite uma recuperação explícita. Não apague state/storage, não
recarregue a extensão para limpar a reserva e não mude para CDP para contorná-la.
Essa escolha deixa recuperação pós-crash deliberadamente manual, sem risco de retry cego.

## Validação e riscos residuais

```powershell
.\.venv\Scripts\python.exe -m compileall .\factory_dispatcher
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Pytest também verifica o manifest, sintaxe de todos os scripts e a suíte Node com Chrome
e DOM simulados. O teste de integração WebSocket usa somente porta efêmera 127.0.0.1,
pairing temporário e extensão falsa, sem Chrome real nem Google. As regressões CDP/CODEX
permanecem na suíte completa.

Sem smoke real, não se afirma compatibilidade validada com a UI atual. Localização,
A/B tests, sidebar recolhida, editor, políticas corporativas de extensão/rede local,
permissões do perfil e suspensão do Chrome podem bloquear preflight ou envio. O adapter
contenteditable usa execCommand insertText, uma API legada que precisa de verificação
manual futura. Não existe exactly-once distribuído com o site: a garantia local é no
máximo uma tentativa de clique e reconciliação obrigatória quando incerta.

Segredos ficam em arquivo local e storage.local, não em cofre criptografado; chmod não
equivale a ACL privada no Windows. Proteja a conta/perfil/pasta e revise sincronização
OneDrive (o projeto está dentro dela). Usuário local comprometido pode ler credenciais
ou alterar state. Mantenha um único dispatcher, um perfil de Chrome e este workspace;
lock de arquivo não coordena outras máquinas/cópias do projeto nem outros modos.
Troca de modo com tentativa ativa exige reconciliação humana primeiro.

## Registro desta entrega — 2026-09-06

- compileall: aprovado.
- pytest: **458 testes aprovados** (399 existentes e 59 novos testes/casos Python).
- Suíte JavaScript: **31 testes aprovados**, além de `node --check` em todos os scripts;
  a execução dessa suíte também faz parte de pytest.
- Ruff check: aprovado; Ruff format --check: 51 arquivos já formatados.
- CLIs bridge, pairing e preflight: `--help` validado, sem ativá-los no config real.
- Instalado apenas websockets 16.1.1 na `.venv` para este executor; utilizado Node já
  presente no Playwright para validação, sem instalação/execução de browser.
- Hash SHA-256 do config real preservado:
  `FB7707F26ED61E3AFA8F0E3CE7A08829EE2D157B03A25B5E1AF53C6BD69C2937`.
- Hashes de CODEX (launcher/worker/prompt/result), chatgpt_browser.py, engine.py e
  google_auth.py conferidos sem alteração.
- Sem Chrome real, smoke, pairing real, Drive/QUEUE, tick real, commit ou push.

Alterações de integração ficaram em config.py, cli.py, config.example.json,
pyproject.toml, requirements-dev.txt, README e documentação; os novos arquivos estão
listados na seção de arquitetura. O config real não foi editado nem habilitado.

## Correção posterior — preflight com clones responsivos

Com base no diagnóstico manual fornecido pelo operador, a seleção passou a ignorar
clones ocultos/não interativos mesmo quando possuem dimensões. A evidência de conta
aceita múltiplos indicadores utilizáveis; controles de ação mantêm unicidade obrigatória.
Os seletores data-testid e as quatro ações de UI permanecem os mesmos.

Arquivos alterados nesta correção: `selectors.js`, `content.js`,
`tests/extension_bridge.test.mjs` e este guia. Não houve alteração de Python, manifest,
permissões, protocolo, pairing, CODEX, ATTACH_CDP ou config real.

Validação da correção: 64 testes JavaScript aprovados (33 novos), 458 testes Python
aprovados, compileall aprovado, Ruff check aprovado e 51 arquivos já formatados.
A suíte Python também validou a sintaxe da extensão e a ausência de leitura de respostas
ou controle genérico. Foram conferidos os hashes dos arquivos protegidos; o hash do
config real no início e no fim desta correção foi
`5B05CECB2FD39A599381B5D587142A559F471C445BB12DAFBE95D81C5B111F31`.

Não foi executado Chrome real, tick, alteração no Drive/QUEUE, commit ou push.
Os testes simulam as propriedades DOM diagnosticadas; não constituem validação do
browser em execução. `isUsable` é um filtro de atributos, estilos e dimensões, não um
teste completo de oclusão por overlays. Mudanças futuras de seletores/estilos ainda podem
exigir revisão. A atualização da extensão carregada e a verificação manual ficam fora
desta execução e devem respeitar a ausência de dispatch/reserva pendente.

## Entrega posterior — registro explícito PROJECT FACTORY TAB

O comportamento de exigir uma única aba ChatGPT no perfil foi removido do
EXTENSION_BRIDGE. Agora apenas o registro explícito nas Opções autoriza a aba destino;
o dispatcher não altera registro, não escolhe aba ativa e não tenta substituir uma aba
fechada. Os fluxos de registro, preflight, binding e recuperação estão descritos acima.

Arquivos desta entrega:

- Novo: `browser_extension/project_factory_bridge/factory_tab.js`.
- Extensão alterada: `controller.js`, `content.js`, `service_worker.js`, `options.html`,
  `options.js`, `protocol.js` e `manifest.json` (versão 0.2.0, mesmas permissões).
- Python alterado: `factory_dispatcher/chatgpt_extension_protocol.py` e
  `factory_dispatcher/chatgpt_extension_bridge.py` para os erros de Factory Tab e
  exigência da capability que bloqueia extensões antigas.
- Testes alterados: `tests/extension_bridge.test.mjs` e `tests/test_chatgpt_extension.py`.
- Documentação alterada: README e este guia.

Validação: **114 testes JavaScript** e **466 testes Python** aprovados, incluindo
regressões CODEX/ATTACH_CDP; compileall, sintaxe da extensão e Ruff check aprovados;
Ruff format --check com 51 arquivos já formatados. O config real manteve o hash
`5B05CECB2FD39A599381B5D587142A559F471C445BB12DAFBE95D81C5B111F31`.
Hashes de launcher/worker CODEX e browser/launcher/worker/selectors ATTACH_CDP também
foram conferidos sem alteração. Não houve novas dependências.

Não foram executados Chrome, tick, mudanças no Drive/QUEUE, commit ou push.
Nenhuma tentativa, reserva, pairing ou registro real foi criado, migrado ou removido.
O estado do smoke relatado foi preservado; recuperação explícita continua necessária
antes de mudar registro caso ainda haja reserva pendente.

Riscos residuais: falta de validação no Chrome real, mudanças na UI/roteamento e escolha
incorreta do Tab ID pelo operador. Uma navegação legítima com mais de uma transição
durante NEW_CHAT pode exigir ajuste futuro; por segurança o comportamento atual é
bloquear. Reinício/reload da extensão que altere browserInstanceId exige novo registro
explícito, mas nunca remove reservas. Atualizar a extensão sem recarregar o content
script da aba em momento seguro é detectado e bloqueado por capability incompatível.
