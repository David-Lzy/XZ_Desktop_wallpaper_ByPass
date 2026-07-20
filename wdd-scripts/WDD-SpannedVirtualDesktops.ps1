# WinDynamicDesktop post-update script:
# combine per-monitor images into one virtual-screen image, use Windows Span,
# and write the same composite path to every Windows 11 virtual desktop.

$ErrorActionPreference = 'Stop'
$mutex = $null
$lockTaken = $false
$logPath = Join-Path $PSScriptRoot 'WDD-SpannedVirtualDesktops.log'

function Write-SpanLog {
    param([string]$Message)
    $line = '[{0:yyyy-MM-dd HH:mm:ss.fff}] {1}' -f (Get-Date), $Message
    [System.IO.File]::AppendAllText(
        $logPath,
        $line + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
}

try {
    $mutex = [System.Threading.Mutex]::new(
        $false,
        'Local\XZWallpaperBypass-WDD-SpannedVirtualDesktops'
    )
    try {
        $lockTaken = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
    }
    catch [System.Threading.AbandonedMutexException] {
        $lockTaken = $true
    }
    if (-not $lockTaken) {
        Write-SpanLog 'Skipped: another compositor instance is still running.'
        return
    }

    $rawInput = ($Input | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($rawInput)) {
        throw 'WinDynamicDesktop did not provide script parameters.'
    }
    $params = $rawInput | ConvertFrom-Json
    if (-not $params.imagePaths) {
        throw 'WinDynamicDesktop did not provide imagePaths.'
    }

    Add-Type -AssemblyName System.Drawing
    if (-not ('XZWallpaperBypass.DesktopWallpaperBridge' -as [type])) {
        Add-Type @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
namespace XZWallpaperBypass {
    [StructLayout(LayoutKind.Sequential)]
    public struct NativeRect {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;
    }

    public enum WallpaperPosition {
        Center = 0, Tile = 1, Stretch = 2, Fit = 3, Fill = 4, Span = 5
    }

    [ComImport]
    [Guid("B92B56A9-8B55-4E14-9A89-0199BBB6F93B")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    public interface IDesktopWallpaper {
        void SetWallpaper([MarshalAs(UnmanagedType.LPWStr)] string monitorId,
            [MarshalAs(UnmanagedType.LPWStr)] string wallpaper);
        [return: MarshalAs(UnmanagedType.LPWStr)]
        string GetWallpaper([MarshalAs(UnmanagedType.LPWStr)] string monitorId);
        [return: MarshalAs(UnmanagedType.LPWStr)]
        string GetMonitorDevicePathAt(uint monitorIndex);
        uint GetMonitorDevicePathCount();
        NativeRect GetMonitorRECT([MarshalAs(UnmanagedType.LPWStr)] string monitorId);
        void SetBackgroundColor(uint color);
        uint GetBackgroundColor();
        void SetPosition(WallpaperPosition position);
        WallpaperPosition GetPosition();
    }

    [ComImport]
    [Guid("C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD")]
    internal class DesktopWallpaperClass { }

    public sealed class WallpaperMonitor {
        public string DeviceName { get; set; }
        public bool Primary { get; set; }
        public Rectangle Bounds { get; set; }
    }

    public static class DesktopWallpaperBridge {
        public static WallpaperMonitor[] GetMonitors() {
            IDesktopWallpaper api = (IDesktopWallpaper)new DesktopWallpaperClass();
            try {
                uint count = api.GetMonitorDevicePathCount();
                List<WallpaperMonitor> monitors = new List<WallpaperMonitor>();
                for (uint index = 0; index < count; index++) {
                    string id = api.GetMonitorDevicePathAt(index);
                    NativeRect rect = api.GetMonitorRECT(id);
                    monitors.Add(new WallpaperMonitor {
                        DeviceName = id,
                        Primary = rect.Left == 0 && rect.Top == 0,
                        Bounds = new Rectangle(
                            rect.Left,
                            rect.Top,
                            rect.Right - rect.Left,
                            rect.Bottom - rect.Top)
                    });
                }
                return monitors.ToArray();
            }
            finally {
                Marshal.FinalReleaseComObject(api);
            }
        }

        public static void SetSpan() {
            IDesktopWallpaper api = (IDesktopWallpaper)new DesktopWallpaperClass();
            try { api.SetPosition(WallpaperPosition.Span); }
            finally { Marshal.FinalReleaseComObject(api); }
        }
    }
}
'@
    }

    # WDD also uses IDesktopWallpaper monitor indices. Reading both the index
    # order and physical RECT from the same API avoids guessing left/right
    # order and bypasses PowerShell DPI virtualization.
    $screens = @([XZWallpaperBypass.DesktopWallpaperBridge]::GetMonitors())
    if ($screens.Count -lt 1) {
        throw 'Windows did not report any active displays.'
    }
    if ($params.imagePaths.Count -lt $screens.Count) {
        throw ('WDD supplied {0} images for {1} displays.' -f $params.imagePaths.Count, $screens.Count)
    }

    $sourcePaths = @()
    for ($index = 0; $index -lt $screens.Count; $index++) {
        $sourcePath = [System.IO.Path]::GetFullPath([string]$params.imagePaths[$index])
        if (-not [System.IO.File]::Exists($sourcePath)) {
            throw ('Wallpaper image does not exist: {0}' -f $sourcePath)
        }
        $sourcePaths += $sourcePath
    }

    $left = ($screens | ForEach-Object { $_.Bounds.Left } | Measure-Object -Minimum).Minimum
    $top = ($screens | ForEach-Object { $_.Bounds.Top } | Measure-Object -Minimum).Minimum
    $right = ($screens | ForEach-Object { $_.Bounds.Right } | Measure-Object -Maximum).Maximum
    $bottom = ($screens | ForEach-Object { $_.Bounds.Bottom } | Measure-Object -Maximum).Maximum
    $canvasWidth = [int]($right - $left)
    $canvasHeight = [int]($bottom - $top)
    if ($canvasWidth -lt 1 -or $canvasHeight -lt 1) {
        throw 'The virtual display bounds are invalid.'
    }

    $signatureParts = [System.Collections.Generic.List[string]]::new()
    $signatureParts.Add(('{0},{1},{2},{3}' -f $left, $top, $right, $bottom))
    for ($index = 0; $index -lt $screens.Count; $index++) {
        $bounds = $screens[$index].Bounds
        $file = [System.IO.FileInfo]::new($sourcePaths[$index])
        $signatureParts.Add(
            ('{0}|{1}|{2}|{3},{4},{5},{6}' -f
                $file.FullName, $file.Length, $file.LastWriteTimeUtc.Ticks,
                $bounds.X, $bounds.Y, $bounds.Width, $bounds.Height)
        )
    }
    $signatureText = [string]::Join([Environment]::NewLine, $signatureParts)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $signatureBytes = [System.Text.Encoding]::UTF8.GetBytes($signatureText)
        $signatureHash = ([BitConverter]::ToString($sha256.ComputeHash($signatureBytes))).Replace('-', '')
    }
    finally {
        $sha256.Dispose()
    }

    $cacheDir = Join-Path $env:LOCALAPPDATA 'XZWallpaperBypass\WDD-Span'
    [System.IO.Directory]::CreateDirectory($cacheDir) | Out-Null
    $outputPath = Join-Path $cacheDir ('span-{0}.jpg' -f $signatureHash.Substring(0, 24))

    if (-not [System.IO.File]::Exists($outputPath)) {
        $temporaryPath = Join-Path $cacheDir ('span-{0}.tmp.jpg' -f [Guid]::NewGuid().ToString('N'))
        $bitmap = $null
        $graphics = $null
        try {
            $bitmap = [System.Drawing.Bitmap]::new(
                $canvasWidth,
                $canvasHeight,
                [System.Drawing.Imaging.PixelFormat]::Format24bppRgb
            )
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            $graphics.Clear([System.Drawing.Color]::Black)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality

            for ($index = 0; $index -lt $screens.Count; $index++) {
                $image = $null
                try {
                    $image = [System.Drawing.Image]::FromFile($sourcePaths[$index])
                    $bounds = $screens[$index].Bounds
                    $targetWidth = [double]$bounds.Width
                    $targetHeight = [double]$bounds.Height
                    $scale = [Math]::Max($targetWidth / $image.Width, $targetHeight / $image.Height)
                    $cropWidth = $targetWidth / $scale
                    $cropHeight = $targetHeight / $scale
                    $cropX = ([double]$image.Width - $cropWidth) / 2.0
                    $cropY = ([double]$image.Height - $cropHeight) / 2.0
                    $destination = [System.Drawing.Rectangle]::new(
                        [int]($bounds.X - $left),
                        [int]($bounds.Y - $top),
                        $bounds.Width,
                        $bounds.Height
                    )
                    $graphics.DrawImage(
                        $image,
                        $destination,
                        [single]$cropX,
                        [single]$cropY,
                        [single]$cropWidth,
                        [single]$cropHeight,
                        [System.Drawing.GraphicsUnit]::Pixel
                    )
                }
                finally {
                    if ($null -ne $image) { $image.Dispose() }
                }
            }

            $jpegCodec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() |
                Where-Object { $_.MimeType -eq 'image/jpeg' } |
                Select-Object -First 1
            $qualityParameters = [System.Drawing.Imaging.EncoderParameters]::new(1)
            try {
                $qualityParameters.Param[0] = [System.Drawing.Imaging.EncoderParameter]::new(
                    [System.Drawing.Imaging.Encoder]::Quality,
                    [long]95
                )
                $bitmap.Save($temporaryPath, $jpegCodec, $qualityParameters)
            }
            finally {
                $qualityParameters.Dispose()
            }
        }
        finally {
            if ($null -ne $graphics) { $graphics.Dispose() }
            if ($null -ne $bitmap) { $bitmap.Dispose() }
        }
        Move-Item -LiteralPath $temporaryPath -Destination $outputPath -Force
    }

    $desktopKey = 'HKCU:\Control Panel\Desktop'
    Set-ItemProperty -LiteralPath $desktopKey -Name WallpaperStyle -Value '22'
    Set-ItemProperty -LiteralPath $desktopKey -Name TileWallpaper -Value '0'
    [XZWallpaperBypass.DesktopWallpaperBridge]::SetSpan()

    if (-not (Get-Module -ListAvailable -Name VirtualDesktop)) {
        if (-not (Get-PackageProvider -ListAvailable -Name NuGet -ErrorAction SilentlyContinue)) {
            Install-PackageProvider -Name NuGet -Force -Scope CurrentUser | Out-Null
        }
        Install-Module `
            -Name VirtualDesktop `
            -Repository PSGallery `
            -AllowClobber `
            -Force `
            -Scope CurrentUser
    }
    Import-Module VirtualDesktop -Force -ErrorAction Stop
    Set-AllDesktopWallpapers -Path $outputPath -ErrorAction Stop

    if (-not ('XZWallpaperBypass.NativeWallpaper' -as [type])) {
        Add-Type @'
using System.Runtime.InteropServices;
namespace XZWallpaperBypass {
    public static class NativeWallpaper {
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool SystemParametersInfo(
            int action, int parameter, string value, int flags);
    }
}
'@
    }
    $applied = [XZWallpaperBypass.NativeWallpaper]::SystemParametersInfo(
        20,
        0,
        $outputPath,
        3
    )
    if (-not $applied) {
        throw ('SystemParametersInfo failed with Win32 error {0}.' -f
            [Runtime.InteropServices.Marshal]::GetLastWin32Error())
    }

    $state = [ordered]@{
        updated = (Get-Date).ToString('o')
        output = $outputPath
        size = @($canvasWidth, $canvasHeight)
        sources = $sourcePaths
        displays = @(
            $screens | ForEach-Object {
                [ordered]@{
                    device = $_.DeviceName
                    primary = $_.Primary
                    bounds = @($_.Bounds.X, $_.Bounds.Y, $_.Bounds.Width, $_.Bounds.Height)
                }
            }
        )
    }
    $statePath = Join-Path $cacheDir 'state.json'
    [System.IO.File]::WriteAllText(
        $statePath,
        ($state | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false)
    )

    Get-ChildItem -LiteralPath $cacheDir -Filter 'span-*.jpg' -File |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -Skip 8 |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Write-SpanLog ('Applied {0} across {1} displays and all virtual desktops.' -f
        $outputPath, $screens.Count)
}
catch {
    Write-SpanLog ('ERROR: {0}' -f ($_ | Out-String).Trim())
}
finally {
    if ($lockTaken -and $null -ne $mutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
