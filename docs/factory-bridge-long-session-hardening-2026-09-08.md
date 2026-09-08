# FactoryBridge — Long-session hardening — 2026-09-08

## Objetivo

Reduzir os gargalos observados no FactoryBridge v0.13 para sessões longas de ChatGPT ↔ notebook:

1. crescimento desnecessário do contexto por `projecthub.snapshot`;
2. falso positivo de executor travado durante ações locais;
3. ordenação de fila dependente do nome do arquivo;
4. fragilidade do self-update quando Google Drive ou Task Scheduler interferem no ciclo local.

## Benchmark de referência

No benchmark anterior ao hardening:

- `projecthub.snapshot`: 445 / 341 / 329 ms, mas ~72.6 KiB por resultado;
- `projecthub.verify`: 23.136 / 22.410 / 21.791 s;
- `projecthub.refresh_validate`: 28.460 / 28.627 / 29.301 s;
- todas as validações do ProjectHub passaram, com 99/99 testes.

O gargalo principal não era CPU; era eficiência de contexto e liveness.

## Hardening aplicado

### Snapshot compacto

`projecthub.snapshot` agora agrega somente:

- `bridge.doctor`;
- `projecthub.status`.

Logs detalhados deixaram de ser copiados para o snapshot saudável. Quando necessários, permanecem disponíveis explicitamente via `projecthub.logs`.

Prova pós-deploy: o resultado caiu de ~72.6 KiB para 2,015 bytes, redução de aproximadamente 97.2%, mantendo branch, HEAD/origin, dirty/ahead/behind, estado da aplicação e saúde do runtime.

### Heartbeat durante ações

O executor mantém `executor.json` atualizado durante a execução de qualquer ação allowlisted, com intervalo de 10 s. A correção preserva o `StartedAt` do processo e não aumenta artificialmente o timeout nem a janela de 90 s do supervisor.

Foi adicionado teste automatizado que confirma avanço do heartbeat durante trabalho em andamento, preservando PID e `StartedAt`.

### Fila FIFO-like

A fila deixou de ordenar `01_COMMANDS` lexicograficamente pelo nome do arquivo. O executor agora ordena por `ModTime` mais antigo primeiro e usa o nome somente como desempate determinístico.

Foi adicionado `TestOrderCommandEntriesUsesOldestTimestampBeforeFilename`, que prova que `Z-older.json` executa antes de `A-newer.json` quando seu timestamp é anterior.

### Singleton de supervisor no self-update

A auditoria encontrou reinícios contínuos mesmo com heartbeat fresco. Em uma ocorrência, `executor.json` havia sido atualizado aproximadamente três segundos antes de o supervisor registrar `heartbeat stale for more than 1m30s`.

A causa compatível com o código e com a evidência operacional era competição de supervisores: a instalação resiliente possui a tarefa agendada `FactoryBridge Supervisor`, enquanto o updater side-by-side também iniciava diretamente outro `--mode supervisor` depois de matar os processos anteriores. Supervisores concorrentes podem observar PIDs de executores diferentes no mesmo `executor.json` e matar um executor saudável.

O updater agora:

1. localiza a tarefa `FactoryBridge Supervisor`;
2. interrompe a tarefa antes do kill dos processos;
3. promove o novo binário;
4. prefere reativar/iniciar a tarefa canônica;
5. usa processo direto somente como fallback;
6. habilita a tarefa caso ela esteja `Disabled` antes do start;
7. se o Task Scheduler ainda falhar, registra a falha e inicia exatamente um supervisor direto como recuperação.

### Staging fora do Google Drive

Uma tentativa auditada de atualização falhou em `go build` com `Acesso negado` ao gravar `G:\Meu Drive\FACTORY_BRIDGE\FactoryBridge.next.exe`.

O staging de build e o script de aplicação foram movidos para:

```text
%LOCALAPPDATA%\FactoryBridge\staging
```

O Google Drive permanece apenas como transporte, status e evidência. Locks do cliente de sincronização não participam mais do build do runtime.

## Deploy e prova local

Fluxo anterior comprovado no notebook `Supremo`:

1. `git.pull` fast-forward até o commit `6f54c30`;
2. updater side-by-side promoveu o runtime local;
3. `bridge.ping` retornou sucesso;
4. `projecthub.snapshot` retornou 2,015 bytes em ~540 ms;
5. `projecthub.verify` passou build e 99/99 testes no commit canônico do ProjectHub `6494e7b51aae694f4559f836199cb78212976edc`.

Na auditoria subsequente:

- o checkout local avançou até `fccd17f`;
- a primeira atualização auditada falhou por lock do Google Drive no staging antigo;
- a atualização seguinte passou testes/build/staging local e promoveu o binário;
- a aplicação parou a tarefa agendada e os processos concorrentes, mas descobriu que `FactoryBridge Supervisor` já estava desabilitada e falhou ao reiniciá-la;
- o runtime ficou offline após a promoção, bloqueando novos `git.pull` pelo próprio Bridge.

A correção para habilitar a tarefa e usar fallback direto está publicada no `main`, junto com `factory_bridge/RECOVER_FACTORY_BRIDGE.cmd`.

## Recovery operacional

Se o Bridge estiver offline porque a tarefa `FactoryBridge Supervisor` está desabilitada, executar uma vez:

```text
factory_bridge\RECOVER_FACTORY_BRIDGE.cmd
```

O script é idempotente: habilita e reinicia a tarefa quando ela existe; se não existir, inicia o supervisor local diretamente. Uma cópia também é publicada em `FACTORY_BRIDGE/RECOVER_FACTORY_BRIDGE.cmd` para ficar disponível pelo Google Drive local.

Depois da recuperação, o ciclo canônico é:

1. `git.pull` para obter o `main` mais recente;
2. recriar `factory-bridge-update.request.json`;
3. `dispatcher.tick`;
4. confirmar `apply-success-side-by-side`;
5. `bridge.ping`;
6. `projecthub.verify`.

## Efeito esperado para sessões longas

```text
projecthub.snapshot
  -> decisão do agente
  -> alteração no GitHub
  -> ação de alto nível
  -> heartbeat contínuo
  -> resultado estruturado
  -> projecthub.snapshot quando necessário
```

`projecthub.logs` deve ser chamado apenas sob falha ou quando evidência detalhada for necessária.

## Estado atual

- snapshot compacto: IMPLEMENTADO E PROVADO;
- heartbeat durante ação: IMPLEMENTADO E PROVADO;
- fila FIFO-like: IMPLEMENTADA E COBERTA POR TESTE;
- staging fora do Drive: IMPLEMENTADO;
- prevenção de supervisor duplicado: IMPLEMENTADA;
- recovery de tarefa desabilitada: PUBLICADO;
- deploy local final da última correção: BLOCKED até uma execução local do recovery, porque o próprio Bridge está offline;
- runtime local promovido ainda se identifica como `0.13.0`; version bump fica para depois da recuperação e da prova final do ciclo completo.
