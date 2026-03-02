"""
Asset Publishing Tool
=====================
Production pipeline tool for versioning, publishing, and tracking assets.

Features:
  - Versioned folder structure  (v001, v002 …)
  - JSON manifest with metadata
  - USD layer stub generation
  - Optional Git integration
  - PySide2/6 UI with drag-and-drop
  - Publish log

Author: Portfolio Tool
Compatible: Python 3.9+, Maya 2020+ (optional), Standalone
"""

import sys
import os
import re
import json
import shutil
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    import maya.cmds as cmds
    MAYA_AVAILABLE = True
except ImportError:
    MAYA_AVAILABLE = False

try:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import Qt, QThread, Signal
    PYSIDE_AVAILABLE = True
except ImportError:
    try:
        from PySide6 import QtWidgets, QtCore, QtGui
        from PySide6.QtCore import Qt, QThread, Signal
        PYSIDE_AVAILABLE = True
    except ImportError:
        PYSIDE_AVAILABLE = False

try:
    from pxr import Usd, UsdGeom, Sdf
    USD_AVAILABLE = True
except ImportError:
    USD_AVAILABLE = False

# ---------------------------------------------------------------------------
# CONFIG  — customise per-studio
# ---------------------------------------------------------------------------
PUBLISH_ROOT = Path(os.path.expanduser("~")) / "publish_root"   # ← change to NFS path

ASSET_TYPES  = ["character", "prop", "environment", "vehicle", "fx", "rig", "lookdev"]
DEPT_STEPS   = ["model", "rig", "lookdev", "fx", "layout", "anim", "lighting"]

FOLDER_TEMPLATE = "{asset_type}/{asset_name}/{dept}/{version}"
VERSION_PREFIX  = "v"
VERSION_PAD     = 3     # v001, v002 …

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _next_version(base_dir: Path) -> str:
    """Scan existing version folders and return the next version string."""
    pattern = re.compile(rf"^{VERSION_PREFIX}(\d+)$")
    existing = [
        int(m.group(1))
        for d in (base_dir.iterdir() if base_dir.exists() else [])
        if (m := pattern.match(d.name))
    ]
    next_num = (max(existing) + 1) if existing else 1
    return f"{VERSION_PREFIX}{str(next_num).zfill(VERSION_PAD)}"


# ---------------------------------------------------------------------------
# MANIFEST
# ---------------------------------------------------------------------------

class Manifest:
    """Reads/writes a structured JSON manifest for a published asset."""

    FILENAME = "manifest.json"

    def __init__(self, publish_path: Path):
        self.publish_path = publish_path
        self.filepath     = publish_path / self.FILENAME
        self.data: dict   = {}

    def write(
        self,
        asset_name: str,
        asset_type: str,
        dept: str,
        version: str,
        source_files: list[str],
        artist: str,
        notes: str,
        tags: list[str],
    ):
        files_meta = []
        for src in source_files:
            p = Path(src)
            files_meta.append({
                "filename": p.name,
                "size_bytes": p.stat().st_size if p.exists() else 0,
                "sha256":     _sha256(src) if p.exists() else "",
                "original_path": str(src),
            })

        self.data = {
            "schema_version": "1.0",
            "asset_name":     asset_name,
            "asset_type":     asset_type,
            "department":     dept,
            "version":        version,
            "publish_path":   str(self.publish_path),
            "published_by":   artist,
            "published_at":   datetime.utcnow().isoformat() + "Z",
            "notes":          notes,
            "tags":           tags,
            "files":          files_meta,
            "dependencies":   [],   # fill in downstream
            "usd_layer":      str(self.publish_path / "payload.usda") if USD_AVAILABLE else None,
            "status":         "published",
        }

        with open(self.filepath, "w") as f:
            json.dump(self.data, f, indent=2)
        return self.data

    def read(self) -> dict:
        if self.filepath.exists():
            with open(self.filepath) as f:
                self.data = json.load(f)
        return self.data


