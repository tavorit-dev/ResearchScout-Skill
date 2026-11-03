# tests/test_skill_contract.py
"""Contract tests for SKILL.md, the protocol the host agent reads.

These tests pin the promises of the document, not its prose: it must stay
host-agnostic and English, name the six steps, state the compliance rules,
and every script path and flag it documents must exist in this repository.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "SKILL.md"
SCRIPTS = REPO / "scripts"
SKILLS = REPO / ".claude" / "skills"

# The four work-mode entry points, mapped to whether v1 implements them.
ENTRY_POINTS = {
    "wanxiang-quick": False,
    "wanxiang-deep": True,
    "wanxiang-comparison": False,
    "wanxiang-monitor": False,
}

# The six steps of one research round, in order.
SIX_STEPS = ("Action", "Observation", "Evidence", "Reflection",
             "Next Action", "Persist")

# Script paths mentioned in the document, e.g. ``scripts/planner.py``.
SCRIPT_PATH = re.compile(r"scripts/[A-Za-z0-9_]+\.py")


def shell_blocks(text):
    """Return the bodies of ```bash fenced blocks in ``text``."""
    return re.findall(r"```bash\n(.*?)```", text, re.DOTALL)


class TestSkillContract(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text(encoding="utf-8")

    def test_has_frontmatter_name_and_description(self):
        self.assertTrue(self.text.startswith("---"))
        self.assertIn("name:", self.text)
        self.assertIn("description:", self.text)

    def test_declares_llm_host_requirement(self):
        self.assertIn("LLM", self.text)

    def test_documents_the_six_steps(self):
        for step in SIX_STEPS:
            self.assertIn(step, self.text)

    def test_documents_compliance_gate_is_mandatory(self):
        self.assertIn("compliance_gate.py", self.text)

    def test_has_no_claude_code_specific_frontmatter(self):
        self.assertNotIn("argument-hint", self.text)

    def test_is_english(self):
        head = self.text[:2000]
        self.assertTrue(head.isascii(), "SKILL.md must be English")

    def test_documents_the_no_host_check(self):
        self.assertIn("--no-host", self.text)

    def test_documents_the_user_authorisation_path(self):
        self.assertIn("save_policy", self.text)
        self.assertIn("source_policies.jsonl", self.text)
        self.assertIn("REVIEW_REQUIRED", self.text)

    def test_documented_script_paths_exist(self):
        """Every scripts/<name>.py named in SKILL.md is a real module."""
        documented = set(SCRIPT_PATH.findall(self.text))
        self.assertIn("scripts/research_loop.py", documented)
        for relative in sorted(documented):
            path = REPO / relative
            self.assertTrue(
                path.is_file(), f"SKILL.md names a missing module: {relative}"
            )
            self.assertTrue(
                path.parent == SCRIPTS,
                f"SKILL.md must document modules under scripts/: {relative}",
            )

    def test_documents_sources_flag_on_the_round_command(self):
        """The round command must show --sources alongside --round."""
        round_blocks = [
            block for block in shell_blocks(self.text)
            if "scripts/research_loop.py" in block and "--round" in block
        ]
        self.assertTrue(round_blocks, "SKILL.md must show the round command")
        self.assertTrue(
            any("--sources" in block for block in round_blocks),
            "the documented round command must show --sources",
        )


class TestWorkModeEntryPoints(unittest.TestCase):
    """The four /wanxiang-* commands are entry points, not four engines."""

    def entry_text(self, name):
        path = SKILLS / name / "SKILL.md"
        self.assertTrue(path.is_file(), f"missing skill entry point: {name}")
        return path.read_text(encoding="utf-8")

    def test_all_four_entry_points_exist(self):
        for name in ENTRY_POINTS:
            self.entry_text(name)

    def test_each_entry_point_declares_its_own_name(self):
        for name in ENTRY_POINTS:
            self.assertIn(f"name: {name}\n", self.entry_text(name))

    def test_deep_entry_point_is_the_canonical_protocol(self):
        deep = (SKILLS / "wanxiang-deep" / "SKILL.md").resolve()
        self.assertEqual(deep, SKILL.resolve())
        self.assertIn("name: wanxiang-deep\n", SKILL.read_text(encoding="utf-8"))

    def test_unimplemented_entry_points_say_so_and_redirect_to_deep(self):
        for name, implemented in ENTRY_POINTS.items():
            if implemented:
                continue
            text = self.entry_text(name)
            self.assertIn("not implemented", text.lower(), name)
            self.assertIn("/wanxiang-deep", text, name)

    def test_unimplemented_entry_points_forbid_doing_work(self):
        for name, implemented in ENTRY_POINTS.items():
            if implemented:
                continue
            text = self.entry_text(name)
            self.assertIn("do not", text.lower(), name)
            self.assertIn("collect", text.lower(), name)
            self.assertIn("archive", text.lower(), name)

    def test_old_research_scout_entry_point_is_gone(self):
        self.assertFalse((SKILLS / "research-scout").exists())


if __name__ == "__main__":
    unittest.main()
