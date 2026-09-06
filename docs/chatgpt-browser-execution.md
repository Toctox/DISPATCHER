# Executor CHATGPT — ATTACH_CDP

ATTACH_CDP permanece disponível. Para novas instalações, o caminho recomendado agora
é [EXTENSION_BRIDGE](chatgpt-extension-bridge.md), com extensão Manifest V3 e pairing local.
Não troque de modo enquanto houver tentativa ou reserva pendente de reconciliação.

Esta versão substitui o persistent-context automático. “Chat descartável” é uma
**nova conversa na mesma aba**, não um novo browser, perfil ou tab por dispatch.

O worker se conecta a uma instância dedicada, já aberta e autenticada pelo humano.
Nenhum componente CODEX foi alterado nesta revisão. O config real, Drive, QUEUE e
repositórios externos não foram modificados. Não houve browser, login, smoke ou tick real.

## Configuração exata

Adicionar/ajustar estas propriedades no config local, preservando as demais:

~~~json
{
  "browserChatGptLaunchEnabled": false,
  "manualChatGptLaunchEnabled": false,
  "chatGptBrowserMode": "ATTACH_CDP",
  "chatGptCdpEndpoint": "http://127.0.0.1:9222",
  "chatGptBrowserExecutable": "",
  "chatGptBrowserProfileDirectory": "state/factory-browser",
  "chatGptBrowserTimeoutSeconds": 2700,
  "chatGptBrowserPollSeconds": 15
}
~~~

O exemplo deixa execução desligada. Depois de preparar a instância e autorizar uso
real, o operador pode habilitar explicitamente `browserChatGptLaunchEnabled`; o
dispatcher também continua exigindo `writesEnabled=true`. Essas flags NÃO foram
habilitadas nesta tarefa. O modo ausente é `DISABLED`; endpoint ausente é recusado.
Não existe fallback de ATTACH_CDP para persistent-context ou launcher manual quando
a conexão falha. O launcher manual legado só opera por sua própria flag explícita,
com browser automático desligado; não é o caminho recomendado.

O endpoint aceito inicialmente é HTTP com porta explícita 1–65535, host
`127.0.0.1` ou `localhost`. O último é normalizado para `127.0.0.1` sem DNS.
IPv6, rede externa, host parecido, credenciais, query, fragmento, HTTPS, WS configurado
diretamente e caminhos extras são recusados. O endpoint recomendado é
`http://127.0.0.1:9222`.

O adapter consulta apenas `/json/version` no servidor CDP local, sem proxy e sem
redirecionamento HTTP. Valida que o WebSocket pertence ao mesmo host/porta e tem
identidade de browser. Esses são endpoints DevTools do navegador, NÃO endpoints
internos do ChatGPT.

## CLI para iniciar a instância Factory

Com o config acima, executar deliberadamente no diretório do projeto:

~~~powershell
.\.venv\Scripts\python.exe -m factory_dispatcher.chatgpt_browser_start --config .\config.json
~~~

O helper funciona com as flags de execução desligadas. Usa Chrome/Edge já instalado.
`chatGptBrowserExecutable` pode conter caminho absoluto, relativo ao config ou nome
resolvido no PATH. Vazio procura Edge/Chrome em ProgramFiles, ProgramFiles(x86) e
LOCALAPPDATA, sem nome de usuário hardcoded.

Se não houver servidor na porta, inicia uma única vez, conceitualmente:

~~~text
<chrome.exe ou msedge.exe>
  --remote-debugging-address=127.0.0.1
  --remote-debugging-port=9222
  --user-data-dir=<state/factory-browser>
  --no-first-run
  --no-default-browser-check
  https://chatgpt.com/
~~~

O humano faz login e deixa exatamente UMA aba ChatGPT funcional aberta. O helper
retorna, mas o browser permanece aberto. Não automatiza login, senha, seleção de
workspace, conector, modo ou aprovação. Não adiciona `--remote-allow-origins=*`,
não desabilita sandbox e não abre firewall.

