# Dot-source from task-owned Word automation. This file never attaches to an active Word instance.
function Resolve-CodexThemeFont($Document,$Slot) {
    # Read the NEW document's flat OPC package, so template theme fonts are resolved without changing the template.
    [xml]$package=$Document.WordOpenXML
    $w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    $styles=$package.SelectSingleNode("//*[local-name()='styles']")
    $attribute=if($Slot -eq 'east_asia'){'eastAsiaTheme'}else{'asciiTheme'}
    $fonts=$styles.SelectSingleNode("*[local-name()='docDefaults']/*[local-name()='rPrDefault']/*[local-name()='rPr']/*[local-name()='rFonts']")
    $token=if($fonts){$fonts.GetAttribute($attribute,$w)}else{''}
    $normal=@($styles.SelectNodes("*[local-name()='style']")|Where-Object {$_.GetAttribute('type',$w) -eq 'paragraph' -and $_.GetAttribute('default',$w) -eq '1'})[0]
    $chain=@();$seen=@{}
    while($normal){
        $id=$normal.GetAttribute('styleId',$w)
        if($seen[$id]){throw 'Cycle in template Normal style inheritance'}
        $seen[$id]=$true;$chain=@($normal)+$chain
        $parent=$normal.SelectSingleNode("*[local-name()='basedOn']")
        if(-not $parent){break}
        $parentID=$parent.GetAttribute('val',$w)
        $normal=@($styles.SelectNodes("*[local-name()='style']")|Where-Object {$_.GetAttribute('styleId',$w) -eq $parentID})[0]
    }
    foreach($style in $chain){
        $font=$style.SelectSingleNode("*[local-name()='rPr']/*[local-name()='rFonts']")
        if($font -and $font.HasAttribute($attribute,$w)){$token=$font.GetAttribute($attribute,$w)}
    }
    if(-not $token){throw ('Cannot resolve template theme font slot: '+$Slot)}
    $group=if($token.StartsWith('major')){'majorFont'}else{'minorFont'}
    $scheme=$package.SelectSingleNode("//*[local-name()='themeElements']/*[local-name()='fontScheme']/*[local-name()='$group']")
    if(-not $scheme){throw 'Template theme font scheme missing'}
    $kind=if($Slot -eq 'east_asia'){'ea'}else{'latin'}
    $face=$scheme.SelectSingleNode("*[local-name()='$kind']").GetAttribute('typeface')
    if(-not $face -and $Slot -eq 'east_asia'){
        $language=[int]$Document.Styles.Item(-1).Font.LanguageIDFarEast
        $script=switch($language){1041 {'Jpan'};1042 {'Hang'};1028 {'Hant'};3076 {'Hant'};default {'Hans'}}
        $extra=$scheme.SelectSingleNode("*[local-name()='font' and @script='$script']")
        if($extra){$face=$extra.GetAttribute('typeface')}
    }
    if(-not $face){throw ('Template theme does not define a resolvable '+$Slot+' font')}
    return $face
}

function Get-CodexBodyFormat($Document) {
    $font=$Document.Styles.Item(-1).Font
    $format=[ordered]@{east_asia=[string]$font.NameFarEast;latin=[string]$font.NameAscii;size_pt=[double]$font.Size}
    if($format.east_asia.StartsWith('+')){$format.east_asia=Resolve-CodexThemeFont $Document 'east_asia'}
    if($format.latin.StartsWith('+')){$format.latin=Resolve-CodexThemeFont $Document 'latin'}
    if(-not $format.east_asia -or -not $format.latin -or $format.size_pt -le 0 -or $format.size_pt -gt 200){
        throw 'The template Normal style must resolve to body fonts and size before native citation insertion.'
    }
    return $format
}

function Get-CodexHeadingLevel($Text,$Plan) {
    if($Text -eq '参考文献'){return 1}
    if($Plan.main_headings -contains $Text){return 1}
    if($Plan.headings -contains $Text){
        if($Plan.heading_levels -and $Plan.heading_levels.PSObject.Properties[$Text]){return [int]$Plan.heading_levels.$Text}
        return 2
    }
    return 0
}

function Set-CodexRangeFont($Range,$BodyFormat) {
    # Assign only family/size/colour. Font.Reset or ClearFormatting would also erase CSL vertical alignment and emphasis.
    $Range.Font.NameAscii=$BodyFormat.latin
    $Range.Font.NameOther=$BodyFormat.latin
    $Range.Font.NameBi=$BodyFormat.latin
    $Range.Font.NameFarEast=$BodyFormat.east_asia
    $Range.Font.Size=$BodyFormat.size_pt
    $Range.Font.SizeBi=$BodyFormat.size_pt
    $Range.Font.Color=0
}

function Set-CodexBodyFormatting($Document,$Plan,$BodyFormat) {
    $end=$Document.Content.End
    if($Document.Bookmarks.Exists('CodexBibliographyHeading')){$end=$Document.Bookmarks.Item('CodexBibliographyHeading').Range.Start}
    foreach($paragraph in $Document.Paragraphs){
        if($paragraph.Range.Start -ge $end){continue}
        $text=$paragraph.Range.Text.Trim()
        $level=Get-CodexHeadingLevel $text $Plan
        if($level){$paragraph.Range.Style=$Document.Styles.Item(-1-$level);$paragraph.Range.ParagraphFormat.FirstLineIndent=0;$paragraph.Range.Font.Color=0}
        else{
            $paragraph.Range.Style=$Document.Styles.Item(-1)
            Set-CodexRangeFont $paragraph.Range $BodyFormat
        }
    }
}

