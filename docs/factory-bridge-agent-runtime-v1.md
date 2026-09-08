# FactoryBridge Agent Runtime V1

## Objetivo

Transformar o FactoryBridge em um runtime local seguro para agentes, usando o notebook como plano de execução e o ChatGPT como plano de decisão.

O ganho de autonomia vem de um ciclo verificável:

```text
ChatGPT
  -> GitHub: leitura/escrita de código e documentação
  -> Drive/01_COMMANDS: ação local allowlisted
  -> FactoryBridge no notebook
  -> Git / .NET / processos / ProjectHub
  -> Drive/02_RESULTS: evidência estruturada
  -> ChatGPT: próxima decisão
```

O Drive não é um terminal remoto. Comandos continuam limitados a `id` + `action`; parâmetros de shell, caminhos, scripts, branches e SHAs não atravessam a fronteira do Drive.

## Separação de responsabilidades

### ChatGPT

- define objetivo e próximo passo;
- lê e altera código pelo GitHub;
- envia ações locais conhecidas pelo Drive;
- lê resultados e decide correções;
- evita pedir intervenção humana para operações mecânicas que o runtime consegue verificar.

### GitHub

- fonte da implementação do Bridge/Dispatcher;
- plano de escrita de código;
- histórico, diff e rollback;
- substitui a necessidade de permitir `file.patch` ou shell arbitrário pelo Drive.

### FactoryBridge / notebook

- executa trabalho computacional real;
- faz sync seguro de checkouts;
- compila e testa;
- inicia e encerra aplicação sob proveniência controlada;
- coleta logs e estado;
- devolve evidência estruturada.

### Google Drive

- fila de comandos simples em `01_COMMANDS`;
- resultados em `02_RESULTS`;
- estado observacional em `00_STATUS`;
- arquivo histórico em `03_ARCHIVE`.

## Invariantes de segurança

1. O comando vindo do Drive tem somente `id` e `action`.
2. Campos desconhecidos são rejeitados.
3. Nenhuma ação recebe shell, script, caminho, branch ou SHA do Drive.
4. Ações ProjectHub de build/test/start exigem checkout canônico: `main`, limpo e `HEAD == origin/main`.
5. `projecthub.sync` é somente fast-forward e recusa checkout sujo ou aplicação em execução.
6. `projecthub.stop` encerra somente PID previamente registrado pelo Bridge.
7. Resultados são idempotentes por ID.
8. A atualização do próprio Bridge executa `go test ./...` antes de gerar e aplicar um novo binário.

## Ações do Agent Runtime

### Existentes

- `bridge.ping`
- `system.info`
- `git.status`
- `git.pull`
- `git.push`
- `dispatcher.test`
- `dispatcher.tick`
- `projecthub.status`
- `projecthub.sync`
- `projecthub.build`
- `projecthub.test`
- `projecthub.start`
- `projecthub.stop`

### V1 adicionadas no FactoryBridge v0.11.0

#### `bridge.doctor`

Verifica ferramentas e diretórios necessários no notebook. Retorna disponibilidade de Git, .NET, PowerShell, Go e dos workdirs configurados. Go é opcional para o runtime normal e obrigatório apenas para self-update.

```json
{
  "id": "BRIDGE-DOCTOR-001",
  "action": "bridge.doctor"
}
```

#### `projecthub.logs`

Lê somente os logs fixos do servidor gerenciado pelo Bridge em `00_STATUS`, limitado a 64 KiB por stream.

```json
{
  "id": "PROJECTHUB-LOGS-001",
  "action": "projecthub.logs"
}
```

#### `projecthub.validate`

Executa uma validação local completa e autossuficiente:

```text
preflight canônico
  -> build Release
  -> testes Release
  -> start gerenciado
  -> health + proveniência
  -> stop gerenciado
  -> RESULT estruturado
```

A validação recusa executar se ProjectHub já estiver rodando, para não assumir nem encerrar um processo preexistente.

```json
{
  "id": "PROJECTHUB-VALIDATE-001",
  "action": "projecthub.validate"
}
```

## Ciclo recomendado para um agente

### Desenvolvimento

1. `bridge.doctor` quando a sessão precisa confirmar o ambiente.
2. Ler estado do GitHub e ProjectHub.
3. Editar código no GitHub.
4. Usar `git.pull` para atualizar o checkout local do DISPATCHER quando houver mudança do Bridge.
5. Para o produto ProjectHub, usar `projecthub.stop` se necessário e `projecthub.sync` para obter o `main` canônico.
6. Executar `projecthub.validate`.
7. Em falha de runtime, executar `projecthub.logs`.
8. Corrigir código e repetir apenas o necessário.

### Regra de conclusão

Uma tarefa não deve ser considerada concluída apenas porque o código foi escrito. Para mudanças executáveis, o agente deve guardar evidência local compatível com a tarefa: build, testes, health/E2E quando aplicável e commit/proveniência correspondente.

## Atualização do próprio Bridge

O bootstrap da v0.11 preserva compatibilidade com a v0.10:

1. alterações são commitadas no repositório `Toctox/DISPATCHER`;
2. o Bridge v0.10 executa seu `git.pull` allowlisted;
3. é criado o marcador fixo `00_STATUS/factory-bridge-update.request.json`;
4. `dispatcher.tick` detecta o marcador e chama `scripts/factory-bridge-update.ps1`;
5. o script exige `go.exe`, roda `go test ./...` e compila `FactoryBridge.next.exe`;
6. somente após sucesso, agenda a troca do executável;
7. o processo antigo é encerrado após o RESULT do tick ter tempo de ser gravado;
8. `FactoryBridge.exe` anterior vira `FactoryBridge.prev.exe` e o novo supervisor é iniciado.

Se testes, build ou pré-condições falharem, o binário atual permanece em uso e o RESULT do `dispatcher.tick` registra a falha.

## GitHub Actions

Para desenvolvimento do ProjectHub, o notebook passa a executar a maior parte do trabalho que antes exigiria runner hospedado: restore, build, testes, start e validação. GitHub Actions pode permanecer para gates remotos específicos quando houver cota disponível, mas não é necessário para o ciclo local de desenvolvimento assistido.

## Próximas extensões

Somente adicionar novas ações quando houver um caso real que não possa ser coberto com segurança pelo plano GitHub + ações locais de alto nível. Prioridade provável: teste E2E Playwright allowlisted, coleta de screenshots/evidências e validação de banco local. Não adicionar execução arbitrária de PowerShell/CMD pelo Drive.
