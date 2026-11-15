#!/usr/bin/env python3
"""Helper for the NxNative IPA Builder workflow (public repo, encrypted transport). Needs gh (logged in) and 7-Zip.

  python tools/nx.py upload <game file or folder> [...]   encrypt the game into game.7z volumes and put them in the 'game-files' release
  python tools/nx.py keys <path to prod.keys>             store prod.keys as the PROD_KEYS_B64 secret (Switch 1 only)
  python tools/nx.py build [--device bc] [--extra "..."]  start the workflow
  python tools/nx.py get-ipa [ipa-N] [--out DIR]          download and decrypt a built IPA
  python tools/nx.py logs [run number] [--out DIR]        download and decrypt a run's logs
  python tools/nx.py clean [--all]                        delete the game-files release (--all: also every ipa-* release)

The archive password lives in .secrets/archive-password.txt (never committed) and in the ARCHIVE_PASSWORD secret.
Everything placed in the public repository is AES-256 encrypted with it, file names included.
"""
import argparse, base64, json, os, secrets, shutil, string, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PW_FILE = ROOT / '.secrets' / 'archive-password.txt'
GAME_EXTS = {'.nup', '.nsp', '.xci', '.nsz', '.xcz'}
SEVEN_ZIP = next((p for p in (shutil.which('7z'), r'C:\Program Files\7-Zip\7z.exe', r'C:\Program Files (x86)\7-Zip\7z.exe')
                  if p and Path(p).exists()), None)


def gh(*args, capture=False, check=True, stdin=None):
    r = subprocess.run(['gh', *args], cwd=ROOT, text=True, input=stdin, capture_output=capture, check=False)
    if check and r.returncode != 0:
        sys.exit(f'gh {" ".join(args[:3])} ... failed' + (f':\n{r.stderr.strip()}' if capture else ''))
    return r


def seven(*args):
    if not SEVEN_ZIP:
        sys.exit('7-Zip is not installed (winget install 7zip.7zip).')
    r = subprocess.run([SEVEN_ZIP, *args], check=False)
    if r.returncode != 0:
        sys.exit(f'7-Zip failed (exit {r.returncode})')


def repo_name(repo):
    return repo or json.loads(gh('repo', 'view', '--json', 'nameWithOwner', capture=True).stdout)['nameWithOwner']


def password(repo, create=False):
    if PW_FILE.is_file():
        return PW_FILE.read_text().strip()
    if not create:
        sys.exit(f'No archive password in {PW_FILE}.')
    pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
    PW_FILE.parent.mkdir(exist_ok=True)
    PW_FILE.write_text(pw + '\n')
    gh('secret', 'set', 'ARCHIVE_PASSWORD', '-R', repo, stdin=pw)
    print(f'Created a new archive password ({PW_FILE}) and the ARCHIVE_PASSWORD secret')
    return pw


def release_exists(repo, tag):
    return gh('release', 'view', tag, '-R', repo, '--json', 'tagName', capture=True, check=False).returncode == 0


