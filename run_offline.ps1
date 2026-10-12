param(
    [Parameter(Mandatory = $true)]
    [string]$InputPdf,
    [string]$OutputDirectory = 'data\output',
    [int]$TranslationLimit = 0
)

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Python = Join-Path $Root 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Bundled Python runtime was not found: $Python"
}

$InputPath = (Resolve-Path -LiteralPath $InputPdf).Path
$OutputPath = Join-Path $Root $OutputDirectory
New-Item -ItemType Directory -Force -Path $OutputPath | Out-Null

$Extracted = Join-Path $OutputPath 'extracted.json'
$Structured = Join-Path $OutputPath 'structured.json'
$TermMemory = Join-Path $OutputPath 'term_memory.json'
$Context = Join-Path $OutputPath 'translation_context.json'
$Translations = Join-Path $OutputPath 'translations.json'
$QaReport = Join-Path $OutputPath 'qa_report.json'
$Approved = Join-Path $OutputPath 'approved_translations.json'
$TranslatedPdf = Join-Path $OutputPath 'translated_cn.pdf'
$BilingualPdf = Join-Path $OutputPath 'bilingual.pdf'
$SummaryJson = Join-Path $OutputPath 'summary.json'
$SummaryMarkdown = Join-Path $OutputPath 'summary.md'

& $Python '01_extract_pdf.py' --input $InputPath --output $Extracted
& $Python '02_structure_document.py' --input $Extracted --output $Structured
& $Python '03_build_term_memory.py' --input $Structured --output $TermMemory
& $Python '04_build_translation_context.py' --document $Structured --terms $TermMemory --output $Context

if ($TranslationLimit -gt 0) {
    & $Python '05_translate_document.py' --input $Context --output $Translations --limit $TranslationLimit
} else {
    & $Python '05_translate_document.py' --input $Context --output $Translations
}

& $Python '06_quality_check.py' --translations $Translations --context $Context --output $QaReport
& $Python '07_auto_repair.py' --translations $Translations --context $Context --qa $QaReport --output $Translations --report $QaReport
& $Python '08_approval_gate.py' --translations $Translations --qa $QaReport --output $Approved
& $Python '09_rebuild_pdf.py' --original $InputPath --structured $Structured --translations $Approved --output $TranslatedPdf --bilingual-output $BilingualPdf
& $Python '10_generate_summary.py' --structured $Structured --translations $Approved --output $SummaryJson --markdown $SummaryMarkdown

Write-Output "Pipeline completed."
Write-Output "Translated PDF: $TranslatedPdf"
Write-Output "Bilingual PDF:  $BilingualPdf"
Write-Output "Summary:        $SummaryMarkdown"
