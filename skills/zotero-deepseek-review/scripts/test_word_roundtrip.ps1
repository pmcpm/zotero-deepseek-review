param([Parameter(Mandatory=$true)][string]$TaskDir,[Parameter(Mandatory=$true)][string]$PythonPath,[Parameter(Mandatory=$true)][string]$StyleID,[Parameter(Mandatory=$true)][string]$StylePath)
# Opt-in native integration test. Uses only explicitly registered, NEW fixture documents and notes.
$ErrorActionPreference='Stop'
$scripts=Split-Path -Parent $PSCommandPath
. (Join-Path $scripts 'word_formatting.ps1')
$root=(Resolve-Path -LiteralPath $TaskDir).Path
$task=Get-Content -LiteralPath (Join-Path $root 'task.json') -Raw|ConvertFrom-Json
$fixture=Get-Content -LiteralPath (Join-Path $root 'smoke-plan.json') -Raw|ConvertFrom-Json
$output=[IO.Path]::GetFullPath($task.output_docx)
if(Test-Path -LiteralPath $output){throw 'Native smoke test requires a new output document'}
if(-not $output.StartsWith($root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Fixture document must be inside the task root'}
$plan=[pscustomobject]@{headings=@('格式回归');main_headings=@('相关工作');heading_levels=[pscustomobject]@{'格式回归'=2}}
$app=$null;$doc=$null;$owned=$false;$report=[ordered]@{stages=@();input_synthesis=$false}
function CallNative($op,$label,$noteKey){
    $q=[ordered]@{op=$op;taskID=$task.task_id;documentPath=$output;requestID=('wordtest_'+[guid]::NewGuid().ToString('N'))}
    if($op -eq 'configureDocument'){$q.styleID=$StyleID;$q.locale='zh-CN'}
    if($noteKey){$q.noteKey=$noteKey}
    $request=Join-Path $root ($label+'.request.json');$response=Join-Path $root ($label+'.response.json')
    if(Test-Path -LiteralPath $request){throw 'Request already exists; inspect its result before starting a new fixture task'}
    $q|ConvertTo-Json|Set-Content -LiteralPath $request -Encoding utf8
    & $PythonPath (Join-Path $scripts 'bridge_client.py') call --request $request --output $response
    if($LASTEXITCODE -ne 0){throw ('Native smoke test command failed: '+$label)}
    $doc.Save()
}
function Snapshot($stage){
    $q=Get-CodexWordFormatReport $doc $plan $bodyFormat $stage
    $path=Join-Path $root ('format-'+$stage+'.json')
    $q|ConvertTo-Json -Depth 10|Set-Content -LiteralPath $path -Encoding utf8
    $report.stages+=,[ordered]@{stage=$stage;citations=$q.citations;body_font_issue_count=@($q.paragraphs|ForEach-Object {$_.font_issues}).Count}
    return $path
}
function Verify($stage,$wordReport,$expectedCount,$mustPass){
    $args=@((Join-Path $scripts 'audit_docx.py'),$output,'--output',(Join-Path $root ('audit-'+$stage+'.json')),'--expected-citations',[string]$expectedCount,'--style-id',$StyleID,'--task',(Join-Path $root 'task.json'),'--style-path',$StylePath,'--word-report',$wordReport)
    & $PythonPath @args|Out-Null
    $ok=$LASTEXITCODE -eq 0
    Write-Output ('Format audit '+$stage+': '+$ok)
    if($ok -ne $mustPass){throw ('Unexpected formatting audit result at '+$stage)}
}
function CitationValues {
    return @($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_ITEM*'}|Sort-Object {$_.Code.Start}|ForEach-Object {[ordered]@{code=$_.Code.Text;result=$_.Result.Text}})
}
try{
    $app=New-Object -ComObject Word.Application
    if($app.Documents.Count -ne 0){throw 'Automation instance contains existing user documents'}
    $owned=$true;$app.Visible=$false;$doc=$app.Documents.Add()
    $doc.PageSetup.PageWidth=$app.CentimetersToPoints(21);$doc.PageSetup.PageHeight=$app.CentimetersToPoints(29.7)
    foreach($side in @('TopMargin','BottomMargin','LeftMargin','RightMargin')){$doc.PageSetup.$side=$app.CentimetersToPoints(2.54)}
    $normal=$doc.Styles.Item(-1);$normal.Font.NameAscii='Times New Roman';$normal.Font.NameOther='Times New Roman';$normal.Font.NameFarEast='宋体';$normal.Font.Size=12;$normal.Font.Color=0
    $normal.ParagraphFormat.LineSpacingRule=1;$normal.ParagraphFormat.FirstLineIndent=24
    $bodyFormat=Get-CodexBodyFormat $doc
    $doc.SaveAs2($output,16)
    CallNative 'configureDocument' 'configure' $null
    $doc.Range(0,0).Select();CallNative 'insertNote' 'insert-base' $fixture.base_note_key
    $at=$doc.Content.End-1;$doc.Range($at,$at).InsertBefore("`r参考文献`r")
    $range=$doc.Range($at+1,$at+5)
    if($range.Text -ne '参考文献'){throw 'Fixture bibliography heading missing at the appended position'}
    $doc.Bookmarks.Add('CodexBibliographyHeading',$range)|Out-Null
    $range.Style=$doc.Styles.Item(-2)
    $range.Font.Color=0
    $doc.Range($doc.Content.End-1,$doc.Content.End-1).Select();CallNative 'addBibliography' 'bibliography' $null
    Snapshot 'inserted'|Out-Null
    # Deliberate negative fixtures establish that the checker detects both historical omissions.
    $last=@($doc.Paragraphs|Where-Object {$_.Range.Start -lt $doc.Bookmarks.Item('CodexBibliographyHeading').Range.Start -and $_.Range.Text.Trim()})[-1]
    $last.Range.Font.NameFarEast='Yu Gothic';$last.Range.Font.NameAscii='Yu Gothic'
    $fields=@($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_ITEM*'}|Sort-Object {$_.Code.Start})
    $first=@($doc.Paragraphs|Where-Object {$_.Range.Text.StartsWith('第一段')})[0]
    $emphasis=$doc.Range($first.Range.Start,$first.Range.Start+3)
    $emphasis.Font.Bold=-1;$emphasis.Font.Italic=-1
    $doc.Save();$dirty=Snapshot 'negative-font';Verify 'negative-font' $dirty 2 $false
    $fontAudit=Get-Content -LiteralPath (Join-Path $root 'audit-negative-font.json') -Raw|ConvertFrom-Json
    if(-not @($fontAudit.issues|Where-Object {$_.type -match 'font|size'}).Count){throw 'Mixed font negative fixture was not diagnosed as a font/size issue'}
    Set-CodexBodyFormatting $doc $plan $bodyFormat
    $report.emphasis_preserved=($emphasis.Font.Bold -eq -1 -and $emphasis.Font.Italic -eq -1)
    if(-not $report.emphasis_preserved){throw 'Font normalization erased semantic emphasis'}
    Snapshot 'formatted'|Out-Null
    # The second negative test has correct body fonts and only incorrect citation vertical alignment.
    if($fixture.numeric){$fields[-1].Result.Font.Superscript=0}else{$fields[-1].Result.Font.Superscript=-1}
    $doc.Save();$wrongCitation=Snapshot 'negative-alignment';Verify 'negative-alignment' $wrongCitation 2 $false
    $alignmentAudit=Get-Content -LiteralPath (Join-Path $root 'audit-negative-alignment.json') -Raw|ConvertFrom-Json
    if(-not @($alignmentAudit.issues|Where-Object {$_.type -match 'alignment'}).Count){throw 'Citation negative fixture was not diagnosed as an alignment issue'}
    Reset-CodexNativeCitationCache $doc $PythonPath $scripts $root 'formatted'
    CallNative 'refreshDocument' 'refresh-formatted' $null
    $clean=Snapshot 'refreshed';Verify 'refreshed' $clean 2 $true
    $doc.Range(0,0).Select();CallNative 'insertNote' 'insert-edit' $fixture.edit_note_key
    $report.insert_immediate=CitationValues
    Set-CodexBodyFormatting $doc $plan $bodyFormat
    Reset-CodexNativeCitationCache $doc $PythonPath $scripts $root 'edit'
    CallNative 'refreshDocument' 'refresh-edit' $null
    $report.insert_refreshed=CitationValues
    $values=$report.insert_refreshed
    $report.duplicate_same_result=$values[1].result -eq $values[2].result
    if(-not $report.duplicate_same_result){throw 'Repeated native citation did not retain the same result'}
    if($fixture.numeric){
        $report.renumbered=($values[0].result -eq '[1]' -and $values[1].result -eq '[2]' -and $values[2].result -eq '[2]' -and $values[3].result -eq '[3]')
        if(-not $report.renumbered){throw 'Native numeric citation reordering failed'}
    }
    foreach($field in @($doc.Fields|Where-Object {$_.Code.Text -like ('*'+$fixture.remove_key+'*')}|Sort-Object {$_.Code.Start} -Descending)){
        $doc.Range($field.Code.Start-1,$field.Result.End+1).Delete()|Out-Null
    }
    Set-CodexBodyFormatting $doc $plan $bodyFormat
    Reset-CodexNativeCitationCache $doc $PythonPath $scripts $root 'delete'
    CallNative 'refreshDocument' 'refresh-delete' $null
    $bib=@($doc.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_BIBL*'})[0]
    $report.bibliography_entries_after_delete=@($bib.Result.Text.Split("`r")|Where-Object {$_.Trim()}).Count
    if($report.bibliography_entries_after_delete -ne 2){throw 'Native bibliography did not shrink to two entries'}
    $report.before_reopen=CitationValues
    $doc.Save();$doc.Close(0);$doc=$null
    $doc=$app.Documents.Open($output,$false,$false)
    Reset-CodexNativeCitationCache $doc $PythonPath $scripts $root 'reopened'
    CallNative 'refreshDocument' 'refresh-reopened' $null
    $report.after_reopen=CitationValues
    $final=Snapshot 'final';Verify 'final' $final 3 $true
    $report.reopen_preserved=($report.before_reopen.result -join '|') -eq ($report.after_reopen.result -join '|')
    if(-not $report.reopen_preserved){throw 'Citation results changed after reopening'}
    $doc.ExportAsFixedFormat((Join-Path $root 'preview.pdf'),17)
    $report.passed=$true
}catch{$report.passed=$false;$report.error=$_.Exception.ToString();throw}
finally{
    if($doc){$doc.Close(0)};if($app -and $owned){$app.Quit(0)}
    $report|ConvertTo-Json -Depth 12|Set-Content -LiteralPath (Join-Path $root 'roundtrip-report.json') -Encoding utf8
}
