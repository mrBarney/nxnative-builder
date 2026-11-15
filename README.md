# NxNative IPA Builder

Builds an unsigned iOS IPA of a game you own with [nxnative](https://github.com/seedds/nxnative) on GitHub's free macOS runner (`xcode-27`, Xcode 27.2). No Mac needed.

This repository is public so the builds are free. **No game data is stored here in readable form**: the game goes in, and the IPA and logs come out, as AES-256 encrypted 7-Zip archives (file names included). The password is a repository secret and a local file that is never committed.

## Build a game

```
python tools/nx.py upload "path\to\game.nup"   # Switch 2: one .nup file
python tools/nx.py build
# ...wait for the run under Actions; progress is mirrored into the "Build progress" issue...
python tools/nx.py get-ipa                     # downloads and decrypts the newest IPA into ipas/
python tools/nx.py clean --all                 # removes the encrypted game files and IPAs from the repo
```

### Switch 1 games

Put the base game, update and DLC (`.nsp`, `.xci`, `.nsz` or `.xcz`) in one folder, then:

```
python tools/nx.py keys "path\to\prod.keys"     # once: stored as the PROD_KEYS_B64 secret
python tools/nx.py upload "path\to\game folder"
python tools/nx.py build                         # or: build --device bc  (iPhone 15 and older)
```

### Options

- `build --extra "--romfs separate --shader-files"`: extra `build_ipa_from_nsp.py` options.
- `build --ref main`: build with the newest nxnative instead of the pinned commit.
- `build --allow-other-metal`: build even when the runner's Metal compiler is not Xcode 27.2's.
- `python tools/nx.py logs`: download and decrypt the last run's build logs.

## Notes

- Only the standard `xcode-27` runner is free for public repositories; never switch it to `xcode-27-xlarge`.
- Upstream requirements: iPhone with A17 Pro or later and iOS 26+. The BC-fallback path uses a fork that also runs some games on A16 iPhones.
- Switch 2 games are tested by nxnative on the Mac only; on iPhone they are experimental.
