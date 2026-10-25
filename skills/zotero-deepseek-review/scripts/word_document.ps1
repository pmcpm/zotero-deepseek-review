param([Parameter(Mandatory=$true)][string]$TaskDir,[Parameter(Mandatory=$true)][string]$PythonPath,[Parameter(Mandatory=$true)][string]$StyleID,[string]$ProfilePath,[string]$TemplatePath)
$ErrorActionPreference='Stop'
$scripts=Split-Path -Parent $PSCommandPath
$taskRoot=(Resolve-Path -LiteralPath $TaskDir).Path
$task=Get-Content -LiteralPath (Join-Path $taskRoot 'task.json') -Raw|ConvertFrom-Json
$latinFont=$task.formatting.body_font;if($task.formatting.latin_font){$latinFont=$task.formatting.latin_font}
$output=[IO.Path]::GetFullPath($task.output_docx)
if(Test-Path -LiteralPath $output){throw 'Output already exists; choose a new filename to preserve user edits'}
$profileArgs=@();if($ProfilePath){$profileArgs=@('--profile',$ProfilePath)}
& $PythonPath (Join-Path $scripts 'prepare_note.py') $taskRoot @profileArgs
if($LASTEXITCODE -ne 0){throw 'Approved draft and live metadata gate failed'}
$plan=Get-Content -LiteralPath (Join-Path $taskRoot 'word\note-plan.json') -Raw|ConvertFrom-Json
$app=$null;$doc=$null;$ownedInstance=$false
$report=[ordered]@{input_synthesis=$false;body_sha256=$plan.body_sha256;style_id=$StyleID;output_docx=$output}
function BridgeCommand($op){
  $request=[ordered]@{op=$op;taskID=$task.task_id;requestID=([guid]::NewGuid().ToString('N'));documentPath=$doc.FullName}
  if($op -eq 'configureDocument'){$request.styleID=$StyleID;$request.locale='zh-CN'}
  if($op -eq 'insertNote'){$request.noteKey=$plan.note_key}
  $path=Join-Path $taskRoot ('word\'+$op+'.request.json');$response=Join-Path $taskRoot ('word\'+$op+'.response.json')
  if(Test-Path -LiteralPath $path){
    $prior=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
    if($prior.op -ne $op -or $prior.taskID -ne $task.task_id -or $prior.documentPath -ne $doc.FullName){throw 'Saved command belongs to another document; inspect recovery state'}
    if($op -eq 'configureDocument' -and $prior.styleID -ne $StyleID){throw 'Saved style differs; inspect recovery state'}
    if($op -eq 'insertNote' -and $prior.noteKey -ne $plan.note_key){throw 'Saved note differs; inspect recovery state'}
  }else{$request|ConvertTo-Json|Set-Content -LiteralPath $path -Encoding utf8}
  & $PythonPath (Join-Path $scripts 'bridge_client.py') @profileArgs call --request $path --output $response
  if($LASTEXITCODE -ne 0){throw ('Native Zotero command failed: '+$op)}
  $result=Get-Content -LiteralPath $response -Raw|ConvertFrom-Json
  if(-not $result.result.ok){throw ('Native Zotero command failed: '+$op)}
  $doc.Save()
}
function CitationFreeBody($end){
  $cursor=0;$text=''
  $citations=@($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_ITEM*'}|Sort-Object {$_.Code.Start})
  foreach($f in $citations){
    $start=$f.Code.Start-1;$finish=$f.Result.End+1
    if($start -ge $end){continue}
    $text += $doc.Range($cursor,$start).Text;$cursor=$finish
  }
  $text += $doc.Range($cursor,$end).Text
  return [regex]::Replace($text,'\s','')
}
try {
  $app=New-Object -ComObject Word.Application
  if($app.Documents.Count -gt 0){throw 'Automation instance contains existing documents; left open'}
  $ownedInstance=$true;$app.Visible=$false
  if($TemplatePath){$doc=$app.Documents.Add([IO.Path]::GetFullPath($TemplatePath))}else{$doc=$app.Documents.Add()}
  if($doc.Content.Text.Trim().Length -gt 0 -or $doc.Fields.Count -gt 0){throw 'Use an empty formatting template; existing manuscript requires a separate scoped edit'}
  if(-not $TemplatePath){
    $doc.PageSetup.PageWidth=$app.CentimetersToPoints(21);$doc.PageSetup.PageHeight=$app.CentimetersToPoints(29.7)
    foreach($side in @('TopMargin','BottomMargin','LeftMargin','RightMargin')){$doc.PageSetup.$side=$app.CentimetersToPoints($task.formatting.margin_cm)}
    $normal=$doc.Styles.Item(-1);$normal.Font.Name=$latinFont;$normal.Font.NameFarEast=$task.formatting.body_font;$normal.Font.Size=$task.formatting.body_pt;$normal.Font.Color=0
    $normal.ParagraphFormat.LineSpacingRule=1;$normal.ParagraphFormat.LineSpacing=$task.formatting.body_pt*$task.formatting.line_spacing
    $normal.ParagraphFormat.FirstLineIndent=$task.formatting.body_pt*$task.formatting.first_line_characters
    $normal.ParagraphFormat.SpaceAfter=0
    $heading=$doc.Styles.Item(-2);$heading.Font.NameFarEast=$task.formatting.heading_font;$heading.Font.Name=$task.formatting.heading_font;$heading.Font.Size=14;$heading.Font.Color=0
    $heading.ParagraphFormat.FirstLineIndent=0;$heading.ParagraphFormat.KeepWithNext=$true
    $heading.ParagraphFormat.SpaceBefore=12;$heading.ParagraphFormat.SpaceAfter=6
  }
  $doc.SaveAs2($output,16)
  BridgeCommand 'configureDocument'
  $doc.Range(0,0).Select();BridgeCommand 'insertNote'
  $end=$doc.Content.End-1
  $doc.Range($end,$end).InsertBefore("`r参考文献`r")
  $headingRange=$doc.Content.Duplicate
  if(-not $headingRange.Find.Execute('参考文献')){throw 'Bibliography heading missing'}
  $doc.Bookmarks.Add('CodexBibliographyHeading',$headingRange)|Out-Null
  $doc.Range($doc.Content.End-1,$doc.Content.End-1).Select();BridgeCommand 'addBibliography'
  foreach($paragraph in $doc.Paragraphs){
    $text=$paragraph.Range.Text.Trim()
    if($plan.headings -contains $text -or $text -eq '参考文献'){$paragraph.Range.Style=$doc.Styles.Item(-2)}
    elseif($paragraph.Range.Start -lt $doc.Bookmarks.Item('CodexBibliographyHeading').Range.Start){$paragraph.Range.Style=$doc.Styles.Item(-1)}
  }
  $bib=@($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_BIBL*'})
  if($bib.Count -ne 1){throw 'Expected one native bibliography'}
  if($TemplatePath){
    if($task.formatting.reference_style){$refs=$doc.Styles.Item($task.formatting.reference_style)}else{$refs=$bib[0].Result.Style}
  }else{
    $refs=$null;try{$refs=$doc.Styles.Item('Codex References')}catch{$refs=$doc.Styles.Add('Codex References',1)}
    $refs.Font.Name=$latinFont;$refs.Font.NameFarEast=$task.formatting.body_font;$refs.Font.Size=$task.formatting.body_pt;$refs.Font.Color=0
    $refs.ParagraphFormat.LeftIndent=18;$refs.ParagraphFormat.FirstLineIndent=-18;$refs.ParagraphFormat.SpaceAfter=0;$refs.ParagraphFormat.Alignment=0
  }
  $bib[0].Result.Style=$refs
  $referenceStyleName=$refs.NameLocal
  if(-not $TemplatePath){$bib[0].Result.ParagraphFormat.Alignment=0}
  for($i=$doc.Paragraphs.Count;$i -ge 1;$i--){$p=$doc.Paragraphs.Item($i);if($p.Range.Start -lt $doc.Bookmarks.Item('CodexBibliographyHeading').Range.Start -and $p.Range.Text.Trim().Length -eq 0 -and $p.Range.Fields.Count -eq 0){$p.Range.Delete()|Out-Null}}
  $doc.Content.Font.Color=0
  foreach($name in @('Hyperlink','FollowedHyperlink')){try{$doc.Styles.Item($name).Font.Color=0}catch{}}
  $plain=[IO.File]::ReadAllText($plan.body_path,[Text.Encoding]::UTF8)
  $expected=[regex]::Replace([regex]::Replace($plain,'\[\[R\d{3,}\]\]',''),'\s','')
  $actual=CitationFreeBody $doc.Bookmarks.Item('CodexBibliographyHeading').Range.Start
  if($actual -ne $expected){throw 'Document prose differs from immutable model body'}
  $report.prose_matches_model=$true
  $report.paragraph_styles=@($doc.Paragraphs|ForEach-Object {$_.Style.NameLocal})
  $report.citation_fields=@($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_ITEM*'}).Count
  if($report.citation_fields -ne $plan.citation_clusters){throw 'Native citation cluster count differs from the approved note'}
  $report.bibliography_fields=$bib.Count
  $report.field_codes=@($doc.Fields|ForEach-Object {$_.Code.Text})
  $report.text=$doc.Content.Text
  $doc.Save();$doc.Close(0);$doc=$null
  $doc=$app.Documents.Open($output,$false,$false)
  BridgeCommand 'refreshDocument'
  $refreshedBibliography=@($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_BIBL*'})
  $refreshedBibliography[0].Result.Style=$doc.Styles.Item($referenceStyleName)
  if(-not $TemplatePath){$refreshedBibliography[0].Result.ParagraphFormat.Alignment=0}
  $doc.Content.Font.Color=0;$doc.Save()
  $doc.ExportAsFixedFormat((Join-Path $taskRoot 'word\preview.pdf'),17)
  $report.reopen_fields=$doc.Fields.Count
  $report.prose_matches_after_refresh=(CitationFreeBody $doc.Bookmarks.Item('CodexBibliographyHeading').Range.Start) -eq $expected
  $report.passed=($report.reopen_fields -eq ($report.citation_fields+1)) -and $report.prose_matches_after_refresh
  if(-not $report.passed){throw 'Native field count changed after reopening'}
}catch{$report.passed=$false;$report.error=$_.Exception.ToString();throw}
finally{
  if($doc){$doc.Close(0)}
  if($app -and $ownedInstance){$app.Quit(0)}
  $report|ConvertTo-Json -Depth 8|Set-Content -LiteralPath (Join-Path $taskRoot 'word\document-audit.json') -Encoding utf8
}
