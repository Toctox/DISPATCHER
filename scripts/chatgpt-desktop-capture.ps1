[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class PFChatGptCaptureNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@

$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$markerDir = Join-Path $projectPath 'state\chatgpt-foreground'
$marker = Join-Path $markerDir 'fresh-home-calibration-v1.marker'

$process = Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if ($null -eq $process) {
    throw 'CHATGPT_DESKTOP_WINDOW_NOT_FOUND'
}

$hwnd = [IntPtr]$process.MainWindowHandle
[PFChatGptCaptureNative]::ShowWindow($hwnd, 3) | Out-Null
[PFChatGptCaptureNative]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 1200

# One-time, non-sending calibration transition. We only need the fresh home surface
# to locate the Chat/Work selector. The marker prevents later diagnostics from
# repeatedly changing the operator's current conversation.
if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
    [System.Windows.Forms.SendKeys]::SendWait('^{n}')
    Start-Sleep -Milliseconds 6000
    if (-not (Test-Path -LiteralPath $markerDir -PathType Container)) {
        New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
    }
    [IO.File]::WriteAllText($marker, [DateTimeOffset]::UtcNow.ToString('o'), (New-Object System.Text.UTF8Encoding($false)))
}

$rect = New-Object PFChatGptCaptureNative+RECT
if (-not [PFChatGptCaptureNative]::GetWindowRect($hwnd, [ref]$rect)) {
    throw 'CHATGPT_DESKTOP_WINDOW_RECT_FAILED'
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 600 -or $height -lt 400) {
    throw 'CHATGPT_DESKTOP_WINDOW_TOO_SMALL'
}

$source = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($source)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $source.Size)
}
finally {
    $graphics.Dispose()
}

$targetWidth = 960
$targetHeight = [Math]::Max(1, [int][Math]::Round($height * $targetWidth / $width))
$target = New-Object System.Drawing.Bitmap($targetWidth, $targetHeight)
$g2 = [System.Drawing.Graphics]::FromImage($target)
try {
    $g2.DrawImage($source, 0, 0, $targetWidth, $targetHeight)
}
finally {
    $g2.Dispose()
    $source.Dispose()
}

$codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
    Where-Object { $_.MimeType -eq 'image/jpeg' } |
    Select-Object -First 1
$params = New-Object System.Drawing.Imaging.EncoderParameters(1)
$params.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter(
    [System.Drawing.Imaging.Encoder]::Quality,
    [long]55
)
$stream = New-Object System.IO.MemoryStream
try {
    $target.Save($stream, $codec, $params)
    $bytes = $stream.ToArray()
}
finally {
    $stream.Dispose()
    $target.Dispose()
}

$meta = [ordered]@{
    kind = 'CHATGPT_DESKTOP_CAPTURE'
    status = 'OK'
    pid = [int]$process.Id
    hwnd = [int64]$process.MainWindowHandle
    left = $rect.Left
    top = $rect.Top
    width = $width
    height = $height
    previewWidth = $targetWidth
    previewHeight = $targetHeight
    jpegBytes = $bytes.Length
    freshHomeCalibration = $true
}
[Console]::Error.WriteLine(($meta | ConvertTo-Json -Compress))
[Console]::Error.WriteLine('CHATGPT_DESKTOP_CAPTURE_BASE64=' + [Convert]::ToBase64String($bytes))