# ---------------------------------------------------------------------------
# USD LAYER WRITER
# ---------------------------------------------------------------------------

def write_usd_stub(publish_path: Path, asset_name: str, geo_files: list[str]):
    """
    Creates a minimal USD stage with:
      - A root Xform for the asset
      - A reference payload for each .obj/.abc/.usd source file
      - Authored metadata (assetInfo)
    """
    stage_path = str(publish_path / "payload.usda")

    if USD_AVAILABLE:
        stage = Usd.Stage.CreateNew(stage_path)
        stage.SetMetadata("comment", f"Auto-generated payload for {asset_name}")

        root = UsdGeom.Xform.Define(stage, f"/{asset_name}")
        model_api = Usd.ModelAPI(root)
        model_api.SetAssetName(asset_name)
        model_api.SetAssetIdentifier(f"{asset_name}/payload.usda")

        for i, gf in enumerate(geo_files):
            ref_prim = stage.DefinePrim(f"/{asset_name}/geo_{i:02d}")
            ref_prim.GetReferences().AddReference(gf)

        stage.GetRootLayer().Save()
    else:
        # Write a plain text stub so the file still exists even without USD libs
        stub = f"""#usda 1.0
(
    comment = "Auto-generated payload for {asset_name}"
    defaultPrim = "{asset_name}"
)

def Xform "{asset_name}" (
    kind = "component"
    assetInfo = {{
        string identifier = "{asset_name}/payload.usda"
        string name = "{asset_name}"
    }}
)
{{
    def Scope "geo"
    {{
        # Geometry references go here
    }}
}}
"""
        with open(stage_path, "w") as f:
            f.write(stub)

    return stage_path


# ---------------------------------------------------------------------------
# GIT INTEGRATION
# ---------------------------------------------------------------------------

class GitHelper:
    """Thin wrapper around subprocess git calls."""

    def __init__(self, repo_path: str):
        self.repo = Path(repo_path)

    def _run(self, *args) -> tuple[int, str]:
        result = subprocess.run(
            ["git", *args],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
        )
        return result.returncode, (result.stdout + result.stderr).strip()

    def init(self):
        if not (self.repo / ".git").exists():
            return self._run("init")
        return 0, "Already a git repo"

    def add(self, path: str = "."):
        return self._run("add", path)

    def commit(self, message: str) -> tuple[int, str]:
        return self._run("commit", "-m", message)

    def tag(self, tag_name: str) -> tuple[int, str]:
        return self._run("tag", tag_name)

    def log(self, n: int = 10) -> str:
        _, out = self._run("log", f"--oneline", f"-{n}")
        return out


# ---------------------------------------------------------------------------
# PUBLISHER CORE
# ---------------------------------------------------------------------------

