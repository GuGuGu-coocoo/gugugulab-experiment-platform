"""Synthetic GEP/1 participant-protocol fault server for the P03R09B suite.

Serves the real participant routes over real HTTP against an in-memory synthetic
store, so both the native (Godot HTTPRequest) and the Web (fetch) clients talk to
a genuine socket. Every response is scripted per admission ordinal:

  event-batches entries: ack | fail | invalid | partial | drop | lost | delay |
                          accepted_null | duplicate_null | accepted_absent |
                          accepted_string
  completion entries:    complete | fail | invalid | pending | drop | delay |
                          missing_absent | missing_null | missing_string |
                          missing_object | missing_nonempty | declaration_absent |
                          declaration_null | declaration_events_string |
                          declaration_segments_null

Control routes (used only by the pytest driver and the browser spec, never by a
client under test):

  POST /control/script   {"sessions": {"0": {"events": [...], "completion": [...]}, "*": {...}}}
  POST /control/session  register a fixture session (old-queue compatibility)
  GET  /control/state    observed sessions, received events and request log
  POST /control/reset    clear the scripts and the observation log

The app prints one JSON line with its real port and then serves until SIGTERM.
This is synthetic engineering evidence: it is not the production server.
"""
import argparse
import json
import re
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL = 'gep/1'
DEFAULT_SCRIPT = {'events': [{'kind': 'ack'}], 'completion': [{'kind': 'complete'}]}
SESSION_ROUTE = re.compile(r'^/v1/participant/sessions/([0-9a-fA-F-]{36})/(event-batches|completion|recover)$')


def _cors_headers():
    return {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Expose-Headers': 'Retry-After',
        'Access-Control-Max-Age': '0',
        'Cache-Control': 'no-store',
    }


class Store:
    def __init__(self):
        self.lock = threading.RLock()
        self.script = {}
        self.sessions = {}
        self.order = []
        self.requests = []

    def new_session(self, binding, proof=''):
        with self.lock:
            session_id = binding.get('session_id') or str(uuid.uuid4())
            self.sessions[session_id] = {
                'session_id': session_id,
                'ordinal': len(self.order),
                'token': binding.get('token') or uuid.uuid4().hex,
                'proof': proof,
                'instance_id': binding.get('instance_id', ''),
                'study_id': binding.get('study_id', ''),
                'release_id': binding.get('release_id', ''),
                'build_id': binding.get('build_id', ''),
                'events': {},
                'batch_requests': 0,
                'completion_requests': 0,
                'completion_declaration': None,
            }
            self.order.append(session_id)
            return self.sessions[session_id]

    def _chosen_script(self, session):
        per_session = self.script.get('sessions', {}) if isinstance(self.script, dict) else {}
        return per_session.get(session['session_id']) or per_session.get(str(session['ordinal'])) or per_session.get('*') or {}

    def next_entry(self, session, key):
        """Consume the next scripted response for this session, if any.

        A script is addressed by the real session id (preferred), the admission
        ordinal or the ``*`` wildcard; an exhausted list falls back to the
        default response.
        """
        with self.lock:
            entries = self._chosen_script(session).get(key)
            if isinstance(entries, list) and entries:
                entry = entries.pop(0)
                return entry if isinstance(entry, dict) else {'kind': 'ack' if key == 'events' else 'complete'}
            return {'kind': 'ack' if key == 'events' else 'complete'}

    def record(self, entry):
        with self.lock:
            self.requests.append(entry)

    def state(self):
        with self.lock:
            return {
                'sessions': {
                    sid: {
                        'session_id': sid,
                        'ordinal': item['ordinal'],
                        'instance_id': item['instance_id'],
                        'study_id': item['study_id'],
                        'release_id': item['release_id'],
                        'build_id': item['build_id'],
                        'events': sorted(item['events']),
                        'batch_requests': item['batch_requests'],
                        'completion_requests': item['completion_requests'],
                        'in_flight': item.get('in_flight', False),
                    }
                    for sid, item in self.sessions.items()
                },
                'requests': list(self.requests),
            }


