#!/usr/bin/env python3
"""The registry's tools.

  registry.py add-release <repository URL> <tag> [--tier official|community]
      Write plugins/<id>.json for a release, from its SHA256SUMS and manifest.
  registry.py check [plugins/<id>.json ...] [--quick] [--changed-since <ref>]
      Check entries (all of them by default). --quick skips the network.
  registry.py build-index
      Rebuild index.json from plugins/*.json.

Only the standard library, so it runs wherever Python 3.9+ does. Checking
build provenance needs the GitHub CLI, `gh`.
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
INDEX = ROOT / "index.json"

# The plugin protocol versions (`api`) the registry lists.
API_VERSIONS = {1}
# What the template's release workflow builds, and the archive each comes in.
TARGETS = {
    "x86_64-unknown-linux-gnu": "tar.gz",
    "aarch64-unknown-linux-gnu": "tar.gz",
    "universal-apple-darwin": "tar.gz",
    "x86_64-pc-windows-msvc": "zip",
}
# CI runs this one's --describe, so every entry needs it.
REQUIRED_TARGET = "x86_64-unknown-linux-gnu"
TIERS = {"official", "community"}
# Only plugins in this GitHub organization can be official.
OFFICIAL_OWNER = "TrunkRecorder"
TOPICS = {"call.start", "call.end", "call.concluded", "unit", "audio", "status"}
FORMATS = {"m4a"}

# An entry's fields, in the order they're written. homepage is optional.
FIELDS = ["id", "name", "description", "repository", "homepage", "license", "tier", "version", "api", "tag", "commit", "assets"]
OPTIONAL = {"homepage"}

ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,40}")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?")
REPO_RE = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/([A-Za-z0-9._-]+)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class Problem(Exception):
    pass


# ─── GitHub ──────────────────────────────────────────────────────────────

def github(path):
    """GET from the GitHub API. GH_TOKEN or GITHUB_TOKEN, when set, raises the rate limit."""
    req = urllib.request.Request("https://api.github.com" + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "trunk-recorder-plugins"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        # Unredirected: never sent on to another host.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise Problem(f"GitHub API {path}: {e.code} {e.reason}") from None


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "trunk-recorder-plugins"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise Problem(f"{url}: {e.code} {e.reason}") from None


def parse_repository(url):
    """(owner, repo) of a GitHub repository URL."""
    url = url.strip().rstrip("/")
    url = url[:-4] if url.endswith(".git") else url
    m = REPO_RE.fullmatch(url)
    if not m:
        raise Problem(f"{url}: not a GitHub repository URL (https://github.com/<owner>/<repo>)")
    return m.group(1), m.group(2)


def tag_commit(owner, repo, tag):
    """The commit a tag points at now (annotated tags followed)."""
    return github(f"/repos/{owner}/{repo}/commits/{tag}")["sha"]


def parse_sums(text):
    """SHA256SUMS (`<sha256>  <name>` lines) → {name: sha256}."""
    sums = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


# ─── Entries ─────────────────────────────────────────────────────────────

def asset_name(id, version, target):
    return f"{id}-{version}-{target}.{TARGETS[target]}"


def asset_url(entry, target):
    return f"{entry['repository']}/releases/download/{entry['tag']}/{asset_name(entry['id'], entry['version'], target)}"


def load(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as e:
        raise Problem(f"{path}: {e}") from None


def dump(entry):
    ordered = {k: entry[k] for k in FIELDS if k in entry}
    return json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"


def entries():
    return [load(p) for p in sorted(PLUGINS.glob("*.json"))]


def validate(entry, file_id=None):
    """What's wrong with an entry on its own, without the network."""
    p = []
    if not isinstance(entry, dict):
        return ["not a JSON object"]
    for k in FIELDS:
        if k not in entry and k not in OPTIONAL:
            p.append(f"{k}: missing")
    for k in entry:
        if k not in FIELDS:
            p.append(f"{k}: not a field the registry knows")
    id = entry.get("id", "")
    if not isinstance(id, str) or not ID_RE.fullmatch(id):
        p.append("id: lowercase letters, digits and dashes (2-41 characters)")
    if file_id is not None and id != file_id:
        p.append(f"id: {id!r}, but the file is plugins/{file_id}.json")
    for k in ("name", "description", "license"):
        if not isinstance(entry.get(k), str) or not entry.get(k, "").strip():
            p.append(f"{k}: empty")
    owner = None
    try:
        owner, _ = parse_repository(entry.get("repository", ""))
        if entry["repository"] != "https://github.com/{}/{}".format(*parse_repository(entry["repository"])):
            p.append("repository: write it as https://github.com/<owner>/<repo>")
    except (Problem, TypeError) as e:
        p.append(f"repository: {e}")
    if "homepage" in entry and not (isinstance(entry["homepage"], str) and entry["homepage"].startswith("https://")):
        p.append("homepage: an https:// URL")
    tier = entry.get("tier")
    if tier not in TIERS:
        p.append(f"tier: one of {', '.join(sorted(TIERS))}")
    elif tier == "official" and owner != OFFICIAL_OWNER:
        p.append(f"tier: only plugins in github.com/{OFFICIAL_OWNER} are official")
    version = entry.get("version", "")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        p.append("version: semver")
    if entry.get("tag") != f"v{version}":
        p.append(f"tag: v{version}, the release workflow's tag for this version")
    if entry.get("api") not in API_VERSIONS:
        p.append(f"api: {entry.get('api')!r} isn't a plugin API the registry lists ({', '.join(map(str, sorted(API_VERSIONS)))})")
    if not isinstance(entry.get("commit"), str) or not COMMIT_RE.fullmatch(entry["commit"]):
        p.append("commit: the tag's full commit SHA")
    assets = entry.get("assets")
    if not isinstance(assets, dict):
        p.append("assets: missing")
        return p
    # The URLs follow from these; without them there's nothing to compare.
    can_url = all(isinstance(entry.get(k), str) for k in ("id", "version", "repository", "tag"))
    if REQUIRED_TARGET not in assets:
        p.append(f"assets: needs {REQUIRED_TARGET}")
    for target, a in assets.items():
        if target not in TARGETS:
            p.append(f"assets: unknown target {target!r}")
            continue
        if not isinstance(a, dict) or set(a) != {"url", "sha256"}:
            p.append(f"assets.{target}: url and sha256")
            continue
        if can_url and a["url"] != asset_url(entry, target):
            p.append(f"assets.{target}.url: should be {asset_url(entry, target)}")
        if not isinstance(a["sha256"], str) or not SHA256_RE.fullmatch(a["sha256"]):
            p.append(f"assets.{target}.sha256: 64 lowercase hex digits")
    return p


