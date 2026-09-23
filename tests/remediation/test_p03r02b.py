"""Bounded in-memory token channel regression checks."""
import os
import subprocess
import threading

def _bounded(callable_, seconds=30):
    box = {}

    def target():
        try:
            box['value'] = callable_()
        except BaseException as error:  # noqa: BLE001 - recorded and re-raised below
            box['error'] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), 'helper did not return within the outer bound'
    if 'error' in box:
        raise box['error']
    return box.get('value')

WRITE_TOKEN = "import fs from 'node:fs'; fs.writeSync(Number(process.env.GEP_TOKEN_FD), 'tok-abc\\n');"

QUIET_EXIT = 'process.exit(0);'

def test_run_chrome_tokens_normal_nonzero_quiet_and_timeout_are_bounded(evidence, run_chrome_tokens):
    env = dict(os.environ)
    result, tokens = _bounded(lambda: run_chrome_tokens(WRITE_TOKEN, env, timeout=60))
    assert result.returncode == 0 and tokens == ['tok-abc']
    assert 'tok-abc' not in result.stdout and 'tok-abc' not in result.stderr

    nonzero = _bounded(lambda: run_chrome_tokens("import fs from 'node:fs'; "
                                                 "fs.writeSync(Number(process.env.GEP_TOKEN_FD), 'tok-n\\n'); "
                                                 "process.exit(3);", env, timeout=60))
    assert nonzero[0].returncode == 3 and nonzero[1] == ['tok-n']

    quiet = _bounded(lambda: run_chrome_tokens(QUIET_EXIT, env, timeout=60))
    assert quiet[0].returncode == 0 and quiet[1] == []

    def hanging():
        try:
            run_chrome_tokens('setTimeout(() => {}, 30000);', env, timeout=1)
        except subprocess.TimeoutExpired:
            return 'timeout'
        return 'returned'

    assert _bounded(hanging, seconds=30) == 'timeout'

    before = len(os.listdir('/dev/fd')) if os.path.isdir('/dev/fd') else None
    for _ in range(10):
        _bounded(lambda: run_chrome_tokens(QUIET_EXIT, env, timeout=60))
    after = len(os.listdir('/dev/fd')) if os.path.isdir('/dev/fd') else None
    if before is not None:
        assert after <= before + 2, (before, after)
    evidence('run_chrome_tokens.json', {'normal': tokens, 'nonzero': nonzero[0].returncode,
                                        'quiet': quiet[1], 'timeout': 'bounded',
                                        'descriptors_before': before, 'descriptors_after': after})
