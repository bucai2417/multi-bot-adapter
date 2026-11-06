#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build distributable packages for the multi-bot-adapter skill.

Outputs (in --out, default dist/):
  multi-bot-adapter-<version>.skill          Claud / Claud Code skill package
  multi-bot-adapter-<version>.zip            Generic distribution zip
  multi-bot-adapter-<version>-codex-plugin.zip  Codex plugin layout
  sha256sums.txt

Only Python standard library is required.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / "SKILL.md"
DIST = ROOT / "dist"

EXCLUDE_DIRS = {
    ".git", ".github", "dist", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".idea", ".vscode", "node_modules",
}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db", ".gitignore"}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".swp", ".tmp")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
DESC_MAX = 1024

REQUIRED_FILES = (
    "SKILL.md",
    "README.md",
    "CHANGELOG.md",
    "agents/openai.yaml",
    ".codex-plugin/plugin.json",
    "assets/icon-512.png",
)


class SkillError(RuntimeError):
    pass


def read_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        raise SkillError("SKILL.md must start with YAML frontmatter")
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        raise SkillError("SKILL.md frontmatter is not terminated")
    data: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.startswith("#") or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key.strip()] = value
    return data


def load_meta() -> dict[str, str]:
    if not SKILL_MD.is_file():
        raise SkillError("SKILL.md not found")
    text = SKILL_MD.read_text(encoding="utf-8")
    fm = read_frontmatter(text)
    name = fm.get("name", "")
    version = fm.get("version", "")
    description = fm.get("description", "")

    errors: list[str] = []
    if not name:
        errors.append("missing 'name'")
    elif not NAME_RE.match(name):
        errors.append(f"name '{name}' must be hyphen-case and at most 64 chars")
    if not version:
        errors.append("missing 'version'")
    elif not VERSION_RE.match(version):
        errors.append(f"version '{version}' must look like 1.2.3")
    if not description:
        errors.append("missing 'description'")
    else:
        if "<" in description or ">" in description:
            errors.append("description cannot contain angle brackets")
        if len(description) > DESC_MAX:
            errors.append(f"description too long ({len(description)} > {DESC_MAX})")

    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            errors.append(f"missing required file: {rel}")

    plugin_json = ROOT / ".codex-plugin" / "plugin.json"
    if plugin_json.is_file():
        try:
            plugin = json.loads(plugin_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f".codex-plugin/plugin.json is invalid JSON: {exc}")
        else:
            if plugin.get("name") and plugin.get("name") != name:
                errors.append("plugin.json name does not match SKILL.md")
            if plugin.get("version") and plugin.get("version") != version:
                errors.append("plugin.json version does not match SKILL.md")

    if errors:
        raise SkillError("\n".join(f"- {e}" for e in errors))
    return {"name": name, "version": version, "description": description}


def iter_files() -> list[str]:
    files: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if rel.name in EXCLUDE_FILES or rel.name.endswith(EXCLUDE_SUFFIX):
            continue
        files.append(rel.as_posix())
    return files


def build_zip(dest: Path, files: list[str], prefix: str = "") -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(ROOT / rel, prefix + rel)


def build_codex_plugin(out: Path, meta: dict[str, str], files: list[str]) -> Path:
    plugin_root = out / "codex-plugin"
    if plugin_root.is_dir():
        shutil.rmtree(plugin_root)
    skill_target = plugin_root / "skills" / meta["name"]

    for rel in files:
        dst = skill_target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)

    src = ROOT / ".codex-plugin" / "plugin.json"
    plugin: dict = {}
    if src.is_file():
        plugin = json.loads(src.read_text(encoding="utf-8"))
    plugin["name"] = meta["name"]
    plugin["version"] = meta["version"]
    plugin["skills"] = "./skills/"
    plugin.setdefault("description", meta["description"])

    plugin_dir = plugin_root / ".codex-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(plugin, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    dest = out / f"{meta['name']}-{meta['version']}-codex-plugin.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(plugin_root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(plugin_root).as_posix())
    shutil.rmtree(plugin_root)
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_zip(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        if "SKILL.md" not in names:
            raise SkillError(f"{path.name}: SKILL.md is not at archive root")
        bad = [n for n in names if n.startswith("/") or ".." in Path(n).parts]
        if bad:
            raise SkillError(f"{path.name}: unsafe entries: {bad[:3]}")


def build(out: Path) -> list[Path]:
    meta = load_meta()
    files = iter_files()

    if out.is_dir():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    skill = out / f"{meta['name']}-{meta['version']}.skill"
    generic = out / f"{meta['name']}-{meta['version']}.zip"
    build_zip(skill, files)
    build_zip(generic, files)
    verify_zip(skill)
    verify_zip(generic)

    codex = build_codex_plugin(out, meta, files)

    sums = out / "sha256sums.txt"
    lines = [
        f"{sha256(p)}  {p.name}"
        for p in sorted(out.iterdir())
        if p.is_file() and p.name != "sha256sums.txt"
    ]
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return [skill, generic, codex, sums]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build multi-bot-adapter skill packages.")
    parser.add_argument("--out", default=str(DIST), help="output directory (default: dist)")
    parser.add_argument("--check-only", action="store_true", help="validate only, do not write files")
    parser.add_argument("--print-version", action="store_true", help="print the SKILL.md version and exit")
    args = parser.parse_args(argv)

    try:
        meta = load_meta()
        if args.print_version:
            print(meta["version"])
            return 0
        if args.check_only:
            print(
                f"valid: name={meta['name']} version={meta['version']} "
                f"description={len(meta['description'])} chars files={len(iter_files())}"
            )
            return 0

        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        written = build(out)
        print(f"built {meta['name']} {meta['version']}:")
        for path in written:
            print(f"  {path.relative_to(ROOT).as_posix()}  ({path.stat().st_size} bytes)")
        return 0
    except SkillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
