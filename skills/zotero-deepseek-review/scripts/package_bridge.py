"""Build a credential-free Zotero XPI and verify its manifest."""
import argparse,json,zipfile,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def build(output):
    src=ROOT/'assets/zotero-bridge';m=json.loads((src/'manifest.json').read_text(encoding='utf8'))
    z=m['applications']['zotero']
    for f in ('id','update_url','strict_min_version','strict_max_version'):
        if not z.get(f):raise ValueError('Required Zotero manifest field: '+f)
    required=['manifest.json','bootstrap.js','csl-runtime.js','LICENSE']
    for f in required:
        if not (src/f).is_file():raise ValueError('Missing addon resource: '+f)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
        for f in required:archive.write(src/f,f)
    return {'version':m['version'],'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'credential_free':True}
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();print(json.dumps(build(a.output),ensure_ascii=True,indent=2))
