"""Read-only native-field, preferences and effective-format audit for Word."""
import argparse
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
CSL = '{http://purl.org/net/xbiblio/csl}'
FONT_SLOTS = ('ascii', 'hAnsi', 'eastAsia', 'cs')


def _json(value):
    if value is None or isinstance(value, dict):
        return value or {}
    return json.loads(Path(value).read_text(encoding='utf-8-sig'))


def _properties(element):
    if element is None:
        return {}
    return {child.tag.removeprefix(W): {k.removeprefix(W): v for k, v in child.attrib.items()} for child in element}


def _merge(base, layer):
    result = {k: dict(v) for k, v in base.items()}
    for key, value in layer.items():
        if key != 'rFonts':
            result[key] = dict(value)
            continue
        fonts = result.setdefault('rFonts', {})
        for slot in FONT_SLOTS:
            theme = 'cstheme' if slot == 'cs' else slot + 'Theme'
            if theme in value:
                fonts.pop(slot, None); fonts[theme] = value[theme]
            elif slot in value:
                fonts.pop(theme, None); fonts[slot] = value[slot]
        for name, item in value.items():
            if name not in FONT_SLOTS and name not in ('asciiTheme', 'hAnsiTheme', 'eastAsiaTheme', 'cstheme'):
                fonts[name] = item
    return result


class Styles:
    def __init__(self, xml, theme=None):
        self.items = {s.get(W + 'styleId'): s for s in xml.findall(W + 'style')}
        self.default = _properties(xml.find(W + 'docDefaults/' + W + 'rPrDefault/' + W + 'rPr'))
        self.normal = next((s.get(W + 'styleId') for s in self.items.values() if s.get(W + 'type') == 'paragraph' and s.get(W + 'default') in ('1', 'true')), 'Normal')
        self.theme = theme

    def chain(self, style_id, seen=None):
        seen = set() if seen is None else seen
        if not style_id or style_id in seen or style_id not in self.items:
            return []
        seen.add(style_id)
        style = self.items[style_id]; parent = style.find(W + 'basedOn')
        return self.chain(parent.get(W + 'val') if parent is not None else None, seen) + [style]

    def effective(self, style_id, direct):
        result = self.default
        for style in self.chain(style_id or self.normal):
            result = _merge(result, _properties(style.find(W + 'rPr')))
        for style in self.chain(direct.get('rStyle', {}).get('val')):
            result = _merge(result, _properties(style.find(W + 'rPr')))
        return _merge(result, direct)

    def heading(self, style_id):
        for style in self.chain(style_id):
            name = style.find(W + 'name'); label = (name.get(W + 'val', '') if name is not None else '').lower()
            outline = style.find(W + 'pPr/' + W + 'outlineLvl')
            if (outline is not None and int(outline.get(W + 'val', '9')) < 9) or re.match(r'heading|标题|title|subtitle', label):
                return True
        return bool(re.match(r'Heading\d|Title|Subtitle', style_id or '', re.I))

    def font(self, properties, slot):
        fonts = properties.get('rFonts', {}); theme_key = 'cstheme' if slot == 'cs' else slot + 'Theme'
        if theme_key not in fonts:
            return fonts.get(slot), None
        token = fonts[theme_key]
        if self.theme is None:
            return None, token
        element = self.theme.find('.//' + A + ('majorFont' if token.startswith('major') else 'minorFont'))
        if element is None:
            return None, token
        child = element.find(A + ('ea' if slot == 'eastAsia' else 'cs' if slot == 'cs' else 'latin'))
        face = child.get('typeface') if child is not None else None
        if not face and slot == 'eastAsia':
            language = properties.get('lang', {}).get('eastAsia', '')
            script = 'Jpan' if language.startswith('ja') else 'Hang' if language.startswith('ko') else 'Hans'
            child = next((x for x in element.findall(A + 'font') if x.get('script') == script), None)
            face = child.get('typeface') if child is not None else None
        return face or None, token


def _visible_runs(document):
    fields, paragraphs, stack = [], [], []
    for index, paragraph in enumerate(document.findall('.//' + W + 'body/' + W + 'p'), 1):
        props = _properties(paragraph.find(W + 'pPr'))
        entry = {'index': index, 'style_id': props.get('pStyle', {}).get('val'), 'segments': []}
        run_count = [0]
        def walk(node, run=None, run_index=None):
            if node.tag == W + 'fldSimple':
                field = {'index': len(fields) + 1, 'paragraph': index, 'code': node.get(W + 'instr', ''), 'mode': 'result', 'segments': []}
                fields.append(field); stack.append(field)
                for child in node: walk(child, run, run_index)
                stack.pop(); return
            if node.tag == W + 'r':
                run_count[0] += 1
                for child in node:
                    if child.tag != W + 'rPr': walk(child, _properties(node.find(W + 'rPr')), run_count[0])
                return
            if node.tag == W + 'fldChar':
                kind = node.get(W + 'fldCharType')
                if kind == 'begin':
                    field = {'index': len(fields) + 1, 'paragraph': index, 'code': '', 'mode': 'code', 'segments': []}
                    fields.append(field); stack.append(field)
                elif kind == 'separate' and stack: stack[-1]['mode'] = 'result'
                elif kind == 'end' and stack: stack.pop()
                return
            if node.tag == W + 'instrText':
                if stack and stack[-1]['mode'] == 'code': stack[-1]['code'] += node.text or ''
                return
            if node.tag == W + 't' and not any(f['mode'] == 'code' for f in stack):
                segment = {'paragraph': index, 'run': run_index, 'text': node.text or '', 'direct': run or {}, 'fields': list(stack), 'style_id': entry['style_id']}
                entry['segments'].append(segment)
                for field in stack: field['segments'].append(segment)
                return
            for child in node: walk(child, run, run_index)
        for child in paragraph:
            if child.tag != W + 'pPr': walk(child)
        entry['text'] = ''.join(s['text'] for s in entry['segments']); paragraphs.append(entry)
    return fields, paragraphs, bool(stack)


