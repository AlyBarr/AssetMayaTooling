"""
Scene Validator Tool
====================
Production pipeline tool for validating Maya/DCC scene files.
Checks naming conventions, file paths, scale, textures, and geometry.

Author: Portfolio Tool
Compatible: Maya 2020+, Standalone PySide2
"""

import sys
import os
import re
import json
import logging
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Try to import Maya / PySide2 — fall back gracefully for standalone testing
# ---------------------------------------------------------------------------
try:
    import maya.cmds as cmds
    import maya.OpenMaya as om
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


# ---------------------------------------------------------------------------
# LOGGING SETUP
# ---------------------------------------------------------------------------
LOG_DIR = Path(os.path.expanduser("~")) / "pipeline_logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"scene_validator_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("SceneValidator")


# ---------------------------------------------------------------------------
# VALIDATION RULES  (edit these to match your studio conventions)
# ---------------------------------------------------------------------------
NAMING_RULES = {
    "geo":      re.compile(r"^[A-Z][a-zA-Z0-9]+_GEO$"),
    "ctrl":     re.compile(r"^[A-Z][a-zA-Z0-9]+_CTRL$"),
    "jnt":      re.compile(r"^[A-Z][a-zA-Z0-9]+_JNT$"),
    "grp":      re.compile(r"^[A-Z][a-zA-Z0-9]+_GRP$"),
    "mat":      re.compile(r"^[A-Z][a-zA-Z0-9]+_MAT$"),
    "tex":      re.compile(r"^[A-Z][a-zA-Z0-9]+_(diffuse|roughness|normal|metalness)_(1k|2k|4k|8k)\.(png|exr|tif)$"),
}

SCALE_TOLERANCE   = 0.001   # world units — objects should be ~1 unit if no scale applied
MAX_POLY_COUNT    = 50_000  # polys per mesh before flagging
EXPECTED_SCALE    = (1.0, 1.0, 1.0)


# ---------------------------------------------------------------------------
# VALIDATION RESULT DATACLASS
# ---------------------------------------------------------------------------
class ValidationResult:
    """Single validation finding."""

    PASS    = "PASS"
    WARNING = "WARNING"
    ERROR   = "ERROR"

    def __init__(self, check, node, status, message, fix_fn=None):
        self.check   = check
        self.node    = node
        self.status  = status
        self.message = message
        self.fix_fn  = fix_fn   # callable or None

    def to_dict(self):
        return {
            "check":   self.check,
            "node":    self.node,
            "status":  self.status,
            "message": self.message,
            "fixable": self.fix_fn is not None,
        }

    def __repr__(self):
        return f"[{self.status}] {self.check} | {self.node} — {self.message}"


