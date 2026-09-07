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
    $normalized = $normalized.Normalize([System.Text.NormalizationForm]::FormD)
    $builder = New-Object System.Text.StringBuilder
    foreach ($ch in $normalized.ToCharArray()) {
        $category = [System.Globalization.CharUnicodeInfo]::GetUnicodeCategory($ch)
        if ($category -ne [System.Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$builder.Append($ch)
        }
    }
    return $builder.ToString().Normalize([System.Text.NormalizationForm]::FormC)
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

    # Chromium/WebView2 may expose a contenteditable composer as Edit or another
    # focusable control. Resolve by the user-visible accessible placeholder instead
    # of relying on a control type or screen coordinate.
    $elements = $root.FindAll(
        [Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.Condition]::TrueCondition
    )

    $matches = @()
    foreach ($element in $elements) {
        try {
            if ($element.Current.IsOffscreen -or -not $element.Current.IsEnabled -or -not $element.Current.IsKeyboardFocusable) {
                continue
            }
            $name = [string]$element.Current.Name
            $help = [string]$element.Current.HelpText
            $automationId = [string]$element.Current.AutomationId
            $candidates = @($name, $help, $automationId) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
            foreach ($candidate in $candidates) {
                if ($allowed -contains (Normalize-PFText $candidate)) {
                    $matches += $element
                    break
                }
            }
        }
        catch [System.Windows.Automation.ElementNotAvailableException] {
            continue
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
    Start-Sleep -Milliseconds 350

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
