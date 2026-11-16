#!/usr/bin/env python3
"""Compress, in place, the raw blobs of an nxnative shader compile cache file (cas-<chain>.db).

Writes the format of tools/patch_nxnative.py (marker + zstd frame), checks every converted blob
against the BLAKE2b hash the cache keeps of its raw bytes, and gives the freed pages back to the
file system as it goes, so it needs little more disk than the file itself.

    python tools/compress_cache.py <cas-*.db> [...]
"""
import concurrent.futures
import hashlib
import os
import sqlite3
import sys
import time

try:
    from compression import zstd
except ImportError:
    from backports import zstd

MARK = b'\x00nxz'
BATCH = 1500


def pack(data):
    if data[:4] == MARK or len(data) < 512:
        return None
    packed = MARK + zstd.compress(data, 3 if data[:4] == b'MTLB' else 1)
    return packed if len(packed) < len(data) else None


def convert(path):
    t0, size0 = time.time(), os.path.getsize(path)
    db = sqlite3.connect(path, isolation_level=None, timeout=600)
    if db.execute('PRAGMA auto_vacuum').fetchone()[0] != 2:
        sys.exit(f'{path}: not an incremental-vacuum cache file')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    ids = [r[0] for r in db.execute('SELECT id FROM blob ORDER BY id')]
    raw = new = done = 0
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 2) as pool:
        for i in range(0, len(ids), BATCH):
            rows = db.execute(f'SELECT id, hash, data FROM blob WHERE id IN ({",".join("?" * len(ids[i:i + BATCH]))})', ids[i:i + BATCH]).fetchall()
            packed = list(pool.map(lambda r: pack(bytes(r[2])), rows))
            db.execute('BEGIN IMMEDIATE')
            for (bid, digest, data), p in zip(rows, packed):
                if p is None:
                    continue
                if hashlib.blake2b(zstd.decompress(p[4:]), digest_size=16).digest() != bytes(digest):
                    db.execute('ROLLBACK')
                    sys.exit(f'{path}: blob {bid} did not round-trip; nothing of this batch was written')
                db.execute('UPDATE blob SET data=? WHERE id=?', (p, bid))
                raw += len(data); new += len(p); done += 1
            db.execute('COMMIT')
            if (i // BATCH) % 20 == 19:
                db.execute('PRAGMA incremental_vacuum').fetchall(); db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()   # the vacuum frees one page per row read
                print(f'  {min(i + BATCH, len(ids)):,}/{len(ids):,} blobs, {os.path.getsize(path) / 2**30:.1f} GiB file', flush=True)
    db.execute('PRAGMA incremental_vacuum').fetchall(); db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()   # the vacuum frees one page per row read
    db.close()
    print(f'{path}: {done:,} of {len(ids):,} blobs compressed ({raw / 2**30:.2f} -> {new / 2**30:.2f} GiB); '
          f'file {size0 / 2**30:.2f} -> {os.path.getsize(path) / 2**30:.2f} GiB in {time.time() - t0:.0f} s', flush=True)


if __name__ == '__main__':
    for p in sys.argv[1:]:
        convert(p)
