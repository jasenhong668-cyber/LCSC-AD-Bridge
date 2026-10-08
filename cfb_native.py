import ctypes, pathlib
from ctypes import wintypes

HRESULT = ctypes.c_long
DWORD = wintypes.DWORD
PTR = ctypes.c_void_p
ole32 = ctypes.OleDLL('ole32')
ole32.StgCreateDocfile.argtypes = [wintypes.LPCWSTR, DWORD, DWORD, ctypes.POINTER(PTR)]
ole32.StgCreateDocfile.restype = HRESULT

def checked(value):
    if value < 0: raise OSError(f'COM structured storage error 0x{value & 0xffffffff:08X}')

def invoke(pointer, index, restype, args, *values):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(PTR))).contents
    method = ctypes.WINFUNCTYPE(restype, PTR, *args)(table[index])
    return method(pointer, *values)

def release(pointer):
    invoke(pointer, 2, wintypes.ULONG, [])

def write_cfb(path, streams):
    path = pathlib.Path(path)
    if path.exists(): raise FileExistsError(path)
    root = PTR()
    checked(ole32.StgCreateDocfile(str(path), 0x1012, 0, ctypes.byref(root)))
    storages = {(): root}
    try:
        for names, data in sorted(streams.items()):
            names = tuple(names)
            for depth in range(1, len(names)):
                prefix = names[:depth]
                if prefix in storages: continue
                child = PTR()
                checked(invoke(storages[prefix[:-1]], 5, HRESULT, [wintypes.LPCWSTR, DWORD, DWORD, DWORD, ctypes.POINTER(PTR)], prefix[-1], 0x1012, 0, 0, ctypes.byref(child)))
                storages[prefix] = child
            stream = PTR()
            checked(invoke(storages[names[:-1]], 3, HRESULT, [wintypes.LPCWSTR, DWORD, DWORD, DWORD, ctypes.POINTER(PTR)], names[-1], 0x1012, 0, 0, ctypes.byref(stream)))
            try:
                written = wintypes.ULONG()
                buffer = ctypes.create_string_buffer(data)
                checked(invoke(stream, 4, HRESULT, [PTR, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)], buffer, len(data), ctypes.byref(written)))
                if written.value != len(data): raise OSError('Incomplete compound stream write')
            finally: release(stream)
        for prefix, storage in sorted(storages.items(), key=lambda item: -len(item[0])):
            checked(invoke(storage, 9, HRESULT, [DWORD], 0))
    finally:
        for prefix, storage in sorted(storages.items(), key=lambda item: -len(item[0])): release(storage)