def citation_presentation(style_path):
    if style_path is None: return None
    root = ET.parse(style_path).getroot(); citation = root.find(CSL + 'citation')
    layouts = citation.findall(CSL + 'layout') if citation is not None else []
    if not layouts: raise ValueError('Actual independent CSL requires a citation layout')
    values = {layout.get('vertical-align', 'baseline') for layout in layouts}
    if len(values) != 1 or not values <= {'sup', 'baseline'}:
        raise ValueError('CSL citation layout has mixed or unsupported vertical alignment')
    return 'superscript' if values == {'sup'} else 'baseline'


def _preferences(z):
    try:
        properties = ET.fromstring(z.read('docProps/custom.xml'))
        parts = {int(p.attrib['name'].rsplit('_', 1)[1]): ''.join(p.itertext()) for p in properties if p.attrib.get('name', '').startswith('ZOTERO_PREF_')}
        if not parts: return {}, 'Missing Zotero document preferences'
        return json.loads(''.join(parts[n] for n in sorted(parts))), None
    except (KeyError, ValueError, ET.ParseError) as error:
        return {}, 'Invalid or missing Zotero document preferences: ' + str(error)


def _font_equal(actual, expected):
    aliases = {'simsun': '宋体', 'simhei': '黑体', 'dengxian': '等线'}
    return aliases.get(str(actual).casefold(), str(actual).casefold()) == aliases.get(str(expected).casefold(), str(expected).casefold())


