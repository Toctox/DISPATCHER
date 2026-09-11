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
127.0.0.1:8765  Factory Local Agent
        |
        +-- PowerShell / CMD / executáveis diretos
        +-- arquivos locais
        +-- processos longos + logs
        +-- Git / .NET / Node / Python / navegador
```

A FactoryBridge existe apenas como bootstrap/recovery enquanto o canal direto ainda não estiver conectado.

## Princípios do LOCAL_SANDBOX

- bind exclusivo em `127.0.0.1`;
- execução ampla sob a identidade Windows do usuário atual;
- sem allowlist de comandos, sem regex de shell e sem working-root artificial;
- token bearer local obrigatório para todas as operações que têm efeito ou expõem dados;
- `/health` é público apenas no loopback e não contém o token;
- token gerado localmente em `%LOCALAPPDATA%\FactoryNode\local-agent\token.txt` e nunca deve ser publicado no GitHub;
- logs em `%LOCALAPPDATA%\FactoryNode\local-agent\logs`;
- inicialização automática por `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FactoryNode-Local-Agent.cmd`;
- o agente não eleva privilégio: tudo roda com as permissões normais da conta Windows.

## Instalação canônica

O script versionado `scripts/local-agent-install.ps1` instala a versão existente no commit exato do DISPATCHER, reinicia uma instância anterior e qualifica `GET /health`.

Instalação final:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\local-agent-install.ps1
```

No fluxo remoto de bootstrap, o mesmo script é executado via `FACTORY_BUS_V2` / `script.run` depois que o commit estiver promovido no FactoryBridge.

## API local

Base URL: `http://127.0.0.1:8765`

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

`POST /v1/process/start`

Retorna `id` e `pid`. A saída é gravada em arquivos de log locais.

`POST /v1/process/output`

```json
{"id":"<task-id>","maxBytes":65536}
```

`POST /v1/process/stop`

```json
{"id":"<task-id>"}
```

No Windows, o stop usa `taskkill /T /F` para encerrar a árvore iniciada por aquela tarefa.

`GET /v1/processes` lista os processos iniciados pela instância atual do agente.

### Arquivos

- `POST /v1/file/read` — `{"path":"C:\\..."}`
- `POST /v1/file/write` — `{"path":"C:\\...","content":"..."}`
- `POST /v1/file/list` — `{"path":"C:\\..."}`

Não há root artificial no modo LOCAL_SANDBOX. O limite real é a permissão da conta Windows.

## Operação manual

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

Ler token localmente:

```powershell
$token = (Get-Content "$env:LOCALAPPDATA\FactoryNode\local-agent\token.txt" -Raw).Trim()
```

Executar:

```powershell
$headers = @{ Authorization = "Bearer $token" }
$body = @{ shell='powershell'; command='Get-Location'; timeoutSec=30 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/v1/exec -Headers $headers -ContentType 'application/json' -Body $body
```

Parar o daemon:

```powershell
powershell -NoProfile -File "$env:LOCALAPPDATA\FactoryNode\local-agent\stop.ps1"
```

Iniciar novamente:

```powershell
& "$env:LOCALAPPDATA\FactoryNode\local-agent\start.cmd"
```

## Próxima camada: ChatGPT ↔ notebook

Este commit entrega o lado local. Para que um chat consiga chamar o agente como ferramenta sem usar GitHub Issue como BUS, é necessário um canal que apresente essas operações ao ChatGPT (por exemplo um conector/MCP remoto compatível). Uma extensão Chrome pode ser interface, mas uma extensão isolada não transforma automaticamente `127.0.0.1` em uma ferramenta do modelo na nuvem.

A próxima camada deve preservar duas propriedades: o token nunca sai para páginas web comuns e o canal externo deve autenticar a máquina do usuário. O executor local já está preparado para ser o backend desse adaptador.

## Relação com FactoryBridge

FactoryBridge continua útil para:

- bootstrap e reinstalação;
- recovery quando o canal direto cair;
- ações administrativas versionadas.

Não é necessário passar o trabalho cotidiano por payload hash, allowlists e `script.run` depois que o canal direto estiver disponível.
