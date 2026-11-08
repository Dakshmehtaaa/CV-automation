"""Copy a provider-free Prospector workspace for integration tests."""
import json
from pathlib import Path
import shutil


def make_prospector_fixture(root):
    """Copy code and settings only; never copy the user's stored records."""
    source = Path(__file__).resolve().parents[2] / "Prospector"
    destination = Path(root) / "Prospector"
    destination.mkdir()
    for name in ("engine", "config", "knowledge", "schemas", "agents", "web"):
        shutil.copytree(source / name, destination / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(source / "server.py", destination / "server.py")
    binaries = destination / "fixture-bin"
    binaries.mkdir()
    for name in ("codex", "claude", "agy"):
        fake = binaries / name
        fake.write_text("#!/bin/sh\necho fixture-cli\n", encoding="utf-8")
        fake.chmod(0o700)
    settings_path = destination / "config" / "settings.json"
    settings = json.loads(settings_path.read_text())
    settings.update(runner="codex", codex_path=str(binaries / "codex"),
                    claude_path=str(binaries / "claude"), gemini_path=str(binaries / "agy"))
    settings["paths"]["cv_repo"] = str(root)
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    data = destination / "data"
    data.mkdir()
    (data / "leads").mkdir()
    # Expired record must survive integrated startup (standalone retention skips it).
    (data / "leads" / "fixture-expired.json").write_text(json.dumps({
        "id": "fixture-expired", "status": "closed", "mode": "consulting",
        "created": "2020-01-01", "updated": "2020-01-01", "company_id": "fictional",
        "person": {"name": "Fictional Person"},
    }))
    return destination
