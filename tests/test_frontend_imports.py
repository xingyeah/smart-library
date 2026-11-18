"""Every function a frontend module calls from another module is imported there.

main.js also hangs most functions on `window` for the inline handlers in index.html, so a call to a function that
was never imported works by accident until it reaches one that is not on that list: the organize tab called
loadAiUsage that way, threw, and never loaded its inbox.
"""

import re
import unittest
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "frontend" / "assets" / "js"
EXPORT = re.compile(r"export (?:async )?function (\w+)|export (?:const|let) (\w+)")
IMPORT_BLOCK = re.compile(r"import\s*\{([^}]*)\}[^\n]*")
LOCAL_NAME = re.compile(r"(?:function|const|let|var)\s+(\w+)")


def missing_imports() -> list:
    sources = {path.name: path.read_text(encoding="utf-8") for path in sorted(JS_DIR.glob("*.js"))}
    home_of = {first or second: name for name, source in sources.items() for first, second in EXPORT.findall(source)}
    missing = []
    for name, source in sources.items():
        imported = {part.strip().split(" as ")[-1] for block in IMPORT_BLOCK.findall(source) for part in block.split(",")}
        known_here = imported | set(LOCAL_NAME.findall(source))
        code = IMPORT_BLOCK.sub("", source)
        for function, home in home_of.items():
            # A call, not a method of some object and not text inside a string
            is_called = re.search(r"(?<![\w.$'\"`])" + re.escape(function) + r"\s*\(", code)
            if home != name and function not in known_here and is_called:
                missing.append(f"{name} calls {function} (from {home}) without importing it")
    return missing


class FrontendImportsTest(unittest.TestCase):
    def test_modules_import_what_they_call(self):
        self.assertEqual(missing_imports(), [])


if __name__ == "__main__":
    unittest.main()
