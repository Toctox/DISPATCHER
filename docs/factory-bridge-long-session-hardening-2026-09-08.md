# FactoryBridge — Long-session hardening — 2026-09-08

## Objetivo

Reduzir os dois gargalos observados no benchmark do FactoryBridge v0.13 para sessões longas de ChatGPT ↔ notebook:

1. crescimento desnecessário do contexto por `projecthub.snapshot`;
2. falso positivo de executor travado durante ações locais acima da janela de 90 s do supervisor.

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

O executor agora mantém `executor.json` atualizado durante a execução de qualquer ação allowlisted, com intervalo de 10 s. A correção preserva o `StartedAt` do processo e não aumenta artificialmente o timeout nem a janela de 90 s do supervisor.

Antes, `runExecutor` só renovava o heartbeat antes/depois de uma ação; um build/test demorado podia parecer travado. Agora o heartbeat ocorre em goroutine enquanto `processOne` está bloqueado na ação.

Foi adicionado teste automatizado que confirma avanço do heartbeat durante trabalho em andamento, preservando PID e `StartedAt`.

## Deploy e prova local

Fluxo executado no notebook `Supremo`:

1. `git.pull` fast-forward até o commit `6f54c30`;
2. `dispatcher.tick` acionou `go test ./...`, build e staging do Bridge;
3. updater side-by-side registrou `local-runtime-promoted`, `local-supervisor-started` e `apply-success-side-by-side`;
4. `bridge.ping` pós-deploy retornou sucesso;
5. `projecthub.snapshot` pós-deploy retornou 2,015 bytes em ~540 ms;
6. `projecthub.verify` pós-deploy passou build e 99/99 testes no commit canônico do ProjectHub `6494e7b51aae694f4559f836199cb78212976edc`.

Durante esse `projecthub.verify`, iniciado às 18:16:07 e concluído às 18:16:40, `executor.json` foi renovado às 18:16:27, comprovando heartbeat no meio da ação.

## Efeito esperado para sessões longas

O loop recomendado passa a ser:

```text
projecthub.snapshot
  -> decisão do agente
  -> alteração no GitHub
  -> ação de alto nível
  -> resultado estruturado
  -> projecthub.snapshot quando necessário
```

`projecthub.logs` deve ser chamado apenas sob falha ou quando evidência detalhada for necessária.

Isso reduz drasticamente a pressão de contexto por observação e evita que operações legitimamente longas sejam mortas apenas por falta de heartbeat.

## Limites ainda abertos

- O runtime ainda se identifica como `0.13.0`; este hardening é uma revisão operacional da mesma linha até que o version bump seja feito.
- A fila local é ordenada lexicograficamente por nome de arquivo, não estritamente por horário de criação. O benchmark mostrou interleaving entre comandos; isso não causou corrupção, mas deve ser tratado antes de aumentar concorrência ou múltiplos produtores.
- O heartbeat foi provado durante uma ação real de ~33 s e por teste automatizado. Não foi introduzida uma ação artificial de 90+ s apenas para benchmark.

## Estado

HARDENING APPLIED AND LOCALLY VERIFIED.
