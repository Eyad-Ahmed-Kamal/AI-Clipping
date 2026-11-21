"""Install the canonical skill files into the selected Windows host(s)."""
import argparse
from datetime import datetime
from filecmp import cmp
import json
import msvcrt
import os
from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile, TemporaryDirectory
import tomllib
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin"
NAME = "video-clipper"


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == text.encode("utf-8"):
        return
    with NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        tmp.write(text.encode("utf-8"))
    try:
        os.replace(tmp.name, path)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def files(folder):
    return {f.relative_to(folder): f for f in folder.rglob("*") if f.is_file()}


def install_tree(destination, exclude, backup_root):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".clip-install-") as temp:
        stage = Path(temp) / NAME
        def ignore(folder, names):
            ignored = {name for name in names if name == "__pycache__" or name.endswith(".pyc")}
            if Path(folder) == SOURCE:
                ignored.add(exclude)
            return ignored

        shutil.copytree(SOURCE, stage, ignore=ignore)
        staged = files(stage)
        previous = files(destination) if destination.is_dir() else {}
        if staged.keys() == previous.keys() and all(cmp(f, previous[k], shallow=False) for k, f in staged.items()):
            return
        backup = None
        if destination.exists():
            backup_root.mkdir(parents=True, exist_ok=True)
            backup = backup_root / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:8])
            destination.rename(backup)
        try:
            stage.rename(destination)
        except OSError:
            if backup:
                backup.rename(destination)
            raise
        if backup:
            print("Previous plugin files retained at", backup)


def codex_config(path, marketplace):
    raw = path.read_bytes().decode("utf-8") if path.exists() else ""
    config = tomllib.loads(raw.lstrip("\ufeff"))
    entry = config.get("marketplaces", {}).get(NAME)
    if entry is None:
        raw += f'\n[marketplaces.{NAME}]\nsource_type = "local"\nsource = {json.dumps(str(marketplace), ensure_ascii=False)}\n'
    else:
        source = Path(entry.get("source", ""))
        same_path = source.resolve() == marketplace.resolve()
        if not same_path and source.exists() and marketplace.exists():
            same_path = source.samefile(marketplace)
        if entry.get("source_type") != "local" or not same_path:
            raise ValueError(f"Existing {NAME} marketplace points elsewhere in {path}.")
    plugin_id = NAME + "@" + NAME
    entry = config.get("plugins", {}).get(plugin_id)
    if entry is None:
        raw += f'\n[plugins."{plugin_id}"]\nenabled = true\n'
    elif entry.get("enabled") is False:
        print("The existing Codex configuration disables Video Clipper. Enable it in Codex to use it.")
    tomllib.loads(raw.lstrip("\ufeff"))
    return raw


def install(target, profile):
    manifest = json.loads((SOURCE / "plugin.json").read_text(encoding="utf-8"))
    codex_manifest = json.loads((SOURCE / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    if (manifest["name"] != NAME or codex_manifest["name"] != NAME or version != codex_manifest["version"]
            or len(version.split(".")) != 3 or not all(p.isascii() and p.isdigit() for p in version.split("."))):
        raise ValueError("Plugin manifests must agree on name and numeric release version.")
    settings = profile / ".ai-clipping"
    settings.mkdir(parents=True, exist_ok=True)
    with (settings / "install.lock").open("a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        path = settings / "config.json"
        settings_data = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
        if not isinstance(settings_data, dict):
            raise ValueError(f"Expected a JSON object in {path}")
        codex = profile / ".codex"
        marketplace = codex / "plugins" / "cache" / NAME
        if target in ("Both", "Codex"):
            config = codex_config(codex / "config.toml", marketplace)
            install_tree(marketplace / NAME / version, "plugin.json", settings / "backups/codex")
            write_json(marketplace / ".agents/plugins/marketplace.json", {
                "name": NAME, "interface": {"displayName": manifest["displayName"]},
                "plugins": [{"name": NAME, "source": {"source": "local", "path": f"./{NAME}/{version}"},
                             "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL",
                                        "products": ["CODEX"]}, "category": "Creative"}]})
            write_text(codex / "config.toml", config)
        if target in ("Both", "Antigravity"):
            install_tree(profile / ".gemini/config/plugins" / NAME, ".codex-plugin", settings / "backups/antigravity")
        settings_data.update(root=str(ROOT), plugin_version=version)
        write_json(path, settings_data)
    print(f"Installed {NAME} {version} for {target}. Reload the selected application.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=["Both", "Codex", "Antigravity"])
    install(parser.parse_args().target, Path.home())
