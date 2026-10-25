"""Convert an approved immutable model body to a Zotero citation note."""
import argparse,hashlib,html,json,re,urllib.parse
from pathlib import Path
from bridge_client import Bridge
from workflow import load_selected,load,normalize_metadata,digest

def prepare(root,profile=None):
    root=Path(root).resolve();selected=load_selected(root)
    task=load(root/'task.json');cards=load(root/'evidence.json')
    body=Path(selected['body_path']).read_bytes().decode('utf8')
    bridge=Bridge(profile);metadata=[];by_id={}
    for card in cards:
        request={'op':'getItem','taskID':task['task_id'],'key':card['zotero_key'],'requestID':'read_'+__import__('uuid').uuid4().hex}
        actual=bridge.call(request)
        if actual.get('state')!='completed':raise ValueError('Zotero readback failed')
        item=actual['result']
        if normalize_metadata(item['data'])!=normalize_metadata(card['metadata']):raise ValueError('Live Zotero metadata changed: '+card['reference_id'])
        if task['zotero']['collection_key'] not in item['data'].get('collections',[]):raise ValueError('Live Zotero collection membership changed: '+card['reference_id'])
        by_id[card['reference_id']]=item
        metadata.append({'uris':[item['uri']],'itemData':item['csl']})
    encode=lambda x:urllib.parse.quote(json.dumps(x,ensure_ascii=False,separators=(',',':')),safe="~()*!.'-")
    def marker(match):
        ids=re.findall(r'\[\[(R\d{3,})\]\]',match.group(0))
        citation={'citationItems':[{'uris':[by_id[rid]['uri']]} for rid in ids],'properties':{}}
        return '<span class="citation" data-citation="'+html.escape(encode(citation),quote=True)+'">引用</span>'
    titles={s['title'] for s in task['outline']}
    paras=[]
    for line in body.splitlines():
        if not line.strip():continue
        # Escape source prose; replace only the model's stable reference markers.
        escaped=html.escape(line)
        converted=re.sub(r'(?:\[\[R\d{3,}\]\])+',marker,escaped)
        tag='h2' if line.strip() in titles else 'p'
        paras.append('<'+tag+'>'+converted+'</'+tag+'>')
    note='<div data-schema-version="2" data-citation-items="'+html.escape(encode(metadata),quote=True)+'">'+''.join(paras)+'</div>'
    request={'op':'createNote','taskID':task['task_id'],'requestID':'note_'+digest({'task_id':task['task_id'],'html':note}),'html':note}
    plan=root/'word';plan.mkdir(exist_ok=True)
    (plan/'create-note.request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf8')
    result=bridge.call(request)
    (plan/'create-note.response.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    if result.get('state')!='completed' or result.get('result',{}).get('ok') is not True:raise ValueError('Create citation note failed or outcome uncertain; inspect saved request status')
    out={'task_id':task['task_id'],'note_key':result['result']['key'],'body_path':selected['body_path'],'body_sha256':selected['body_sha256'],'task_sha256':digest(task),'evidence_sha256':digest(cards),'note_html_sha256':digest(note),'output_docx':task['output_docx'],'headings':list(titles),'reference_ids':re.findall(r'\[\[(R\d{3,})\]\]',body),'citation_clusters':len(re.findall(r'(?:\[\[R\d{3,}\]\])+',body))}
    (plan/'note-plan.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
    return out
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('task_dir',type=Path);p.add_argument('--profile');a=p.parse_args();print(json.dumps(prepare(a.task_dir,a.profile),ensure_ascii=True,indent=2))
