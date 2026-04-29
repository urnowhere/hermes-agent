import types

from tools.environments import base


def test_poll_pipe_chunk_unix_reads_available_bytes(monkeypatch):
    monkeypatch.setattr(base, "_IS_WINDOWS", False)
    monkeypatch.setattr(base.select, "select", lambda r, w, x, t: ([123], [], []))
    monkeypatch.setattr(base.os, "read", lambda fd, n: b"hello")

    status, chunk = base._poll_pipe_chunk(123)

    assert status == "data"
    assert chunk == b"hello"


def test_poll_pipe_chunk_windows_reads_available_bytes(monkeypatch):
    monkeypatch.setattr(base, "_IS_WINDOWS", True)

    class _FakeKernel32:
        def PeekNamedPipe(self, handle, buf, size, read, avail_ptr, total):
            avail_ptr._obj.value = 5
            return 1

    fake_ctypes = types.SimpleNamespace(
        windll=types.SimpleNamespace(kernel32=_FakeKernel32()),
        byref=lambda obj: types.SimpleNamespace(_obj=obj),
        get_last_error=lambda: 0,
    )
    fake_wintypes = types.SimpleNamespace(
        DWORD=lambda value=0: types.SimpleNamespace(value=value),
        HANDLE=lambda value: value,
    )
    fake_msvcrt = types.SimpleNamespace(get_osfhandle=lambda fd: fd)
    fake_ctypes.wintypes = fake_wintypes

    import sys

    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setitem(sys.modules, "ctypes.wintypes", fake_wintypes)
    monkeypatch.setattr(base.os, "read", lambda fd, n: b"world")

    status, chunk = base._poll_pipe_chunk(456)

    assert status == "data"
    assert chunk == b"world"
