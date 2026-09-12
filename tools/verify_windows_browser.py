"""Real Windows Chrome acceptance in a dedicated profile (no human-usability claim).

Run inside the explicitly selected synthetic evidence directory, with a pinned
websocket-client installed in ./deps and a public run_url.txt prepared there.
"""
import base64
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

root = Path.cwd()
sys.path.insert(0, str(root / 'deps'))
import websocket


class CDP:
    def __init__(self, url):
        self.socket = websocket.create_connection(url, timeout=30, suppress_origin=True)
        self.counter = 0
        self.responses = {}
        self.hold = False
        self.held = []

    def call(self, method, params=None):
        self.counter += 1
        identity = self.counter
        self.socket.send(json.dumps({'id': identity, 'method': method, 'params': params or {}}))
        while identity not in self.responses:
            message = json.loads(self.socket.recv())
            if 'id' in message:
                self.responses[message['id']] = message
            elif message.get('method') == 'Fetch.requestPaused':
                request = message['params']
                if self.hold and request['request']['url'].endswith('/event-batches'):
                    self.held.append(request['requestId'])
                else:
                    self.call('Fetch.continueRequest', {'requestId': request['requestId']})
        response = self.responses.pop(identity)
        assert 'error' not in response, response.get('error')
        return response.get('result', {})

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'awaitPromise': True, 'returnByValue': True})
        assert 'exceptionDetails' not in result, result.get('exceptionDetails', {}).get('text')
        return result.get('result', {}).get('value')

    def wait(self, expression, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(0.1)
        raise AssertionError('Browser condition timed out: ' + expression)

    def key(self, key, code):
        for kind in ('keyDown', 'keyUp'):
            self.call('Input.dispatchKeyEvent', {'type': kind, 'key': key, 'code': key, 'windowsVirtualKeyCode': code})

    def screenshot(self, name):
        image = self.call('Page.captureScreenshot', {'format': 'png'})
        (root / name).write_bytes(base64.b64decode(image['data']))


assert platform.system() == 'Windows'
url = (root / 'run_url.txt').read_text().strip()
assert url.startswith('http://experiment.localhost:8000/run/')
chrome = next(p for p in [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe', Path(os.environ['LOCALAPPDATA']) / 'Google/Chrome/Application/chrome.exe'] if p.exists())
with socket.socket() as probe:
    probe.settimeout(1)
    assert probe.connect_ex(('127.0.0.1', 9226)) != 0, 'Test debugging port is already in use; do not attach to an existing browser'
profile = root / ('profile_' + uuid.uuid4().hex)
with (root / 'chrome.log').open('ab') as log:
    proc = subprocess.Popen([str(chrome), '--headless=new', '--remote-debugging-port=9226', '--user-data-dir=' + str(profile), '--window-size=1200,900', '--no-first-run', '--no-default-browser-check', '--host-resolver-rules=MAP experiment.localhost 127.0.0.1', 'about:blank'], stdout=log, stderr=log)
client = None
try:
    tabs = None
    for _ in range(100):
        try:
            tabs = json.load(urllib.request.urlopen('http://127.0.0.1:9226/json', timeout=1))
            break
        except OSError:
            time.sleep(0.1)
    assert tabs, 'Dedicated Chrome did not expose its local test endpoint'
    client = CDP(next(t['webSocketDebuggerUrl'] for t in tabs if t['type'] == 'page'))
    version = client.call('Browser.getVersion')['product']
    client.call('Page.enable'); client.call('Runtime.enable')
    client.call('Page.navigate', {'url': url})
    client.wait("!!globalThis.GECBridge && !!document.querySelector('#gec-input-permit') && !document.querySelector('#status')")
    rectangles = "JSON.stringify([...document.querySelectorAll('[id^=gec-input-]')].map(e=>{const r=e.getBoundingClientRect();return [e.id,r.x,r.y,r.width,r.height]}))"
    before = client.evaluate(rectangles)
    client.screenshot('start.png')
    point = client.evaluate("(()=>{const r=document.querySelector('#gec-input-permit').getBoundingClientRect();return {x:r.x+r.width/2,y:r.bottom+r.height/2+4}})()")
    for kind in ('mousePressed', 'mouseReleased'):
        client.call('Input.dispatchMouseEvent', {'type': kind, 'x': point['x'], 'y': point['y'], 'button': 'left', 'clickCount': 1})
    client.wait("document.querySelector('#canvas').getAttribute('aria-label')?.includes('Trial 1:')")
    client.call('Fetch.enable', {'patterns': [{'urlPattern': '*event-batches', 'requestStage': 'Request'}]})
    client.hold = True
    client.key('ArrowLeft', 37)
    client.wait("document.querySelector('#canvas').getAttribute('aria-label')?.includes('Trial 2:')")
    assert client.evaluate(rectangles) == before
    # Polling CDP also consumes request-paused events; the server cannot ACK them.
    deadline = time.monotonic() + 10
    while not client.held and time.monotonic() < deadline:
        client.evaluate('true'); time.sleep(0.1)
    assert client.held, 'No actual upload was held'
    client.screenshot('trial2.png')
    client.key('ArrowRight', 39)
    client.wait("GECBridge.status().state==='finished'")
    records = client.evaluate("new Promise(resolve=>{const r=indexedDB.open('gec-1');r.onsuccess=()=>{const db=r.result,tx=db.transaction('sessions'),q=tx.objectStore('sessions').getAll();tx.oncomplete=()=>{resolve(q.result.find(s=>s.kind==='session'));db.close()}}})")
    assert len(records['records']) == 4 and len(records['pending']) == 4
    assert len({e['event_id'] for e in records['records']}) == 4
    assert client.evaluate(rectangles) == before
    client.hold = False
    for identity in client.held:
        client.call('Fetch.continueRequest', {'requestId': identity})
    client.wait("GECBridge.status().state==='remote_acknowledged'")
    client.screenshot('finished.png')
    assert client.evaluate(rectangles) == before
    # Exercise the same shipped SDK with a small, explicit buffer bound.
    result = client.evaluate("""(async()=>{
      await GECBridge.call('test-close','close',[]);
      const {GEC}=await import('./gec/sdk.js');const c=new GEC(GEP_CONTEXT);await c.prepare();clearInterval(c.timer);await c.begin();
      const ids=[],start=performance.now();for(let i=0;i<64;i++)ids.push(c.record('exp.rt',{trial_id:'bounded',choice:'left',rt_ms:321.5,response_status:'responded'},{id:'rt',version:'1'}).event_id);
      const record_ms=performance.now()-start;let rejected=false;try{c.record('exp.rt',{},{});}catch(e){rejected=e.message==='not_recording_or_backpressure';}
      const before=(await c.get(c.id)).records.length,t=performance.now();await c.commit();const commit_ms=performance.now()-t;
      const saved=await c.get(c.id);await c.finish();await c.flush();const partial=await c.get(c.id);await c.flush();const final=await c.get(c.id);c.close();
      return {id:c.id,rejected,unique:new Set(ids).size,before,committed:saved.records.length,partial_kind:partial.kind,partial_pending:partial.pending.length,final,record_ms,commit_ms};
    })()""")
    assert result['rejected'] and result['unique'] == 64 and result['before'] == 0 and result['committed'] == 64
    assert result['partial_kind'] == 'session' and result['partial_pending'] == 32
    assert result['final'] == {'id': result['id'], 'kind': 'cleaned', 'state': 'remote_acknowledged'}
    report = {'date': '2026-09-12', 'os': platform.platform(), 'chrome': version, 'transport': 'LAN SSH tunnel to Windows loopback; earlier HTTPS physical-network evidence remains separate', 'run_url': url, 'layout_stable': True, 'trial2_and_local_finish_while_upload_held': True, 'session_id': records['id'], 'records': records['records'], 'buffer_test': result, 'independent_human_acceptance': False}
    (root / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('WINDOWS_GODOT_LAYOUT_NONBLOCKING_AND_BACKPRESSURE_VERIFIED')
finally:
    if client:
        client.socket.close()
    proc.terminate()
    proc.wait(timeout=10)
