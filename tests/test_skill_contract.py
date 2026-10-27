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


if __name__ == "__main__":
    unittest.main()