def cmd_upload(a):
    repo = repo_name(a.repo)
    files = []
    for p in map(Path, a.paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in GAME_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            sys.exit(f'Not found: {p}')
    files = [f for f in files if not f.name.lower().endswith('.keys')]
    if not files:
        sys.exit('No game files given (.nup, .nsp, .xci, .nsz or .xcz).')
    pw = password(repo, create=True)
    stage = files[0].parent / '.nx-upload'
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    try:
        print(f'Encrypting {len(files)} file(s) into {stage}')
        seven('a', '-t7z', '-mx=0', '-mhe=on', '-v1900m', '-bsp1', f'-p{pw}', str(stage / 'game.7z'), *map(str, files))
        vols = sorted(stage.glob('game.7z.*'))
        if release_exists(repo, 'game-files'):
            print('Replacing the existing game-files release')
            gh('release', 'delete', 'game-files', '-R', repo, '-y', '--cleanup-tag')
        gh('release', 'create', 'game-files', '-R', repo, '--prerelease', '--title', 'Encrypted input',
           '--notes', 'AES-256 encrypted input for the build workflow. Remove after building: python tools/nx.py clean')
        for i, v in enumerate(vols, 1):
            print(f'[{i}/{len(vols)}] Uploading {v.name} ({v.stat().st_size / 2**30:.2f} GiB)', flush=True)
            gh('release', 'upload', 'game-files', str(v), '-R', repo, '--clobber')
        print(f'Done: {len(vols)} encrypted volume(s) in the game-files release of {repo}')
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def cmd_keys(a):
    data = Path(a.path).read_bytes()
    if b'=' not in data:
        sys.exit(f'{a.path} does not look like a prod.keys file.')
    repo = repo_name(a.repo)
    gh('secret', 'set', 'PROD_KEYS_B64', '-R', repo, stdin=base64.b64encode(data).decode())
    print(f'Stored {a.path} as the PROD_KEYS_B64 secret of {repo}')


def cmd_build(a):
    repo = repo_name(a.repo)
    device = 'iPhone 15 or older (BC fallback)' if a.device == 'bc' else 'iPhone 15 Pro or newer (standard)'
    args = ['workflow', 'run', 'build.yml', '-R', repo, '-f', f'device={device}']
    if a.extra:
        args += ['-f', f'extra_args={a.extra}']
    if a.ref:
        args += ['-f', f'nxnative_ref={a.ref}']
    if a.allow_other_metal:
        args += ['-f', 'allow_other_metal=true']
    gh(*args)
    print(f'Started. Watch it at https://github.com/{repo}/actions')


def download_and_extract(repo, tag, pattern, out, pw):
    tmp = out / f'.{tag}-download'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    gh('release', 'download', tag, '-R', repo, '-D', str(tmp), '-p', pattern)
    first = sorted(tmp.glob(pattern))[0]
    seven('x', '-y', f'-p{pw}', f'-o{out}', str(first))
    shutil.rmtree(tmp, ignore_errors=True)


def cmd_get_ipa(a):
    repo = repo_name(a.repo)
    tag = a.tag
    if not tag:
        rel = json.loads(gh('release', 'list', '-R', repo, '--limit', '100', '--json', 'tagName,createdAt', capture=True).stdout)
        rel = sorted((r for r in rel if r['tagName'].startswith('ipa-')), key=lambda r: r['createdAt'])
        if not rel:
            sys.exit('No ipa-* release yet: has a build finished successfully?')
        tag = rel[-1]['tagName']
    out = Path(a.out).resolve()
    print(f'Downloading and decrypting {tag}')
    download_and_extract(repo, tag, 'ipa.7z.*', out, password(repo))
    for f in out.glob('*.ipa'):
        print(f'{f} ({f.stat().st_size / 2**30:.2f} GiB)')


def cmd_logs(a):
    repo = repo_name(a.repo)
    run = a.run
    if not run:
        runs = json.loads(gh('run', 'list', '-R', repo, '-w', 'build.yml', '--limit', '1', '--json', 'number,databaseId', capture=True).stdout)
        if not runs:
            sys.exit('No runs yet.')
        run_id, run = runs[0]['databaseId'], runs[0]['number']
    else:
        runs = json.loads(gh('run', 'list', '-R', repo, '-w', 'build.yml', '--limit', '100', '--json', 'number,databaseId', capture=True).stdout)
        run_id = next((r['databaseId'] for r in runs if str(r['number']) == str(run)), None) or sys.exit(f'No run {run}')
    out = Path(a.out).resolve() / f'run-{run}'
    tmp = out / '.download'
    shutil.rmtree(tmp, ignore_errors=True)
    gh('run', 'download', str(run_id), '-R', repo, '-n', f'logs-{run}', '-D', str(tmp))
    seven('x', '-y', f'-p{password(repo)}', f'-o{out}', str(next(tmp.glob('*.7z'))))
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'Logs in {out}')


def cmd_clean(a):
    repo = repo_name(a.repo)
    tags = ['game-files']
    if a.all:
        rel = json.loads(gh('release', 'list', '-R', repo, '--limit', '200', '--json', 'tagName', capture=True).stdout)
        tags += [r['tagName'] for r in rel if r['tagName'].startswith('ipa-')]
    for t in tags:
        if release_exists(repo, t):
            gh('release', 'delete', t, '-R', repo, '-y', '--cleanup-tag')
            print(f'Deleted release {t}')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-R', '--repo', help='owner/name (default: this folder\'s GitHub remote)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('upload'); p.add_argument('paths', nargs='+'); p.set_defaults(f=cmd_upload)
    p = sub.add_parser('keys'); p.add_argument('path'); p.set_defaults(f=cmd_keys)
    p = sub.add_parser('build'); p.add_argument('--device', choices=('standard', 'bc'), default='standard')
    p.add_argument('--extra', default=''); p.add_argument('--ref', default='')
    p.add_argument('--allow-other-metal', action='store_true'); p.set_defaults(f=cmd_build)
    p = sub.add_parser('get-ipa'); p.add_argument('tag', nargs='?'); p.add_argument('--out', default=str(ROOT / 'ipas')); p.set_defaults(f=cmd_get_ipa)
    p = sub.add_parser('logs'); p.add_argument('run', nargs='?'); p.add_argument('--out', default=str(ROOT / 'logs')); p.set_defaults(f=cmd_logs)
    p = sub.add_parser('clean'); p.add_argument('--all', action='store_true'); p.set_defaults(f=cmd_clean)
    a = ap.parse_args()
    a.f(a)


if __name__ == '__main__':
    main()
