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
