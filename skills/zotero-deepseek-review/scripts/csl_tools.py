"""Build independent task CSL styles without editing an installed base style.

DOI URL filtering and bilingual shadow data are supplied by csl-runtime.js.
"""
from __future__ import annotations
import argparse, copy, hashlib, json, re, sys
from pathlib import Path
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET

CSL = 'http://purl.org/net/xbiblio/csl'
XML = 'http://www.w3.org/XML/1998/namespace'
ET.register_namespace('', CSL)
N = {'c': CSL}
Q = lambda name: '{'+CSL+'}'+name
HYPHENS = str.maketrans({c:'-' for c in '\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe63\uff0d'})
DISPLAY_FIELDS = set('title title-short container-title container-title-short collection-title original-title event-title event publisher publisher-place event-place authority jurisdiction genre medium archive archive-place archive_location number page volume issue edition chapter-number collection-number version annote abstract status dimensions scale section source'.split())
NAME_FIELDS = set('author editor translator recipient interviewer composer original-author container-author collection-editor editorial-director illustrator director reviewed-author'.split())
DATE_FIELDS = set('issued accessed original-date event-date submitted available-date'.split())
PROTECTED_TOKEN = re.compile(r'((?:[a-z][a-z0-9+.-]*:\S+)|(?:\b10\.\d{4,9}/\S+))',re.I)


def ascii_display_text(text: str) -> str:
    """Normalize display dashes while preserving embedded URI and DOI tokens."""
    return ''.join(part if i%2 else part.translate(HYPHENS) for i,part in enumerate(PROTECTED_TOKEN.split(text)))


def normalize_display_item(item: dict) -> dict:
    item=copy.deepcopy(item)
    for field in DISPLAY_FIELDS:
        if isinstance(item.get(field),str):item[field]=ascii_display_text(item[field])
    for field in NAME_FIELDS:
        for name in item.get(field,[]) if isinstance(item.get(field),list) else []:
            if isinstance(name,dict):
                for key,value in name.items():
                    if isinstance(value,str):name[key]=ascii_display_text(value)
    for field in DATE_FIELDS:
        date=item.get(field)
        if isinstance(date,dict):
            for key in ('raw','literal'):
                if isinstance(date.get(key),str):date[key]=ascii_display_text(date[key])
    # Known CSL field overrides in Extra/note must follow the same display rule.
    if isinstance(item.get('note'),str):
        def line(text):
            match=re.match(r'^(\s*([A-Za-z][A-Za-z -]*?)\s*:\s*)(.*)$',text)
            if match and match[2].strip().lower().replace(' ','-') in DISPLAY_FIELDS:
                return match[1]+ascii_display_text(match[3])
            return text
        item['note']='\n'.join(line(text) for text in item['note'].split('\n'))
    return item
BASE_STYLES = {
    'gbt7714': 'china-national-standard-gb-t-7714-2015-numeric',
    'apa': 'apa',
}

def is_doi_url(value: str) -> bool:
    """Match DOI resolvers or DOI literal forms; never match a normal URL path."""
    value=unquote(str(value or '')).strip()
    if re.match(r'^(?:doi\s*:\s*)?10\.\d{4,9}/\S+', value, re.I): return True
    try:
        u=urlsplit(value if '://' in value else '//'+value)
        return (u.hostname or '').lower().rstrip('.') in {'doi.org','dx.doi.org','www.doi.org','www.dx.doi.org'}
    except ValueError: return False

