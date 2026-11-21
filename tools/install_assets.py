"""Install pinned runtime inputs. Reuse verified files on subsequent setup runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = json.loads((ROOT / "provenance.json").read_text(encoding="utf-8"))
WHISPER_REV = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
WHISPER = "https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo/resolve/" + WHISPER_REV
WHISPER_FILES = {
    "config.json": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
    "preprocessor_config.json": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
    "tokenizer.json": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
    "vocabulary.json": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
    "model.bin": PROVENANCE["models"]["turbo"]["sha256"],
}
YUNET_REV = "f12e12798e8314f7c074a6656816c048dcc95b7a"
YUNET = "face_detection_yunet_2023mar.onnx"
YUNET_SHA = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, destination, digest):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and file_hash(destination) == digest:
        return
    partial = destination.with_name(digest + ".part")
    print(f"Downloading {destination.name}", flush=True)
    options = ["curl.exe", "--fail", "--location", "--silent", "--show-error",
               "--retry", "3", "--connect-timeout", "20", "--speed-limit", "1024", "--speed-time", "90"]
    status = subprocess.run([*options, "--continue-at", "-", "--output", partial, url])
    if status.returncode in (33, 36):  # Some origins do not support resuming.
        subprocess.run([*options, "--output", partial, url], check=True)
    else:
        status.check_returncode()
    if file_hash(partial) != digest:
        partial.unlink()  # Only the installer's failed download.
        raise ValueError(f"SHA-256 mismatch for {destination.name}. Rerun setup to retry.")
    os.replace(partial, destination)


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", repo, *arguments], text=True).strip()


def checkout(repo, spec):
    pin = spec["commit"]
    if git(repo, "rev-parse", "--verify", "HEAD") != pin:
        if git(repo, "status", "--porcelain"):
            raise RuntimeError(f"Local edits in {repo}. Resolve them before updating OpenShorts.")
        git(repo, "fetch", "--depth=1", "--filter=blob:none", "origin", pin)
        git(repo, "checkout", "--detach", pin)


def install_openshorts():
    spec = PROVENANCE["openshorts"]
    repo = ROOT / "vendor" / "openshorts"
    if repo.exists():
        if not (repo / ".git").exists():
            raise ValueError(f"Expected an OpenShorts Git checkout at {repo}")
        checkout(repo, spec)
    else:
        with TemporaryDirectory(dir=repo.parent) as stage:
            src = Path(stage) / "openshorts"
            subprocess.run(["git", "init", "--quiet", src], check=True)
            git(src, "remote", "add", "origin", spec["url"])
            git(src, "sparse-checkout", "set", "--no-cone", *["/" + name for name in spec["files"]])
            git(src, "fetch", "--depth=1", "--filter=blob:none", "origin", spec["commit"])
            git(src, "checkout", "--detach", spec["commit"])
            src.rename(repo)
    for name in spec["files"]:
        # Git compares with the commit after applying its checkout line-ending rules.
        if git(repo, "hash-object", "--path", name, name) != git(repo, "rev-parse", spec["commit"] + ":" + name):
            raise ValueError(f"OpenShorts file differs from its pinned version: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-only", action="store_true", help="Install render dependencies for regression tests")
    args = parser.parse_args()
    install_openshorts()
    face = ROOT / "models" / "face-detect" / YUNET
    url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{YUNET_REV}/models/face_detection_yunet/{YUNET}"
    download(url, face, YUNET_SHA)
    if args.render_only:
        return
    model = ROOT / "models" / "whisper-turbo"
    for name, digest in WHISPER_FILES.items():
        download(f"{WHISPER}/{name}", model / name, digest)
    (model / "verified-sha256.txt").write_text(WHISPER_FILES["model.bin"] + "  model.bin\n", encoding="ascii")


if __name__ == "__main__":
    main()
