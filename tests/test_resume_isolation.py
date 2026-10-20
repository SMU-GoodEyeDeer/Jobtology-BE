import subprocess
import sys


def test_parser_network_filter_blocks_native_and_descendant_inet_sockets():
    # A real child process protects the test runner from the irreversible filter.
    code = """
import socket, subprocess, sys
from jobtology_be.resume.isolation import restrict_parser_network
restrict_parser_network()
a, b = socket.socketpair()
a.send(b'ok')
assert b.recv(2) == b'ok'
a.close(); b.close()
for family in (socket.AF_INET, socket.AF_INET6):
    try:
        socket.socket(family, socket.SOCK_STREAM)
    except PermissionError:
        pass
    else:
        raise AssertionError('parser could create a network socket')
assert subprocess.run([sys.executable, '-c', 'import socket; socket.socket()'],
                      capture_output=True).returncode != 0
assert 'NoNewPrivs:\t1' in open('/proc/self/status').read()
"""
    result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr


def test_isolation_failure_stops_before_document_extraction(tmp_path, monkeypatch):
    import json
    from unittest.mock import Mock

    from jobtology_be.resume import isolation, worker

    extract = Mock(side_effect=AssertionError('document must stay unopened'))
    output = tmp_path / 'result.json'
    monkeypatch.setattr(worker.sys, 'argv', ['worker', str(tmp_path / 'private.docx'), str(output)])
    monkeypatch.setattr(worker.resource, 'setrlimit', Mock())
    monkeypatch.setattr(worker.signal, 'signal', Mock())
    monkeypatch.setattr(worker.signal, 'alarm', Mock())
    monkeypatch.setattr(worker, 'extract', extract)
    monkeypatch.setattr(isolation, 'restrict_parser_network', Mock(
        side_effect=OSError('private deployment details must not leak'),
    ))
    worker.main()
    assert json.loads(output.read_text()) == {
        'ok': False, 'code': 'PARSER_ISOLATION_UNAVAILABLE', 'error_type': 'OSError',
    }
    extract.assert_not_called()