def audit(path, expected_citations=None, style_id=None, hide_doi=False, task=None, style_path=None, word_report=None):
    task, word_report = _json(task), _json(word_report); issues = []
    with zipfile.ZipFile(path) as z:
        doc = ET.fromstring(z.read('word/document.xml')); styles_xml = ET.fromstring(z.read('word/styles.xml'))
        theme = ET.fromstring(z.read('word/theme/theme1.xml')) if 'word/theme/theme1.xml' in z.namelist() else None
        prefs, preference_error = _preferences(z)
    if preference_error: issues.append({'type': 'preferences', 'problem': preference_error})
    fields, paragraphs, unclosed = _visible_runs(doc)
    citations = [f for f in fields if 'CSL_CITATION' in f['code']]; bibliography = [f for f in fields if 'ZOTERO_BIBL' in f['code']]
    codes = ''.join(n.text or '' for n in doc.iter(W + 'instrText'))
    citation_count = len(citations) if fields else codes.count('CSL_CITATION')
    bibliography_count = len(bibliography) if fields else codes.count('ZOTERO_BIBL')
    text = ''.join(n.text or '' for n in doc.iter(W + 't')); colors = {n.attrib.get(W + 'val') for n in doc.iter(W + 'color')}
    styles_used = {n.attrib.get(W + 'val') for n in doc.iter(W + 'pStyle')}
    checks = {
        'native_bibliography': bibliography_count == 1, 'native_citations': citation_count > 0,
        'immediate_updates': prefs.get('prefs', {}).get('delayCitationUpdates') is False,
        'field_type': prefs.get('prefs', {}).get('fieldType') == 'Field', 'session_id': bool(prefs.get('sessionID')),
        'black_visible_run_colors': colors <= {'000000', 'auto'}, 'no_stable_id_placeholders': not re.search(r'\[\[R\d+', text),
        'ascii_hyphens': not re.search(r'[\u2010-\u2015\u2212\ufe63\uff0d]', text), 'real_paragraph_styles': len(styles_used) >= 2,
    }
    if expected_citations is not None: checks['citation_count'] = citation_count == expected_citations
    if style_id: checks['style_id'] = prefs.get('style', {}).get('styleID') == style_id
    if hide_doi: checks['doi_hidden'] = not re.search(r'(?:https?://(?:dx\.)?doi\.org/|\b10\.\d{4,9}/)', text, re.I)
    strict = bool(task or word_report or style_path); styles = Styles(styles_xml, theme)
    expected = task.get('formatting', {})
    body_format = {'east_asia': expected.get('body_font'), 'latin': expected.get('latin_font') or expected.get('body_font'), 'size_pt': expected.get('body_pt')}
    body_format.update({k: v for k, v in word_report.get('body_format', {}).items() if v is not None})
    presentation = citation_presentation(style_path); headings = {'相关工作', '参考文献'} | {s['title'] for s in task.get('outline', [])}
    in_bibliography = False
    for paragraph in paragraphs:
        if paragraph['text'].strip() == '参考文献': in_bibliography = True
        is_heading = paragraph['text'].strip() in headings or styles.heading(paragraph['style_id'])
        has_bib = any('ZOTERO_BIBL' in f['code'] for s in paragraph['segments'] for f in s['fields'])
        kind = 'heading' if is_heading else 'bibliography' if in_bibliography or has_bib else 'body'
        for segment in paragraph['segments']:
            if not segment['text'].strip(): continue
            effective = styles.effective(segment['style_id'], segment['direct'])
            cite = next((f for f in segment['fields'] if 'CSL_CITATION' in f['code']), None)
            vertical = effective.get('vertAlign', {}).get('val', 'baseline')
            location = {'paragraph': paragraph['index'], 'run': segment['run'], 'text_prefix': segment['text'][:50]}
            if cite:
                location['field'] = citations.index(cite) + 1
                if presentation is not None and vertical != presentation:
                    issues.append({**location, 'type': 'citation_alignment', 'expected': presentation, 'observed': vertical})
            elif strict and kind == 'body' and vertical != 'baseline':
                issues.append({**location, 'type': 'body_alignment', 'expected': 'baseline', 'observed': vertical})
            # Citation result font size/emphasis belongs to CSL, not the body font contract.
            if not strict or kind != 'body' or cite: continue
            slots = []
            if re.search(r'[\u2e80-\u9fff\uf900-\ufaff]', segment['text']): slots.append(('eastAsia', body_format['east_asia']))
            if re.search(r'[A-Za-z0-9]', segment['text']): slots.append(('ascii', body_format['latin']))
            for slot, wanted in slots:
                if wanted is None: continue
                observed, token = styles.font(effective, slot)
                if not _font_equal(observed, wanted):
                    issues.append({**location, 'type': 'body_font', 'script': slot, 'expected': wanted, 'observed': observed, **({'theme': token} if token else {})})
            wanted_size = body_format.get('size_pt'); observed_size = effective.get('sz', {}).get('val')
            if wanted_size is not None:
                actual_size = float(observed_size) / 2 if observed_size is not None else None
                if actual_size != float(wanted_size): issues.append({**location, 'type': 'body_size', 'expected': wanted_size, 'observed': actual_size})
    if strict:
        if unclosed or not fields: issues.append({'type': 'field_structure', 'problem': 'Delimited native fields are required for format validation'})
        for index, field in enumerate(citations, 1):
            if not field['segments']: issues.append({'type': 'empty_citation_result', 'field': index, 'paragraph': field['paragraph']})
        for paragraph in word_report.get('paragraphs', []):
            if paragraph.get('kind') == 'body':
                for problem in paragraph.get('font_issues', []):
                    issues.append({'type': 'word_body_format', 'paragraph': paragraph.get('index'), 'text_prefix': paragraph.get('text_prefix', ''), 'detail': problem})
        if presentation is not None:
            for field in word_report.get('citations', []):
                wanted = presentation == 'superscript'
                if type(field.get('superscript')) is not bool or field['superscript'] != wanted:
                    issues.append({'type': 'word_citation_alignment', 'field': field.get('index'), 'paragraph': field.get('paragraph'), 'expected': wanted, 'observed': field.get('superscript')})
        if word_report and len(word_report.get('citations', [])) != citation_count:
            issues.append({'type': 'word_citation_count', 'expected': citation_count, 'observed': len(word_report.get('citations', []))})
        checks['native_field_structure'] = not any(x['type'] == 'field_structure' for x in issues)
        checks['body_font_and_size'] = not any(x['type'] in ('body_font', 'body_size', 'word_body_format') for x in issues)
        checks['body_baseline'] = not any(x['type'] == 'body_alignment' for x in issues)
        if presentation is not None:
            checks['citation_alignment'] = not any(x['type'] in ('citation_alignment', 'word_citation_alignment', 'empty_citation_result') for x in issues)
        checks['word_report_consistent'] = not any(x['type'] == 'word_citation_count' for x in issues)
    return {'passed': all(checks.values()) and not issues, 'checks': checks, 'issues': issues, 'citation_fields': citation_count,
            'bibliography_fields': bibliography_count, 'preferences': prefs, 'paragraph_styles': sorted(styles_used),
            'visible_colors': sorted(colors), 'body_format': body_format, 'citation_presentation': presentation, 'visual_review_required': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('docx', type=Path)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--expected-citations', type=int)
    parser.add_argument('--style-id'); parser.add_argument('--hide-doi', action='store_true')
    parser.add_argument('--task', type=Path); parser.add_argument('--style-path', type=Path); parser.add_argument('--word-report', type=Path)
    args = parser.parse_args(); result = audit(args.docx, args.expected_citations, args.style_id, args.hide_doi, args.task, args.style_path, args.word_report)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=True, indent=2)); raise SystemExit(0 if result['passed'] else 1)
