"""
Maya Shelf Launcher
===================
Paste this into Maya's Script Editor (Python tab) once to install
both pipeline tools onto a custom shelf.

Run this script ONCE to set up the shelf.
After that, click the shelf buttons to launch each tool.
"""

import maya.cmds as cmds
import maya.mel  as mel

SHELF_NAME = "PipelineTools"
TOOLS_DIR  = r"C:/pipeline_tools"   # ← update to your actual path


def _make_shelf():
    # Delete existing shelf of same name to avoid duplicates
    if cmds.shelfLayout(SHELF_NAME, exists=True):
        cmds.deleteUI(SHELF_NAME)

    top_shelf = mel.eval("$tmp = $gShelfTopLevel")
    cmds.shelfLayout(SHELF_NAME, parent=top_shelf)

    # ── Scene Validator ─────────────────────────────────────────────
    validator_cmd = f"""
import sys
if r"{TOOLS_DIR}/scene_validator" not in sys.path:
    sys.path.insert(0, r"{TOOLS_DIR}/scene_validator")
import importlib, scene_validator
importlib.reload(scene_validator)
scene_validator.show_ui()
"""
    cmds.shelfButton(
        parent=SHELF_NAME,
        label="SceneVal",
        annotation="Scene Validator — check naming, paths, scale, textures, geometry",
        image1="fileOpen.png",
        command=validator_cmd,
        sourceType="python",
        style="iconAndTextVertical",
    )

    # ── Asset Publisher ──────────────────────────────────────────────
    publisher_cmd = f"""
import sys
if r"{TOOLS_DIR}/asset_publisher" not in sys.path:
    sys.path.insert(0, r"{TOOLS_DIR}/asset_publisher")
import importlib, asset_publisher
importlib.reload(asset_publisher)
asset_publisher.show_ui()
"""
    cmds.shelfButton(
        parent=SHELF_NAME,
        label="Publish",
        annotation="Asset Publisher — version, manifest, USD layer, Git commit",
        image1="publish.png",
        command=publisher_cmd,
        sourceType="python",
        style="iconAndTextVertical",
    )

    print(f"[PipelineTools] Shelf '{SHELF_NAME}' created with 2 buttons.")


_make_shelf()