function Reset-CodexNativeCitationCache($Document,$PythonPath,$ScriptsPath,$TaskRoot,$Stage) {
    $task=Get-Content -LiteralPath (Join-Path $TaskRoot 'task.json') -Raw|ConvertFrom-Json
    if([IO.Path]::GetFullPath($Document.FullName) -ne [IO.Path]::GetFullPath($task.output_docx)){throw 'Citation cache reset is limited to the task-owned document'}
    $fields=@($Document.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_ITEM*'}|Sort-Object {$_.Code.Start})
    if(-not $fields.Count){return}
    $inputRows=@();for($i=0;$i -lt $fields.Count;$i++){$inputRows+=,[ordered]@{index=$i+1;code=$fields[$i].Code.Text}}
    $inputPath=Join-Path $TaskRoot ('citation-cache-'+$Stage+'.input.json')
    $outputPath=Join-Path $TaskRoot ('citation-cache-'+$Stage+'.output.json')
    ConvertTo-Json -InputObject @($inputRows) -Depth 5|Set-Content -LiteralPath $inputPath -Encoding utf8
    & $PythonPath (Join-Path $ScriptsPath 'citation_cache.py') --input $inputPath --output $outputPath
    if($LASTEXITCODE -ne 0){throw 'Native citation cache reset rejected; no field codes have been changed'}
    $updates=@(Get-Content -LiteralPath $outputPath -Raw|ConvertFrom-Json)
    if($updates.Count -ne $fields.Count){throw 'Citation cache reset result count mismatch'}
    # Verify all live snapshots before any write, then update backwards to preserve field indexes.
    for($i=0;$i -lt $fields.Count;$i++){
        if($updates[$i].index -ne $i+1 -or $fields[$i].Code.Text -ne $inputRows[$i].code){throw 'Citation field changed while preparing native format regeneration'}
    }
    for($i=$fields.Count-1;$i -ge 0;$i--){if($updates[$i].changed){$fields[$i].Code.Text=$updates[$i].code}}
}

function Get-CodexWordFormatReport($Document,$Plan,$BodyFormat,$Stage) {
    $end=$Document.Content.End
    if($Document.Bookmarks.Exists('CodexBibliographyHeading')){$end=$Document.Bookmarks.Item('CodexBibliographyHeading').Range.Start}
    $citations=@($Document.Fields|Where-Object {$_.Code.Text -like '*ZOTERO_ITEM*'}|Sort-Object {$_.Code.Start})
    $paragraphs=@();$paragraphStarts=@{};$index=0
    foreach($paragraph in $Document.Paragraphs){
        $index++;$start=$paragraph.Range.Start;$finish=$paragraph.Range.End
        $paragraphStarts[$start]=$index
        $text=$paragraph.Range.Text.Trim()
        $kind='body'
        if($start -ge $end){$kind='bibliography'}
        elseif((Get-CodexHeadingLevel $text $Plan) -gt 0){$kind='heading'}
        $issues=@()
        if($kind -eq 'body' -and $text){
            # Exclude citation code AND results: CSL may intentionally change citation size or emphasis.
            $segments=@();$cursor=$start
            foreach($field in $citations){
                $a=$field.Code.Start-1;$b=$field.Result.End+1
                if($a -lt $start -or $a -ge $finish){continue}
                if($a -gt $cursor){$segments+=,@($cursor,$a)}
                $cursor=$b
            }
            if($cursor -lt ($finish-1)){$segments+=,@($cursor,($finish-1))}
            foreach($segment in $segments){
                $range=$Document.Range([int]$segment[0],[int]$segment[1])
                if(-not $range.Text.Trim()){continue}
                $actual=[ordered]@{east_asia=[string]$range.Font.NameFarEast;latin=[string]$range.Font.NameAscii;size_pt=[double]$range.Font.Size}
                foreach($property in @('east_asia','latin','size_pt')){
                    if($actual[$property] -ne $BodyFormat[$property]){
                        $issues+=,[ordered]@{property=$property;expected=$BodyFormat[$property];observed=$actual[$property];start=[int]$segment[0];end=[int]$segment[1];text_prefix=$range.Text.Substring(0,[Math]::Min(45,$range.Text.Length))}
                    }
                }
            }
        }
        $paragraphs+=,[ordered]@{index=$index;kind=$kind;text_prefix=$text.Substring(0,[Math]::Min(55,$text.Length));font_issues=@($issues)}
    }
    $fields=@();$index=0
    foreach($field in $citations){
        $index++;$sup=$field.Result.Font.Superscript
        $value=$sup
        if($sup -eq -1){$value=$true}elseif($sup -eq 0){$value=$false}
        $paragraph=$paragraphStarts[$field.Result.Paragraphs.Item(1).Range.Start]
        $fields+=,[ordered]@{index=$index;paragraph=$paragraph;text=$field.Result.Text;superscript=$value}
    }
    return [ordered]@{stage=$Stage;body_format=$BodyFormat;paragraphs=@($paragraphs);citations=@($fields)}
}
