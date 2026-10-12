<#
Offline OCR helper for the medical document agent.

Uses the OCR engine that ships with Windows (Windows.Media.Ocr) so scanned PDFs
can be processed without any extra download. Reads a manifest of page images and
writes word-level results (text plus pixel bounding boxes) as JSON.

Manifest format (UTF-8, no BOM):
{
  "languages": ["en-GB", "zh-Hans-CN"],
  "images": [ {"id": "1", "path": "C:\\...\\page-1.png"} ]
}

Result format (UTF-8, no BOM):
{"engine": "windows-media-ocr", "language": "en-GB", "pages": {"1": {"width": 1654, "height": 2339, "words": [...]}}}

Each word is {"text": "...", "x0": 0, "y0": 0, "x1": 0, "y1": 0} with pixel
coordinates in the source image. Callers scale them to PDF points with 72 / dpi.
#>
param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = 'Stop'

function Write-JsonFile {
    param([string]$Path, $Value)
    $json = $Value | ConvertTo-Json -Depth 12 -Compress
    [System.IO.File]::WriteAllText($Path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Write-OcrError {
    param([string]$Message)
    Write-JsonFile -Path $Output -Value @{ error = $Message }
    exit 1
}

try {
    if (-not (Test-Path -LiteralPath $Manifest)) {
        Write-OcrError "OCR manifest not found: $Manifest"
    }

    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

    # WinRT types have to be projected before they can be used from PowerShell 5.1.
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

    $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

    function Await-Operation {
        param($Operation, [Type]$ResultType)
        $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
        $task.Wait(-1) | Out-Null
        $task.Result
    }

    $manifestData = [System.IO.File]::ReadAllText($Manifest, [System.Text.Encoding]::UTF8) | ConvertFrom-Json

    $engine = $null
    $engineTag = $null
    foreach ($tag in @($manifestData.languages)) {
        if ([string]::IsNullOrWhiteSpace($tag)) { continue }
        try {
            $language = New-Object Windows.Globalization.Language($tag)
        } catch {
            continue
        }
        $candidate = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        if ($candidate) { $engine = $candidate; $engineTag = $tag; break }
    }
    if (-not $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
        $engineTag = 'user-profile'
    }
    if (-not $engine) {
        Write-OcrError 'No Windows OCR language pack is installed (available: ' + (([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }) -join ', ') + ')'
    }
    if ($engine.MaxImageDimension -gt 0 -and $engine.MaxImageDimension -lt 100000) {
        $maxDimension = $engine.MaxImageDimension
    } else {
        $maxDimension = 100000
    }

    function Get-SoftwareBitmap {
        param([string]$Path, [int]$MaxDimension)
        $file = Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
        $stream = Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        try {
            $decoder = Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await-Operation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        } finally {
            $stream.Dispose()
        }
        $largest = [Math]::Max($bitmap.PixelWidth, $bitmap.PixelHeight)
        if ($largest -le $MaxDimension) { return $bitmap }

        $scale = $MaxDimension / [double]$largest
        $width = [Math]::Max(1, [int][Math]::Round($bitmap.PixelWidth * $scale))
        $height = [Math]::Max(1, [int][Math]::Round($bitmap.PixelHeight * $scale))
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $transform.ScaledWidth = $width
        $transform.ScaledHeight = $height
        $transform.InterpolationMode = [Windows.Graphics.Imaging.BitmapInterpolationMode]::Fant
        return Await-Operation ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,
            $transform,
            [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage
        )) ([Windows.Graphics.Imaging.SoftwareBitmap])
    }

    $pages = [ordered]@{}
    foreach ($image in @($manifestData.images)) {
        if (-not $image) { continue }
        $id = [string]$image.id
        $path = [string]$image.path
        if (-not (Test-Path -LiteralPath $path)) {
            Write-OcrError "OCR page image not found: $path"
        }

        $bitmap = Get-SoftwareBitmap -Path $path -MaxDimension $maxDimension
        try {
            $decodedWidth = $bitmap.PixelWidth
            $decodedHeight = $bitmap.PixelHeight
            if ($decodedWidth -gt $maxDimension -or $decodedHeight -gt $maxDimension) {
                # PowerShell exposes MaxImageDimension; keep the reported geometry consistent.
                $decodedWidth = [Math]::Min($decodedWidth, $maxDimension)
                $decodedHeight = [Math]::Min($decodedHeight, $maxDimension)
            }
            $result = Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

            $words = New-Object System.Collections.Generic.List[object]
            foreach ($line in @($result.Lines)) {
                foreach ($word in @($line.Words)) {
                    $rect = $word.BoundingRect
                    $words.Add([ordered]@{
                        text = [string]$word.Text
                        x0   = [Math]::Round([double]$rect.X, 2)
                        y0   = [Math]::Round([double]$rect.Y, 2)
                        x1   = [Math]::Round([double]$rect.X + [double]$rect.Width, 2)
                        y1   = [Math]::Round([double]$rect.Y + [double]$rect.Height, 2)
                    })
                }
            }
            $pages[$id] = [ordered]@{
                width  = $decodedWidth
                height = $decodedHeight
                words  = $words
            }
        } finally {
            $bitmap.Dispose()
        }
    }

    Write-JsonFile -Path $Output -Value ([ordered]@{
        engine   = 'windows-media-ocr'
        language = $engineTag
        pages    = $pages
    })
    exit 0
} catch {
    Write-OcrError ($_.Exception.Message + ' | ' + $_.ScriptStackTrace)
}
