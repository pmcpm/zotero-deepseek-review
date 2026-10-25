"""Build public source/skill ZIPs and a credential-free Zotero XPI.

An allowlist prevents local task files, credentials and caches entering releases.
"""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/zotero-deepseek-review"


def sources():
    paths = [ROOT / p for p in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "VERSION", ".gitignore", ".gitattributes",
                               ".github/workflows/tests.yml", "config/project-defaults.json",
                               "examples/model-cstcloud.json", "examples/model-openai-compatible.json", "examples/model-local.json",
                               "tools/install-skill.ps1", "tools/build-release.py")]
    paths += [SKILL / "SKILL.md", SKILL / "agents/openai.yaml"]
    for directory, extensions in (("scripts", {".py", ".ps1"}), ("references", {".md"}), ("tests", {".py"})):
        paths += [p for p in (SKILL / directory).iterdir() if p.is_file() and p.suffix in extensions]
    paths += [SKILL / "assets/zotero-bridge" / p for p in ("manifest.json", "bootstrap.js", "csl-runtime.js", "LICENSE")]
    for path in paths:
        if not path.is_file() or path.is_symlink():
            raise ValueError("Required regular source file missing: " + str(path.relative_to(ROOT)))
    return sorted(paths)


def archive(output, paths, relative_to, prefix):
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in paths:
            info = zipfile.ZipInfo(prefix + path.relative_to(relative_to).as_posix(), (2026, 10, 25, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            handle.writestr(info, path.read_bytes())


def main():
    version = (ROOT / "VERSION").read_text(encoding="utf8").strip()
    files = sources()
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    spec = importlib.util.spec_from_file_location("package_bridge", SKILL / "scripts/package_bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    bridge_version = json.loads((SKILL / "assets/zotero-bridge/manifest.json").read_text(encoding="utf8"))["version"]
    xpi = output / f"codex-literature-bridge-{bridge_version}.xpi"
    bridge.build(xpi)
    project_zip = output / f"zotero-deepseek-review-project-{version}.zip"
    skill_zip = output / f"zotero-deepseek-review-skill-{version}.zip"
    archive(project_zip, files, ROOT, "zotero-deepseek-review/")
    archive(skill_zip, [p for p in files if p.is_relative_to(SKILL)], SKILL, "zotero-deepseek-review/")
    manifest = {"version": version, "bridge_version": bridge_version, "default_provider": "cstcloud",
                "files": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
                "artifacts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (xpi, project_zip, skill_zip)}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"version": version, "source_files": len(files), "artifacts": list(manifest["artifacts"])}, indent=2))


if __name__ == "__main__":
    main()
