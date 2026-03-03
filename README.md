# Pipeline Tools — Portfolio Project
*A production-ready Python toolkit demonstrating TD/pipeline skills for VFX/games studios.*

---

## Tools Included

### 1. 🎬 Scene Validator (`scene_validator/scene_validator.py`)
Validates Maya scenes against production standards before handoff.

**Checks performed:**
| Check | What it catches |
|-------|----------------|
| Naming Conventions | Objects not matching `PascalCase_TYPE` pattern (GEO, CTRL, JNT, GRP, MAT) |
| File Paths | Missing or empty texture paths on file nodes |
| Scale Consistency | Non-frozen transforms — scale ≠ (1, 1, 1) |
| Missing Textures | Shaders with no texture connections |
| Heavy Geometry | Meshes exceeding configurable poly-count limit (default 50k faces) |

**Features:**
- PySide2/PySide6 UI with live filtering and search
- Per-row auto-fix buttons for fixable issues
- JSON report export with SHA-256 file hashes
- Threaded validation (UI stays responsive)
- Log panel + file-based logging to `~/pipeline_logs/`

**Standalone demo (no Maya required):**
```bash
python scene_validator.py
```

**From Maya Script Editor:**
```python
import importlib, scene_validator
importlib.reload(scene_validator)
scene_validator.show_ui()
```

---

### 2. 📦 Asset Publisher (`asset_publisher/asset_publisher.py`)
Versions, packages, and publishes assets to a shared location.

**What it does:**
1. Resolves the next semantic version (`v001`, `v002` …) automatically
2. Creates a clean folder hierarchy: `{type}/{name}/{dept}/{version}/`
3. Copies and checksums all source files
4. Writes a structured **JSON manifest** with file metadata, timestamps, and artist info
5. Generates a minimal **USD payload layer** (`payload.usda`) — no OpenUSD install required
6. Optionally **commits and tags** the publish in Git

**Folder output example:**
```
~/publish_root/
  character/
    HeroCharacter/
      model/
        v001/
          HeroCharacter.ma
          HeroCharacter.obj
          payload.usda        ← USD layer stub
          manifest.json       ← full publish record
        v002/
          ...
```

**manifest.json structure:**
```json
{
  "schema_version": "1.0",
  "asset_name": "HeroCharacter",
  "asset_type": "character",
  "department": "model",
  "version": "v001",
  "published_by": "Jane Smith",
  "published_at": "2024-11-20T14:23:00Z",
  "notes": "First approved model pass",
  "tags": ["hero", "approved"],
  "files": [
    {
      "filename": "HeroCharacter.ma",
      "size_bytes": 2048576,
      "sha256": "abc123...",
      "original_path": "/projects/hero/HeroCharacter.ma"
    }
  ],
  "usd_layer": "path/to/payload.usda",
  "status": "published"
}
```

**Standalone demo:**
```bash
python asset_publisher.py
```

---

## Installation

```bash
# Minimum (UI only)
pip install PySide6

# For full USD layer writing
pip install usd-core

# Already included in Maya 2022+:
# PySide2, Python 3
```

To change the publish root or naming rules, edit the CONFIG section at the top of each file.

---

## Skills Demonstrated

| Skill | Where |
|-------|-------|
| Maya API (cmds, OpenMaya) | scene_validator.py checks |
| PySide2/6 UI design | Both tools |
| QThread (non-blocking UI) | ValidatorThread, PublishThread |
| USD / OpenUSD basics | write_usd_stub() |
| Git automation via subprocess | GitHelper class |
| JSON data schemas | Manifest class |
| Versioning logic | _next_version() |
| SHA-256 file integrity | _sha256() helper |
| Logging (file + UI) | Both tools |
| Production naming conventions | NAMING_RULES dict |

---

*Designed to show studios: "I understand production constraints, asset tracking, and pipeline hygiene."*
