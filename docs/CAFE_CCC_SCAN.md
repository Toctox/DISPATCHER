# Café CCC/SVRS — inventário de certificados e IEs habilitadas

## Objetivo

Implementar uma capacidade local, auditável e restrita para:

1. localizar o pacote `Certificados PFX.zip` no Google Drive local ou, na ausência dele, localizar arquivos `.pfx/.p12` no Drive;
2. abrir certificados A1 apenas em memória, sem instalação permanente e sem exportação de chave privada;
3. identificar e-CPFs válidos;
4. selecionar um certificado válido que consiga autenticar a conexão TLS mútua (mTLS) com o CCC/SVRS;
5. consultar o cadastro por CPF no serviço oficial `NFeConsultaCadastro`;
6. manter somente inscrições estaduais com `cSit = 1`;
7. devolver IE, município, UF, nome e CPF do titular, além de inventário sanitizado e erros;
8. copiar apenas os resultados cadastrais e metadados públicos para o Google Drive.

## Serviço oficial

Produção / SVRS:

`https://cad.svrs.rs.gov.br/ws/cadconsultacadastro/cadconsultacadastro4.asmx`

Contrato:

- Web Service: `CadConsultaCadastro4` / `NFeConsultaCadastro` versão 4.00;
- método: `consultaCadastro`;
- parâmetro SOAP de entrada: `nfeDadosMsg`;
- XML cadastral: `ConsCad` versão `2.00`;
- `xServ`: `CONS-CAD`;
- UF desta implementação: `ES`;
- documento consultado: `CPF`;
- filtro obrigatório de saída: `infCad/cSit = 1` e IE não vazia.

## Arquitetura

```text
GitHub Issue #7
  FACTORY_BUS_V2 / MISSION kind=cafe.ccc.scan
          |
          v
FactoryBridge instalado
  - valida hash/expiração/replay
  - valida targetCommit == commit realmente instalado
  - agenda uma missão por vez
          |
          v
executor fixo cafe_ccc.go
  - NÃO aceita shell/comando/caminho remoto
  - chama somente scripts/cafe-ccc-scan.ps1
  - único parâmetro variável: MissionId validado
          |
          v
cafe-ccc-scan.ps1
  - localiza Google Drive
  - prefere Certificados PFX.zip
  - fallback: varre .pfx/.p12
  - abre PFX com EphemeralKeySet
  - extrai CPF e metadados públicos
  - estabelece mTLS com um certificado válido
  - consulta CPF no CCC/SVRS
  - filtra cSit=1
          |
          +--> %LOCALAPPDATA%\FactoryBridge\cafe_ccc\<MissionId>\output
          |
          +--> G:\Meu Drive\07_TECNOLOGIA E AUTOMAÇÕES\CAFE_CCC\RESULTADOS\<MissionId>
```

## Segurança

A implementação deliberadamente não cria uma superfície de PowerShell arbitrária.

O FactoryBridge chama um arquivo conhecido do próprio commit instalado. A missão não pode fornecer script, path, argumento, URL, senha, CPF ou comando. O único argumento repassado é o `MissionId`, limitado pelo regex do FactoryBridge.

Os PFX são carregados com `X509KeyStorageFlags.EphemeralKeySet`. A chave privada não é exportada, não é gravada em PEM e não é instalada no repositório de certificados do Windows.

Senhas são usadas apenas quando explicitamente codificadas no nome do arquivo no padrão `senha ...`, `password ...` ou `pass ...`, ou quando o PFX aceita senha vazia. Não há brute force. Senhas nunca entram nos CSV/JSON/logs. Nomes de arquivo que contenham senha são sanitizados para `[REDACTED]` antes de qualquer saída.

Os resultados copiados ao Drive não contêm PFX/P12, chave privada ou senha.

## Fonte dos certificados

Ordem:

1. localizar exatamente `Certificados PFX.zip` em um Google Drive local reconhecido, por exemplo `G:\Meu Drive`;
2. se localizado, copiar o ZIP para workspace local temporário da missão e extrair apenas para processamento local;
3. se o ZIP não estiver disponível, localizar `.pfx/.p12` no Google Drive e no workspace legado `%LOCALAPPDATA%\Cafe`, se ainda existir.

Arquivos são deduplicados por SHA-256 e certificados por thumbprint.

Há limite defensivo de 1.000 certificados por execução.

## Seleção do certificado mTLS

