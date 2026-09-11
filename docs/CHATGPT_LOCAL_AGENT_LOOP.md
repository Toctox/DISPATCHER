# ChatGPT Local Agent Loop

## Objetivo

Criar um ciclo operacional direto entre uma aba dedicada do ChatGPT e o `Factory Local Agent` já qualificado no notebook, sem usar API de LLM e sem depender do GitHub Issue BUS para o trabalho cotidiano.

Fluxo alvo:

```text
ChatGPT (aba dedicada)
    ↓ LOCAL_AGENT_V1
Extensão Chrome local
    ↓ HTTP autenticado em loopback
127.0.0.1:18765 — Factory Local Agent
    ↓
PowerShell / CMD / Git / .NET / arquivos / processos
    ↑
resultado local
    ↑
Extensão envia LOCAL_AGENT_RESULT_V1 ao ChatGPT
    ↑
ChatGPT decide o próximo passo
```

## Estado qualificado

- `Factory Local Agent` responde em `127.0.0.1:18765`.
- modo: `LOCAL_SANDBOX`.
- Python 3 real foi qualificado no host Windows.
- a antiga porta `8765` foi descartada após `WinError 10013` no host real.
- token bearer permanece somente no notebook em `%LOCALAPPDATA%\FactoryNode\local-agent\token.txt`.
- o agente não chama OpenAI nem executa LLM.

## Por que extensão e não MCP personalizado

No plano atual, MCP personalizado com ações completas de escrita/execução não é o caminho disponível. O loop pela extensão usa a sessão web normal do ChatGPT como controlador e o executor local como ferramenta, sem API do GPT.

## Protocolo de ação

O assistente emite um bloco de código cujo conteúdo começa com:

```text
LOCAL_AGENT_V1
```

seguido por um objeto JSON com `id`, `method`, `path` e `body`.

Exemplo conceitual:

```text
LOCAL_AGENT_V1
{"id":"probe-001","method":"POST","path":"/v1/exec","body":{"shell":"powershell","command":"Get-Location","timeoutSec":30}}
```

A extensão aceita apenas as rotas já expostas pelo Local Agent:

- `/health`
- `/v1/status`
- `/v1/processes`
- `/v1/exec`
- `/v1/process/start`
- `/v1/process/stop`
- `/v1/process/output`
- `/v1/file/read`
- `/v1/file/write`
- `/v1/file/list`

O resultado é devolvido automaticamente ao ChatGPT como:

```text
LOCAL_AGENT_RESULT_V1
{...}
END_LOCAL_AGENT_RESULT_V1
```

Se o assistente não emitir outro `LOCAL_AGENT_V1`, o ciclo encerra naturalmente.

## Controles operacionais

- somente uma aba ChatGPT explicitamente armada executa comandos;
- comandos já renderizados antes do armamento são ignorados;
- IDs executados são mantidos para impedir repetição de efeitos durante reprocessamento do DOM;
- o token fica no armazenamento privado da extensão e não deve ser enviado ao chat;
- a extensão possui comando explícito de desativação;
- recarregar a aba exige novo armamento;
- o Local Agent permanece restrito a `127.0.0.1`, sem exposição pública da porta `18765`.

## Distribuição

A implementação executável v0.1.0 foi empacotada como `chatgpt-local-agent-loop-v0.1.0.zip` no artefato da conversa de bootstrap. O pacote contém:

- `extension/manifest.json`
- `extension/service_worker.js`
- `extension/content.js`
- `extension/popup.html`
- `extension/popup.js`
- `install-extension.ps1`
- `README.md`

A gravação do relay executável diretamente pelo conector GitHub foi recusada pelo controle de segurança do próprio conector; por isso o repositório mantém a documentação canônica e o binário-fonte da extensão é entregue como artefato local para carregamento manual no Chrome.

## Instalação resumida

1. Extrair o ZIP no notebook.
2. Executar `install-extension.ps1`.
3. Em `chrome://extensions`, habilitar Modo do desenvolvedor e usar **Carregar sem compactação** apontando para `%LOCALAPPDATA%\FactoryNode\chatgpt-local-agent-loop`.
4. Em uma aba dedicada do ChatGPT, copiar o token local para a área de transferência sem imprimi-lo:

```powershell
(Get-Content "$env:LOCALAPPDATA\FactoryNode\local-agent\token.txt" -Raw).Trim() | Set-Clipboard
```

5. No popup da extensão, colar o token, manter porta `18765`, salvar, testar a saúde e ativar a aba atual.

## Observação

Este mecanismo é automação da interface web do ChatGPT, não integração MCP nativa. Alterações futuras no DOM/composer do ChatGPT podem exigir atualização dos seletores da extensão.
