# tests/test_markdown.py
import unittest

from scripts.lib.markdown import neutralise_line


class Newliney:
    """An object whose ``str`` contains a newline."""

    def __str__(self):
        return "a\nb"


class TestNeutraliseLine(unittest.TestCase):
    def test_lf_cr_and_crlf_collapse_to_one_space(self):
        self.assertEqual(neutralise_line("a\nb"), "a b")
        self.assertEqual(neutralise_line("a\rb"), "a b")
        self.assertEqual(neutralise_line("a\r\nb"), "a b")

    def test_unicode_line_separators_collapse_to_one_space(self):
        # str.splitlines() treats all of these as line boundaries, so a value
        # carrying one could start a new line and forge a section.
        for separator in (" ", " ", "\u0085", "\v", "\f"):
            with self.subTest(separator=repr(separator)):
                self.assertEqual(neutralise_line("a" + separator + "b"), "a b")

    def test_backtick_is_neutralised_without_losing_content(self):
        result = neutralise_line("id`x")
        self.assertNotIn("`", result)
        self.assertIn("id", result)
        self.assertIn("x", result)

    def test_non_string_input_becomes_a_single_safe_line(self):
        self.assertEqual(neutralise_line(None), "None")
        self.assertEqual(neutralise_line(123), "123")
        self.assertEqual(neutralise_line(Newliney()), "a b")

    def test_ordinary_punctuation_is_preserved_unchanged(self):
        # '#', '*', '-' and '>' cannot forge structure once the value cannot
        # start a line, so they are not escaped: escaping them corrupts text
        # that is also read back as memory.
        text = "C# 与 issue #123 —— *重点* - 列表 > 引用"
        self.assertEqual(neutralise_line(text), text)

    def test_hash_and_dash_are_preserved_in_code_spans(self):
        self.assertEqual(neutralise_line("doc#7"), "doc#7")
        self.assertEqual(neutralise_line("## 不是标题"), "## 不是标题")
        self.assertEqual(neutralise_line("- 不是列表"), "- 不是列表")

    def test_is_idempotent(self):
        value = " a\r\nb `c` # d   e\tf "
        once = neutralise_line(value)
        self.assertEqual(neutralise_line(once), once)


if __name__ == "__main__":
    unittest.main()
