"""Tests for the adapter scaffold generator."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD_PATH = ROOT / "scaffolds" / "new_adapter.py"


def _load_scaffold_module():
    spec = importlib.util.spec_from_file_location("new_adapter_scaffold", SCAFFOLD_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCAFFOLD_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NewAdapterScaffoldTest(unittest.TestCase):
    def test_plan_files_uses_expected_paths(self) -> None:
        scaffold = _load_scaffold_module()
        files = scaffold.plan_files("demo_provider", ROOT)
        paths = {generated.path.relative_to(ROOT).as_posix() for generated in files}
        self.assertIn("src/contextweaver/adapters/demo_provider.py", paths)
        self.assertIn("tests/test_adapters_demo_provider.py", paths)
        self.assertIn("docs/integration_demo_provider.md", paths)
        self.assertIn("examples/demo_provider_adapter_demo.py", paths)
        self.assertIn("docs/adapter_checklists/demo_provider.md", paths)

    def test_generated_content_documents_adapter_invariants(self) -> None:
        scaffold = _load_scaffold_module()
        files = scaffold.plan_files("demo_provider", ROOT)
        joined = "\n".join(generated.content for generated in files)
        self.assertIn("Provider SDK imports must stay optional", joined)
        self.assertIn("do not leak", joined.lower())
        self.assertIn("from_demo_provider_tools", joined)
        self.assertIn("from_demo_provider_history", joined)
        self.assertIn("Install", joined)

    def test_write_files_refuses_to_overwrite_without_force(self) -> None:
        scaffold = _load_scaffold_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = scaffold.plan_files("demo_provider", root)
            scaffold.write_files(files)
            with self.assertRaises(FileExistsError):
                scaffold.write_files(files)

    def test_main_dry_run_does_not_write_files(self) -> None:
        scaffold = _load_scaffold_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc = scaffold.main(["demo_provider", "--root", str(root), "--dry-run"])
            self.assertEqual(rc, 0)
            self.assertFalse((root / "src" / "contextweaver" / "adapters" / "demo_provider.py").exists())

    def test_invalid_names_are_rejected(self) -> None:
        scaffold = _load_scaffold_module()
        with self.assertRaises(ValueError):
            scaffold.plan_files("123bad", ROOT)
        with self.assertRaises(ValueError):
            scaffold.plan_files("bad__name", ROOT)


if __name__ == "__main__":
    unittest.main()
