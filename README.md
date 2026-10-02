# Trunk Recorder Pro plugins

The plugins that [Trunk Recorder Pro](https://github.com/TrunkRecorder/trunk-recorder-pro)'s
plugin store offers. Each one has an entry here that pins one release of it:
its version, the commit it was built from, and the SHA-256 of the download
for each platform. The recorder installs only files that match, so what
users get is what was reviewed.

| Plugin | | |
|---|---|---|
| [OpenMHz](https://github.com/TrunkRecorder/trunk-plugin-openmhz) | official | Uploads recorded calls to OpenMHz |
| [Broadcastify Calls](https://github.com/TrunkRecorder/trunk-plugin-broadcastify) | official | Uploads recorded calls to Broadcastify Calls |
| [Rdio Scanner](https://github.com/TrunkRecorder/trunk-plugin-rdioscanner) | official | Uploads recorded calls to an Rdio Scanner server |
| [simplestream](https://github.com/TrunkRecorder/trunk-plugin-simplestream) | official | Streams the audio of calls as they're recorded, over UDP or TCP |
| [Upload script](https://github.com/TrunkRecorder/trunk-plugin-upload-script) | official | Runs a script of yours on each recorded call, as Trunk Recorder's uploadScript does |

To write a plugin, start from the
[plugin template](https://github.com/TrunkRecorder/trunk-plugin-template).

## Listing your plugin

1. Release it with the template's release workflow. That workflow names the
   files, writes `SHA256SUMS` and attests each file's build provenance, and
   the registry needs all three. See
   [Releasing](https://github.com/TrunkRecorder/trunk-plugin-template/blob/main/docs/releasing.md).
2. Fork this repository and add your entry:

   ```sh
   ./add-release https://github.com/you/trunk-plugin-pager v0.1.0
   ./check plugins/pager.json
   ```

   `add-release` writes `plugins/<id>.json` from the release and rebuilds
   `index.json`. `check` downloads every file and checks it (below). It needs
   Python 3.9 or later and the [GitHub CLI](https://cli.github.com) (`gh`).
3. Open a pull request with both files. A maintainer reviews it.

**Releasing an update** is the same: release the new version, run
`add-release` with its tag, and open a pull request. Your entry keeps its
tier.

## An entry

```json
{
  "id": "openmhz",
  "name": "OpenMHz",
  "description": "Uploads recorded calls to OpenMHz.",
  "repository": "https://github.com/TrunkRecorder/trunk-plugin-openmhz",
  "homepage": "https://openmhz.com",
  "license": "GPL-3.0-or-later",
  "tier": "official",
  "version": "0.1.1",
  "api": 1,
  "tag": "v0.1.1",
  "commit": "669ea2d10b193728b82276ec9ed77c81118f164f",
  "assets": {
    "x86_64-unknown-linux-gnu": {
      "url": "https://github.com/TrunkRecorder/trunk-plugin-openmhz/releases/download/v0.1.1/openmhz-0.1.1-x86_64-unknown-linux-gnu.tar.gz",
      "sha256": "74c54539…"
    },
    "aarch64-unknown-linux-gnu": { "url": "…", "sha256": "…" },
    "universal-apple-darwin": { "url": "…", "sha256": "…" },
    "x86_64-pc-windows-msvc": { "url": "…", "sha256": "…" }
  }
}
```

- `name`, `description`, `homepage`, `license` and `api` are what the
  plugin's `--describe` says. `homepage` is optional.
- `tier` is `official` for plugins maintained with the recorder, in the
  TrunkRecorder organization, and `community` for everyone else's. The store
  shows which is which.
- `tag` is `v<version>`, and `commit` is the commit it pointed at when the
  entry was added. Tags can be moved; the commit says which source was
  reviewed.
- `assets` has one download for each platform the plugin is built for.
  `x86_64-unknown-linux-gnu` is required, because CI runs that build.

`index.json` holds every entry in one file, which is what the recorder
fetches. Don't edit it by hand: `add-release` rebuilds it, and so does
`tools/registry.py build-index`.

## What the checks do

On every pull request, for each entry it adds or changes, CI:

- checks the entry's fields, and that every URL is a download from the
  entry's own repository, at its tag
- checks that the tag still points at the entry's commit
- downloads every file and checks its SHA-256
- checks every file's build provenance with `gh attestation verify`: GitHub
  Actions built it in the entry's repository, from the tag, at the commit
- unpacks the Linux build and runs its `--describe`, which must agree with
  the entry on id, name, description, version, `api` and repository

A weekly run checks every entry again, which catches a release deleted or a
tag moved after its entry was merged.

`--describe` runs code from the pull request. It runs on a GitHub-hosted
runner with read-only permissions, and without the workflow's token in its
environment.

## Review

The checks prove where the files came from. A maintainer reviews the rest:

- **The source is public**, and its license allows it to be listed.
- **It does what it says**, and nothing else. In particular, it sends no data
  anywhere the user didn't configure.
- **Secrets are marked** `x-secret` in its settings, and never logged.
- **It copes without M4A**, if it asks for M4A.
- **It starts with its default settings**, or says in its error what to set.
- **The id is fitting** and doesn't impersonate another plugin or service.

For an update, review looks at what changed since the commit in the current
entry: `https://github.com/<owner>/<repo>/compare/<old commit>...<new commit>`.

## License

MIT.