class AssetPublisher:
    """
    Handles the full publish pipeline:
      1. Resolve publish path & version
      2. Copy source files
      3. Write manifest
      4. Write USD layer
      5. Git commit & tag (optional)
    """

    def __init__(
        self,
        asset_name: str,
        asset_type: str,
        dept: str,
        source_files: list[str],
        artist: str,
        notes: str = "",
        tags: list[str] = None,
        use_git: bool = False,
    ):
        self.asset_name   = asset_name
        self.asset_type   = asset_type
        self.dept         = dept
        self.source_files = source_files
        self.artist       = artist
        self.notes        = notes
        self.tags         = tags or []
        self.use_git      = use_git

        # Resolve paths
        self.base_dir    = PUBLISH_ROOT / asset_type / asset_name / dept
        self.version     = _next_version(self.base_dir)
        self.publish_dir = self.base_dir / self.version

    def publish(self) -> dict:
        """Run all steps. Returns the manifest data."""
        log = []

        # 1. Create publish directory
        self.publish_dir.mkdir(parents=True, exist_ok=True)
        log.append(f"Created: {self.publish_dir}")

        # 2. Copy source files
        copied = []
        for src in self.source_files:
            src_path  = Path(src)
            if not src_path.exists():
                log.append(f"[SKIP] Not found: {src}")
                continue
            dest = self.publish_dir / src_path.name
            shutil.copy2(src, dest)
            copied.append(str(dest))
            log.append(f"Copied: {src_path.name}")

        # 3. Write manifest
        manifest   = Manifest(self.publish_dir)
        manifest_data = manifest.write(
            asset_name   = self.asset_name,
            asset_type   = self.asset_type,
            dept         = self.dept,
            version      = self.version,
            source_files = self.source_files,
            artist       = self.artist,
            notes        = self.notes,
            tags         = self.tags,
        )
        log.append(f"Manifest: {manifest.filepath}")

        # 4. Write USD stub
        geo_files = [f for f in self.source_files if Path(f).suffix in (".obj", ".abc", ".usda", ".usdc", ".usd", ".fbx")]
        usd_path  = write_usd_stub(self.publish_dir, self.asset_name, geo_files)
        log.append(f"USD layer: {usd_path}")

        # 5. Git
        if self.use_git:
            git = GitHelper(str(PUBLISH_ROOT))
            git.init()
            git.add(str(self.publish_dir))
            tag = f"{self.asset_name}-{self.dept}-{self.version}"
            rc, out = git.commit(f"publish: {tag} by {self.artist}")
            log.append(f"Git commit: {out}")
            git.tag(tag)
            log.append(f"Git tag: {tag}")

        manifest_data["_publish_log"] = log
        return manifest_data


