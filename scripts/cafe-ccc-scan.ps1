[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')]
    [string]$MissionId
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http

$Endpoint = 'https://cad.svrs.rs.gov.br/ws/cadconsultacadastro/cadconsultacadastro4.asmx'
$Uf = 'ES'
$MaxCertificates = 1000
$RequestDelayMs = 800
$StartedAt = [DateTimeOffset]::UtcNow
$LocalRoot = Join-Path $env:LOCALAPPDATA ("FactoryBridge\cafe_ccc\" + $MissionId)
$InputRoot = Join-Path $LocalRoot 'input'
$OutputRoot = Join-Path $LocalRoot 'output'
New-Item -ItemType Directory -Path $InputRoot,$OutputRoot -Force | Out-Null

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-SafeFileName([string]$Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) { return '' }
    $safe = $Name
    $safe = [regex]::Replace($safe, '(?i)(senha|password|pass)\s*[:=_-]?\s*[^\s.()\[\]{}]+', '$1 [REDACTED]')
    return $safe
}

function Get-PasswordFromName([string]$Name) {
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $match = [regex]::Match($base, '(?i)(?:senha|password|pass)\s*[:=_-]?\s*([^\s()\[\]{}]+)')
    if ($match.Success) { return $match.Groups[1].Value.Trim('"',"'") }
    return $null
}

function Test-Cpf([string]$Cpf) {
    $digits = ($Cpf -replace '\D','')
    if ($digits.Length -ne 11) { return $false }
    if ($digits -match '^(\d)\1{10}$') { return $false }
    for ($t = 9; $t -le 10; $t++) {
        $sum = 0
        for ($i = 0; $i -lt $t; $i++) {
            $sum += ([int]::Parse($digits[$i].ToString())) * (($t + 1) - $i)
        }
        $check = (11 - ($sum % 11))
        if ($check -ge 10) { $check = 0 }
        if ($check -ne [int]::Parse($digits[$t].ToString())) { return $false }
    }
    return $true
}

function Find-CpfInText([string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    foreach ($m in [regex]::Matches($Text, '(?<!\d)(\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[-.\s]?\d{2})(?!\d)')) {
        $candidate = ($m.Groups[1].Value -replace '\D','')
        if (Test-Cpf $candidate) { return $candidate }
    }
    return $null
}

function Get-CertificateCpf([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate, [string]$FileName) {
    $parts = New-Object System.Collections.Generic.List[string]
    $parts.Add([string]$Certificate.Subject)
    try { $parts.Add([string]$Certificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false)) } catch { }
    foreach ($ext in $Certificate.Extensions) {
        try { $parts.Add([string]$ext.Format($false)) } catch { }
    }
    foreach ($part in $parts) {
        $cpf = Find-CpfInText $part
        if ($cpf) { return $cpf }
    }
    return (Find-CpfInText $FileName)
}

function Get-CertificateDisplayName([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate) {
    $name = ''
    try { $name = [string]$Certificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) } catch { }
    if ([string]::IsNullOrWhiteSpace($name)) {
        $m = [regex]::Match([string]$Certificate.Subject, '(?i)(?:^|,\s*)CN=([^,]+)')
        if ($m.Success) { $name = $m.Groups[1].Value }
    }
    $name = [regex]::Replace($name, '[:\s-]*\d{11}\s*$', '').Trim()
    return $name
}

function Get-GoogleDriveRoots {
    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add('G:\Meu Drive')
    $candidates.Add('G:\My Drive')
    if ($env:USERPROFILE) {
        $candidates.Add((Join-Path $env:USERPROFILE 'Meu Drive'))
        $candidates.Add((Join-Path $env:USERPROFILE 'My Drive'))
        $candidates.Add((Join-Path $env:USERPROFILE 'Google Drive'))
    }
    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        if (-not (Test-Path -LiteralPath $candidate -PathType Container)) { continue }
        $key = $candidate.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        $candidate
    }
}

function Find-ExactZip([string[]]$Roots) {
    foreach ($root in $Roots) {
        try {
            $match = Get-ChildItem -LiteralPath $root -Filter 'Certificados PFX.zip' -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($null -ne $match) { return $match.FullName }
        } catch { }
    }
    return $null
}

function Find-CertificateFiles([string[]]$Roots) {
    $zip = Find-ExactZip $Roots
    $files = New-Object System.Collections.Generic.List[System.IO.FileInfo]
    if ($zip) {
        $zipCopy = Join-Path $InputRoot 'Certificados PFX.zip'
        Copy-Item -LiteralPath $zip -Destination $zipCopy -Force
        $extract = Join-Path $InputRoot 'certificados_extraidos'
        if (Test-Path -LiteralPath $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
        Expand-Archive -LiteralPath $zipCopy -DestinationPath $extract -Force
        foreach ($item in Get-ChildItem -LiteralPath $extract -File -Recurse -ErrorAction SilentlyContinue) {
            if ($item.Extension -ieq '.pfx' -or $item.Extension -ieq '.p12') { $files.Add($item) }
        }
        return [pscustomobject]@{ Mode = 'ZIP'; ZipPath = $zip; Files = @($files) }
    }

    $extraRoots = New-Object System.Collections.Generic.List[string]
    foreach ($r in $Roots) { $extraRoots.Add($r) }
    $legacyCafe = Join-Path $env:LOCALAPPDATA 'Cafe'
    if (Test-Path -LiteralPath $legacyCafe -PathType Container) { $extraRoots.Add($legacyCafe) }

    $dedupe = @{}
    foreach ($root in $extraRoots) {
        try {
            foreach ($item in Get-ChildItem -LiteralPath $root -File -Recurse -ErrorAction SilentlyContinue) {
                if ($item.Extension -ine '.pfx' -and $item.Extension -ine '.p12') { continue }
                $key = $item.FullName.ToLowerInvariant()
                if ($dedupe.ContainsKey($key)) { continue }
                $dedupe[$key] = $true
                $files.Add($item)
                if ($files.Count -gt $MaxCertificates) { throw "Mais de $MaxCertificates certificados encontrados; varredura abortada para evitar execução não limitada." }
            }
        } catch {
            if ($_.Exception.Message -like 'Mais de * certificados encontrados*') { throw }
        }
    }
    return [pscustomobject]@{ Mode = 'DRIVE_SCAN'; ZipPath = $null; Files = @($files) }
}

function Import-PfxEphemeral([System.IO.FileInfo]$File) {
    $password = Get-PasswordFromName $File.Name
    $attempts = New-Object System.Collections.Generic.List[object]
    if ($null -ne $password) { $attempts.Add($password) }
    $attempts.Add('')
    $last = $null
    foreach ($candidate in $attempts) {
        try {
            $flags = [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
            $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($File.FullName, [string]$candidate, $flags)
            if (-not $cert.HasPrivateKey) {
                $cert.Dispose()
                throw 'certificado não contém chave privada'
            }
            return $cert
        } catch { $last = $_.Exception }
    }
    if ($last) { throw $last }
    throw 'não foi possível abrir o PFX/P12'
}

function Get-ChildText($Node, [string]$LocalName) {
    if ($null -eq $Node) { return '' }
    $child = $Node.SelectSingleNode("./*[local-name()='$LocalName']")
    if ($null -eq $child) { return '' }
    return ([string]$child.InnerText).Trim()
}

function New-CccHttpClient([System.Security.Cryptography.X509Certificates.X509Certificate2]$Certificate) {
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.ClientCertificateOptions = [System.Net.Http.ClientCertificateOption]::Manual
    [void]$handler.ClientCertificates.Add($Certificate)
    try { $handler.SslProtocols = [System.Security.Authentication.SslProtocols]::Tls12 } catch { }
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(60)
    return $client
}

function Invoke-CccQuery([System.Net.Http.HttpClient]$Client, [string]$Cpf) {
    $soap = @"
<?xml version="1.0" encoding="utf-8"?>
<soap12:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap12="http://www.w3.org/2003/05/soap-envelope">
  <soap12:Body>
    <nfeDadosMsg xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/CadConsultaCadastro4">
      <ConsCad versao="2.00" xmlns="http://www.portalfiscal.inf.br/nfe">
        <infCons>
          <xServ>CONS-CAD</xServ>
          <UF>$Uf</UF>
          <CPF>$Cpf</CPF>
        </infCons>
      </ConsCad>
    </nfeDadosMsg>
  </soap12:Body>
</soap12:Envelope>
"@
    $content = New-Object System.Net.Http.StringContent($soap, [System.Text.Encoding]::UTF8, 'application/soap+xml')
    try {
        $response = $Client.PostAsync($Endpoint, $content).GetAwaiter().GetResult()
        $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            throw "HTTP $([int]$response.StatusCode): $($response.ReasonPhrase)"
        }
        [xml]$xml = $text
        $fault = $xml.SelectSingleNode("//*[local-name()='Fault']")
        if ($null -ne $fault) { throw ('SOAP Fault: ' + $fault.InnerText.Trim()) }
        $ret = $xml.SelectSingleNode("//*[local-name()='retConsCad']")
        if ($null -eq $ret) { throw 'retConsCad não encontrado na resposta SOAP' }
        $infCons = $ret.SelectSingleNode("./*[local-name()='infCons']")
        if ($null -eq $infCons) { throw 'infCons não encontrado na resposta CCC' }
        $cStat = Get-ChildText $infCons 'cStat'
        $xMotivo = Get-ChildText $infCons 'xMotivo'
        $enabled = New-Object System.Collections.Generic.List[object]
        foreach ($cad in $infCons.SelectNodes("./*[local-name()='infCad']")) {
            $ie = Get-ChildText $cad 'IE'
            $cSit = Get-ChildText $cad 'cSit'
            if ($cSit -ne '1' -or [string]::IsNullOrWhiteSpace($ie)) { continue }
            $name = Get-ChildText $cad 'xNome'
            $ender = $cad.SelectSingleNode("./*[local-name()='ender']")
            $municipio = Get-ChildText $ender 'xMun'
            $cMun = Get-ChildText $ender 'cMun'
            $ufRet = Get-ChildText $cad 'UF'
            if ([string]::IsNullOrWhiteSpace($ufRet)) { $ufRet = $Uf }
            $enabled.Add([pscustomobject]@{
                CPF = $Cpf
                Nome = $name
                IE = $ie
                Municipio = $municipio
                CodigoMunicipio = $cMun
                UF = $ufRet
                cSit = $cSit
            })
        }
        return [pscustomobject]@{ CStat = $cStat; Motivo = $xMotivo; Enabled = @($enabled); RawLength = $text.Length }
    }
    finally { $content.Dispose() }
}

function Invoke-CccWithRetry([System.Net.Http.HttpClient]$Client, [string]$Cpf) {
    $last = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try { return (Invoke-CccQuery -Client $Client -Cpf $Cpf) }
        catch {
            $last = $_.Exception
            if ($attempt -lt 3) { Start-Sleep -Seconds ([Math]::Pow(2, $attempt - 1)) }
        }
    }
    throw $last
}

function Copy-ResultsToDrive([string[]]$DriveRoots, [string]$Source) {
    foreach ($root in $DriveRoots) {
        try {
            $dest = Join-Path $root ("07_TECNOLOGIA E AUTOMAÇÕES\CAFE_CCC\RESULTADOS\" + $MissionId)
            New-Item -ItemType Directory -Path $dest -Force | Out-Null
            foreach ($file in Get-ChildItem -LiteralPath $Source -File) {
                Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $dest $file.Name) -Force
            }
            return $dest
        } catch { }
    }
    return $null
}

$inventory = New-Object System.Collections.Generic.List[object]
$errors = New-Object System.Collections.Generic.List[object]
$enabledRows = New-Object System.Collections.Generic.List[object]
$loaded = New-Object System.Collections.Generic.List[object]
$clientsToDispose = New-Object System.Collections.Generic.List[System.IDisposable]

try {
    $driveRoots = @(Get-GoogleDriveRoots)
    if ($driveRoots.Count -eq 0) { throw 'Google Drive local não foi localizado. Esperado, por exemplo, G:\Meu Drive.' }

    $source = Find-CertificateFiles -Roots $driveRoots
    $files = @($source.Files)
    if ($files.Count -eq 0) { throw 'Nenhum arquivo .pfx/.p12 foi localizado no ZIP esperado nem no Google Drive.' }

    $hashSeen = @{}
    $thumbSeen = @{}
    foreach ($file in $files) {
        $safeName = Get-SafeFileName $file.Name
        try {
            $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($hashSeen.ContainsKey($hash)) { continue }
            $hashSeen[$hash] = $true
            $cert = Import-PfxEphemeral $file
            $cpf = Get-CertificateCpf -Certificate $cert -FileName $file.Name
            $name = Get-CertificateDisplayName $cert
            $validNow = ($cert.NotBefore.ToUniversalTime() -le [DateTime]::UtcNow -and $cert.NotAfter.ToUniversalTime() -ge [DateTime]::UtcNow)
            $thumb = ([string]$cert.Thumbprint).ToUpperInvariant()
            $inventory.Add([pscustomobject]@{
                Arquivo = $safeName
                Nome = $name
                CPF = $cpf
                Thumbprint = $thumb
                ValidoDe = $cert.NotBefore.ToString('o')
                ValidoAte = $cert.NotAfter.ToString('o')
                ValidoAgora = $validNow
                ChavePrivada = $cert.HasPrivateKey
                Estado = if ($cpf) { 'ECPF_IDENTIFICADO' } else { 'CERTIFICADO_SEM_CPF' }
            })
            if ($thumbSeen.ContainsKey($thumb)) { $cert.Dispose(); continue }
            $thumbSeen[$thumb] = $true
            $loaded.Add([pscustomobject]@{ Certificate = $cert; CPF = $cpf; Nome = $name; SafeName = $safeName; ValidNow = $validNow })
        } catch {
            $errors.Add([pscustomobject]@{ Etapa = 'ABRIR_CERTIFICADO'; Arquivo = $safeName; CPF = ''; Erro = $_.Exception.Message })
        }
    }

    $targetCpfs = @($loaded | Where-Object { $_.ValidNow -and -not [string]::IsNullOrWhiteSpace($_.CPF) } | ForEach-Object { $_.CPF } | Sort-Object -Unique)
    if ($targetCpfs.Count -eq 0) { throw 'Nenhum e-CPF válido e legível foi identificado nos certificados encontrados.' }

    $client = $null
    $clientCertificate = $null
    $probeCpf = $targetCpfs[0]
    foreach ($candidate in @($loaded | Where-Object { $_.ValidNow })) {
        $candidateClient = $null
        try {
            $candidateClient = New-CccHttpClient $candidate.Certificate
            $probe = Invoke-CccWithRetry -Client $candidateClient -Cpf $probeCpf
            if ($probe.CStat -eq '257') {
                $candidateClient.Dispose()
                continue
            }
            $client = $candidateClient
            $clientCertificate = $candidate
            break
        } catch {
            if ($candidateClient) { $candidateClient.Dispose() }
            $errors.Add([pscustomobject]@{ Etapa = 'TLS_PROBE'; Arquivo = $candidate.SafeName; CPF = $candidate.CPF; Erro = $_.Exception.Message })
        }
    }
    if ($null -eq $client) { throw 'Nenhum certificado PFX válido conseguiu autenticar a comunicação mTLS com o CCC/SVRS.' }
    $clientsToDispose.Add($client)

    foreach ($cpf in $targetCpfs) {
        try {
            $result = Invoke-CccWithRetry -Client $client -Cpf $cpf
            foreach ($row in @($result.Enabled)) { $enabledRows.Add($row) }
            if ($result.CStat -eq '257') {
                $errors.Add([pscustomobject]@{ Etapa = 'CONSULTA_CCC'; Arquivo = ''; CPF = $cpf; Erro = 'cStat 257 - solicitante não habilitado' })
            }
        } catch {
            $errors.Add([pscustomobject]@{ Etapa = 'CONSULTA_CCC'; Arquivo = ''; CPF = $cpf; Erro = $_.Exception.Message })
        }
        Start-Sleep -Milliseconds $RequestDelayMs
    }

    $dedup = @{}
    $enabledUnique = New-Object System.Collections.Generic.List[object]
    foreach ($row in $enabledRows) {
        $key = (($row.UF + '|' + $row.IE).ToUpperInvariant())
        if ($dedup.ContainsKey($key)) { continue }
        $dedup[$key] = $true
        $enabledUnique.Add($row)
    }

    $inventoryPath = Join-Path $OutputRoot 'certificados_inventario.csv'
    $enabledCsvPath = Join-Path $OutputRoot 'ccc_ies_habilitadas.csv'
    $enabledJsonPath = Join-Path $OutputRoot 'ccc_ies_habilitadas.json'
    $errorsPath = Join-Path $OutputRoot 'erros.csv'
    $summaryPath = Join-Path $OutputRoot 'resultado.json'

    @($inventory) | Export-Csv -LiteralPath $inventoryPath -NoTypeInformation -Encoding UTF8
    @($enabledUnique) | Sort-Object Nome,CPF,IE | Export-Csv -LiteralPath $enabledCsvPath -NoTypeInformation -Encoding UTF8
    Write-Utf8NoBom -Path $enabledJsonPath -Text ((@($enabledUnique) | Sort-Object Nome,CPF,IE | ConvertTo-Json -Depth 8))
    @($errors) | Export-Csv -LiteralPath $errorsPath -NoTypeInformation -Encoding UTF8

    $driveOutput = Copy-ResultsToDrive -DriveRoots $driveRoots -Source $OutputRoot
    $summaryText = "CCC/SVRS concluído: $($inventory.Count) certificados inventariados, $($targetCpfs.Count) CPFs consultados e $($enabledUnique.Count) IEs habilitadas."
    $summary = [ordered]@{
        missionId = $MissionId
        state = 'DONE'
        summary = $summaryText
        sourceMode = [string]$source.Mode
        certificatesInventoried = $inventory.Count
        uniqueCpfsQueried = $targetCpfs.Count
        enabledIEs = $enabledUnique.Count
        errors = $errors.Count
        clientCertificateName = [string]$clientCertificate.Nome
        clientCertificateThumbprint = [string]$clientCertificate.Certificate.Thumbprint
        endpoint = $Endpoint
        filter = 'cSit=1'
        localOutput = $OutputRoot
        driveOutput = $driveOutput
        startedAt = $StartedAt.ToString('o')
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
    }
    Write-Utf8NoBom -Path $summaryPath -Text ($summary | ConvertTo-Json -Depth 8)
    if ($driveOutput) {
        Copy-Item -LiteralPath $summaryPath -Destination (Join-Path $driveOutput 'resultado.json') -Force
    }
    Write-Output ($cafe = 'CAFE_CCC_RESULT ' + ($summary | ConvertTo-Json -Compress -Depth 8))
    exit 0
}
catch {
    $failure = [ordered]@{
        missionId = $MissionId
        state = 'FAILED'
        summary = ('Falha no CCC/SVRS: ' + $_.Exception.Message)
        localOutput = $OutputRoot
        endpoint = $Endpoint
        filter = 'cSit=1'
        startedAt = $StartedAt.ToString('o')
        finishedAt = [DateTimeOffset]::UtcNow.ToString('o')
    }
    try { Write-Utf8NoBom -Path (Join-Path $OutputRoot 'resultado.json') -Text ($failure | ConvertTo-Json -Depth 8) } catch { }
    Write-Output ('CAFE_CCC_RESULT ' + ($failure | ConvertTo-Json -Compress -Depth 8))
    Write-Error $_.Exception.Message
    exit 1
}
finally {
    foreach ($item in $clientsToDispose) { try { $item.Dispose() } catch { } }
    foreach ($item in $loaded) { try { $item.Certificate.Dispose() } catch { } }
}
