param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
$app=$null;$doc=$null;$ownedInstance=$false
try {
  $absolute=(Resolve-Path -LiteralPath $InputPath).Path
  $body=[IO.File]::ReadAllText($absolute,[Text.Encoding]::UTF8)
  $plain=[regex]::Replace($body,'\[\[R\d{3,}\]\]','')
  $app=New-Object -ComObject Word.Application
  if($app.Documents.Count -ne 0){throw 'Automation instance contains existing documents'}
  $ownedInstance=$true
  $app.Visible=$false;$doc=$app.Documents.Add()
  $doc.Content.Text=$plain
  $count=$doc.ComputeStatistics(0)
  $result=[ordered]@{count=$count;basis='word';exact=$true;body_sha256=(Get-FileHash -LiteralPath $absolute -Algorithm SHA256).Hash.ToLowerInvariant()}
  $result|ConvertTo-Json|Set-Content -LiteralPath $OutputPath -Encoding utf8
  $result|ConvertTo-Json
}finally{if($doc){$doc.Close(0)};if($app -and $ownedInstance){$app.Quit(0)}}