# ---------------------------------------------------------------------------
# PYSIDE2 UI
# ---------------------------------------------------------------------------
if PYSIDE_AVAILABLE:

    class PublishThread(QThread):
        log_signal     = Signal(str)
        finished       = Signal(dict)

        def __init__(self, publisher: AssetPublisher):
            super().__init__()
            self._pub = publisher

        def run(self):
            import io, contextlib

            class _Emit:
                def __init__(self, sig): self.sig = sig
                def write(self, m):
                    if m.strip(): self.sig.emit(m.strip())
                def flush(self): pass

            out = _Emit(self.log_signal)
            with contextlib.redirect_stdout(out):
                result = self._pub.publish()
            self.finished.emit(result)

    class AssetPublisherUI(QtWidgets.QMainWindow):

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Asset Publisher  |  Pipeline Tools v1.0")
            self.resize(920, 760)
            self._build_ui()

        def _build_ui(self):
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            root = QtWidgets.QVBoxLayout(central)
            root.setSpacing(10)
            root.setContentsMargins(14, 14, 14, 14)

            # ── Header ────────────────────────────────────────────────
            hdr = QtWidgets.QLabel("📦  Asset Publisher")
            hdr.setStyleSheet("font-size:22px; font-weight:bold; color:#ecf0f1;")
            root.addWidget(hdr)

            sub = QtWidgets.QLabel(
                "Version, manifest, USD layer, and (optionally) Git-commit your assets."
            )
            sub.setStyleSheet("color:#95a5a6; font-size:12px;")
            root.addWidget(sub)

            # ── Form ──────────────────────────────────────────────────
            form = QtWidgets.QFormLayout()
            form.setLabelAlignment(Qt.AlignRight)
            form.setSpacing(8)

            self.inp_name   = QtWidgets.QLineEdit()
            self.inp_name.setPlaceholderText("e.g.  HeroCharacter")

            self.cmb_type   = QtWidgets.QComboBox()
            self.cmb_type.addItems(ASSET_TYPES)

            self.cmb_dept   = QtWidgets.QComboBox()
            self.cmb_dept.addItems(DEPT_STEPS)

            self.inp_artist = QtWidgets.QLineEdit()
            self.inp_artist.setPlaceholderText("Your name")

            self.inp_notes  = QtWidgets.QPlainTextEdit()
            self.inp_notes.setPlaceholderText("Release notes, changes, known issues…")
            self.inp_notes.setMaximumHeight(70)

            self.inp_tags   = QtWidgets.QLineEdit()
            self.inp_tags.setPlaceholderText("comma-separated, e.g.  hero, approved, final")

            self.chk_git    = QtWidgets.QCheckBox("Commit to Git")
            self.chk_git.setStyleSheet("color:#ecf0f1;")

            lbl_style = "color:#bdc3c7; font-size:12px;"
            for label, widget in [
                ("Asset Name",  self.inp_name),
                ("Asset Type",  self.cmb_type),
                ("Department",  self.cmb_dept),
                ("Artist",      self.inp_artist),
                ("Notes",       self.inp_notes),
                ("Tags",        self.inp_tags),
                ("Git",         self.chk_git),
            ]:
                lbl = QtWidgets.QLabel(label)
                lbl.setStyleSheet(lbl_style)
                form.addRow(lbl, widget)

            root.addLayout(form)

            # ── File List ──────────────────────────────────────────────
            file_hdr = QtWidgets.QLabel("Source Files")
            file_hdr.setStyleSheet("color:#bdc3c7; font-size:12px; font-weight:bold; margin-top:6px;")
            root.addWidget(file_hdr)

            self.file_list = QtWidgets.QListWidget()
            self.file_list.setAcceptDrops(True)
            self.file_list.setDragDropMode(QtWidgets.QAbstractItemView.DropOnly)
            self.file_list.setFixedHeight(120)
            self.file_list.setStyleSheet(
                "background:#1e2028; color:#ecf0f1; border:1px dashed #3d4052; font-size:11px;"
            )
            root.addWidget(self.file_list)

            file_btns = QtWidgets.QHBoxLayout()
            self.btn_add_files    = QtWidgets.QPushButton("Add Files…")
            self.btn_add_scene    = QtWidgets.QPushButton("Add Current Maya Scene")
            self.btn_remove_files = QtWidgets.QPushButton("Remove Selected")
            self.btn_add_scene.setEnabled(MAYA_AVAILABLE)
            for b in (self.btn_add_files, self.btn_add_scene, self.btn_remove_files):
                b.setStyleSheet("background:#2c2f3f; color:#ecf0f1; border-radius:3px; padding:4px 10px;")
                file_btns.addWidget(b)
            file_btns.addStretch()
            self.btn_add_files.clicked.connect(self._add_files)
            self.btn_add_scene.clicked.connect(self._add_maya_scene)
            self.btn_remove_files.clicked.connect(self._remove_selected)
            root.addLayout(file_btns)

            # ── Preview ──────────────────────────────────────────────
            self.lbl_preview = QtWidgets.QLabel("Publish path will appear here after filling in the form.")
            self.lbl_preview.setStyleSheet("color:#7f8c8d; font-family:monospace; font-size:11px;")
            root.addWidget(self.lbl_preview)
            self.inp_name.textChanged.connect(self._update_preview)
            self.cmb_type.currentIndexChanged.connect(self._update_preview)
            self.cmb_dept.currentIndexChanged.connect(self._update_preview)

            # ── Log ───────────────────────────────────────────────────
            self.log_box = QtWidgets.QPlainTextEdit()
            self.log_box.setReadOnly(True)
            self.log_box.setMaximumHeight(130)
            self.log_box.setStyleSheet(
                "background:#111318; color:#7f8c8d; font-family:monospace; font-size:11px; border:1px solid #2c2f3f;"
            )
            root.addWidget(self.log_box)

            # ── Publish Button ────────────────────────────────────────
            self.btn_publish = QtWidgets.QPushButton("🚀  Publish Asset")
            self.btn_publish.setFixedHeight(40)
            self.btn_publish.setStyleSheet(
                "background:#e67e22; color:white; font-weight:bold; border-radius:5px; font-size:14px;"
            )
            self.btn_publish.clicked.connect(self._publish)
            root.addWidget(self.btn_publish)

            self.setStyleSheet("QMainWindow, QWidget { background:#181a22; color:#ecf0f1; }"
                               "QLineEdit, QComboBox, QPlainTextEdit { background:#252836; color:#ecf0f1;"
                               " border:1px solid #3d4052; border-radius:3px; padding:3px; }")

        # ---- SLOTS -----------------------------------------------

        def _add_files(self):
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(
                self, "Select Source Files", "",
                "All Files (*);;Maya (*.ma *.mb);;FBX (*.fbx);;USD (*.usda *.usdc *.usd);;OBJ (*.obj)"
            )
            for f in files:
                if not self.file_list.findItems(f, Qt.MatchExactly):
                    self.file_list.addItem(f)

        def _add_maya_scene(self):
            if MAYA_AVAILABLE:
                scene = cmds.file(query=True, sceneName=True)
                if scene and not self.file_list.findItems(scene, Qt.MatchExactly):
                    self.file_list.addItem(scene)

        def _remove_selected(self):
            for item in self.file_list.selectedItems():
                self.file_list.takeItem(self.file_list.row(item))

        def _update_preview(self):
            name = self.inp_name.text().strip() or "<asset_name>"
            atype = self.cmb_type.currentText()
            dept  = self.cmb_dept.currentText()
            base  = PUBLISH_ROOT / atype / name / dept
            nxt   = _next_version(base)
            self.lbl_preview.setText(f"→  {base / nxt}")

        def _log(self, msg, color="#7f8c8d"):
            self.log_box.appendHtml(f'<span style="color:{color}">{msg}</span>')
            self.log_box.verticalScrollBar().setValue(self.log_box.verticalScrollBar().maximum())

        def _publish(self):
            name   = self.inp_name.text().strip()
            artist = self.inp_artist.text().strip()
            files  = [self.file_list.item(i).text() for i in range(self.file_list.count())]

            if not name:
                QtWidgets.QMessageBox.warning(self, "Missing Field", "Please enter an Asset Name.")
                return
            if not artist:
                QtWidgets.QMessageBox.warning(self, "Missing Field", "Please enter your name.")
                return
            if not files:
                QtWidgets.QMessageBox.warning(self, "No Files", "Please add at least one source file.")
                return

            tags = [t.strip() for t in self.inp_tags.text().split(",") if t.strip()]

            publisher = AssetPublisher(
                asset_name   = name,
                asset_type   = self.cmb_type.currentText(),
                dept         = self.cmb_dept.currentText(),
                source_files = files,
                artist       = artist,
                notes        = self.inp_notes.toPlainText(),
                tags         = tags,
                use_git      = self.chk_git.isChecked(),
            )

            self.btn_publish.setEnabled(False)
            self._log(f"Publishing {name} v{publisher.version}…", "#e67e22")

            self._thread = PublishThread(publisher)
            self._thread.log_signal.connect(lambda m: self._log(m))
            self._thread.finished.connect(self._on_publish_done)
            self._thread.start()

        def _on_publish_done(self, result):
            self.btn_publish.setEnabled(True)
            version = result.get("version", "?")
            path    = result.get("publish_path", "?")
            for entry in result.get("_publish_log", []):
                color = "#e74c3c" if "SKIP" in entry or "ERROR" in entry else "#2ecc71"
                self._log(f"  {entry}", color)
            self._log(f"✔  Published {version}  →  {path}", "#2ecc71")
            self._update_preview()
            QtWidgets.QMessageBox.information(
                self, "Publish Complete",
                f"Asset published as {version}\n\nPath: {path}"
            )


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def show_ui():
    if not PYSIDE_AVAILABLE:
        print("PySide2/6 not installed. Run: pip install PySide6")
        return
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = AssetPublisherUI()
    win.show()
    if not MAYA_AVAILABLE:
        sys.exit(app.exec_())


if __name__ == "__main__":
    show_ui()
