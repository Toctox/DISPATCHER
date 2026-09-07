[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class PFWin32 {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);
}
'@

$process = Get-Process | Where-Object {
    $_.MainWindowHandle -ne 0 -and ($_.ProcessName -match 'ChatGPT' -or $_.MainWindowTitle -match 'ChatGPT')
} | Sort-Object StartTime -Descending | Select-Object -First 1

if ($null -eq $process) { throw 'CHATGPT_DESKTOP_WINDOW_NOT_FOUND' }

$hwnd = [IntPtr]$process.MainWindowHandle
[PFWin32]::ShowWindow($hwnd, 9) | Out-Null
Start-Sleep -Milliseconds 250
if (-not [PFWin32]::SetForegroundWindow($hwnd)) { throw 'CHATGPT_DESKTOP_ACTIVATE_FAILED' }
Start-Sleep -Milliseconds 700

$rect = New-Object PFWin32+RECT
if (-not [PFWin32]::GetWindowRect($hwnd, [ref]$rect)) { throw 'CHATGPT_DESKTOP_RECT_FAILED' }
$width = $rect.Right - $rect.Left
$height = $rect.Bottom - $rect.Top
if ($width -lt 600 -or $height -lt 400) { throw 'CHATGPT_DESKTOP_WINDOW_TOO_SMALL' }

# Relative target: center of the composer area near the bottom of the ChatGPT start screen.
$x = [int]($rect.Left + ($width * 0.50))
$y = [int]($rect.Top + ($height * 0.84))
[PFWin32]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 250
[PFWin32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
[PFWin32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 350

$message = 'FACTORY_FOREGROUND_SMOKE_V1 — responda apenas READY'
[System.Windows.Forms.Clipboard]::SetText($message)
[System.Windows.Forms.SendKeys]::SendWait('^v')
Start-Sleep -Milliseconds 450
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Milliseconds 500

[ordered]@{
    outcome = 'FOREGROUND_SMOKE_SENT'
    processId = $process.Id
    windowTitle = $process.MainWindowTitle
    clickX = $x
    clickY = $y
} | ConvertTo-Json -Compress
