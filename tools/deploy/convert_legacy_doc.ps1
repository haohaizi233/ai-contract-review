param([Parameter(Mandatory=$true)][string]$Source,
      [Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$office = $null
$document = $null
try {
    $office = New-Object -ComObject Word.Application
    $office.Visible = $false
    $office.DisplayAlerts = 0
    $office.AutomationSecurity = 3
    $office.Options.UpdateLinksAtOpen = $false
    $document = $office.Documents.Open($Source, $false, $true, $false)
    $document.SaveAs2($Destination, 12)
} finally {
    if ($null -ne $document) {
        try { $document.Close(0) } catch { }
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) } catch { }
    }
    if ($null -ne $office) {
        try { $office.Quit(0) } catch { }
        try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($office) } catch { }
    }
}
