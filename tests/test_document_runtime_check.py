import json
import subprocess

import pytest

from jobtology_be.document_processor import runtime_check


def test_missing_native_tools_leave_python_formats_available(monkeypatch):
    monkeypatch.setattr(runtime_check.shutil, 'which', lambda _: None)

    def no_jvm():
        raise runtime_check.jpype.JVMNotFoundException('not installed')

    monkeypatch.setattr(runtime_check.jpype, 'getDefaultJVMPath', no_jvm)
    report = runtime_check.runtime_report()
    assert report['formats'] == {'docx': True, 'hwpx': True, 'pdf': False, 'hwp': False, 'doc': False}
    assert all(report['bundles'].values())


@pytest.mark.parametrize(('version', 'doc'), [('8.0.34', True), ('9.0.1', False)])
def test_legacy_doc_requires_the_net8_framework(monkeypatch, version, doc):
    monkeypatch.setattr(runtime_check, '_run_tool', lambda name, _: (
        'openjdk version "25.0.4"' if name == 'java' else f'Microsoft.NETCore.App {version} [/runtime]\n'
    ))
    monkeypatch.setattr(runtime_check.jpype, 'getDefaultJVMPath', lambda: __file__)
    report = runtime_check.runtime_report()
    assert report['formats']['pdf'] is True
    assert report['formats']['hwp'] is True
    assert report['formats']['doc'] is doc


def test_java17_supports_pdf_but_not_the_bundled_java25_hwp_converter(monkeypatch):
    monkeypatch.setattr(runtime_check, '_run_tool', lambda name, _: (
        'openjdk version "17.0.20"' if name == 'java' else ''
    ))
    monkeypatch.setattr(runtime_check.jpype, 'getDefaultJVMPath', lambda: __file__)
    report = runtime_check.runtime_report()
    assert report['java_major'] == 17
    assert report['formats']['pdf'] is True
    assert report['formats']['hwp'] is False


def test_native_probe_timeout_is_reported_unavailable(monkeypatch):
    monkeypatch.setattr(runtime_check.shutil, 'which', lambda _: '/tool')

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr(runtime_check.subprocess, 'run', timeout)
    assert runtime_check._run_tool('java', '-version') is None


def test_strict_cli_reports_missing_tools_and_fails(monkeypatch, capsys):
    monkeypatch.setattr(runtime_check, 'runtime_report', lambda: {'formats': {'pdf': False}})
    monkeypatch.setattr(runtime_check.sys, 'argv', ['runtime_check', '--require-all'])
    with pytest.raises(SystemExit) as error:
        runtime_check.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)['formats']['pdf'] is False
