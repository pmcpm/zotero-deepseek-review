#!/usr/bin/env python3
"""Direct local Zotero bridge client. Credentials stay in the Zotero profile."""
import argparse,configparser,json,os,urllib.request,urllib.error,uuid,hashlib
from pathlib import Path

def profile_path(explicit=None):
    if explicit:return Path(explicit).expanduser().resolve()
    base=Path(os.environ.get('APPDATA',''))/'Zotero/Zotero'
    ini=configparser.ConfigParser();ini.read(base/'profiles.ini',encoding='utf8')
    profiles=[s for s in ini.sections() if s.startswith('Profile')]
    preferred=[s for s in profiles if ini[s].get('Default')=='1']
    candidates=preferred or profiles
    if len(candidates)!=1:raise ValueError('Specify --profile; Zotero profile is ambiguous or absent')
    sec=ini[candidates[0]];p=Path(sec['Path'])
    return (base/p if sec.get('IsRelative')=='1' else p).resolve()

class Bridge:
    def __init__(self,profile=None):
        self.profile=profile_path(profile)
        config=json.loads((self.profile/'codex-literature-bridge/config.json').read_text(encoding='utf8'))
        self._token=config['token'];self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def call(self,request):
        data=dict(request)
        if data.get('op') not in ('health','status') and not data.get('requestID'):raise ValueError('Persist a requestID before sending a bridge mutation')
        req=urllib.request.Request('http://127.0.0.1:23119/codex-bridge/v1',json.dumps(data,ensure_ascii=False).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+self._token})
        try:
            with self.opener.open(req,timeout=180) as r:result=json.load(r)
        except urllib.error.HTTPError as e:raise RuntimeError(f'Bridge HTTP {e.code}: '+e.read().decode('utf8','replace')) from None
        result.pop('signature',None)
        if result.get('state')=='started' and data.get('op')!='status':raise RuntimeError('Request outcome is uncertain; inspect status and document before retrying')
        return result
    def api(self,path):
        with self.opener.open('http://127.0.0.1:23119/api/'+path,timeout=30) as r:return json.load(r)

def register_payload(task_path,evidence_path=None):
    task_path=Path(task_path).resolve();t=json.loads(task_path.read_text(encoding='utf-8-sig'))
    cards=json.loads(Path(evidence_path or task_path.parent/'evidence.json').read_text(encoding='utf-8-sig'))
    return {'op':'registerTask','taskID':t['task_id'],'libraryID':t['zotero']['library_id'],
            'taskRoot':str(task_path.parent),'attachmentRoots':[str(Path(t['supplement_dir']).resolve())],
            'documents':[str(Path(t['output_docx']).resolve())],
            'collectionKey':t['zotero'].get('collection_key'),'collectionName':t['zotero']['collection_name'],
            'allowedItemKeys':[c['zotero_key'] for c in cards if c.get('zotero_key')]}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('health')
    c=sub.add_parser('call');c.add_argument('--request',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    c=sub.add_parser('register');c.add_argument('--task',type=Path,required=True);c.add_argument('--evidence',type=Path);c.add_argument('--output',type=Path,required=True)
    a=p.parse_args();b=Bridge(a.profile)
    if a.command=='health':result=b.call({'op':'health'})
    elif a.command=='call':
        request=json.loads(a.request.read_text(encoding='utf-8-sig'))
        if request.get('op') not in ('health','status') and not request.get('requestID'):
            request['requestID']=uuid.uuid4().hex;a.request.write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf8')
        result=b.call(request)
    else:
        request=register_payload(a.task,a.evidence)
        fingerprint=hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        pending=a.output.parent/('bridge-register-'+fingerprint+'.request.json')
        if pending.exists():request=json.loads(pending.read_text(encoding='utf8'))
        else:
            request['requestID']=uuid.uuid4().hex
            pending.write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf8')
        result=b.call(request)
        if result.get('result',{}).get('ok'):
            task=json.loads(a.task.read_text(encoding='utf-8-sig'));task['zotero']['collection_key']=result['result']['collectionKey']
            a.task.write_text(json.dumps(task,ensure_ascii=False,indent=2),encoding='utf8')
    if a.command!='health':a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    # No token or credentials enter printed summaries.
    print(json.dumps(result,ensure_ascii=True,indent=2))
    if result.get('state')=='failed':raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as e:raise SystemExit(str(e))
