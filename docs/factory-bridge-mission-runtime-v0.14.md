# FactoryBridge — Mission Runtime V0.14

## Objetivo

Transformar o FactoryBridge de uma ponte de comandos de baixa granularidade em um runtime local persistente. O ChatGPT fornece missão, decisão e estratégia; o notebook executa trabalho determinístico e computacional mesmo quando a conversa não está aberta.

## Fronteira arquitetural

### Cérebro — Google Drive

O Drive é uma mailbox e um estado compacto de decisão. Ele não participa de watchdog, heartbeat, lifecycle, build, testes, logs volumosos ou staging.

Estrutura alvo:

- `00_BRAIN/CURRENT_STATE.json`
- `00_BRAIN/DECISIONS.md`
- `01_INBOX/MISSION__<id>.json`
- `02_OUTBOX/CHECKPOINT__<id>.json`
- `03_DOCS/`

### Motor — notebook

O runtime operacional fica em `%LOCALAPPDATA%\FactoryBridge`:

- `bin/`
- `staging/`
- `state/`
- `missions/<id>/`
- `logs/`

A evidência completa de cada missão é persistida localmente. O supervisor usa somente estado local para decisões de saúde.

### Vitrine — ProjectHub-Lab

Builds testáveis ficam em `%USERPROFILE%\ProjectHub-Lab`:

- `BUILDS/<timestamp>_<commit>/`
- `LATEST.txt`
- `START_LATEST.cmd`
- `STOP_PROJECTHUB.cmd`
- `README.txt`

Cada build publicada é imutável. O operador pode abrir a versão mais recente diretamente sem depender do chat.

## Mission protocol

Schema inicial:

```json
{
  "id": "M-20260908-001",
  "kind": "projecthub.full_cycle",
  "createdAt": "2026-09-08T22:00:00-03:00",
  "objective": "Sincronizar, validar, publicar e executar smoke local."
}
```

Kinds allowlisted:

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`

Campos desconhecidos e kinds desconhecidos são rejeitados. Não existe shell arbitrário.

## projecthub.full_cycle

O motor executa, sem novas interações do chat:

1. `projecthub.sync`;
2. preflight canônico;
3. restore/build Release;
4. suite de testes;
5. quando a falha é de teste, até duas reproduções adicionais para gerar evidência;
6. `dotnet publish` para um build versionado em `ProjectHub-Lab`;
7. inicia o executável publicado localmente;
8. consulta `/health`;
9. encerra a instância de smoke;
10. grava evidência completa local;
11. publica somente checkpoint compacto no Drive.

## Estados de missão

- `RUNNING`: execução local em andamento.
- `DONE`: critérios determinísticos concluídos.
- `BLOCKED`: ambiente impede a execução.
- `NEEDS_BRAIN`: o motor esgotou ações allowlisted e requer decisão/implementação nova.

## Recuperação

Uma missão ingerida é copiada para `%LOCALAPPDATA%\FactoryBridge\missions\<id>` antes da execução. O estado não depende da conversa. Reinícios do chat não afetam a missão local; reinícios do Bridge preservam a evidência já escrita.

## Migração do Drive

A limpeza dos artefatos legados deve ocorrer somente depois de uma missão `projecthub.full_cycle` completar `DONE` no runtime novo. Até essa prova, `01_COMMANDS`, `02_RESULTS`, `03_ARCHIVE` e os scripts antigos permanecem como canal de bootstrap/rollback.

Após a prova final, o plano é remover do Drive os artefatos operacionais históricos (binários, zips, logs, resultados e comandos antigos), preservando apenas documentação canônica, mailbox e recovery mínimo.