def filter_item_data(item: dict, hide_doi: bool=False, *, url_policy: str='all', url_required: bool=False) -> dict:
    item=copy.deepcopy(item)
    if url_policy not in {'resource_only','all'}:raise ValueError('Unsupported URL policy')
    resource=url_required or item.get('type') in {'webpage','post','post-weblog','dataset','software'} or bool(re.search(r'\bEB\s*/\s*OL\b',' '.join(str(item.get(f,'')) for f in ['genre','medium']),re.I))
    def keep_url(value):
        return not (hide_doi and is_doi_url(value)) and (url_policy!='resource_only' or resource or is_doi_url(value))
    if item.get('URL') and not keep_url(item['URL']):item.pop('URL',None)
    if url_policy=='resource_only' and not resource:item.pop('accessed',None)
    if isinstance(item.get('note'),str):
        def keep_line(line):
            match=re.match(r'^\s*URL\s*:\s*(.*)$',line,re.I)
            if match and not keep_url(match[1]):return False
            return not (url_policy=='resource_only' and not resource and re.match(r'^\s*(?:accessed|access[- ]date)\s*:',line,re.I))
        item['note']='\n'.join(line for line in item['note'].splitlines() if keep_line(line))
    if hide_doi:
        item.pop('DOI',None)
        if is_doi_url(item.get('URL','')):item.pop('URL',None)
        if isinstance(item.get('note'),str):
            item['note']='\n'.join(line for line in item['note'].splitlines() if not re.match(r'^\s*DOI\s*:',line,re.I) and not (re.match(r'^\s*URL\s*:',line,re.I) and is_doi_url(line.split(':',1)[1])))
    return normalize_display_item(item)

