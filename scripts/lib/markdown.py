"""Markdown-safe rendering of untrusted text at the output boundary.

``neutralise_line`` is for **render boundaries only**: the place where
untrusted text is written into a Markdown product (the research summary in
``scripts/reflection.py``, and the report exporter when it lands). It must
never be applied before merging or persisting a record, because it *rewrites*
text: newlines and backticks are replaced. A rewritten value no longer equals
what the agent wrote, so applying this helper in the data layer would make
exact-statement matching fail and turn a display decision into data
corruption.

Keeping one copy of the rule here lets every Markdown renderer share it
(CLAUDE.md: shared deterministic logic lives once in ``scripts/lib/``). The
function is deterministic and performs no I/O.
"""
import re

# Every character Python's ``str.splitlines`` treats as a line boundary, plus
# CRLF handled as a unit so it collapses to one space rather than two. Any of
# these left in a value would let it start a line and forge a heading, bullet
# or code fence.
_LINE_SEPARATORS = re.compile(r"\r\n|[\r\n  \u0085\v\f]")


def neutralise_line(value):
    """Return ``value`` as a single Markdown-safe line.

    Untrusted values -- a finding statement, an id, an evidence quote, a gap,
    an action's reason -- are written by an LLM that has read untrusted web
    pages (CLAUDE.md invariant 6). At a render boundary they must not be able
    to change the document's structure:

    * CRLF, CR, LF, U+2028 LINE SEPARATOR, U+2029 PARAGRAPH SEPARATOR,
      U+0085 NEXT LINE (NEL), vertical tab and form feed each collapse to a
      single space. These are exactly the characters ``str.splitlines``
      recognises, so none can survive to start a new line; a value that
      cannot start a line cannot open a heading, bullet or code fence.
    * A backtick becomes an apostrophe, so a value rendered inside a code
      span cannot close it early and turn the rest into markup.

    Everything else is kept unchanged. In particular ``#``, ``*``, ``-`` and
    ``>`` are **not** escaped: once a value cannot start a line they cannot
    forge a section, and escaping them would corrupt text that is also read
    back as research memory (``C#`` would become ``C\\#``, and a later
    exact-statement match would fail).

    Non-strings are rendered with ``str`` so a dirty value still cannot
    smuggle a line break in. The function is idempotent: neutralising an
    already-neutralised value returns it unchanged.
    """
    return _LINE_SEPARATORS.sub(" ", str(value)).replace("`", "'")
