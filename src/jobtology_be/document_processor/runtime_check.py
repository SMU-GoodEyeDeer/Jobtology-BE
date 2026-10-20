"""Check native tools bundled with the monolithic backend, without parsing input."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import jpype

from .core.doc_converter import _converter_path
from .core.hwp_converter import _converter_classpath
from .pdf.odl.runner import resolve_odl_jar_path


def _run_tool(name: str, argument: str) -> str | None:
    executable = shutil.which(name)
    if executable is None:
        return None
    try:
        result = subprocess.run([executable, argument], capture_output=True, text=True,
                                check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout + result.stderr


def runtime_report() -> dict[str, object]:
    java_output = _run_tool('java', '-version')
    java = java_output is not None
    match = re.search(r'(?:openjdk|java) version "(\d+)(?:\.(\d+))?', java_output or '')
    java_major = (int(match[2]) if match[1] == '1' else int(match[1])) if match else 0
    dotnet = _run_tool('dotnet', '--list-runtimes') or ''
    dotnet8 = re.search(r'^Microsoft\.NETCore\.App 8\.0\.\d+\s', dotnet, re.MULTILINE) is not None
    try:
        jvm = Path(jpype.getDefaultJVMPath()).is_file()
    except (jpype.JVMNotFoundException, jpype.JVMNotSupportedException):
        jvm = False
    try:
        hwp_bundle = bool(_converter_classpath())
    except FileNotFoundError:
        hwp_bundle = False
    try:
        pdf_bundle = resolve_odl_jar_path().is_file()
    except FileNotFoundError:
        pdf_bundle = False
    try:
        doc_bundle = _converter_path().is_file()
    except FileNotFoundError:
        doc_bundle = False
    formats = {
        'docx': True, 'hwpx': True,
        'pdf': java_major >= 11 and pdf_bundle, 'hwp': java_major >= 25 and jvm and hwp_bundle,
        'doc': dotnet8 and doc_bundle,
    }
    return {'python': sys.version.split()[0], 'java': java, 'java_major': java_major, 'jvm': jvm,
            'dotnet8': dotnet8, 'bundles': {'pdf': pdf_bundle, 'hwp': hwp_bundle, 'doc': doc_bundle},
            'formats': formats,
            'note': 'Checks runtime discovery and bundled files; does not prove arbitrary-file parsing or OCR.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-all', action='store_true', help='Exit nonzero if any format runtime is missing.')
    arguments = parser.parse_args()
    report = runtime_report()
    print(json.dumps(report, indent=2))
    if arguments.require_all and not all(report['formats'].values()):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
