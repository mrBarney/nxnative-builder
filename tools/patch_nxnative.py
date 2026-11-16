#!/usr/bin/env python3
"""Make nxnative's shader compile cache store its blobs zstd-compressed.

A GitHub runner has far less disk than the Macs nxnative is built on: Pokopia's compile cache
(dm/tools/shader_cache.py, one SQLite file) would reach about 70 GB of raw Metal sources and
metallibs. Compressed it is about a third of that. Compressed blobs carry a marker, so blobs written
before the patch (or by an unpatched build) are still read as they are.

    python tools/patch_nxnative.py <nxnative checkout>

Fails loudly when the code it patches has changed, rather than patching something else.
"""
import pathlib
import sys

HELPERS = '''
# ---- nxnative-builder patch: blobs are stored zstd-compressed behind a marker; unmarked blobs are read as they are ----
try: from compression import zstd as _zstd
except ImportError: from backports import zstd as _zstd
_ZMARK = b'\\x00nxz'


def _zpack(name, data):
    data = bytes(data)
    if len(data) < 512 and data[:4] != _ZMARK: return data
    packed = _ZMARK + _zstd.compress(data, 3 if name.startswith('metallib/') else 1)
    return packed if len(packed) < len(data) or data[:4] == _ZMARK else data


def _zunpack(data):
    data = bytes(data)
    return _zstd.decompress(data[4:]) if data[:4] == _ZMARK else data
# ---- end of patch ----
'''


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'nxnative')
    path = root / 'dm' / 'tools' / 'shader_cache.py'
    s = path.read_text(encoding='utf-8')
    if '_zpack(' in s:
        print(f'{path}: already patched')
        return

    def rep(old, new, count=1):
        nonlocal s
        found = s.count(old)
        if found != count:
            sys.exit(f'patch_nxnative: expected {count} of {old[:80]!r} in {path}, found {found}: nxnative changed, update this patch')
        s = s.replace(old, new)

    rep('from pathlib import Path\n', 'from pathlib import Path\n' + HELPERS)
    rep('if rows: return bytes(rows[0][0])', 'if rows: return _zunpack(rows[0][0])', 2)   # Readonly.get, Store.get
    rep('digest = hashlib.blake2b(data, digest_size=16).digest()\n',
        'digest = hashlib.blake2b(data, digest_size=16).digest(); packed = _zpack(name, data)\n')   # the hash stays that of the raw bytes
    rep("'INSERT INTO blob(hash, data) VALUES(?, ?)', (digest, data))", "'INSERT INTO blob(hash, data) VALUES(?, ?)', (digest, packed))")
    path.write_text(s, encoding='utf-8', newline='\n')
    compile(s, str(path), 'exec')
    print(f'{path}: patched (blobs stored zstd-compressed)')


if __name__ == '__main__':
    main()
