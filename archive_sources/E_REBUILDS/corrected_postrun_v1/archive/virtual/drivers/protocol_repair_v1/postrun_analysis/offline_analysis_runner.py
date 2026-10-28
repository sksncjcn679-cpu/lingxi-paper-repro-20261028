"""Offline child boundary for the corrected-study archive/rebuild controller.

Preparation source only. This module never rewrites scientific code or its
globals. The parent invokes it in a fresh --network=none, read-only-rootfs
container with only an archive view, local dependencies and empty output space.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import traceback


SDK_PREFIXES = ('openai', 'anthropic', 'litellm', 'cohere', 'boto3', 'botocore',
                'google.genai', 'google.generativeai', 'huggingface_hub', 'transformers')
COLLECTION_NAMES = {'real_driver.py', 'external_runner.py', 'public_pilot.py',
                    'formal_sequence.py', 'retrieval_driver.py', 'formal_reproduce.py'}
FONTS = {'/usr/bin/fc-list', '/usr/bin/fc-match', '/bin/fc-list', '/bin/fc-match'}


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('configuration', type=Path)
    args = parser.parse_args()
    config = json.loads(args.configuration.read_text(encoding='utf-8'))
    script = Path(config['script'])
    if not script.is_absolute() or script.name in COLLECTION_NAMES or sha(script) != config['script_sha256']:
        raise RuntimeError('only a hash-bound offline analysis entry may execute')
    arguments = config.get('arguments') or []
    if script.name == 'statistical_gates.py' and (not arguments or arguments[0] != 'formal'):
        raise RuntimeError('the frozen statistical entry is formal-only; prospective is not replayed')
    if script.name == 'retrieval_postrun_analysis_v1.py' and (not arguments or arguments[0] != 'analyze'):
        raise RuntimeError('reproduction reuses the original pre-outcome binding; never bind/run queries')
    allowed_scripts = config.get('approved_offline_sources') or {}
    if allowed_scripts.get(str(script)) != config['script_sha256']:
        raise RuntimeError('entry has no explicit offline-source approval')
    result_path = Path(config['guard_result'])
    log_path = Path(config['guard_events'])
    if result_path.exists() or log_path.exists():
        raise RuntimeError('retain the earlier child boundary record')
    protected = {os.path.normpath(name) for name in config['protected_inputs']}
    scratch_locks = {os.path.normpath(name) for name in config.get('scratch_locks', [])}
    protected -= scratch_locks
    ancestors = set()
    for name in protected:
        ancestors.update(str(parent) for parent in Path(name).parents)
    writable = [Path(name) for name in config['writable_roots']]
    denied = []
    recording = False
    font_executables = {os.path.realpath(name) for name in FONTS if Path(name).is_file()}

    def is_font_executable(value):
        name = os.fsdecode(value)
        resolved = shutil.which(name) if '/' not in name else name
        return resolved is not None and os.path.realpath(resolved) in font_executables

    def normalized(value):
        if isinstance(value, int) or value is None:
            return None
        return os.path.abspath(os.fsdecode(value))

    def writable_path(value):
        name = normalized(value)
        return name is None or any(Path(name).is_relative_to(root) for root in writable)

    def deny(code, detail=None):
        nonlocal recording
        item = {'code': code}
        if detail is not None:
            item['metadata'] = detail
        denied.append(item)
        if not recording:
            recording = True
            try:
                with log_path.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + '\n')
            finally:
                recording = False
        raise RuntimeError('offline boundary denied ' + code + '; body/credential content omitted')

    def secret_path(name):
        if name is None:
            return False
        path = Path(name); lower = path.name.lower()
        return (lower == '.env' or lower.startswith('.env.') or lower in
                {'auth.json', 'credentials', 'credentials.json', 'id_rsa', 'id_ed25519'} or
                any(name == prefix or name.startswith(prefix + '/') for prefix in
                    ('/run/secrets', '/root/.ssh', '/root/.aws', '/root/.config', '/var/run/docker.sock')))

    def audit(event, values):
        if recording:
            return
        if event in {'socket.__new__', 'socket.connect', 'socket.bind', 'socket.listen',
                     'socket.sendto', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr'}:
            deny('NETWORK_OR_SOCKET_ATTEMPT')
        if event == 'open':
            name = normalized(values[0])
            if secret_path(name):
                deny('SECRET_PATH_ACCESS')
            mode = values[1] if len(values) > 1 else None
            flags = values[2] if len(values) > 2 else 0
            writing = (isinstance(mode, str) and any(flag in mode for flag in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            if writing and name not in scratch_locks and (name in protected or not writable_path(name)):
                deny('IMMUTABLE_OR_OUTSIDE_VIEW_WRITE')
        if event in {'os.remove', 'os.rmdir', 'os.chmod', 'os.truncate', 'os.rename', 'os.link', 'os.symlink'}:
            names = [normalized(value) for value in values[:2] if isinstance(value, (str, bytes))]
            if any(secret_path(name) or name in protected or name in ancestors or not writable_path(name) for name in names):
                deny('IMMUTABLE_OR_OUTSIDE_VIEW_MUTATION')
        if event == 'subprocess.Popen':
            if not is_font_executable(values[0]):
                deny('CHILD_PROCESS_NOT_ALLOWED')
        if event in {'os.posix_spawn', 'os.posix_spawnp'} and not is_font_executable(values[0]):
            deny('PROCESS_ESCAPE_ATTEMPT')
        if event in {'os.system', 'os.exec', 'pty.spawn'}:
            deny('PROCESS_ESCAPE_ATTEMPT')

    class NoSDK(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in SDK_PREFIXES):
                deny('PROVIDER_SDK_IMPORT', fullname)
            return None

    # The allowlist is established before loading any archived helper/module.
    sys.addaudithook(audit)
    sys.meta_path.insert(0, NoSDK())
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script), *arguments]
    code = 0; error = None
    try:
        runpy.run_path(str(script), run_name='__main__')
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    except BaseException as exc:
        code = 1
        error = {'type': type(exc).__name__, 'frames': [
            {'file': frame.filename, 'line': frame.lineno, 'function': frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)]}
    if denied:
        code = 97
    if not log_path.exists():
        log_path.write_text('', encoding='utf-8')
    write_json(result_path, {'status': 'DENIED_RETAINED' if denied else 'OFFLINE_BOUNDARY_COMPLETED',
        'finished_utc': datetime.now(timezone.utc).isoformat(), 'script': str(script),
        'script_sha256': config['script_sha256'], 'exit_code': code, 'denied_count': len(denied),
        'denials': denied, 'exception_metadata_only': error,
        'scientific_source_or_globals_rewritten': False,
        'boundary': 'fresh no-network container plus socket/DNS/SDK/process/secret-path/write denyhook; fontconfig binaries only'})
    return code


if __name__ == '__main__':
    raise SystemExit(main())
