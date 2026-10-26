"""Test process only. No COM imports or app access."""
from pathlib import Path
import json,sys,time
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
if '--never-read' in sys.argv:time.sleep(10)
for line in sys.stdin:
    req=json.loads(line);payload=req['payload'];method=req['method']
    if method=='hold':time.sleep(10);result={}
    elif method=='echo':result=payload
    elif method=='probe':result={**payload['guard'],'busy':False,'root_ready':True}
    elif method=='read_xml':
        file=Path(payload['effect_file']);value=file.read_text() if file.exists() else 'before'
        result=['<string>'+value+'</string>' for _ in payload['expressions']]
    elif method=='execute':
        op=payload['operation'];file=Path(op['effect_file']);file.write_text(op['id'])
        with file.with_suffix('.calls').open('a') as log:log.write(op['id']+'\n')
        time.sleep(op.get('simulated_delay',0));result={'dispatched':True,'code':0,'output':'','seconds':op.get('simulated_delay',0)}
    else:raise RuntimeError('Unknown simulated method')
    print(json.dumps({'id':req['id'],'ok':True,'result':result}),flush=True)
