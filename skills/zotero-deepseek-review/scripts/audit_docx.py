"""Read-only native field/preferences and visible-format audit for a Word artifact."""
import argparse,json,re,zipfile,xml.etree.ElementTree as ET
from pathlib import Path
W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
def audit(path,expected_citations=None,style_id=None,hide_doi=False):
    with zipfile.ZipFile(path) as z:
        doc=ET.fromstring(z.read('word/document.xml'));styles=ET.fromstring(z.read('word/styles.xml'))
        properties=ET.fromstring(z.read('docProps/custom.xml'))
    parts={int(p.attrib['name'].rsplit('_',1)[1]):''.join(p.itertext()) for p in properties if p.attrib.get('name','').startswith('ZOTERO_PREF_')}
    prefs=json.loads(''.join(parts[n] for n in sorted(parts)))
    codes=''.join(n.text or '' for n in doc.iter(W+'instrText'))
    text=''.join(n.text or '' for n in doc.iter(W+'t'))
    colors={n.attrib.get(W+'val') for n in doc.iter(W+'color')}
    styles_used={n.attrib.get(W+'val') for n in doc.iter(W+'pStyle')}
    checks={
        'native_bibliography':codes.count('ZOTERO_BIBL')==1,
        'native_citations':codes.count('CSL_CITATION')>0,
        'immediate_updates':prefs['prefs'].get('delayCitationUpdates') is False,
        'field_type':prefs['prefs'].get('fieldType')=='Field',
        'session_id':bool(prefs.get('sessionID')),
        'black_visible_run_colors':colors <= {'000000','auto'},
        'no_stable_id_placeholders':not re.search(r'\[\[R\d+',text),
        'ascii_hyphens':not re.search(r'[\u2010-\u2015\u2212\ufe63\uff0d]',text),
        'real_paragraph_styles':len(styles_used)>=2,
    }
    if expected_citations is not None:checks['citation_count']=codes.count('CSL_CITATION')==expected_citations
    if style_id:checks['style_id']=prefs['style']['styleID']==style_id
    if hide_doi:checks['doi_hidden']=not re.search(r'(?:https?://(?:dx\.)?doi\.org/|\b10\.\d{4,9}/)',text,re.I)
    return {'passed':all(checks.values()),'checks':checks,'citation_fields':codes.count('CSL_CITATION'),'bibliography_fields':codes.count('ZOTERO_BIBL'),'preferences':prefs,'paragraph_styles':sorted(styles_used),'visible_colors':sorted(colors),'visual_review_required':True}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('docx',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--expected-citations',type=int);p.add_argument('--style-id');p.add_argument('--hide-doi',action='store_true');a=p.parse_args()
    result=audit(a.docx,a.expected_citations,a.style_id,a.hide_doi);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(result,ensure_ascii=True,indent=2));raise SystemExit(0 if result['passed'] else 1)