Depois do inventário, a implementação identifica CPFs válidos e escolhe o primeiro certificado A1 válido que consiga autenticar uma consulta de prova ao CCC/SVRS sem retornar erro de solicitante `cStat=257`.

Esse certificado passa a ser o certificado de cliente da sessão HTTP para consultar os demais CPFs. O thumbprint e o nome público do certificado usado são registrados no resumo; a chave privada não é exposta.

## SOAP

A requisição usa SOAP 1.2 e `Content-Type: application/soap+xml`.

Estrutura lógica:

```xml
<soap12:Envelope ...>
  <soap12:Body>
    <nfeDadosMsg xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/CadConsultaCadastro4">
      <ConsCad versao="2.00" xmlns="http://www.portalfiscal.inf.br/nfe">
        <infCons>
          <xServ>CONS-CAD</xServ>
          <UF>ES</UF>
          <CPF>...</CPF>
        </infCons>
      </ConsCad>
    </nfeDadosMsg>
  </soap12:Body>
</soap12:Envelope>
```

A resposta é analisada por `local-name()` para não depender do prefixo XML usado pela SVRS.

## Regra de habilitação

Uma inscrição só entra no resultado quando:

- existe `infCad`;
- `IE` não está vazia;
- `cSit` é literalmente `1`.

Registros não habilitados não aparecem em `ccc_ies_habilitadas.csv` nem em `ccc_ies_habilitadas.json`.

## Saídas

Cada missão produz:

- `certificados_inventario.csv` — metadados públicos dos certificados, com filename sanitizado;
- `ccc_ies_habilitadas.csv` — somente IEs habilitadas;
- `ccc_ies_habilitadas.json` — mesma relação em JSON;
- `erros.csv` — PFX não legíveis, falhas TLS ou falhas cadastrais;
- `resultado.json` — resumo da missão, contagens, endpoint, certificado mTLS usado e paths de saída.

Colunas principais do resultado cadastral:

- CPF;
- Nome;
- IE;
- Município;
- Código IBGE do município, quando retornado;
- UF;
- `cSit`.

## Resiliência

- timeout HTTP: 60 segundos;
- até 3 tentativas por consulta;
- backoff de 1 e 2 segundos;
- intervalo de aproximadamente 800 ms entre CPFs;
- timeout máximo da missão no FactoryBridge: 25 minutos;
- deduplicação de IEs por `UF + IE`.

## Governança da missão

`cafe.ccc.scan` usa `FACTORY_BUS_V2` e mantém:

- `issuedAt` e `expiresAt` obrigatórios;
- SHA-256 do payload canônico;
- proteção contra replay por MissionId;
- journal durável;
- controle PAUSE/CANCEL/RESUME;
- serialização no scheduler global;
- `targetCommit` obrigatório.

Para esta missão, `targetCommit` é comparado ao `sourceCommit` de `%LOCALAPPDATA%\FactoryBridge\state\installed-runtime.json`. Assim uma missão só executa contra o código explicitamente instalado e autorizado.

## Atualização segura

O FactoryBridge já possui um canal administrativo separado (`FACTORY_ADMIN_V1`) que permite somente `bridge.self_update`. O updater:

1. exige commit completo de 40 caracteres;
2. exige que o commit aprovado seja exatamente `origin/main`;
3. faz checkout do commit;
4. executa `go test ./...`;
5. compila um binário staged;
6. preserva o binário anterior;
7. promove o novo binário;
8. testa `/public/health` e `executorOnline`;
9. restaura o binário anterior se a saúde falhar.

## Limitações conhecidas

- O conector Gmail do ChatGPT identifica `Certificados PFX.zip`, mas não fornece os bytes porque o MIME ZIP está marcado como não suportado para leitura direta. Por isso a execução local procura o ZIP no Drive; se ele ainda não estiver lá, usa os PFX/P12 já existentes no Drive como fallback.
- Certificados cuja senha não possa ser obtida de forma explícita não são quebrados nem submetidos a tentativa de brute force; são registrados em `erros.csv`.
- O scanner consulta somente CPF/ES nesta versão. Não consulta CNPJ nem outras UFs.
- A disponibilidade e os códigos de retorno do CCC são autoridade externa; falhas da SVRS são registradas, não convertidas em dados presumidos.

## Arquivos de implementação

- `factory_bridge/cafe_ccc.go`
- `factory_bridge/cafe_ccc_test.go`
- `factory_bridge/mission_hardening.go`
- `factory_bridge/mission_runtime.go`
- `scripts/cafe-ccc-scan.ps1`
- este documento