# ---------------------------------------------------------------------------
# VALIDATOR CORE
# ---------------------------------------------------------------------------
class SceneValidator:
    """
    Runs all validation checks and collects ValidationResult objects.
    Works in Maya or in standalone mode (with mock data for demo).
    """

    def __init__(self):
        self.results: list[ValidationResult] = []

    def run_all(self) -> list[ValidationResult]:
        """Execute every registered check and return results."""
        self.results = []
        logger.info("=== Scene Validation Started ===")

        if MAYA_AVAILABLE:
            self._check_naming_conventions()
            self._check_file_paths()
            self._check_scale_consistency()
            self._check_missing_textures()
            self._check_heavy_geometry()
        else:
            logger.warning("Maya not found — running in DEMO mode with mock data.")
            self._mock_results()

        self._log_summary()
        return self.results

    # ------------------------------------------------------------------
    # CHECKS
    # ------------------------------------------------------------------

    def _check_naming_conventions(self):
        logger.info("Checking naming conventions…")
        suffix_map = {
            "mesh":         ("_GEO", NAMING_RULES["geo"]),
            "joint":        ("_JNT", NAMING_RULES["jnt"]),
            "nurbsCurve":   ("_CTRL", NAMING_RULES["ctrl"]),
            "transform":    ("_GRP", NAMING_RULES["grp"]),
        }
        for node_type, (suffix, pattern) in suffix_map.items():
            nodes = cmds.ls(type=node_type, long=False) or []
            for node in nodes:
                short = node.split("|")[-1]
                if not pattern.match(short):
                    self.results.append(ValidationResult(
                        check="Naming Convention",
                        node=node,
                        status=ValidationResult.ERROR,
                        message=f"'{short}' does not match expected pattern (e.g. 'Body{suffix}')",
                        fix_fn=None,  # rename requires human decision
                    ))
                    logger.error("Naming: %s", node)
                else:
                    self.results.append(ValidationResult(
                        check="Naming Convention",
                        node=node,
                        status=ValidationResult.PASS,
                        message="OK",
                    ))

    def _check_file_paths(self):
        logger.info("Checking file paths…")
        file_nodes = cmds.ls(type="file") or []
        for fn in file_nodes:
            path_attr = f"{fn}.fileTextureName"
            tex_path  = cmds.getAttr(path_attr) or ""
            if not tex_path:
                self.results.append(ValidationResult(
                    check="File Path",
                    node=fn,
                    status=ValidationResult.WARNING,
                    message="Texture path is empty.",
                ))
            elif not os.path.exists(tex_path):
                self.results.append(ValidationResult(
                    check="File Path",
                    node=fn,
                    status=ValidationResult.ERROR,
                    message=f"File not found on disk: {tex_path}",
                    fix_fn=lambda n=fn: self._fix_missing_path(n),
                ))
                logger.error("Missing file: %s → %s", fn, tex_path)
            else:
                self.results.append(ValidationResult(
                    check="File Path",
                    node=fn,
                    status=ValidationResult.PASS,
                    message=f"OK: {tex_path}",
                ))

    def _check_scale_consistency(self):
        logger.info("Checking scale consistency…")
        meshes = cmds.ls(type="mesh", long=True) or []
        for mesh in meshes:
            transform = (cmds.listRelatives(mesh, parent=True, fullPath=True) or [None])[0]
            if not transform:
                continue
            sx = cmds.getAttr(f"{transform}.scaleX")
            sy = cmds.getAttr(f"{transform}.scaleY")
            sz = cmds.getAttr(f"{transform}.scaleZ")
            scale = (round(sx, 4), round(sy, 4), round(sz, 4))
            if any(abs(v - 1.0) > SCALE_TOLERANCE for v in scale):
                self.results.append(ValidationResult(
                    check="Scale Consistency",
                    node=transform,
                    status=ValidationResult.ERROR,
                    message=f"Non-uniform/non-frozen scale: {scale}",
                    fix_fn=lambda n=transform: cmds.makeIdentity(n, apply=True, scale=True),
                ))
                logger.error("Scale: %s → %s", transform, scale)
            else:
                self.results.append(ValidationResult(
                    check="Scale Consistency",
                    node=transform,
                    status=ValidationResult.PASS,
                    message=f"Scale OK: {scale}",
                ))

    def _check_missing_textures(self):
        """Same as file path check but focused on arnold/vray texture nodes."""
        logger.info("Checking for missing textures (aiImage / aiStandardSurface)…")
        # Already covered in _check_file_paths; add shader-level check here
        shaders = cmds.ls(materials=True) or []
        for sh in shaders:
            conns = cmds.listConnections(sh, source=True, destination=False) or []
            has_tex = any(cmds.nodeType(c) == "file" for c in conns)
            if not has_tex:
                self.results.append(ValidationResult(
                    check="Missing Texture",
                    node=sh,
                    status=ValidationResult.WARNING,
                    message="Shader has no texture connections — may be a placeholder.",
                ))

    def _check_heavy_geometry(self):
        logger.info("Checking polygon counts…")
        meshes = cmds.ls(type="mesh", long=True) or []
        for mesh in meshes:
            poly_count = cmds.polyEvaluate(mesh, face=True)
            if isinstance(poly_count, int) and poly_count > MAX_POLY_COUNT:
                self.results.append(ValidationResult(
                    check="Heavy Geometry",
                    node=mesh,
                    status=ValidationResult.WARNING,
                    message=f"{poly_count:,} faces exceeds limit of {MAX_POLY_COUNT:,}",
                ))
                logger.warning("Heavy geo: %s — %d faces", mesh, poly_count)
            else:
                self.results.append(ValidationResult(
                    check="Heavy Geometry",
                    node=mesh,
                    status=ValidationResult.PASS,
                    message=f"{poly_count} faces — OK",
                ))

    # ------------------------------------------------------------------
    # AUTO-FIX HELPERS
    # ------------------------------------------------------------------

    def _fix_missing_path(self, file_node):
        """Open a file browser so the user can relocate the texture."""
        if MAYA_AVAILABLE:
            new_path = cmds.fileDialog2(fileMode=1, caption="Locate Missing Texture")
            if new_path:
                cmds.setAttr(f"{file_node}.fileTextureName", new_path[0], type="string")
                logger.info("Fixed path: %s → %s", file_node, new_path[0])

    def fix_all_auto(self):
        """Run all available auto-fix functions."""
        fixed = 0
        for r in self.results:
            if r.fix_fn and r.status in (ValidationResult.ERROR, ValidationResult.WARNING):
                try:
                    r.fix_fn()
                    r.status  = ValidationResult.PASS
                    r.message = "(Auto-fixed) " + r.message
                    fixed += 1
                except Exception as e:
                    logger.error("Auto-fix failed for %s: %s", r.node, e)
        logger.info("Auto-fixed %d issue(s).", fixed)
        return fixed

    # ------------------------------------------------------------------
    # DEMO / MOCK
    # ------------------------------------------------------------------

    def _mock_results(self):
        """Return realistic-looking results for portfolio demo without Maya."""
        mock = [
            ("Naming Convention", "pCube1",           ValidationResult.ERROR,   "Does not match pattern (expected 'Body_GEO')"),
            ("Naming Convention", "Body_GEO",          ValidationResult.PASS,    "OK"),
            ("Naming Convention", "Head_GEO",          ValidationResult.PASS,    "OK"),
            ("Naming Convention", "ctrl_left_hand",    ValidationResult.ERROR,   "Does not match pattern (expected 'LeftHand_CTRL')"),
            ("File Path",         "file1",             ValidationResult.ERROR,   "File not found: /proj/tex/body_diffuse.exr", lambda: None),
            ("File Path",         "file2",             ValidationResult.PASS,    "OK: /proj/tex/head_diffuse_4k.exr"),
            ("File Path",         "file3",             ValidationResult.WARNING, "Texture path is empty."),
            ("Scale Consistency", "Body_GEO",          ValidationResult.ERROR,   "Non-frozen scale: (1.5, 1.5, 1.5)", lambda: None),
            ("Scale Consistency", "Head_GEO",          ValidationResult.PASS,    "Scale OK: (1.0, 1.0, 1.0)"),
            ("Missing Texture",   "lambert1",          ValidationResult.WARNING, "Shader has no texture connections."),
            ("Heavy Geometry",    "crowd_mesh_GEO",    ValidationResult.WARNING, "82,400 faces exceeds limit of 50,000"),
            ("Heavy Geometry",    "Body_GEO",          ValidationResult.PASS,    "12,340 faces — OK"),
            ("Heavy Geometry",    "Head_GEO",          ValidationResult.PASS,    "8,200 faces — OK"),
        ]
        for check, node, status, msg, *fix in mock:
            self.results.append(ValidationResult(
                check=check,
                node=node,
                status=status,
                message=msg,
                fix_fn=fix[0] if fix else None,
            ))
            logger.log(
                logging.ERROR if status == ValidationResult.ERROR
                else logging.WARNING if status == ValidationResult.WARNING
                else logging.INFO,
                "%s | %s — %s", check, node, msg,
            )

    # ------------------------------------------------------------------
    # REPORTING
    # ------------------------------------------------------------------

    def _log_summary(self):
        errors   = sum(1 for r in self.results if r.status == ValidationResult.ERROR)
        warnings = sum(1 for r in self.results if r.status == ValidationResult.WARNING)
        passed   = sum(1 for r in self.results if r.status == ValidationResult.PASS)
        logger.info("=== SUMMARY: %d PASS | %d WARNING | %d ERROR ===", passed, warnings, errors)

    def export_report(self, filepath: str):
        """Write JSON report to disk."""
        data = {
            "timestamp": datetime.now().isoformat(),
            "results":   [r.to_dict() for r in self.results],
            "summary": {
                "total":    len(self.results),
                "pass":     sum(1 for r in self.results if r.status == ValidationResult.PASS),
                "warnings": sum(1 for r in self.results if r.status == ValidationResult.WARNING),
                "errors":   sum(1 for r in self.results if r.status == ValidationResult.ERROR),
            },
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Report saved: %s", filepath)


# ---------------------------------------------------------------------------
# PYSIDE2 UI
# ---------------------------------------------------------------------------
if PYSIDE_AVAILABLE:

    STATUS_COLORS = {
        ValidationResult.PASS:    "#2ecc71",
        ValidationResult.WARNING: "#f39c12",
        ValidationResult.ERROR:   "#e74c3c",
    }

    class ValidatorThread(QThread):
        """Run validation off the main thread so the UI stays responsive."""
        finished = Signal(list)

        def run(self):
            v = SceneValidator()
            results = v.run_all()
            self._validator = v
            self.finished.emit(results)

    class ResultItem(QtWidgets.QTreeWidgetItem):
        def __init__(self, result: ValidationResult):
            super().__init__()
            self.result = result
            self.setText(0, result.status)
            self.setText(1, result.check)
            self.setText(2, result.node)
            self.setText(3, result.message)
            self.setText(4, "✔ Auto-fix" if result.fix_fn else "—")

            color = QtGui.QColor(STATUS_COLORS[result.status])
            for col in range(5):
                self.setForeground(col, color)

    class SceneValidatorUI(QtWidgets.QMainWindow):

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Scene Validator  |  Pipeline Tools v1.0")
            self.resize(1100, 680)
            self._validator = None
            self._results   = []
            self._build_ui()

        # ---- UI BUILD ------------------------------------------------

        def _build_ui(self):
            central = QtWidgets.QWidget()
            self.setCentralWidget(central)
            root = QtWidgets.QVBoxLayout(central)
            root.setSpacing(8)
            root.setContentsMargins(12, 12, 12, 12)

            # ── Header ──────────────────────────────────────────────
            header = QtWidgets.QLabel("🎬  Scene Validator")
            header.setStyleSheet("font-size:22px; font-weight:bold; color:#ecf0f1;")
            root.addWidget(header)

            sub = QtWidgets.QLabel(
                "Validates naming conventions, file paths, scale, textures, and geometry."
            )
            sub.setStyleSheet("color:#95a5a6; font-size:12px;")
            root.addWidget(sub)

            # ── Filter Bar ──────────────────────────────────────────
            filter_row = QtWidgets.QHBoxLayout()
            self.filter_all  = self._filter_btn("All",      True)
            self.filter_pass = self._filter_btn("Pass",     False)
            self.filter_warn = self._filter_btn("Warning",  False)
            self.filter_err  = self._filter_btn("Error",    False)
            for btn in (self.filter_all, self.filter_pass, self.filter_warn, self.filter_err):
                filter_row.addWidget(btn)
            filter_row.addStretch()
            self.search_box = QtWidgets.QLineEdit()
            self.search_box.setPlaceholderText("Search node / message…")
            self.search_box.setFixedWidth(260)
            self.search_box.textChanged.connect(self._apply_filter)
            filter_row.addWidget(self.search_box)
            root.addLayout(filter_row)

            # ── Results Tree ─────────────────────────────────────────
            self.tree = QtWidgets.QTreeWidget()
            self.tree.setHeaderLabels(["Status", "Check", "Node", "Message", "Fix"])
            self.tree.setColumnWidth(0, 90)
            self.tree.setColumnWidth(1, 170)
            self.tree.setColumnWidth(2, 200)
            self.tree.setColumnWidth(3, 420)
            self.tree.setColumnWidth(4, 90)
            self.tree.setAlternatingRowColors(True)
            self.tree.setRootIsDecorated(False)
            self.tree.setSortingEnabled(True)
            self.tree.setStyleSheet("""
                QTreeWidget { background:#1e2028; color:#ecf0f1; border:1px solid #3d4052; font-size:12px; }
                QTreeWidget::item:alternate { background:#252836; }
                QTreeWidget::item:selected  { background:#2980b9; }
                QHeaderView::section { background:#2c2f3f; color:#bdc3c7; padding:4px; border:none; }
            """)
            root.addWidget(self.tree)

            # ── Stats Bar ────────────────────────────────────────────
            stats_row = QtWidgets.QHBoxLayout()
            self.lbl_pass = self._stat_label("Pass: 0",    "#2ecc71")
            self.lbl_warn = self._stat_label("Warn: 0",    "#f39c12")
            self.lbl_err  = self._stat_label("Error: 0",   "#e74c3c")
            self.lbl_total= self._stat_label("Total: 0",   "#95a5a6")
            for lbl in (self.lbl_pass, self.lbl_warn, self.lbl_err, self.lbl_total):
                stats_row.addWidget(lbl)
            stats_row.addStretch()
            root.addLayout(stats_row)

            # ── Buttons ──────────────────────────────────────────────
            btn_row = QtWidgets.QHBoxLayout()

            self.btn_validate = QtWidgets.QPushButton("▶  Run Validation")
            self.btn_validate.setFixedHeight(36)
            self.btn_validate.setStyleSheet(
                "background:#2980b9; color:white; font-weight:bold; border-radius:4px; font-size:13px;"
            )
            self.btn_validate.clicked.connect(self._run_validation)

            self.btn_fix = QtWidgets.QPushButton("⚡  Auto-Fix All")
            self.btn_fix.setFixedHeight(36)
            self.btn_fix.setEnabled(False)
            self.btn_fix.setStyleSheet(
                "background:#27ae60; color:white; font-weight:bold; border-radius:4px; font-size:13px;"
                "QPushButton:disabled{background:#1a6e3c;}"
            )
            self.btn_fix.clicked.connect(self._auto_fix)

            self.btn_report = QtWidgets.QPushButton("💾  Export Report")
            self.btn_report.setFixedHeight(36)
            self.btn_report.setEnabled(False)
            self.btn_report.setStyleSheet(
                "background:#8e44ad; color:white; font-weight:bold; border-radius:4px; font-size:13px;"
            )
            self.btn_report.clicked.connect(self._export_report)

            self.progress = QtWidgets.QProgressBar()
            self.progress.setRange(0, 0)
            self.progress.setFixedHeight(8)
            self.progress.setVisible(False)
            self.progress.setTextVisible(False)
            self.progress.setStyleSheet("QProgressBar::chunk{background:#2980b9;}")

            btn_row.addWidget(self.btn_validate)
            btn_row.addWidget(self.btn_fix)
            btn_row.addWidget(self.btn_report)
            root.addLayout(btn_row)
            root.addWidget(self.progress)

            # ── Log Panel ─────────────────────────────────────────────
            self.log_text = QtWidgets.QPlainTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setMaximumHeight(120)
            self.log_text.setStyleSheet(
                "background:#111318; color:#7f8c8d; font-family:monospace; font-size:11px; border:1px solid #2c2f3f;"
            )
            root.addWidget(self.log_text)

            # Install log handler to mirror into UI
            ui_handler = _UILogHandler(self.log_text)
            ui_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(ui_handler)

            # Dark window
            self.setStyleSheet("QMainWindow, QWidget { background:#181a22; color:#ecf0f1; }")

        # ---- HELPERS -------------------------------------------------

        def _filter_btn(self, label, checked):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(checked)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton{background:#2c2f3f; color:#bdc3c7; border-radius:3px; padding:0 12px;}"
                "QPushButton:checked{background:#2980b9; color:white;}"
            )
            btn.toggled.connect(self._apply_filter)
            return btn

        def _stat_label(self, text, color):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet(f"color:{color}; font-weight:bold; font-size:13px; padding:2px 10px;")
            return lbl

        # ---- SLOTS ---------------------------------------------------

        def _run_validation(self):
            self.tree.clear()
            self.btn_validate.setEnabled(False)
            self.btn_fix.setEnabled(False)
            self.btn_report.setEnabled(False)
            self.progress.setVisible(True)

            self._thread = ValidatorThread()
            self._thread.finished.connect(self._on_validation_done)
            self._thread.start()

        def _on_validation_done(self, results):
            self._results   = results
            self._validator = self._thread._validator
            self.progress.setVisible(False)
            self.btn_validate.setEnabled(True)
            self.btn_fix.setEnabled(any(r.fix_fn for r in results))
            self.btn_report.setEnabled(True)
            self._populate_tree(results)
            self._update_stats(results)

        def _populate_tree(self, results):
            self.tree.clear()
            status_filter = set()
            if self.filter_all.isChecked():
                status_filter = {ValidationResult.PASS, ValidationResult.WARNING, ValidationResult.ERROR}
            else:
                if self.filter_pass.isChecked(): status_filter.add(ValidationResult.PASS)
                if self.filter_warn.isChecked(): status_filter.add(ValidationResult.WARNING)
                if self.filter_err.isChecked():  status_filter.add(ValidationResult.ERROR)

            query = self.search_box.text().lower()
            for r in results:
                if r.status not in status_filter:
                    continue
                if query and query not in r.node.lower() and query not in r.message.lower():
                    continue
                self.tree.addTopLevelItem(ResultItem(r))

        def _apply_filter(self):
            if self._results:
                self._populate_tree(self._results)

        def _update_stats(self, results):
            p = sum(1 for r in results if r.status == ValidationResult.PASS)
            w = sum(1 for r in results if r.status == ValidationResult.WARNING)
            e = sum(1 for r in results if r.status == ValidationResult.ERROR)
            self.lbl_pass.setText(f"Pass: {p}")
            self.lbl_warn.setText(f"Warn: {w}")
            self.lbl_err.setText(f"Error: {e}")
            self.lbl_total.setText(f"Total: {len(results)}")

        def _auto_fix(self):
            if not self._validator:
                return
            fixed = self._validator.fix_all_auto()
            QtWidgets.QMessageBox.information(
                self, "Auto-Fix Complete", f"Fixed {fixed} issue(s).\nRe-run validation to confirm."
            )
            self._results = self._validator.results
            self._populate_tree(self._results)
            self._update_stats(self._results)

        def _export_report(self):
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Export Report", str(LOG_DIR / "validation_report.json"),
                "JSON (*.json)"
            )
            if path and self._validator:
                self._validator.export_report(path)
                QtWidgets.QMessageBox.information(self, "Exported", f"Report saved to:\n{path}")

    class _UILogHandler(logging.Handler):
        """Pipe log output into the UI log widget."""
        def __init__(self, widget):
            super().__init__()
            self._widget = widget

        def emit(self, record):
            msg = self.format(record)
            color = {"ERROR": "#e74c3c", "WARNING": "#f39c12", "INFO": "#7f8c8d"}.get(record.levelname, "#7f8c8d")
            self._widget.appendHtml(f'<span style="color:{color}">{msg}</span>')
            self._widget.verticalScrollBar().setValue(self._widget.verticalScrollBar().maximum())


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
def show_ui():
    """Launch standalone or inside Maya."""
    if not PYSIDE_AVAILABLE:
        logger.error("PySide2/6 not installed. Run: pip install PySide6")
        return

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = SceneValidatorUI()
    win.show()

    if not MAYA_AVAILABLE:
        sys.exit(app.exec_())


if __name__ == "__main__":
    show_ui()
