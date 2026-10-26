"""The content extractor: turning a fetched HTML page into clean text.

This module owns the extraction half of stage three (spec 8.6). The collector
brings back a body and leaves it at ``FETCHED``; extraction turns that body
into the plain text and the metadata the later stages judge and summarise. It
is the only place HTML is parsed.

Three rules shape what is here.

*The page is untrusted input (spec 10.5).* The HTML is read as data and
nothing else. Nothing in this module executes, evaluates or interprets page
content as an instruction; nothing fetches a URL the page mentions; nothing
writes a file whose name or path comes from the page. The parser's job is to
classify markup, and the text it produces is passed on as text.

*An unknown date stays unknown (spec 10.2).* ``parse_date`` answers with a
confidence alongside the value, and the only value it ever returns is one the
input stated unambiguously in full. It never substitutes a nearby date, a
"last modified" hint, or today. A bare year, a month name with no day, a
relative phrase and a stray number all come back as ``(None, "unknown")``,
because each of them would need a guess to become a date. A caller can
therefore trust that a non-null ``published_at`` is a date the page actually
stated.

*No summary is produced here (spec 10.6).* The extract is the text and the
metadata; deciding what the document says and why it matters is semantic work
that belongs to the host agent. There is deliberately no ``summary`` field,
and nothing in the return value pretends to be a conclusion about the page.

Standard library only: ``html.parser.HTMLParser``, no third-party HTML or
readability library (spec 3.1 C2). The extraction is deliberately conservative
in the direction that matters -- when the page is ambiguous, this module
returns less text rather than text from a region it cannot identify.
"""
import re
from html.parser import HTMLParser

# Date confidence values (spec 10.2). A date the page stated in full is
# "explicit"; anything else is "unknown" and the value stays None.
DATE_CONFIDENCE_EXPLICIT = "explicit"
DATE_CONFIDENCE_UNKNOWN = "unknown"

# Regions that are chrome rather than article body. Their text is dropped
# wherever they appear, including inside the chosen article.
DROPPED_TAGS = ("script", "style", "nav", "header", "footer", "aside")

# Tags that mark a candidate content root, in preference order. The first one
# that actually holds text wins; otherwise the whole body is used.
CONTENT_TAGS = ("article", "main")

# Heading tags, read only as a fallback title when the page has no <title>.
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Tags whose text is kept but separated from neighbouring text.
BLOCK_TAGS = (
    "p",
    "div",
    "section",
    "article",
    "main",
    "li",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "blockquote",
    "pre",
    "figure",
    "figcaption",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "address",
    "details",
    "summary",
)

# Tags that end the current line without also opening a new block. A <br> is a
# line break, not a concatenation point, so "one<br>two" must not become
# "onetwo".
LINE_BREAK_TAGS = ("br", "hr")

# Elements with no text content of their own.
VOID_TAGS = (
    "area",
    "base",
    "col",
    "embed",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
)

# Elements that never nest in valid HTML, so their end tag arrives without an
# open one. Closing them is a no-op rather than an error.
OPTIONAL_END_TAGS = ("p", "li", "dt", "dd", "option", "tr", "td", "th")

# A date stated in full. Both ISO 8601 (2026-10-20) and the common slashed and
# dotted forms are accepted, with an optional time part that is discarded: the
# field is a date, and the time of day is not what it records. The month must
# be two digits -- "2026-1-5" is left to the caller as unknown rather than
# guessed at -- and the day must be present.
DATE_PATTERNS = (
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"),
    re.compile(r"^(\d{4})/(\d{2})/(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$"),
    re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$"),
)

# Metadata names that state the author (spec 9.7). Matched case-insensitively.
AUTHOR_META_NAMES = ("author", "article:author", "citation_author", "dc.creator")

# Metadata names that state a publication date. Matched case-insensitively.
PUBLISHED_META_NAMES = (
    "article:published_time",
    "date",
    "datepublished",
    "dc.date",
    "dc.date.issued",
    "pubdate",
    "publishdate",
    "publication_date",
    "citation_publication_date",
)