STORE = Store()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'GEP-Fault/1'

    def log_message(self, *_args):
        pass

    # ---- plumbing ---------------------------------------------------------
    def _body(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode('utf-8'))
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _respond(self, status, payload=None, retry_after=None, drop=False):
        if drop:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.close_connection = True
            return
        body = b'' if payload is None else json.dumps(payload).encode('utf-8')
        try:
            self.send_response(status)
            for key, value in _cors_headers().items():
                self.send_header(key, value)
            if retry_after is not None:
                self.send_header('Retry-After', str(retry_after))
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True

    def _reject(self, status, code, retryable=False):
        self._respond(status, {'code': code, 'retryable': retryable, 'outcome_unknown': False})

    def _failure(self, entry):
        status = int(entry.get('status', 503))
        payload = {'code': str(entry.get('code', 'unavailable')), 'retryable': bool(entry.get('retryable', True)), 'outcome_unknown': False}
        self._respond(status, payload, retry_after=entry.get('retry_after'))

    def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._respond(204)

    def do_GET(self):  # noqa: N802
        path = self.path.split('?', 1)[0]
        if path == '/control/state':
            self._respond(200, STORE.state())
            return
        self._reject(404, 'not_found')

    def do_POST(self):  # noqa: N802
        path = self.path.split('?', 1)[0]
        body = self._body()
        if body is None:
            self._reject(400, 'invalid_request')
            return
        if path == '/control/script':
            with STORE.lock:
                STORE.script = body
            self._respond(200, {'state': 'scripted'})
            return
        if path == '/control/session':
            session = STORE.new_session(body, proof=str(body.get('proof', '')))
            self._respond(200, {'session_id': session['session_id'], 'ordinal': session['ordinal']})
            return
        if path == '/control/reset':
            with STORE.lock:
                STORE.script = {}
                STORE.requests = []
            self._respond(200, {'state': 'reset'})
            return
        match = SESSION_ROUTE.match(path)
        if match:
            session = STORE.sessions.get(match.group(1))
            if session is None:
                self._reject(403, 'session_unavailable')
                return
            action = match.group(2)
            if action == 'recover':
                # The original private proof is the credential on this route.
                if session['proof'] and str(body.get('proof', '')) != session['proof']:
                    self._reject(403, 'recovery_denied')
                    return
                # A successful reauthentication rotates the session credential.
                session['token'] = 'renewed-' + session['token']
                self._respond(200, {'token': session['token'], 'task_finished': False})
                return
            token = self.headers.get('Authorization', '')
            if token != 'Bearer ' + session['token']:
                self._reject(403, 'session_unavailable')
                return
            if action == 'event-batches':
                self._event_batches(session, body)
                return
            if action == 'completion':
                self._completion(session, body)
                return
        if path == '/v1/participant/sessions':
            self._admission(body)
            return
        self._reject(404, 'not_found')

    # ---- participant protocol --------------------------------------------
    def _admission(self, body):
        for key in ('instance_id', 'study_id', 'release_id', 'build_id'):
            if not isinstance(body.get(key), str) or not body.get(key):
                self._reject(400, 'invalid_request')
                return
        session = STORE.new_session(body, proof=str(body.get('proof', '')))
        STORE.record({'kind': 'admission', 'session_id': session['session_id'], 'ordinal': session['ordinal']})
        self._respond(200, {
            'protocol_version': PROTOCOL,
            'session_id': session['session_id'],
            'token': session['token'],
            'instance_id': session['instance_id'],
            'study_id': session['study_id'],
            'release_id': session['release_id'],
            'build_id': session['build_id'],
        })

    def _event_batches(self, session, body):
        entry = STORE.next_entry(session, 'events')
        kind = str(entry.get('kind', 'ack'))
        batch_id = body.get('batch_id')
        events = body.get('events') if isinstance(body.get('events'), list) else []
        with STORE.lock:
            session['batch_requests'] += 1
        STORE.record({'kind': 'event-batches', 'session_id': session['session_id'], 'ordinal': session['ordinal'], 'batch_id': batch_id, 'events': len(events), 'response': kind})
        if kind == 'delay':
            with STORE.lock:
                session['in_flight'] = True
            try:
                time.sleep(max(0, int(entry.get('delay_ms', 1000))) / 1000.0)
            finally:
                with STORE.lock:
                    session['in_flight'] = False
        if kind == 'drop':
            self._record_events(session, events)
            self._respond(200, drop=True)
            return
        if kind == 'lost':
            # The server really stored the batch but the client gets no usable
            # ACK (a 200 without the required coverage), so a later retry must
            # see duplicates and only then make progress.
            self._record_events(session, events)
            self._respond(200, {'protocol_version': PROTOCOL, 'session_id': session['session_id']})
            return
        if kind == 'fail':
            self._failure(entry)
            return
        if kind in ('accepted_null', 'duplicate_null', 'accepted_absent', 'accepted_string'):
            # A real 200 with a malformed list shape: the exact coverage cannot
            # be established, so a strict client must reject it. The server still
            # stored the events, so a later retry sees duplicates.
            accepted, duplicate = self._record_events(session, events)
            payload = {'protocol_version': PROTOCOL, 'instance_id': session['instance_id'],
                       'session_id': session['session_id'], 'batch_id': batch_id}
            if kind == 'accepted_null':
                payload['accepted'] = None
                payload['duplicate'] = duplicate
            elif kind == 'duplicate_null':
                payload['accepted'] = accepted
                payload['duplicate'] = None
            elif kind == 'accepted_absent':
                payload['duplicate'] = duplicate
            else:
                payload['accepted'] = ''
                payload['duplicate'] = duplicate
            self._respond(200, payload)
            return
        accepted, duplicate = self._record_events(session, events)
        if kind == 'invalid':
            # A well-formed 200 that does not cover the batch (wrong batch id).
            self._respond(200, {
                'protocol_version': PROTOCOL, 'instance_id': session['instance_id'], 'session_id': session['session_id'],
                'batch_id': '00000000-0000-0000-0000-000000000000', 'accepted': accepted, 'duplicate': duplicate,
            })
            return
        if kind == 'partial':
            # A 200 that only covers the first event of the batch.
            first = accepted[:1] + duplicate[:1]
            self._respond(200, {
                'protocol_version': PROTOCOL, 'instance_id': session['instance_id'], 'session_id': session['session_id'],
                'batch_id': batch_id, 'accepted': first, 'duplicate': [],
            })
            return
        self._respond(200, {
            'protocol_version': PROTOCOL, 'instance_id': session['instance_id'], 'session_id': session['session_id'],
            'batch_id': batch_id, 'accepted': accepted, 'duplicate': duplicate,
        })

    def _record_events(self, session, events):
        accepted, duplicate = [], []
        with STORE.lock:
            for event in events:
                if not isinstance(event, dict) or not isinstance(event.get('event_id'), str):
                    continue
                if event['event_id'] in session['events']:
                    duplicate.append(event['event_id'])
                else:
                    session['events'][event['event_id']] = event
                    accepted.append(event['event_id'])
        return accepted, duplicate

    def _completion(self, session, body):
        entry = STORE.next_entry(session, 'completion')
        kind = str(entry.get('kind', 'complete'))
        event_ids = body.get('event_ids') if isinstance(body.get('event_ids'), list) else []
        segment_ids = body.get('segment_ids') if isinstance(body.get('segment_ids'), list) else []
        with STORE.lock:
            session['completion_requests'] += 1
            session['completion_declaration'] = {'event_ids': event_ids, 'segment_ids': segment_ids}
        STORE.record({'kind': 'completion', 'session_id': session['session_id'], 'ordinal': session['ordinal'], 'events': len(event_ids), 'response': kind})
        if kind == 'delay':
            with STORE.lock:
                session['in_flight'] = True
            try:
                time.sleep(max(0, int(entry.get('delay_ms', 1000))) / 1000.0)
            finally:
                with STORE.lock:
                    session['in_flight'] = False
        if kind == 'drop':
            self._respond(200, drop=True)
            return
        if kind == 'fail':
            self._failure(entry)
            return
        if kind in ('missing_absent', 'missing_null', 'missing_string', 'missing_object',
                    'missing_nonempty', 'declaration_absent', 'declaration_null',
                    'declaration_events_string', 'declaration_segments_null'):
            # A real 200 whose completion ACK has a missing/null/wrongly typed
            # field: the declaration and the empty missing list must be complete
            # arrays, so a strict client must reject it and keep its raw data.
            payload = self._completion_body(session, 'complete', [], {'event_ids': event_ids, 'segment_ids': segment_ids})
            if kind == 'missing_absent':
                payload.pop('missing')
            elif kind == 'missing_null':
                payload['missing'] = None
            elif kind == 'missing_string':
                payload['missing'] = ''
            elif kind == 'missing_object':
                payload['missing'] = {}
            elif kind == 'missing_nonempty':
                payload['missing'] = [event_ids[0]] if event_ids else ['00000000-0000-0000-0000-000000000000']
            elif kind == 'declaration_absent':
                payload.pop('declaration')
            elif kind == 'declaration_null':
                payload['declaration'] = None
            elif kind == 'declaration_events_string':
                payload['declaration'] = {'event_ids': event_ids[0] if event_ids else '', 'segment_ids': segment_ids}
            else:
                payload['declaration'] = {'event_ids': event_ids, 'segment_ids': None}
            self._respond(200, payload)
            return
        with STORE.lock:
            received = set(session['events'])
        missing = sorted(set(event_ids) - received)
        if kind == 'pending':
            self._respond(200, self._completion_body(session, 'pending', missing or [event_ids[0]] if event_ids else [], {'event_ids': event_ids, 'segment_ids': segment_ids}))
            return
        if kind == 'invalid':
            wrong = {'event_ids': list(event_ids) + ['00000000-0000-0000-0000-000000000000'], 'segment_ids': list(segment_ids)}
            self._respond(200, self._completion_body(session, 'complete', [], wrong))
            return
        self._respond(200, self._completion_body(session, 'complete' if not missing else 'pending', missing, {'event_ids': event_ids, 'segment_ids': segment_ids}))

    def _completion_body(self, session, state, missing, declaration):
        return {
            'protocol_version': PROTOCOL, 'instance_id': session['instance_id'], 'session_id': session['session_id'],
            'state': state, 'task_finished': True, 'missing': missing, 'declaration': declaration,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(json.dumps({'port': server.server_address[1]}), flush=True)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    sys.exit(main())
