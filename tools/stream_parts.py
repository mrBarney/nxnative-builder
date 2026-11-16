#!/usr/bin/env python3
"""Stream a large file into, or out of, numbered release assets, with at most two parts on disk.

    ... | python tools/stream_parts.py upload <tag> <prefix>     # stdin -> <prefix>001, <prefix>002, ... on release <tag>
    python tools/stream_parts.py download <tag> <prefix> | ...   # those assets, in order -> stdout

GitHub caps a release asset at 2 GiB, and the runner's disk cannot hold a second copy of a big
shader cache, so parts are uploaded (or read) one at a time while the next one is written (or
fetched). Uses gh with GH_TOKEN and GITHUB_REPOSITORY from the environment.
"""
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

PART = int(os.environ.get('NX_PART_BYTES', 1900 * 2**20))
CHUNK = 8 * 2**20


def gh(*args, tries=4):
    for n in range(tries):
        r = subprocess.run(['gh', *args, '-R', os.environ['GITHUB_REPOSITORY']], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if r.returncode == 0:
            return r.stdout
        print(f'gh {" ".join(args[:2])} failed ({r.stderr.strip()[-300:]}); attempt {n + 1} of {tries}', file=sys.stderr, flush=True)
        time.sleep(15 * (n + 1))
    raise SystemExit(f'gh {" ".join(args)} failed {tries} times')


def worker(jobs, errors, fn):
    while True:
        item = jobs.get()
        if item is None:
            return
        try:
            fn(item)
        except BaseException as e:   # noqa: BLE001 - reported to the main thread
            errors.append(e)


def upload(tag, prefix):
    tmp = tempfile.mkdtemp(prefix='parts-', dir=os.environ.get('RUNNER_TEMP'))
    jobs, errors = queue.Queue(maxsize=1), []

    def send(path):
        gh('release', 'upload', tag, path, '--clobber')
        os.remove(path)
        print(f'uploaded {os.path.basename(path)}', file=sys.stderr, flush=True)

    t = threading.Thread(target=worker, args=(jobs, errors, send), daemon=True)
    t.start()
    src, n, sent, total = sys.stdin.buffer, 0, 0, 0
    try:
        while not errors:
            n += 1
            path = os.path.join(tmp, f'{prefix}{n:03d}')
            size = 0
            with open(path, 'wb') as f:
                while size < PART:
                    b = src.read(min(CHUNK, PART - size))
                    if not b:
                        break
                    f.write(b); size += len(b)
            if size == 0:
                os.remove(path)
                break
            total += size; sent += 1
            jobs.put(path)
            if size < PART:
                break
    finally:
        jobs.put(None)
        t.join()
        shutil.rmtree(tmp, ignore_errors=True)
    if errors:
        raise SystemExit(f'upload failed: {errors[0]}')
    if not sent:
        raise SystemExit('upload: no input')
    print(f'{sent} part(s), {total / 2**30:.2f} GiB on {tag}', file=sys.stderr, flush=True)


def download(tag, prefix):
    names = sorted(a for a in gh('release', 'view', tag, '--json', 'assets', '--jq', '.assets[].name').split() if a.startswith(prefix))
    if not names:
        raise SystemExit(f'{tag} has no {prefix}* assets')
    tmp = tempfile.mkdtemp(prefix='parts-', dir=os.environ.get('RUNNER_TEMP'))
    ready, errors = queue.Queue(maxsize=1), []

    def fetch():
        try:
            for name in names:
                if errors:
                    return
                gh('release', 'download', tag, '-p', name, '-D', tmp, '--clobber')
                ready.put(os.path.join(tmp, name))
        except BaseException as e:   # noqa: BLE001
            errors.append(e)
        finally:
            ready.put(None)

    threading.Thread(target=fetch, daemon=True).start()
    out, total = sys.stdout.buffer, 0
    try:
        while (path := ready.get()) is not None:
            with open(path, 'rb') as f:
                shutil.copyfileobj(f, out, CHUNK)
            total += os.path.getsize(path)
            os.remove(path)
        out.flush()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if errors:
        raise SystemExit(f'download failed: {errors[0]}')
    print(f'{len(names)} part(s), {total / 2**30:.2f} GiB from {tag}', file=sys.stderr, flush=True)


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] not in ('upload', 'download'):
        raise SystemExit(__doc__)
    (upload if sys.argv[1] == 'upload' else download)(sys.argv[2], sys.argv[3])