# Attributes that hold a machine-readable date on a <time> element.
TIME_ATTRIBUTES = ("datetime", "content", "data-date", "data-published", "data-published-at")


def _collapse(text):
    """Collapse runs of whitespace inside one line of text."""
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def parse_date(text):
    """Read a date string and report how much it can be trusted.

    Returns ``(iso_date_or_none, confidence)``. The value is an ISO 8601
    ``YYYY-MM-DD`` string and the confidence is ``"explicit"`` only when the
    whole input is a date stated in full. Everything else -- an empty string,
    prose, a relative phrase, a bare year, a month name with no day, a number
    that is not a date -- returns ``(None, "unknown")`` (spec 10.2).

    The function never invents a date. There is no fallback to the current
    date, to a nearby date, or to a lower-precision reading of the input: an
    unknown date is recorded as unknown and left for a human or a later
    collection attempt.
    """
    if not isinstance(text, str):
        return None, DATE_CONFIDENCE_UNKNOWN
    candidate = text.strip()
    if not candidate:
        return None, DATE_CONFIDENCE_UNKNOWN
    for pattern in DATE_PATTERNS:
        match = pattern.match(candidate)
        if not match:
            continue
        year, month, day = (int(part) for part in match.groups())
        if _is_real_calendar_date(year, month, day):
            return "%04d-%02d-%02d" % (year, month, day), DATE_CONFIDENCE_EXPLICIT
        # A shape that matches but is not a real date is not a date.
        return None, DATE_CONFIDENCE_UNKNOWN
    return None, DATE_CONFIDENCE_UNKNOWN


def _is_real_calendar_date(year, month, day):
    """Return True when the three numbers name a date that exists."""
    if not 1 <= month <= 12:
        return False
    if not 1 <= day <= 31:
        return False
    if month == 2:
        leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
        return day <= (29 if leap else 28)
    if month in (4, 6, 9, 11):
        return day <= 30
    return True


def extract_content(html, url):
    """Extract title, body text, author and publication date from a page.

    ``url`` is the address the body was fetched from. It is carried for the
    caller's bookkeeping and is never dereferenced here: extraction performs
    no network access of any kind.

    Returns a dict with:

    * ``title`` -- the ``<title>`` text, else the first ``<h1>``, else ``""``.
    * ``text`` -- the article body as plain text with chrome removed.
    * ``author`` -- the author the page states, else ``None``.
    * ``published_at`` -- an ISO date the page stated in full, else ``None``.
    * ``date_confidence`` -- ``"explicit"`` when ``published_at`` was stated
      in full, ``"unknown"`` otherwise (spec 10.2).

    The page is untrusted input (spec 10.5) and is treated as data only. The
    return value is built in one place so later stages can add fields without
    restructuring it.
    """
    del url  # Carried for the caller; extraction never fetches anything.
    if not isinstance(html, str):
        html = ""
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # A page that cannot be tokenised must not take the caller down.
        # Whatever was classified before the failure is still usable, so the
        # extract is returned rather than raising.
        pass

    title = parser.title_text() or parser.first_h1()
    published_at, date_confidence = _page_date(parser)
    text = parser.body_text()

    return {
        "title": title,
        "text": text,
        "author": parser.author(),
        "published_at": published_at,
        "date_confidence": date_confidence,
    }


def _page_date(parser):
    """Pick the publication date, preferring what the page states in markup.

    A ``<time datetime=...>`` attribute is read first because it is the
    machine-readable statement of the same date the element displays, then a
    publication-date meta tag, then the text of a ``<time>`` element that
    carries no machine-readable attribute. Each candidate is passed through
    ``parse_date``, so a candidate that cannot be read in full yields
    ``(None, "unknown")`` and the next one is tried (spec 10.2).
    """
    for value in parser.time_attribute_values():
        date, confidence = parse_date(value)
        if date:
            return date, confidence
    for value in parser.meta_values(PUBLISHED_META_NAMES):
        date, confidence = parse_date(value)
        if date:
            return date, confidence
    for value in parser.time_texts():
        date, confidence = parse_date(value)
        if date:
            return date, confidence
    return None, DATE_CONFIDENCE_UNKNOWN