Se CDP válido já estiver disponível, retorna `BROWSER_ALREADY_RUNNING`, sem processo,
perfil ou aba adicional. Se a porta estiver ocupada por serviço não verificável,
retorna `CHATGPT_CDP_PORT_OCCUPIED`, sem tentar outra porta ou outro browser.
Inicialização incerta não mata nem relança processos. O marcador de startup/PID no
perfil impede repetir uma inicialização quando o processo anterior ainda pode estar
ativo. Após saída normal, o helper pode reiniciar o browser; PID desconhecido ou
reutilizado por outro processo exige revisão, não novo lançamento por inferência.

Somente o helper cria/usa o perfil dedicado, obrigatoriamente abaixo de stateDirectory.
Ele recusa raízes amplas, perfil pessoal Chrome/Edge e diretório preexistente sem
marcador Factory. Dispatch/preflight/worker não criam perfil nem dependem do caminho
do executável: usam a conexão existente.

O CLI antigo `chatgpt_profile_setup` agora retorna
`CHATGPT_LEGACY_PROFILE_DISABLED` e orienta usar o novo helper. O código que lançava
persistent-context foi removido; os dados de perfis anteriores não foram apagados.

Chrome exige perfil não padrão para remote debugging nas versões recentes.
[Chrome — remote debugging switches](https://developer.chrome.com/blog/remote-debugging-port).

## Arquitetura e arquivos alterados

~~~text
preflight: lock → CDP existente → única aba → auth/controles → fixar identidades → detach
dispatcher: claim/lease → staging/bootstrap → proteção de retry → spawn do worker
worker: lock → reconectar → conferir browserId/targetId → validar lease/request
        → clicar New Chat → composer vazio → bootstrap exato → Send uma vez
        → polling read-only do receipt → confirmar → detach + liberar lock
browser e aba: permanecem abertos
~~~

| Arquivo | Mudança nesta revisão |
| --- | --- |
| `factory_dispatcher/chatgpt_browser.py` | Adapter CDP, endpoint local, descoberta exata de aba, binding, lock e reserva |
| `factory_dispatcher/launchers/chatgpt_browser.py` | Preflight completo e manifesto v2 com identidade browser/aba |
| `factory_dispatcher/chatgpt_worker.py` | Reconexão e verificação de identidade; detach sem fechar browser/tab |
| `factory_dispatcher/chatgpt_selectors.py` | Preflight dos controles sem criar conversa; New Chat somente na execução |
| `factory_dispatcher/chatgpt_browser_start.py` | Novo helper explícito para browser duradouro |
| `factory_dispatcher/chatgpt_profile_setup.py` | Stub que desabilita o setup persistent-context antigo |
| `factory_dispatcher/config.py`, `config.example.json` | Modo e endpoint explícitos, novo default de perfil |
| `pyproject.toml`, `requirements-browser.txt` | Extra opcional Playwright >=1.60 para no_defaults |
| `tests/test_chatgpt_browser.py` | Atualização dos testes anteriores para attach/detach |
| `tests/test_chatgpt_cdp.py` | Novos testes CDP, helper, identidade, concorrência e segurança |
| `README.md`, este documento | Instruções operacionais atualizadas |

Engine, arquivos específicos de CODEX e cliente OAuth Google não foram modificados
nesta revisão. A política durável de reconciliação implementada anteriormente permanece.

## Descoberta da aba e identidade

São examinados todos os contextos/páginas existentes. Somente páginas não fechadas
com origem HTTPS exata `chatgpt.com`, sem credenciais na URL e porta padrão 443 são
elegíveis. Subdomínios e hosts que apenas contêm esse texto não são aceitos.

- Zero: `CHATGPT_TAB_NOT_FOUND`.
- Uma: usar aquela página existente, inclusive se estiver em uma conversa antiga.
- Mais de uma: `CHATGPT_TAB_AMBIGUOUS`. Nenhuma escolha arbitrária ou nova aba.

O preflight fixa a identidade do browser do WebSocket DevTools e o `targetId` obtido
com `Target.getTargetInfo`. Não usa título nem texto da conversa. O manifesto v2
contém `browserMode=ATTACH_CDP` e `browserBinding` com endpoint/browser_id/target_id.

O worker compara essas mesmas identidades ao reconectar e novamente antes de enviar.
Browser reiniciado, aba substituída ou nova ambiguidade falham fechados, mesmo quando
a nova aba tem a mesma URL. O manifesto antigo v1 não pode executar o novo worker.

O adapter usa `connect_over_cdp(..., no_defaults=True)` e, ao sair, encerra somente a
conexão Playwright. Não chama `browser.close`, `context.close`, `page.close`, `goto`,
`new_page`, `new_context`, `reload` ou `launch_persistent_context`. A sessão CDP auxiliar
de metadados recebe apenas `detach`. A documentação descreve o uso de contexto existente
e a opção que evita overrides no contexto.
[Playwright — connect_over_cdp](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp).

## Preflight, UI e New Chat

Antes do claim:

1. verifica flag, ATTACH_CDP, endpoint local e dependência;
2. adquire lock exclusivo de uso da instância/aba;
3. verifica que não há envio anterior pendente de reconciliação;
4. confirma CDP alcançável e conectável;
5. exige exatamente uma aba elegível;
6. exige indicadores de autenticação;
7. detecta New Chat e composer editável;
8. testa actionability de New Chat com `trial=True`, sem clicar de fato;
9. fixa identidades e desconecta.

Falha produz `PREFLIGHT_BLOCKED`, audit local e nenhum claim, incremento de attempt,
lease, staging ou mutação da linha candidata. As reconciliações já existentes de
outros jobs ainda podem ocorrer antes da seleção normal do tick. Dry-run/writes
desligados não conectam ao browser.

Erros incluem `CHATGPT_CDP_UNAVAILABLE`, `CHATGPT_TAB_NOT_FOUND`,
`CHATGPT_TAB_AMBIGUOUS`, `CHATGPT_TAB_BUSY`, `CHATGPT_AUTH_REQUIRED`,
`CHATGPT_AUTH_UNVERIFIED`, `CHATGPT_SELECTOR_UNAVAILABLE`,
`CHATGPT_SELECTOR_AMBIGUOUS`, `CHATGPT_BROWSER_CHANGED` e `CHATGPT_TAB_CHANGED`.

Os seletores ficam isolados em `chatgpt_selectors.py`. New Chat usa, em ordem:
`data-testid=create-new-chat-button`, link ARIA com nome exato `New chat`/`Novo chat`,
ou botão ARIA com esses nomes. A camada não usa coordenadas, OCR nem endpoints
internos. Não clica Retry, não faz refresh e não tenta contornar challenge/login.

Na execução, New Chat é clicado explicitamente na mesma aba. A rota raiz e o composer
vazio são confirmados depois desse clique, sem navegação programática. O bootstrap
permanece UTF-8 exato, validado por hash e comparação do próprio campo de entrada.
Essa é a única leitura textual da UI; não há leitura, cópia ou interpretação de resposta.

## Lock, receipt e incerteza

MAX_CONCURRENT_CHATGPT_EXECUTIONS continua limitado a 1. O lock é nomeado pelo hash do
endpoint canônico em `<project_root>/state/chatgpt-cdp-locks/<hash>.lock`.
`localhost` e `127.0.0.1` compartilham o mesmo lock, inclusive se configs do mesmo
projeto usarem stateDirectory/profile diferentes. O lock cobre preflight, worker
completo e startup. O `worker.lock` por tentativa continua impedindo replay simultâneo.
A proteção é local a esta instalação, não distribuída entre cópias/máquinas.

Antes de Send, uma reserva local `<hash>.json` registra que o envio pode produzir
efeitos. Um único clique é tentado. Exceção de clique mantém polling, sem reenviar.
O worker consulta mecanicamente o nome exato do receipt no Drive e valida contrato,
dispatchId, attemptId, leaseToken, agentId, requestDriveId, pasta e lease corrente.

Receipt válido registra receiptDriveId. Depois do detach, marca a aba disponível,
registra FINISHED/disconnectedAt e libera o lock. A conversa e a aba ficam abertas.
O próximo dispatch fará outro New Chat. FINISHED é sucesso operacional; um receipt
BLOCKED também termina o polling, sem inferir sucesso da tarefa. O worker não altera
Drive/QUEUE, promove estados ou escolhe próximo agente.

Em timeout/stale/falha, browser e aba também permanecem abertos. A política
RECONCILE_BEFORE_RETRY, o bloqueio de replay e a evidência local continuam preservados.
Após envio incerto, a reserva NÃO é liberada como se o trabalho tivesse terminado:
outro dispatch recebe `CHATGPT_TAB_RECONCILIATION_REQUIRED` antes do claim. Isso evita
sobrescrever uma execução remota que ainda possa estar ativa. Receipts posteriores ao
lease não recuperam autoridade; o worker não renova lease.

Após falha/crash, um humano deve reconciliar os efeitos e confirmar que a aba está
ociosa. Não há desbloqueio automático por tempo nem comando que resete essa reserva
cegamente. Se for necessário liberar a instalação após a reconciliação, encerrar os
workers, guardar a evidência de tentativa e arquivar o arquivo de reserva `<hash>.json`
com o histórico dessa decisão. Não apagar/forçar o arquivo `.lock` enquanto houver
processos usando a aba. O helper de startup não apaga pendências.

Os arquivos da tentativa continuam em `state/chatgpt-runs/<dispatchId>/<attemptId>/`:
launch.json, bootstrap.txt, status.json, browser.log e worker.lock. Manifesto/bootstrap
contêm contexto privado; logs não incluem lease, cookies, senhas, OAuth ou resposta.
Reservas não contêm lease. O perfil continua guardando a sessão normal do browser.

## Dependência, validação e riscos

Foi encontrado Playwright **1.62.0** instalado. O requisito opcional agora é >=1.60
para suportar no_defaults. Nada foi instalado e nenhum navegador foi iniciado nesta
tarefa. A execução real do setup anterior e o erro 400 foram informados pelo humano;
esta revisão não afirma ter diagnosticado a causa desse erro.

Validação local:

~~~powershell
.\.venv\Scripts\python.exe -m compileall .\factory_dispatcher
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
~~~

Os testes usam processos, transporte CDP, DOM/controles, relógio e Google simulados.
Cobrem conexão existente, ausência/ambiguidade de aba, preflight, identidade fixada,
New Chat, texto exato, ausência de leitura de respostas, polling/receipt, permanência
de browser/tab, startup idempotente, lock e lease stale. Não são smoke de integração.

Riscos residuais: CDP tem menor fidelidade que o protocolo nativo Playwright; seletores,
idioma, overlays, conta/modo/workspace, CAPTCHA, acesso ao Drive e permissões podem
bloquear o fluxo. Mudanças entre preflight e claim ainda podem consumir uma tentativa,
mas o worker revalida antes de enviar. Não há proteção contra interação simultânea
do humano na aba; deixar essa instância exclusivamente para a Factory.

CDP fornece controle privilegiado sem autenticação própria. Nunca expor a porta à
rede, usar túnel público ou apontar para o browser pessoal. O helper verifica o
serviço existente, mas não prova a origem do perfil de uma instância iniciada fora
dele; cabe ao operador selecionar a instância dedicada correta.

Perfil/state ficam sob OneDrive no default desta máquina: revisar sincronização,
backup e ACLs, pois `.gitignore` não protege cookies contra sincronização ou outros
processos locais. Não salvar senhas no perfil. Chamadas de rede ou detach em andamento
podem atrasar a saída efetiva, sem autorizar receipt tardio ou retry automático.