def check_manifest(m, entry):
    """What `--describe` printed, against the entry."""
    p = []
    for k in ("id", "version", "api", "name", "description"):
        if m.get(k) != entry.get(k):
            p.append(f"--describe says {k} {m.get(k)!r}, the entry {entry.get(k)!r}")
    try:
        if parse_repository(m.get("repository", "")) != parse_repository(entry["repository"]):
            p.append(f"--describe says repository {m.get('repository')!r}, the entry {entry['repository']!r}")
    except Problem:
        p.append(f"--describe says repository {m.get('repository')!r}: not a GitHub repository")
    if not m.get("subscribe"):
        p.append("--describe: subscribes to nothing")
    for t in m.get("subscribe", []):
        if t not in TOPICS:
            p.append(f"--describe: unknown topic {t!r}")
    for f in m.get("audio_formats", []):
        if f not in FORMATS:
            p.append(f"--describe: unknown audio format {f!r}")
    return p


# ─── Archives ────────────────────────────────────────────────────────────

def host_target():
    """The release target this machine runs, if it's one the registry has."""
    s, m = platform.system(), platform.machine().lower()
    if s == "Linux":
        return {"x86_64": "x86_64-unknown-linux-gnu", "amd64": "x86_64-unknown-linux-gnu", "aarch64": "aarch64-unknown-linux-gnu", "arm64": "aarch64-unknown-linux-gnu"}.get(m)
    if s == "Darwin":
        return "universal-apple-darwin"
    if s == "Windows" and m in ("amd64", "x86_64"):
        return "x86_64-pc-windows-msvc"
    return None


def safe_member(name):
    parts = Path(name).parts
    return not (name.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]))


