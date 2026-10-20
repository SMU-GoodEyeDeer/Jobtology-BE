"""Kernel-enforced network restrictions for the disposable Linux parser process.

This is defense in depth, not a filesystem/container sandbox. Local IPC remains
available to Java/.NET; no application/database credentials enter this process.
"""

import ctypes
import errno
import socket


class _Argument(ctypes.Structure):
    _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                ("datum_a", ctypes.c_uint64), ("datum_b", ctypes.c_uint64)]


def restrict_parser_network() -> None:
    # libseccomp resolves native syscalls and rejects foreign architectures. Its
    # default CTL_NNP setting also prevents exec from gaining new privileges.
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_int, ctypes.c_uint,
                                              ctypes.POINTER(_Argument)]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Parser isolation initialization failed")
    denied = 0x00050000 | errno.EPERM  # SCMP_ACT_ERRNO(EPERM)
    try:
        for name in (b"socket", b"socketpair", b"io_uring_setup"):
            syscall = library.seccomp_syscall_resolve_name(name)
            if syscall < 0:
                raise RuntimeError("Parser isolation syscall unavailable")
            # Only AF_UNIX sockets are needed by local runtime/converter IPC.
            # Disable io_uring to prevent its asynchronous socket operation from
            # bypassing the socket syscall filter.
            argument = _Argument(0, 1, socket.AF_UNIX, 0)  # SCMP_CMP_NE
            count = 0 if name == b"io_uring_setup" else 1
            result = library.seccomp_rule_add_array(
                context, denied, syscall, count,
                ctypes.byref(argument) if count else None,
            )
            if result < 0:
                raise RuntimeError("Parser isolation rule failed")
        if library.seccomp_load(context) < 0:
            raise RuntimeError("Parser isolation filter failed")
    finally:
        library.seccomp_release(context)
