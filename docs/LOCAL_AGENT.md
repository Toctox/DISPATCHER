# Factory Local Agent — LOCAL_SANDBOX

## Objetivo

O Factory Local Agent é um executor local leve para o notebook Windows. Ele não usa API de LLM, não chama OpenAI e não consome franquia de modelo. O papel dele é ser os olhos e braços locais: executar comandos, ler/escrever arquivos e iniciar/parar processos. O raciocínio pode continuar no ChatGPT quando desejado.

A implementação usa somente a biblioteca padrão do Python já presente no host. Não há dependências pip.

## Topologia

```text
ChatGPT / controlador
        |
        | futuro conector/MCP/extensão
        v
127.0.0.1:18765  Factory Local Agent
        |
        +-- PowerShell / CMD / executáveis diretos
        +-- arquivos locais
        +-- processos longos + logs
        +-- Git / .NET / Node / Python / navegador
```

A FactoryBridge existe apenas como bootstrap/recovery enquanto o canal direto ainda não estiver conectado.

## Princípios do LOCAL_SANDBOX

- bind exclusivo em `127.0.0.1`;
- porta canônica local `18765`, registrada em `%LOCALAPPDATA%\FactoryNode\local-agent\port.txt`;
- execução ampla sob a identidade Windows do usuário atual;
- sem allowlist de comandos, sem regex de shell e sem working-root artificial;
- token bearer local obrigatório para todas as operações que têm efeito ou expõem dados;
- `/health` é público apenas no loopback e não contém o token;
- token gerado localmente em `%LOCALAPPDATA%\FactoryNode\local-agent\token.txt` e nunca deve ser publicado no GitHub;
- logs em `%LOCALAPPDATA%\FactoryNode\local-agent\logs`;
- inicialização automática e silenciosa por `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FactoryNode-Local-Agent.vbs`, usando `pythonw.exe`;
- o agente não eleva privilégio: tudo roda com as permissões normais da conta Windows.

## Instalação canônica

O script versionado `scripts/local-agent-install.ps1` resolve e qualifica um Python 3 real, exige o `pythonw.exe` correspondente, compila `agent.py`, instala um launcher VBS silencioso na pasta Startup, inicia o agente sem console visível e valida `GET /health`.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-agent-install.ps1
```

No fluxo remoto de bootstrap, o mesmo script pode ser executado via FactoryBridge. A operação cotidiana não depende desse BUS.

## API local

Base URL: `http://127.0.0.1:18765`

### Health

`GET /health`

Não exige autenticação e só devolve metadados de saúde.

### Autenticação

Nas demais rotas, usar uma das formas:

```text
Authorization: Bearer <token>
```

ou

```text
X-Local-Agent-Token: <token>
```

### Executar e aguardar

`POST /v1/exec`

```json
{
  "shell": "powershell",
  "command": "git status",
  "cwd": "C:\\caminho\\repo",
  "timeoutSec": 600
}
```

`shell` aceita `powershell`, `cmd` ou `direct`. Em `direct`, enviar `argv` como array.

### Processo longo

`POST /v1/process/start` retorna `id` e `pid`. A saída é gravada em arquivos de log locais.

`POST /v1/process/output`:

```json
{"id":"<task-id>","maxBytes":65536}
```

`POST /v1/process/stop`:

```json
{"id":"<task-id>"}
```

No Windows, o stop usa `taskkill /T /F` para encerrar a árvore iniciada por aquela tarefa. `GET /v1/processes` lista os processos iniciados pela instância atual.

### Arquivos

- `POST /v1/file/read` — `{"path":"C:\\..."}`
- `POST /v1/file/write` — `{"path":"C:\\...","content":"..."}`
- `POST /v1/file/list` — `{"path":"C:\\..."}`

Não há root artificial no modo LOCAL_SANDBOX. O limite real é a permissão da conta Windows.

## Operação manual

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:18765/health
```

Ler token localmente sem publicá-lo:

```powershell
$token = (Get-Content "$env:LOCALAPPDATA\FactoryNode\local-agent\token.txt" -Raw).Trim()
```

Executar:

```powershell
$headers = @{ Authorization = "Bearer $token" }
$body = @{ shell='powershell'; command='Get-Location'; timeoutSec=30 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:18765/v1/exec -Headers $headers -ContentType 'application/json' -Body $body
```

Parar a instância persistente:

```powershell
$pid = [int](Get-Content "$env:LOCALAPPDATA\FactoryNode\local-agent\agent.pid" -Raw)
taskkill.exe /PID $pid /T /F
```

Iniciar novamente sem abrir console:

```powershell
$launcher = Join-Path ([Environment]::GetFolderPath('Startup')) 'FactoryNode-Local-Agent.vbs'
Start-Process wscript.exe -ArgumentList ('"' + $launcher + '"') -WindowStyle Hidden
```

Diagnóstico:

```powershell
Invoke-RestMethod http://127.0.0.1:18765/health
Get-Process pythonw -ErrorAction SilentlyContinue
Get-Content (Join-Path ([Environment]::GetFolderPath('Startup')) 'FactoryNode-Local-Agent.vbs')
```

## Incidente de bootstrap — porta 8765

No host Windows real, o bind em `127.0.0.1:8765` falhou com `WinError 10013`, indicando que a porta estava bloqueada/reservada pelo stack de rede local. A porta canônica foi movida para `18765`; isso evita depender da faixa problemática e o instalador registra explicitamente a porta usada.

## Próxima camada: ChatGPT ↔ notebook

Este componente entrega o lado local. Para que um chat consiga chamar o agente como ferramenta sem usar GitHub Issue como BUS, é necessário um canal que apresente essas operações ao ChatGPT, por exemplo um conector/MCP/extensão compatível. Uma extensão Chrome pode ser interface, mas uma extensão isolada não transforma automaticamente `127.0.0.1` em uma ferramenta do modelo na nuvem.

A próxima camada deve preservar duas propriedades: o token nunca sai para páginas web comuns e o canal externo deve autenticar a máquina do usuário. O executor local já está preparado para ser o backend desse adaptador.

## Relação com FactoryBridge

FactoryBridge continua útil para bootstrap, reinstalação, recovery e ações administrativas versionadas. O trabalho cotidiano não precisa passar por payload hash, allowlists e `script.run` depois que o canal direto estiver disponível.
