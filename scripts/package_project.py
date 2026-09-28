"""Build a source-only archive, deliberately excluding secrets, evidence, and dependencies."""
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "LogForge-AI-Source.zip"
DIRECTORIES = {
    "backend/app": {".py"}, "backend/config": {".yaml"}, "backend/tests": {".py"},
    "frontend/src": {".ts", ".tsx", ".css"}, "frontend/public": {".svg"},
    "docs": {".md", ".json", ".png"}, "scripts": {".py", ".ps1", ".sh"},
    "deploy": {".py", ".conf"}, ".github/workflows": {".yml", ".yaml"},
}
FILES = ["README.md", ".env.example", ".gitignore", "LOGFORGE_AI_PHASE_1_TO_9_COMPLETE_FLOW.txt",
         "backend/requirements.txt", "backend/requirements.lock.txt", "frontend/package.json",
         "frontend/package-lock.json", "frontend/pnpm-lock.yaml", "frontend/pnpm-workspace.yaml", "frontend/tsconfig.json", "frontend/vite.config.ts", "frontend/index.html"]


def main():
    paths = {ROOT / name for name in FILES if (ROOT / name).is_file()}
    for directory, suffixes in DIRECTORIES.items():
        paths.update(path for path in (ROOT / directory).rglob("*") if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts)
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(paths):
            archive.write(path, "LogForge-AI/" + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(OUTPUT) as archive:
        if archive.testzip(): raise RuntimeError("Archive integrity check failed")
        assert all(not item.endswith("/.env") and "/data/" not in item and "node_modules" not in item for item in archive.namelist())
    print(f"Packaged {len(paths)} source/documentation files: {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__": main()
