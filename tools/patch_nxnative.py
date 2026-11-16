#!/usr/bin/env python3
"""Patches nxnative for the runner: a compressed shader compile cache, and a compiler identity that survives runner image updates.

Compressed cache:

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


class Patcher:
    def __init__(self, path):
        self.path, self.s = path, path.read_text(encoding='utf-8')

    def rep(self, old, new, count=1):
        found = self.s.count(old)
        if found != count:
            sys.exit(f'patch_nxnative: expected {count} of {old[:80]!r} in {self.path}, found {found}: nxnative changed, update this patch')
        self.s = self.s.replace(old, new)

    def save(self, what):
        compile(self.s, str(self.path), 'exec')
        self.path.write_text(self.s, encoding='utf-8', newline='\n')
        print(f'{self.path}: patched ({what})')


def compressed_cache(root):
    p = Patcher(root / 'dm' / 'tools' / 'shader_cache.py')
    if '_zpack(' in p.s:
        return print(f'{p.path}: already patched')
    p.rep('from pathlib import Path\n', 'from pathlib import Path\n' + HELPERS)
    p.rep('if rows: return bytes(rows[0][0])', 'if rows: return _zunpack(rows[0][0])', 2)   # Readonly.get, Store.get
    p.rep('digest = hashlib.blake2b(data, digest_size=16).digest()\n',
          'digest = hashlib.blake2b(data, digest_size=16).digest(); packed = _zpack(name, data)\n')   # the hash stays that of the raw bytes
    p.rep("'INSERT INTO blob(hash, data) VALUES(?, ?)', (digest, data))", "'INSERT INTO blob(hash, data) VALUES(?, ?)', (digest, packed))")
    p.save('blobs stored zstd-compressed')


def stable_compiler_id(root):
    """metal --version ends with the HOST's default target (air64-apple-darwin27.0.0 on one runner image, darwin27.2.0 on the next),
    and nxnative's compiler identity, part of every compile cache key, takes that text whole: a runner image update made the whole
    saved cache miss although the compiler, its toolchain asset and the SDK were identical. iOS shaders are compiled for the iOS target
    (-sdk iphoneos), never the host's, so the line is pinned to the value the saved cache was made with (NX_METAL_HOST_TARGET)."""
    p = Patcher(root / 'dm' / 'gfx' / 'tools' / 'dmsh_mtlcache.py')
    if 'NX_METAL_HOST_TARGET' in p.s:
        return print(f'{p.path}: already patched')
    old = r"    return re.sub(r'(?m)^InstalledDir:.*\n?', '', tools) + run('--show-sdk-version') + run('--show-sdk-build-version')"
    p.rep(old, "    tools = re.sub(r'(?m)^Target: air64-apple-darwin[0-9.]+$', 'Target: ' + os.environ.get('NX_METAL_HOST_TARGET', 'air64-apple-darwin27.0.0'), tools)   # nxnative-builder patch\n" + old)
    p.save('compiler identity independent of the host macOS version')


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else 'nxnative')
    compressed_cache(root)
    stable_compiler_id(root)


if __name__ == '__main__':
    main()
