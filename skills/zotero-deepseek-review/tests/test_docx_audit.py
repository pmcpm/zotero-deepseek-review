import sys,tempfile,unittest,zipfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from audit_docx import audit
class NativeDocumentAudit(unittest.TestCase):
 def fixture(self,root,delay=False,citations=True):
  p=root/'fixture.docx';prefs=json.dumps({'style':{'styleID':'test-style'},'prefs':{'fieldType':'Field','delayCitationUpdates':delay},'sessionID':'independent-test'})
  import xml.sax.saxutils as X
  w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
  code='ADDIN ZOTERO_ITEM CSL_CITATION {}' if citations else 'Flat text without native citation'
  doc=f'<w:document xmlns:w="{w}"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:instrText>{code}</w:instrText><w:t>Heading</w:t></w:r></w:p><w:p><w:pPr><w:pStyle w:val="Reference"/></w:pPr><w:r><w:instrText>ADDIN ZOTERO_BIBL</w:instrText><w:t>Reference</w:t></w:r></w:p></w:body></w:document>'
  with zipfile.ZipFile(p,'w') as z:
   z.writestr('word/document.xml',doc);z.writestr('word/styles.xml',f'<w:styles xmlns:w="{w}"/>')
   z.writestr('docProps/custom.xml','<Properties><property name="ZOTERO_PREF_1"><value>'+X.escape(prefs)+'</value></property></Properties>')
  return p
 def test_native_fields_and_immediate_preferences_are_required(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=self.fixture(root);self.assertTrue(audit(p,1,'test-style')['passed'])
   p=self.fixture(root,delay=True);self.assertFalse(audit(p)['passed'])
   p=self.fixture(root,citations=False);self.assertFalse(audit(p)['passed'])
 def test_wrong_style_or_cluster_count_fails(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.fixture(Path(d));self.assertFalse(audit(p,2)['passed']);self.assertFalse(audit(p,style_id='other-style')['passed'])
if __name__=='__main__':unittest.main()
