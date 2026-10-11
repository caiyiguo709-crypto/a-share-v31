"""Supplement real network_probe with HTTP write/read observation only."""
import contextlib
import io
import json
import subprocess
import sys
import time
from pathlib import Path
import network_probe as probe


def child():
    import urllib3.connection as connection
    clean = probe.sanitizer()

    def observe(phase, original):
        def wrapped(*args, **kwargs):
            event = {'record_kind': 'http_phase', 'phase': phase, 'started_at': probe.now()}
            tick = time.monotonic()
            try:
                response = original(*args, **kwargs)
                event['status'] = 'ok'
                if phase == 'http_response_read':
                    event['http_status'] = response.status
                return response
            except Exception as exc:
                event.update(status='error', error_type=type(exc).__name__, message=clean(exc))
                raise
            finally:
                event.update(finished_at=probe.now(), duration_seconds=round(time.monotonic() - tick, 4))
                sys.__stdout__.write(json.dumps(event, ensure_ascii=False) + '\n')
                sys.__stdout__.flush()
        return wrapped
    connection.HTTPConnection.request = observe('http_request_write', connection.HTTPConnection.request)
    connection.HTTPConnection.getresponse = observe('http_response_read', connection.HTTPConnection.getresponse)
    probe.child()


def main():
    old = json.loads((probe.ROOT / 'docs/interface-report-stage2.json').read_text())
    keys = ['index:000001:em', 'stock:601001:em', 'meta:601001', 'flow:601001', 'index:000001:sina', 'stock:601001:tx']
    cases = [{'key': r['key'], 'interface': r['interface'], 'parameters': r['parameters']} for r in old['requests'] if r['key'] in keys]
    result = {'started_at': probe.now(), 'scope': 'six supplemental representative actual calls, one application attempt each; not merged into fixed 42-case results', 'results': []}
    for case in cases:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--child'], input=json.dumps(case),
                              text=True, capture_output=True, timeout=22, check=False)
        lines = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith('{')]
        summaries = [record for record in lines if record.get('record_kind') == 'result']
        outcome = summaries[-1] if summaries else {'status': 'worker_error', 'returncode': proc.returncode}
        result['results'].append({**case, **outcome, 'http_phase_details': [record for record in lines if record.get('record_kind') == 'http_phase'], 'application_attempts': 1})
        print(json.dumps({'key': case['key'], 'status': outcome['status']}, ensure_ascii=False), flush=True)
    result['finished_at'] = probe.now()
    (probe.OUT / 'http-phase-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    child() if '--child' in sys.argv else main()
