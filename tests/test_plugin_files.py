from __future__ import annotations

import json
import unittest

from compactor import __version__
from tests.helpers import REPO_ROOT


class PluginFilesTest(unittest.TestCase):
    def test_manifest(self):
        manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "compactor")
        self.assertEqual(manifest["version"], __version__)

    def test_marketplace_points_at_repo_root(self):
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        (plugin,) = market["plugins"]
        self.assertEqual((plugin["name"], plugin["source"], plugin["version"]), ("compactor", "./", __version__))

    def test_skill_frontmatter_and_commands(self):
        text = (REPO_ROOT / "skills" / "compactor" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nname: compactor\ndescription: "))
        for command in ('compactor hold "', "compactor release --note", "compactor note --clear", "compactor status"):
            self.assertIn(command, text)

    def test_skill_covers_subagent_dispatch(self):
        text = (REPO_ROOT / "skills" / "compactor" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('compactor hold "waiting on <task> report"', text)
        self.assertIn("Don't release just because a report arrived", text)
        self.assertIn("write findings to a file as it goes", text)


if __name__ == "__main__":
    unittest.main()
