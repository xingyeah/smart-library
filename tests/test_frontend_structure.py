"""Frontend structure gate: JS/CSS files stay under the size limit and the reader modules do not import in a loop."""

import re
import unittest
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "assets"
MAX_FILE_LINES = 500
IMPORT_PATTERN = re.compile(r"import\s*\{[^}]*\}\s*from\s*'\./(\w+)\.js'")
# Existing cycle ratchet: this set may only shrink. Everything else, the reader modules included, must be acyclic.
KNOWN_CYCLE_MODULES = {"api", "organize", "recommend", "shelf", "shelf_sheets"}


def import_graph():
    graph = {}
    for path in (ASSETS_DIR / "js").glob("*.js"):
        graph[path.stem] = set(IMPORT_PATTERN.findall(path.read_text(encoding="utf-8")))
    return graph


def modules_in_cycles(graph):
    """Modules that can reach themselves through imports."""
    def reaches(start, target, seen):
        for nxt in graph.get(start, ()):
            if nxt == target:
                return True
            if nxt not in seen:
                seen.add(nxt)
                if reaches(nxt, target, seen):
                    return True
        return False

    return {module for module in graph if reaches(module, module, set())}


class FrontendStructureTests(unittest.TestCase):
    def test_js_and_css_files_stay_under_the_line_limit(self):
        too_long = []
        for path in sorted(ASSETS_DIR.glob("*/*")):
            if path.suffix not in (".js", ".css"):
                continue
            lines = len(path.read_text(encoding="utf-8").splitlines())
            if lines > MAX_FILE_LINES:
                too_long.append(f"{path.relative_to(ASSETS_DIR)}: {lines} lines")
        self.assertEqual(too_long, [])

    def test_only_known_modules_are_in_import_cycles(self):
        unexpected = modules_in_cycles(import_graph()) - KNOWN_CYCLE_MODULES
        self.assertEqual(sorted(unexpected), [])

    def test_reader_hooks_is_a_leaf_and_reader_fills_it(self):
        self.assertEqual(import_graph()["reader_hooks"], set())
        self.assertIn("Object.assign(readerHooks", (ASSETS_DIR / "js" / "reader.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
