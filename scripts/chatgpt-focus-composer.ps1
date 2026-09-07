[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [Int64]$Hwnd
)

$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

function Normalize-PFText {
    param([string]$Value)
    if ($null -eq $Value) { return '' }
    $normalized = $Value.Trim().ToLowerInvariant()
    $normalized = $normalized.Normalize([Text.NormalizationForm]::FormD)
    $builder = New-Object Text.StringBuilder
    foreach ($ch in $normalized.ToCharArray()) {
        $category = [Globalization.CharUnicodeInfo]::GetUnicodeCategory($ch)
        if ($category -ne [Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$builder.Append($ch)
        }
    }
    return $builder.ToString().Normalize([Text.NormalizationForm]::FormC)
}

$allowed = @(
    'mensagem para o chatgpt',
    'message chatgpt'
) | ForEach-Object { Normalize-PFText $_ }

try {
    $root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
    if ($null -eq $root) {
        throw 'CHATGPT_DESKTOP_UIA_ROOT_NOT_FOUND'
    }

    $condition = New-Object Windows.Automation.PropertyCondition(
        [Windows.Automation.AutomationElement]::ControlTypeProperty,
        [Windows.Automation.ControlType]::Edit
    )
    $elements = $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)

    $matches = @()
    foreach ($element in $elements) {
        $name = [string]$element.Current.Name
        $help = [string]$element.Current.HelpText
        $automationId = [string]$element.Current.AutomationId
        $candidates = @($name, $help, $automationId) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        $matched = $false
        foreach ($candidate in $candidates) {
            if ($allowed -contains (Normalize-PFText $candidate)) {
                $matched = $true
                break
            }
        }
        if ($matched) {
            $matches += $element
        }
    }

    if ($matches.Count -eq 0) {
        throw 'CHATGPT_DESKTOP_COMPOSER_PLACEHOLDER_NOT_FOUND'
    }
    if ($matches.Count -ne 1) {
        throw 'CHATGPT_DESKTOP_COMPOSER_PLACEHOLDER_AMBIGUOUS'
    }

    $target = $matches[0]
    $target.SetFocus()
    Start-Sleep -Milliseconds 250

    if (-not $target.Current.HasKeyboardFocus) {
        throw 'CHATGPT_DESKTOP_COMPOSER_FOCUS_FAILED'
    }

    $payload = [ordered]@{
        kind = 'CHATGPT_DESKTOP_COMPOSER_FOCUS'
        status = 'OK'
        hwnd = $Hwnd
        name = [string]$target.Current.Name
        helpText = [string]$target.Current.HelpText
        automationId = [string]$target.Current.AutomationId
        controlType = [string]$target.Current.ControlType.ProgrammaticName
        hasKeyboardFocus = [bool]$target.Current.HasKeyboardFocus
    }
    [Console]::Out.WriteLine(($payload | ConvertTo-Json -Compress))
    exit 0
}
catch {
    $message = [string]$_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = 'CHATGPT_DESKTOP_COMPOSER_FOCUS_FAILED'
    }
    [Console]::Error.WriteLine($message)
    exit 2
}
