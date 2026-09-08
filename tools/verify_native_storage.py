"""Read-only audit of one explicitly selected synthetic native queue."""
import json
import sqlite3
import sys
from pathlib import Path
path=Path(sys.argv[1])
assert path.name=='queue.sqlite'
connection=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
for raw, in connection.execute('SELECT value FROM sessions'):
    s=json.loads(raw)
    if s['kind']=='cleaned':
        assert set(s)=={'id','kind','state'}
        print('cleaned: no payload/checkpoint/credential remains')
    elif s['kind']=='session':
        ids={e['event_id'] for e in s['records']}
        assert set(s['pending'])<=ids
        if s['checkpoint']:assert set(s['checkpoint']['dependencies'])<=ids
        print('session:',len(ids),'records;',len(s['pending']),'pending; checkpoint consistent')
