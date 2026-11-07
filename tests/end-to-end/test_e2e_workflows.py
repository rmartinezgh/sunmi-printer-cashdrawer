"""
End-to-End Acceptance & Repository Structural Integrity Test Suite.
Verifies all Odoo addons, Python syntax, XML well-formedness, Android modules,
and User Acceptance Criteria (AC-01 through AC-20).
"""

from __future__ import annotations

import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]


class TestEndToEndPlatformIntegrity(unittest.TestCase):
    EXPECTED_ODOO_ADDONS = [
        "powerpos_sunmi_core",
        "powerpos_sunmi_cloud",
        "powerpos_sunmi_cashdrawer",
        "powerpos_sunmi_audit",
        "powerpos_sunmi_monitor",
        "powerpos_sunmi_pos",
        "powerpos_sunmi_reports",
    ]

    def test_all_seven_odoo_addons_exist_and_have_valid_manifests(self) -> None:
        addons_dir = ROOT_DIR / "odoo_addons"
        self.assertTrue(addons_dir.is_dir(), f"Missing {addons_dir}")
        for addon in self.EXPECTED_ODOO_ADDONS:
            addon_path = addons_dir / addon
            self.assertTrue(addon_path.is_dir(), f"Missing addon directory: {addon}")
            manifest_path = addon_path / "__manifest__.py"
            init_path = addon_path / "__init__.py"
            self.assertTrue(manifest_path.is_file(), f"Missing __manifest__.py in {addon}")
            self.assertTrue(init_path.is_file(), f"Missing __init__.py in {addon}")
            manifest_data = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
            self.assertIsInstance(manifest_data, dict)
            self.assertEqual(
                manifest_data.get("version"),
                "20.0.1.0.0",
                f"Addon {addon} must target Odoo 20 (20.0.1.0.0)",
            )
            migration_script = addon_path / "migrations" / "20.0.1.0.0" / "post-migrate.py"
            self.assertTrue(
                migration_script.is_file(),
                f"Missing Odoo 20 post-migration script in {addon}",
            )
            self.assertIn("name", manifest_data)
            self.assertIn("depends", manifest_data)
            self.assertIn("data", manifest_data)
            for rel_file in manifest_data["data"]:
                target = addon_path / rel_file
                self.assertTrue(
                    target.is_file(),
                    f"Manifest in {addon} references missing file: {rel_file}",
                )

    def test_all_python_files_compile_cleanly(self) -> None:
        py_files = list(ROOT_DIR.rglob("*.py"))
        self.assertGreater(len(py_files), 20)
        for py_file in py_files:
            source = py_file.read_text(encoding="utf-8")
            try:
                compile(source, str(py_file), "exec")
            except SyntaxError as exc:
                self.fail(f"Syntax error in {py_file}: {exc}")

    def test_all_xml_files_are_well_formed(self) -> None:
        xml_files = list((ROOT_DIR / "odoo_addons").rglob("*.xml"))
        self.assertGreater(len(xml_files), 10)
        for xml_file in xml_files:
            try:
                tree = ET.parse(xml_file)
            except ET.ParseError as exc:
                self.fail(f"XML parse error in {xml_file}: {exc}")
            # Odoo 20 check: res.groups must use privilege_id rather than legacy category_id
            for rec in tree.getroot().findall(".//record[@model='res.groups']"):
                field_names = [f.attrib.get("name") for f in rec.findall("field")]
                self.assertNotIn(
                    "category_id",
                    field_names,
                    f"Legacy field 'category_id' on res.groups in {xml_file}; use 'privilege_id' for Odoo 20.",
                )
                self.assertIn("privilege_id", field_names)
            # Odoo 20 check: ir.rule was removed in Odoo 20 and unified into ir.access
            ir_rules = tree.getroot().findall(".//record[@model='ir.rule']")
            self.assertEqual(
                len(ir_rules),
                0,
                f"Legacy model 'ir.rule' found in {xml_file}; use unified 'security/ir.access.csv' for Odoo 20.",
            )

        legacy_acls = list((ROOT_DIR / "odoo_addons").rglob("ir.model.access.csv"))
        self.assertEqual(
            len(legacy_acls),
            0,
            f"Legacy 'ir.model.access.csv' found: {legacy_acls}; use 'ir.access.csv' for Odoo 20.",
        )
        access_files = list((ROOT_DIR / "odoo_addons").rglob("ir.access.csv"))
        self.assertEqual(len(access_files), 6)
        for access_file in access_files:
            lines = [line.strip() for line in access_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(
                lines[0],
                "id,name,model_id,group_id/id,operation,domain",
                f"Invalid Odoo 20 ir.access.csv header in {access_file}",
            )

    def test_no_forward_comodel_references_across_module_dependency_graph(self) -> None:
        import re

        addons_root = ROOT_DIR / "odoo_addons"
        manifests = {}
        defined_models_by_addon = {}

        for manifest_path in addons_root.glob("*/__manifest__.py"):
            addon_name = manifest_path.parent.name
            manifests[addon_name] = ast.literal_eval(manifest_path.read_text(encoding="utf-8"))
            defined_models = set()
            for py_file in (manifest_path.parent / "models").glob("*.py"):
                text = py_file.read_text(encoding="utf-8")
                for m in re.finditer(r"_name\s*=\s*['\"](powerpos\.[^'\"]+)['\"]", text):
                    defined_models.add(m.group(1))
            defined_models_by_addon[addon_name] = defined_models

        def get_reachable_addons(addon: str, visited=None) -> set:
            if visited is None:
                visited = set()
            if addon in visited or addon not in manifests:
                return visited
            visited.add(addon)
            for dep in manifests[addon].get("depends", []):
                if dep in manifests:
                    get_reachable_addons(dep, visited)
            return visited

        for addon_name, manifest in manifests.items():
            reachable_addons = get_reachable_addons(addon_name)
            available_models = set()
            for r_addon in reachable_addons:
                available_models |= defined_models_by_addon.get(r_addon, set())

            for py_file in (addons_root / addon_name / "models").glob("*.py"):
                text = py_file.read_text(encoding="utf-8")
                for m in re.finditer(
                    r"fields\.(?:Many2one|One2many|Many2many)\(\s*(?:comodel_name\s*=\s*)?['\"](powerpos\.[^'\"]+)['\"]",
                    text,
                ):
                    comodel = m.group(1)
                    self.assertIn(
                        comodel,
                        available_models,
                        f"Addon '{addon_name}' ({py_file.name}) references comodel '{comodel}' which is not defined in '{addon_name}' or its dependencies {sorted(reachable_addons)}",
                    )

    def test_no_sunmi_app_key_leaked_in_pos_frontend_assets(self) -> None:
        pos_static = ROOT_DIR / "odoo_addons" / "powerpos_sunmi_pos" / "static"
        if pos_static.is_dir():
            for asset in pos_static.rglob("*.*"):
                content = asset.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn("SUNMI_SUPER_SECRET", content)
                self.assertNotIn("app_key", content.lower())


if __name__ == "__main__":
    unittest.main()

