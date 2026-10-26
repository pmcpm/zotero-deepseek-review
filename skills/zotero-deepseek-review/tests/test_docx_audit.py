import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_docx import audit

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
TASK = {'formatting': {'body_font': '宋体', 'latin_font': 'Times New Roman', 'body_pt': 12},
        'outline': [{'title': '相关工作'}]}


class NativeDocumentAudit(unittest.TestCase):
    def fixture(self, root, delay=False, citations=True):
        p = root / 'fixture.docx'
        prefs = json.dumps({'style': {'styleID': 'test-style'}, 'prefs': {'fieldType': 'Field', 'delayCitationUpdates': delay}, 'sessionID': 'independent-test'})
        code = 'ADDIN ZOTERO_ITEM CSL_CITATION {}' if citations else 'Flat text without native citation'
        doc = f'<w:document xmlns:w="{W}"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:instrText>{code}</w:instrText><w:t>Heading</w:t></w:r></w:p><w:p><w:pPr><w:pStyle w:val="Reference"/></w:pPr><w:r><w:instrText>ADDIN ZOTERO_BIBL</w:instrText><w:t>Reference</w:t></w:r></w:p></w:body></w:document>'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('word/document.xml', doc)
            z.writestr('word/styles.xml', f'<w:styles xmlns:w="{W}"/>')
            z.writestr('docProps/custom.xml', '<Properties><property name="ZOTERO_PREF_1"><value>' + escape(prefs) + '</value></property></Properties>')
        return p

    def test_native_fields_and_immediate_preferences_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); p = self.fixture(root)
            self.assertTrue(audit(p, 1, 'test-style')['passed'])
            p = self.fixture(root, delay=True); self.assertFalse(audit(p)['passed'])
            p = self.fixture(root, citations=False); self.assertFalse(audit(p)['passed'])

    def test_wrong_style_or_cluster_count_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.fixture(Path(d))
            self.assertFalse(audit(p, 2)['passed'])
            self.assertFalse(audit(p, style_id='other-style')['passed'])


class EffectiveFormatAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def field(self, number, props='', split=False):
        code = 'ADDIN ZOTERO_ITEM CSL_CITATION {}'
        chunks = [code] if not split else ['ADDIN ZOTERO_ITEM CSL_', 'CITATION {}']
        return ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                + ''.join('<w:r><w:instrText>' + c + '</w:instrText></w:r>' for c in chunks)
                + '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                + '<w:r><w:rPr>' + props + '</w:rPr><w:t>[' + str(number) + ']</w:t></w:r>'
                + '<w:r><w:fldChar w:fldCharType="end"/></w:r>')

    def fixture(self, last_font='', last_props='', citation_props='<w:vertAlign w:val="superscript"/>',
                last_citation_props=None, split=False, extra_styles='', missing_prefs=False,
                defaults_theme=False, normal_fonts=True):
        normal = ('<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:eastAsia="宋体"/>' if normal_fonts else '')
        defaults = '<w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia"/>' if defaults_theme else ''
        styles = f'<w:styles xmlns:w="{W}"><w:docDefaults><w:rPrDefault><w:rPr>{defaults}<w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr>{normal}<w:sz w:val="24"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Body"><w:basedOn w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>{extra_styles}</w:styles>'
        body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>相关工作</w:t></w:r></w:p>'
                + '<w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:r><w:t>第一段 Research</w:t></w:r>'
                + self.field(1, citation_props, split) + '</w:p>'
                + '<w:p><w:pPr><w:pStyle w:val="Body"/></w:pPr><w:r><w:rPr>' + last_font + last_props + '</w:rPr><w:t>最后段 Research</w:t></w:r>'
                + self.field(2, citation_props if last_citation_props is None else last_citation_props, split) + '</w:p>'
                + '<w:p><w:pPr><w:pStyle w:val="Reference"/></w:pPr><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>ADDIN ZOTERO_BIBL</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>[1] Reference</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        path = self.root / 'format.docx'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('word/document.xml', f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
            z.writestr('word/styles.xml', styles)
            if not missing_prefs:
                prefs = json.dumps({'style': {'styleID': 'test-style'}, 'prefs': {'fieldType': 'Field', 'delayCitationUpdates': False}, 'sessionID': 'session'})
                z.writestr('docProps/custom.xml', '<Properties><property name="ZOTERO_PREF_1"><value>' + escape(prefs) + '</value></property></Properties>')
        return path

    def csl(self, superscript=True):
        path = self.root / ('numeric.csl' if superscript else 'apa.csl')
        vertical = ' vertical-align="sup"' if superscript else ''
        path.write_text('<style xmlns="http://purl.org/net/xbiblio/csl"><citation><layout' + vertical + '><text variable="citation-number"/></layout></citation></style>', encoding='utf8')
        return path

    def check(self, path, **kwargs):
        return audit(path, 2, 'test-style', task=TASK, style_path=self.csl(), **kwargs)

    def test_style_inheritance_and_split_instruction_runs(self):
        result = self.check(self.fixture(split=True))
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['citation_fields'], 2)

    def test_last_paragraph_direct_font_is_reported(self):
        result = self.check(self.fixture(last_font='<w:rFonts w:eastAsia="Yu Gothic"/>'))
        self.assertFalse(result['passed'])
        issue = next(i for i in result['issues'] if i['type'] == 'body_font')
        self.assertEqual((issue['paragraph'], issue['expected'], issue['observed']), (3, '宋体', 'Yu Gothic'))

    def test_last_citation_missing_superscript_is_reported(self):
        result = self.check(self.fixture(last_citation_props=''))
        self.assertFalse(result['passed'])
        issue = next(i for i in result['issues'] if i['type'] == 'citation_alignment')
        self.assertEqual((issue['paragraph'], issue['field'], issue['observed']), (3, 2, 'baseline'))

    def test_character_style_inherited_superscript_is_valid(self):
        style = '<w:style w:type="character" w:styleId="Citation"><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'
        result = self.check(self.fixture(citation_props='<w:rStyle w:val="Citation"/>', extra_styles=style))
        self.assertTrue(result['passed'], result)

    def test_apa_requires_baseline_instead_of_numeric_superscript(self):
        path = self.fixture(citation_props='')
        result = audit(path, 2, 'test-style', task=TASK, style_path=self.csl(False))
        self.assertTrue(result['passed'], result)
        result = audit(self.fixture(), 2, 'test-style', task=TASK, style_path=self.csl(False))
        self.assertFalse(result['passed'])

    def test_direct_theme_override_cannot_silently_pass(self):
        result = self.check(self.fixture(last_font='<w:rFonts w:eastAsiaTheme="minorEastAsia"/>'))
        issue = next(i for i in result['issues'] if i['type'] == 'body_font')
        self.assertEqual(issue['theme'], 'minorEastAsia')
        self.assertIsNone(issue['observed'])
        self.assertFalse(result['passed'])

    def test_explicit_style_font_overrides_document_default_theme(self):
        result = self.check(self.fixture(defaults_theme=True))
        self.assertTrue(result['passed'], result)

    def test_body_accidentally_superscript_is_rejected(self):
        result = self.check(self.fixture(last_props='<w:vertAlign w:val="superscript"/>'))
        self.assertTrue(any(i['type'] == 'body_alignment' and i['paragraph'] == 3 for i in result['issues']))

    def test_body_size_mismatch_and_csl_result_size_are_separate(self):
        result = self.check(self.fixture(last_props='<w:sz w:val="22"/>'))
        self.assertTrue(any(i['type'] == 'body_size' and i['observed'] == 11 for i in result['issues']))
        result = self.check(self.fixture(citation_props='<w:vertAlign w:val="superscript"/><w:sz w:val="20"/>'))
        self.assertTrue(result['passed'], result)

    def test_missing_preferences_returns_clear_failure(self):
        result = self.check(self.fixture(missing_prefs=True))
        self.assertFalse(result['passed'])
        self.assertEqual(result['issues'][0]['type'], 'preferences')

    def test_word_report_mixed_font_and_undefined_superscript_fail(self):
        report = {'body_format': {'east_asia': '宋体', 'latin': 'Times New Roman', 'size_pt': 12},
                  'paragraphs': [{'index': 3, 'kind': 'body', 'font_issues': [{'property': 'NameFarEast', 'observed': 'Yu Gothic'}]}],
                  'citations': [{'index': 1, 'paragraph': 2, 'superscript': True}, {'index': 2, 'paragraph': 3, 'superscript': 9999999}]}
        result = self.check(self.fixture(), word_report=report)
        self.assertFalse(result['passed'])
        self.assertTrue(any(i['type'] == 'word_body_format' for i in result['issues']))
        self.assertTrue(any(i['type'] == 'word_citation_alignment' for i in result['issues']))

    def test_com_template_format_has_priority_and_font_aliases_work(self):
        altered = {'formatting': {'body_font': 'Wrong', 'latin_font': 'Wrong', 'body_pt': 20}}
        report = {'body_format': {'east_asia': 'SimSun', 'latin': 'Times New Roman', 'size_pt': 12},
                  'paragraphs': [], 'citations': [{'index': 1, 'superscript': True}, {'index': 2, 'superscript': True}]}
        result = audit(self.fixture(), task=altered, style_path=self.csl(), word_report=report)
        self.assertTrue(result['passed'], result)


if __name__ == '__main__':
    unittest.main()
