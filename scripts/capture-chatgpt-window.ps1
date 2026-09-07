$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$definition = @'
using System;
using System.Runtime.InteropServices;
public static class PFWindowCaptureNative {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
}
'@
Add-Type -TypeDefinition $definition

$process = Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object -First 1
if ($null -eq $process) {
    [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_SCREENSHOT","status":"WINDOW_NOT_FOUND"}')
    exit 0
}

$rect = New-Object PFWindowCaptureNative+RECT
if (-not [PFWindowCaptureNative]::GetWindowRect($process.MainWindowHandle, [ref]$rect)) {
    [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_SCREENSHOT","status":"RECT_FAILED"}')
    exit 0
}
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 600 -or $height -lt 400) {
    [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_SCREENSHOT","status":"WINDOW_TOO_SMALL"}')
    exit 0
}

$source = New-Object System.Drawing.Bitmap($width, $height)
$graphics = [System.Drawing.Graphics]::FromImage($source)
try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $source.Size)
}
finally {
    $graphics.Dispose()
}

$targetWidth = [Math]::Min(960, $width)
$targetHeight = [int][Math]::Round($height * ($targetWidth / [double]$width))
$target = New-Object System.Drawing.Bitmap($targetWidth, $targetHeight)
$targetGraphics = [System.Drawing.Graphics]::FromImage($target)
try {
    $targetGraphics.DrawImage($source, 0, 0, $targetWidth, $targetHeight)
}
finally {
    $targetGraphics.Dispose()
    $source.Dispose()
}

$stream = New-Object System.IO.MemoryStream
try {
    $target.Save($stream, [System.Drawing.Imaging.ImageFormat]::Jpeg)
    $base64 = [Convert]::ToBase64String($stream.ToArray())
}
finally {
    $stream.Dispose()
    $target.Dispose()
}

$payload = [ordered]@{
    kind = 'CHATGPT_DESKTOP_SCREENSHOT'
    status = 'OK'
    pid = [int]$process.Id
    hwnd = [int64]$process.MainWindowHandle
    originalWidth = $width
    originalHeight = $height
    captureWidth = $targetWidth
    captureHeight = $targetHeight
    jpegBase64 = $base64
}
[Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
