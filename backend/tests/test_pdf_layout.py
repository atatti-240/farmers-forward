"""Text in the PDF report never sits on top of the line below it.

ParagraphStyle inherits its line height (leading) from its parent, so a style
that raises fontSize without raising leading draws its letters into the next
paragraph. That is how the 22-pt "Farmers Forward" header (inheriting Title's
22-pt leading) ran into "Soil Health Report" on every page.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _appfixture import app  # noqa: F401  (imports the backend once, test DB)
from backend import pdf_report


class LineSpacingTests(unittest.TestCase):
    def test_every_style_leaves_room_for_its_own_letters(self):
        for name, style in pdf_report._styles().items():
            with self.subTest(style=name):
                self.assertGreaterEqual(style.leading, 1.2 * style.fontSize,
                                        "%s: %spt text on %spt lines"
                                        % (name, style.fontSize, style.leading))


if __name__ == "__main__":
    unittest.main()
