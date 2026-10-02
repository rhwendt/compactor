from __future__ import annotations

import ast
import unittest

from tests.helpers import REPO_ROOT

PURE_IMPORTS = {"config", "model"}  # modules without file I/O


def package_imports(name: str) -> set:
    tree = ast.parse((REPO_ROOT / "compactor" / f"{name}.py").read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            found.add(node.module)
    return found


class PurityTest(unittest.TestCase):
    def test_policy_and_messages_import_no_io_module(self):
        for name in ("policy", "messages"):
            with self.subTest(module=name):
                self.assertLessEqual(package_imports(name), PURE_IMPORTS)


if __name__ == "__main__":
    unittest.main()
