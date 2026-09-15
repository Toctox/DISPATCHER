# E-Docs / Chat Ops automation (V1)

Status: guarded foundation. Public API V2 first; browser/HAR data is diagnostic only.

## Goal

Allow Chat Ops to validate a producer package, upload/capture the three PDFs in E-Docs as
the authenticated citizen, and create the forwarding to the correct SEFAZ/ES Agência da
Receita Estadual (ARE).

The existing Local Agent can execute this module with `python -m factory_dispatcher.edocs_cli`.
No independent daemon is required.

## Why API V2, not replaying the web app

The supplied HAR confirms the current web flow and useful destination identifiers, but it did
not record the final capture/forwarding POST. More importantly, the web endpoints
(`/Documento/Captura/...`, `/Encaminhamento/Novo`) are implementation details of the UI.

E-Docs publishes API V2 expressly for integrations. The V2 contract has stable endpoints for:

- upload URL generation;
- citizen capture of digitalized, unsigned nato-digital, and ICP-Brasil PDFs;
- event polling;
- destination discovery;
- forwarding.

The HAR is therefore used to cross-check destination UUIDs and detect routing drift, not to
copy cookies, anti-forgery tokens, or private web calls.

## Authentication

Authorship operations require an Acesso Cidadão user token from Authorization Code/Hybrid.
Client Credentials is not sufficient for capture/forwarding.

Required E-Docs scopes:

- `api-sigades-consultar`
- `api-sigades-documento`
- `api-sigades-encaminhamento`

Never commit bearer tokens, refresh tokens, client secrets, browser cookies, HAR credentials
or anti-forgery tokens. Chat Ops reads the short-lived bearer token from
`EDOCS_ACCESS_TOKEN`.

E-Docs API application onboarding is a separate administrative prerequisite. Having an
authenticated E-Docs browser session is useful for manual validation, but does not itself
turn that browser session into a Public API bearer token.

## Environments and write gates

Training is the default:

- Training: `https://api.treinamento.e-docs.es.gov.br`
- Production: `https://api.e-docs.es.gov.br`

All writes require both the CLI flag and environment gate:

```text
--execute
EDOCS_ENABLE_WRITE=1
```

Production adds a second gate:

```text
EDOCS_ENABLE_PRODUCTION=1
```

This prevents a Chat Ops parsing mistake from becoming a live submission.

## Canonical API V2 contracts implemented

Upload:

```text
GET /v2/documentos/upload-arquivo/gerar-url-upload/{tamanhoArquivo}
POST multipart/form-data -> temporary storage URL
```

The value carried to capture is
`identificadorTemporarioArquivoNaNuvem` (not a document UUID).

Citizen capture modes:

```text
POST /v2/documentos/capturar/digitalizado/cidadao
POST /v2/documentos/capturar/nato-digital/copia/cidadao
POST /v2/documentos/capturar/nato-digital/icp-brasil/cidadao
```

Before each capture, the matching `/validar` endpoint is called.

For ordinary citizen digitalization, `valorLegal` is `CopiaSimples`, as required by the
published E-Docs rules. Capture mode is never inferred from a filename; every manifest must
declare it explicitly.

Event polling:

```text
GET /v2/eventos/{idEvento}
```

Terminal success states are `Executado` and `Concluido`. Capture events yield
`idDocumento`; forwarding events yield `idEncaminhamento`.

Forwarding:

```text
POST /v2/encaminhamento/novo
```

The module uses the V2 fields `assunto`, `idsDestinos`, `mensagem`, `idResponsavel`,
`idsDocumentos`, `enviarEmailNotificacoes` and `restricaoAcesso`.

The default access setting matches the Organizational option shown in the user's manual
flow:

```json
{
  "transparenciaAtiva": false,
  "idsFundamentosLegais": null,
  "classificacaoInformacao": null
}
```

Do not silently change this to Public, Sigiloso or Classificado. If SEFAZ requires another
level, make it an explicit manifest/policy change.

## HAR cross-checks from the authenticated E-Docs flow

These identifiers were observed in the supplied HAR and are used only as drift guards:

| Item | UUID |
| --- | --- |
| GOVES patriarca | `fe88eb2a-a1f3-4cb1-a684-87317baf5a57` |
| SEFAZ | `9145ec80-a7b9-43c8-a230-cc0d3b7257fe` |
| ARE Aracruz | `62ab5dc1-de3f-4374-a46a-ab3a69fbb9a9` |
| ARE Linhares | `bb55922b-2140-41a2-9295-e00dd2eddd12` |

`discover` resolves the hierarchy live and fails if any known UUID disappears. This is
deliberate: no silent rerouting.

Current known package routing:

- Eunice Mariano Gonçalves -> ARE Aracruz
- Jocimar Gama -> ARE Aracruz
- Afonso Dolizete Avancini -> ARE Aracruz
- Lucelia Caliman Rampinelli -> ARE Linhares

## Manifest

A package contains exactly three roles:

- `procuracao`
- `termoAdesao`
- `documentosPessoais`

Each file must explicitly declare one capture mode:

- `digitalizado`
- `nato-digital-copia`
- `icp-brasil`

Do not guess whether a procuração or termo is digitalized merely because it is a PDF. If the
final file was printed, signed on paper, and scanned, use `digitalizado`; if it was created
digitally and remains unsigned, use `nato-digital-copia`; if the PDF itself has a valid
ICP-Brasil signature, use `icp-brasil`.

## Chat Ops commands

Validate only; no network write:

```powershell
python -m factory_dispatcher.edocs_cli plan .\examples\edocs-package.example.json
```

Verify authenticated user and live destination UUIDs:

```powershell
$env:EDOCS_ACCESS_TOKEN = "<short-lived user bearer token>"
python -m factory_dispatcher.edocs_cli discover --environment training
```

Submit the three-document package in Training:

```powershell
$env:EDOCS_ENABLE_WRITE = "1"
python -m factory_dispatcher.edocs_cli submit `
  .\examples\edocs-package.example.json `
  --state .\state\edocs\eunice.json `
  --environment training `
  --execute
```

Production additionally requires:

```powershell
$env:EDOCS_ENABLE_PRODUCTION = "1"
```

Chat Ops can invoke the same command through the existing Local Agent `/v1/exec` channel.

## Duplicate prevention / recovery

`submit` stores an explicit checkpoint after every meaningful transition. The package
fingerprint includes CPF, IE, destination UUID, subject/message, PDF SHA-256 hashes and
capture modes. A checkpoint cannot be reused for a changed package.

Before an institutional mutation, the state file is marked as uncertain. If the process loses
the response after E-Docs may already have accepted the write, a later run fails closed
instead of repeating the capture/forwarding blindly. The operator must inspect E-Docs and
record the verified event id with `reconcile-capture` or `reconcile-forwarding` before the
workflow resumes.

This is intentional: avoiding duplicate legal submissions is more important than automatic
retry.

## Before first production submission

1. Register/authorize the integrating application in Acesso Cidadão and obtain the required
   E-Docs scopes.
2. Execute `discover` in Training and confirm GOVES, SEFAZ, ARE Aracruz and ARE Linhares.
3. Test one non-production package end-to-end in Training.
4. Verify the resulting E-Docs document metadata, access level and destination manually.
5. Only then enable `EDOCS_ENABLE_PRODUCTION=1`.

The browser/HAR route remains a diagnostic fallback. Do not ship a production integration
that depends on session cookies or undocumented `/Documento/...` web endpoints while the
official V2 API is available.