class _PageParser(HTMLParser):
    """A single pass over the page, recording metadata and body regions.

    The parser never acts on what it reads. It keeps the text of candidate
    content roots in order, the body text outside them, the metadata the page
    states, and the headings, so the caller can choose what to return
    afterwards without a second parse.
    """

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        # (name, text) for each <article>/<main> that accumulated text, in the
        # order the elements opened.
        self._roots = []
        # Everything kept outside a content root -- the body text of a page
        # that has no <article> or <main> at all.
        self._whole_parts = []
        # (text, depth) for each heading, used when the page states no title.
        self._headings = []
        self._meta = []
        self._time_elements = []
        # Depth of the region being dropped, so an unclosed <script>
        # swallows the rest of the page as markup rather than spilling it
        # into the body text.
        self._drop_depth = 0
        self._stack = []
        self._root = None
        self._root_depth = None
        self._parts = []
        self._in_title = False
        self._title_parts = []
        self._heading = None
        self._heading_depth = None
        self._heading_parts = []

    # -- element bookkeeping ------------------------------------------

    def handle_starttag(self, tag, attrs):
        attributes = {name.lower(): (value if value is not None else "") for name, value in attrs}
        if self._drop_depth:
            self._drop_depth += 1
            return
        if tag in VOID_TAGS:
            if tag == "meta":
                # A meta tag is void, so it is recorded here or not at all.
                # This is where the page states its author and, often, its
                # publication date.
                self._record_meta(attributes)
            elif tag in LINE_BREAK_TAGS:
                self._break_line()
            return
        if tag in DROPPED_TAGS:
            self._drop_depth = 1
            self._stack.append(tag)
            return
        self._stack.append(tag)
        if tag == "title":
            self._in_title = True
        if tag == "time":
            self._record_time(attributes)
        if tag in LINE_BREAK_TAGS:
            self._break_line()
            return
        if tag in BLOCK_TAGS:
            self._break_line()
        if tag in CONTENT_TAGS and self._root is None:
            self._root = tag
            self._root_depth = len(self._stack)
            self._parts = []
        if tag in HEADING_TAGS and self._heading is None:
            self._heading = tag
            self._heading_depth = len(self._stack)
            self._heading_parts = []

    def handle_startendtag(self, tag, attrs):
        # A self-closing <p/> still ends the line it would have occupied. A
        # self-closing <script/> is markup with no content, so it opens no
        # region to drop -- treating it as an opening tag would swallow the
        # rest of the page.
        if tag in DROPPED_TAGS:
            return
        if tag in VOID_TAGS:
            if tag == "meta":
                self._record_meta({name.lower(): (value if value is not None else "") for name, value in attrs})
            elif tag in LINE_BREAK_TAGS:
                self._break_line()
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self._drop_depth:
            self._drop_depth -= 1
            if self._drop_depth == 0 and self._stack:
                self._stack.pop()
            return
        if tag not in self._stack:
            # An end tag with no open element: ignored rather than treated as
            # an error, because malformed pages are normal.
            return
        depth = len(self._stack)
        while self._stack:
            open_tag = self._stack.pop()
            if open_tag == tag:
                break
        if tag == "title":
            self._in_title = False
        if tag == "time":
            self._record_time_end()
        if tag == "article" or tag == "main":
            # An inner <article> does not close an outer one; only the element
            # that opened the root ends it.
            if tag == self._root and depth == self._root_depth:
                self._roots.append((tag, "".join(self._parts)))
                self._root = None
                self._root_depth = None
                self._parts = []
        if tag in HEADING_TAGS and tag == self._heading and depth == self._heading_depth:
            self._headings.append((tag, "".join(self._heading_parts), self._heading_depth))
            self._heading = None
            self._heading_depth = None
            self._heading_parts = []
        if tag in BLOCK_TAGS:
            self._break_line()

    # -- text ---------------------------------------------------------

    def handle_data(self, data):
        if self._drop_depth:
            return
        if self._in_title:
            # The document title is head content, not body text. It reaches
            # the caller through ``title`` and must not also appear in the
            # extracted body, and its text is never read as an author or a
            # date (spec 10.2).
            self._title_parts.append(data)
            return
        if self._heading is not None:
            self._heading_parts.append(data)
        if self._root is not None:
            self._parts.append(data)
        else:
            self._whole_parts.append(data)
        if self._stack and self._stack[-1] == "time":
            self._time_elements[-1][1].append(data)

    def handle_comment(self, data):
        # A comment is markup, not body text, and is never read as an
        # instruction (spec 10.5).
        return

    def unknown_decl(self, data):
        return

    # -- recorded metadata --------------------------------------------

    def _record_meta(self, attributes):
        name = attributes.get("name") or attributes.get("property") or attributes.get("itemprop")
        content = attributes.get("content")
        if name and content is not None:
            self._meta.append((name.strip().lower(), content.strip()))

    def _record_time(self, attributes):
        attribute = None
        for name in TIME_ATTRIBUTES:
            value = attributes.get(name)
            if value:
                attribute = value.strip()
                break
        # Each <time> keeps its own machine-readable attribute and its own
        # visible text. A shared slot would let one element's attribute stand
        # in for the next one's.
        self._time_elements.append([attribute, []])

    def _record_time_end(self):
        if not self._time_elements:
            return
        self._time_elements[-1][1] = "".join(self._time_elements[-1][1])

    # -- line assembly ------------------------------------------------

    def _break_line(self):
        if self._drop_depth:
            return
        if self._root is not None:
            self._parts.append("\n")
        else:
            self._whole_parts.append("\n")

    # -- read-only views ----------------------------------------------

    def title_text(self):
        """Return the ``<title>`` text, collapsed, or ``""``."""
        return _collapse("".join(self._title_parts)).strip()

    def first_h1(self):
        """Return the page's top heading, or ``""``.

        A page with no ``<title>`` usually still names itself in its first
        heading, so the heading is the fallback. ``<h1>`` is preferred and
        lower levels are used only when the page states no ``<h1>`` at all.
        """
        for level in HEADING_TAGS:
            for tag, text, _depth in self._headings:
                if tag == level:
                    cleaned = _collapse(text).strip()
                    if cleaned:
                        return cleaned
        return ""

    def author(self):
        """Return the author the page states, or ``None``.

        A name only counts when the page marks it as the author through a meta
        tag. A stray name in the body is not read as an author, because
        guessing one would put an invented value in a field the archive treats
        as a fact.
        """
        for value in self.meta_values(AUTHOR_META_NAMES):
            cleaned = _collapse(value).strip()
            if cleaned:
                return cleaned
        return None

    def meta_values(self, names):
        """Return the content of every meta tag whose name is in ``names``."""
        wanted = tuple(name.lower() for name in names)
        return [content for name, content in self._meta if name in wanted]

    def time_attribute_values(self):
        """Return the machine-readable dates stated on ``<time>`` elements."""
        return [attribute for attribute, _text in self._time_elements if attribute]

    def time_texts(self):
        """Return the visible text of each ``<time>`` element."""
        return [text for _attribute, text in self._time_elements if isinstance(text, str)]

    def body_text(self):
        """Return the best available body text as cleaned plain text.

        The chosen root is the first ``<article>`` or ``<main>`` that holds
        text, else the whole document. Chrome is already absent from what was
        recorded, so the only work left is normalising the whitespace.
        """
        for name in CONTENT_TAGS:
            for root_name, text in self._roots:
                if root_name == name and text.strip():
                    return _clean_text(text)
        # A content root exists but held no text, so the body text collected
        # outside it is what the page offers; failing that, the empty roots.
        outside = "".join(self._whole_parts)
        if outside.strip():
            return _clean_text(outside)
        return _clean_text("".join(text for _name, text in self._roots))


def _clean_text(text):
    """Normalise extracted text: entities decoded, whitespace collapsed.

    Every source of newlines -- block boundaries, ``<br>`` -- survives as a
    single newline, so line structure is preserved without blank-line runs.
    """
    lines = [_collapse(line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)