def unpack(archive, into):
    """Unpack a release archive, refusing paths that leave `into` and links."""
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for n in z.namelist():
                if not safe_member(n):
                    raise Problem(f"{archive.name}: unsafe path {n!r}")
            z.extractall(into)
        return
    with tarfile.open(archive) as t:
        for m in t.getmembers():
            if not safe_member(m.name) or not (m.isfile() or m.isdir()):
                raise Problem(f"{archive.name}: unsafe entry {m.name!r}")
        if hasattr(tarfile, "data_filter"):
            t.extractall(into, filter="data")
        else:
            t.extractall(into)


def describe(archive, id, tmp):
    """Unpack the archive and run the plugin's --describe, with nothing of
    this process's environment (tokens) passed on."""
    out = tmp / "unpacked"
    unpack(archive, out)
    names = {id, f"{id}.exe"}
    exe = next((f for f in out.rglob("*") if f.name in names and f.is_file()), None)
    if exe is None:
        raise Problem(f"{archive.name}: no executable named {id}")
    exe.chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp)}
    if platform.system() == "Windows":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    try:
        r = subprocess.run([str(exe), "--describe"], capture_output=True, timeout=30, env=env, cwd=tmp)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Problem(f"{archive.name}: --describe: {e}") from None
    if r.returncode != 0:
        raise Problem(f"{archive.name}: --describe exited {r.returncode}: {r.stderr.decode(errors='replace')[:500]}")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Problem(f"{archive.name}: --describe didn't print JSON") from None