def _parse(source: str) -> ET.Element:
    root=ET.fromstring(source, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    if root.tag != Q('style'):raise ValueError('Expected a CSL style')
    if root.find('c:citation',N) is None or root.find('c:bibliography',N) is None:
        raise ValueError('An independent style with citation and bibliography is required; resolve its parent first')
    return root

def _set_child(parent: ET.Element, name: str, text: str) -> ET.Element:
    child=parent.find('c:'+name,N)
    if child is None:child=ET.SubElement(parent,Q(name))
    child.text=text
    return child

def _remove_doi_output(root: ET.Element) -> None:
    # Leave DOI tests in choose branches: runtime item filtering makes them false
    # and allows the base style's correct URL/retrieval fallback to run.
    doi_groups=[g for g in root.iter(Q('group')) if any('DOI' in n.get('variable','').split() for n in g.iter())]
    for parent in list(root.iter()):
        for node in list(parent):
            if node.tag not in {Q('text'),Q('label'),Q('number')}:
                continue
            variables=node.get('variable','').split()
            if 'DOI' not in variables:continue
            remaining=[v for v in variables if v!='DOI']
            if remaining:node.set('variable',' '.join(remaining))
            else:parent.remove(node)
    # Remove an orphaned literal such as "DOI:" if its containing group lost
    # its only variable. An ordinary URL or other dynamic output keeps the group.
    dynamic={Q('text'),Q('label'),Q('number'),Q('names'),Q('date')}
    for group in reversed(doi_groups):
        remaining=any(n.tag in dynamic and (n.get('variable') or n.get('macro')) for n in group.iter())
        if remaining:continue
        for parent in root.iter():
            if group in list(parent):parent.remove(group);break

def _ascii_ranges(root: ET.Element) -> None:
    for node in root.iter():
        for attr in ['range-delimiter','delimiter','prefix','suffix','value']:
            if attr in node.attrib:node.set(attr,ascii_display_text(node.get(attr,'')))
        if node.tag==Q('term'):
            for term_part in node.iter():
                if term_part.text:term_part.text=ascii_display_text(term_part.text)
        if node.tag==Q('term') and node.get('name') in {'citation-range-delimiter','page-range-delimiter','year-range-delimiter'}:
            node.text='-'
    # A generic locale plus explicit base locale overrides cover CSL default and
    # localized layouts. Append to existing terms; avoid duplicate term names.
    locales=root.findall('c:locale',N)
    if not any(n.get('{'+XML+'}lang') is None for n in locales):
        locale=ET.Element(Q('locale'));info=root.find('c:info',N)
        root.insert(list(root).index(info)+1,locale);locales.append(locale)
    for locale in locales:
        terms=locale.find('c:terms',N)
        if terms is None:terms=ET.SubElement(locale,Q('terms'))
        for name in ['citation-range-delimiter','page-range-delimiter','year-range-delimiter']:
            matches=[t for t in terms.findall('c:term',N) if t.get('name')==name]
            if matches:
                for t in matches:t.text='-'
            else:ET.SubElement(terms,Q('term'),{'name':name}).text='-'

def make_style(source: str, task_id: str, *, hide_doi: bool=False, bilingual: bool=False, english_render: bool=False, title: str|None=None, url_policy: str='resource_only') -> tuple[str,dict]:
    if url_policy not in {'resource_only','all'}:raise ValueError('Unsupported URL policy')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',task_id):raise ValueError('task_id must contain ASCII letters, digits, _ or -')
    root=_parse(source)
    info=root.find('c:info',N)
    if info is None:raise ValueError('CSL info is required')
    base_id=info.findtext('c:id',default='',namespaces=N)
    base_title=info.findtext('c:title',default='CSL',namespaces=N)
    flags={'hide_doi':bool(hide_doi),'bilingual':bool(bilingual and not english_render),'english_render':bool(english_render),'url_policy':url_policy}
    digest=hashlib.sha256((source+json.dumps(flags,sort_keys=True)).encode()).hexdigest()
    style_id=f'http://www.zotero.org/styles/codex-review-{task_id}-{digest[:12]}'
    _set_child(info,'id',style_id)
    _set_child(info,'title',title or f'Codex Review {task_id} - {base_title}')
    for link in list(info.findall('c:link',N)):
        if link.get('rel') in {'self','independent-parent'}:info.remove(link)
    ET.SubElement(info,Q('link'),{'rel':'self','href':style_id})
    if base_id:ET.SubElement(info,Q('link'),{'rel':'template','href':base_id})
    _ascii_ranges(root)
    if hide_doi:_remove_doi_output(root)
    # Remove annote output supplied by another task/previous transform.
    for parent in list(root.iter()):
        for node in list(parent):
            if node.tag==Q('text') and node.get('variable')=='annote':parent.remove(node)
    if bilingual and not english_render:
        for layout in root.findall('c:bibliography/c:layout',N):
            # The base entry's terminal punctuation belongs before the appended
            # translation, not after its closing parenthesis.
            suffix=layout.attrib.pop('suffix',None)
            if suffix:
                entry=ET.Element(Q('group'),{'suffix':suffix})
                for child in list(layout):
                    if child.tag is ET.Comment:continue
                    # Keep the leading numeric label as its own first field so
                    # second-field-align still gives only [n] a left margin.
                    if not len(entry) and child.tag in {Q('text'),Q('number')} and child.get('variable')=='citation-number':continue
                    layout.remove(child);entry.append(child)
                layout.append(entry)
            ET.SubElement(layout,Q('text'),{'variable':'annote','prefix':' (','suffix':')'})
    if english_render:
        for parent in list(root.iter()):
            for node in list(parent):
                if node.tag in {Q('text'),Q('number'),Q('label')} and node.get('variable')=='citation-number':parent.remove(node)
        b=root.find('c:bibliography',N)
        for attr in ['second-field-align','hanging-indent','subsequent-author-substitute','subsequent-author-substitute-rule']:
            b.attrib.pop(attr,None)
        root.set('default-locale','en-US')
    output=ET.tostring(root,encoding='unicode',xml_declaration=True)
    fingerprint=hashlib.sha256(output.encode('utf8')).hexdigest()
    return output,{'style_id':style_id,'base_style_id':base_id,'base_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'style_fingerprint':fingerprint,**flags}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--task-id',required=True);p.add_argument('--hide-doi',action='store_true');p.add_argument('--bilingual',action='store_true');p.add_argument('--english-render',action='store_true');p.add_argument('--title');p.add_argument('--url-policy',choices=['resource_only','all'],default='resource_only')
    a=p.parse_args(argv)
    xml,policy=make_style(a.source.read_text(encoding='utf-8-sig'),a.task_id,hide_doi=a.hide_doi,bilingual=a.bilingual,english_render=a.english_render,title=a.title,url_policy=a.url_policy)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(xml,encoding='utf8')
    a.output.with_suffix('.policy.json').write_text(json.dumps(policy,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(policy,ensure_ascii=False))

if __name__=='__main__':main()