def attestation(path, entry):
    """Check the file's build provenance: built by GitHub Actions in the
    entry's repository, from its tag, at its commit."""
    if not shutil.which("gh"):
        raise Problem("gh (the GitHub CLI) is needed to check build provenance")
    owner, repo = parse_repository(entry["repository"])
    r = subprocess.run(["gh", "attestation", "verify", str(path), "--repo", f"{owner}/{repo}", "--format", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Problem(f"{path.name}: no valid build provenance from {owner}/{repo}: {r.stderr.strip()[:300]}")
    certs = [a["verificationResult"]["signature"]["certificate"] for a in json.loads(r.stdout)]
    want_ref = f"refs/tags/{entry['tag']}"
    if not any(c.get("sourceRepositoryRef") == want_ref and c.get("sourceRepositoryDigest") == entry["commit"] for c in certs):
        got = ", ".join(f"{c.get('sourceRepositoryRef')} at {str(c.get('sourceRepositoryDigest'))[:7]}" for c in certs)
        raise Problem(f"{path.name}: built from {got}, not {want_ref} at {entry['commit'][:7]}")


# ─── Commands ────────────────────────────────────────────────────────────

def add_release(repository, tag, tier=None):
    owner, repo = parse_repository(repository)
    repository = f"https://github.com/{owner}/{repo}"
    rel = github(f"/repos/{owner}/{repo}/releases/tags/{tag}")
    if rel.get("prerelease"):
        raise Problem(f"{tag} is a prerelease")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if "SHA256SUMS" not in assets:
        raise Problem(f"{tag} has no SHA256SUMS: release it with the template's release workflow")
    sums = parse_sums(download(assets["SHA256SUMS"]).decode())
    manifests = [n for n in assets if n.endswith(".manifest.json")]
    if len(manifests) != 1:
        raise Problem(f"{tag} should have one <id>-<version>.manifest.json, it has {len(manifests)}")
    raw = download(assets[manifests[0]])
    if hashlib.sha256(raw).hexdigest() != sums.get(manifests[0]):
        raise Problem(f"{manifests[0]} doesn't match SHA256SUMS")
    m = json.loads(raw)
    id, version = m.get("id", ""), m.get("version", "")
    if tag != f"v{version}":
        raise Problem(f"the tag is {tag}, but the manifest's version is {version}")
    path = PLUGINS / f"{id}.json"
    old = load(path) if path.exists() else {}
    if old and parse_repository(old["repository"]) != (owner, repo):
        raise Problem(f"plugins/{id}.json is {old['repository']}'s: the id {id!r} is taken")
    entry = {
        "id": id,
        "name": m.get("name", ""),
        "description": m.get("description", ""),
        "repository": repository,
        "license": m.get("license", ""),
        "tier": tier or old.get("tier") or "community",
        "version": version,
        "api": m.get("api"),
        "tag": tag,
        "commit": tag_commit(owner, repo, tag),
        "assets": {},
    }
    if m.get("homepage"):
        entry["homepage"] = m["homepage"]
    for target in TARGETS:
        name = asset_name(id, version, target)
        if name in sums and name in assets:
            entry["assets"][target] = {"url": assets[name], "sha256": sums[name]}
    problems = validate(entry, id)
    if problems:
        raise Problem(f"{repository} {tag}:\n  " + "\n  ".join(problems))
    PLUGINS.mkdir(exist_ok=True)
    path.write_text(dump(entry))
    build_index()
    was = f" (was {old['version']})" if old else ""
    print(f"plugins/{id}.json: {entry['name']} {version}{was}, {len(entry['assets'])} platforms, {entry['tier']}")
    print(f"Check it before you open the pull request: ./check plugins/{id}.json")


def build_index(write=True):
    """index.json: every entry, by id. What the recorder fetches."""
    text = json.dumps({"format": 1, "plugins": [json.loads(dump(e)) for e in sorted(entries(), key=lambda e: e["id"])]}, indent=2, ensure_ascii=False) + "\n"
    if write:
        INDEX.write_text(text)
    return text


def check_entry(path, quick):
    """Problems with one entry. Without `quick`: its tag, every file's
    checksum and provenance, and the plugin's own --describe."""
    entry = load(path)
    problems = validate(entry, Path(path).stem)
    if problems or quick:
        return problems
    owner, repo = parse_repository(entry["repository"])
    try:
        now = tag_commit(owner, repo, entry["tag"])
        if now != entry["commit"]:
            problems.append(f"tag {entry['tag']} now points at {now[:7]}, not {entry['commit'][:7]}: it was moved")
    except Problem as e:
        problems.append(str(e))
    host = host_target()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for target, a in entry["assets"].items():
            try:
                data = download(a["url"])
                if hashlib.sha256(data).hexdigest() != a["sha256"]:
                    problems.append(f"{target}: the download doesn't match its sha256")
                    continue
                f = tmp / a["url"].rsplit("/", 1)[1]
                f.write_bytes(data)
                attestation(f, entry)
                if target == host:
                    d = tmp / target
                    d.mkdir()
                    problems += check_manifest(describe(f, entry["id"], d), entry)
            except Problem as e:
                problems.append(f"{target}: {e}")
    if host not in entry["assets"]:
        print(f"  ({entry['id']}: no {host} build to run --describe on here)")
    return problems


def changed_since(ref):
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", f"{ref}...HEAD", "--", "plugins/"], capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise Problem(f"git diff against {ref}: {r.stderr.strip()}")
    return [ROOT / n for n in r.stdout.split() if n.endswith(".json")]


def check(paths, quick):
    failed = False
    for p in PLUGINS.iterdir():
        if p.suffix != ".json" or p.name.startswith("."):
            print(f"plugins/{p.name}: only <id>.json files belong here")
            failed = True
    if INDEX.exists() and INDEX.read_text() != build_index(write=False):
        print("index.json is out of date: run tools/registry.py build-index")
        failed = True
    for path in paths:
        name = Path(path).relative_to(ROOT) if Path(path).is_absolute() else path
        try:
            problems = check_entry(path, quick)
        except Problem as e:
            problems = [str(e)]
        print(f"{name}: " + ("ok" if not problems else "\n  " + "\n  ".join(problems)))
        failed |= bool(problems)
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(prog="registry.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add-release", help="write plugins/<id>.json for a release")
    a.add_argument("repository")
    a.add_argument("tag")
    a.add_argument("--tier", choices=sorted(TIERS), help="for a new entry: community unless given; an existing one keeps its tier")
    c = sub.add_parser("check", help="check entries")
    c.add_argument("paths", nargs="*")
    c.add_argument("--quick", action="store_true", help="only what can be checked without the network")
    c.add_argument("--changed-since", metavar="REF", help="only entries added or changed since this git ref")
    sub.add_parser("build-index", help="rebuild index.json")
    args = ap.parse_args()
    try:
        if args.cmd == "add-release":
            add_release(args.repository, args.tag, args.tier)
        elif args.cmd == "build-index":
            build_index()
            print("index.json rebuilt")
        else:
            if args.paths:
                paths = [Path(p).resolve() for p in args.paths]
            elif args.changed_since:
                paths = changed_since(args.changed_since)
            else:
                paths = sorted(PLUGINS.glob("*.json"))
            sys.exit(check(paths, args.quick))
    except Problem as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
