"""Deduplication for the raw-record layer (spec 10.1).

The same document arrives more than once: the same article under three URL
spellings, a syndicated copy under a different host, a republication that
differs only in boilerplate. Left alone, the archive fills with the same
document many times over and every later count -- how much evidence there is,
how many sources agree -- is inflated. This module removes that repetition.

The removal happens in three layers, and only the first two remove anything.

*Layer 1 -- URL identity.* Two records whose addresses normalize to the same
identity are one document. The normalization lives in
``scripts.lib.urlnorm.url_identity`` (tracking parameters, default ports,
fragments, the two spellings of a paged article).

*Layer 2 -- content fingerprint.* Two records whose bodies fingerprint alike
are the same body, whatever addresses they were fetched from. This is the
syndicated copy, and it is why layer 2 runs after layer 1 rather than instead
of it.

*Layer 3 -- semantic near-duplicates. Reported, never merged.* Two documents
that are *about the same thing* are not duplicates. A vendor's announcement
and a critic's rebuttal of the same product share a topic and are two
documents; merging them would destroy the very contradiction the research loop
exists to surface. Spec 10.1 is explicit -- 语义近似去重需谨慎：不同来源可能提供
不同证据和观点，不得因主题相同就合并 -- so this layer only names the pairs it
suspects, and the judgement stays with a human or the host agent. ``dedupe``
therefore never drops a document on this ground, and a pair that layer 3
reports always survives into ``kept``.

Two consequences of that division are worth stating plainly, because a reader
of ``dedupe``'s return value depends on them. The counts in the report are
documents *removed*, so a report of ``url_dupes == 2`` means three records
became one. And ``semantic_candidates`` is a list of suspected pairs, not a
list of duplicates -- an empty report field means nothing was suspected, and a
full one means only that someone should look.

Nothing here fetches, resolves or dereferences anything: the addresses are
compared as strings, never requested (spec 10.4). Nothing here reads or writes
a finding; deduplication works on the raw-record layer alone (spec 9.8).
Standard library only (spec 3.1 C2) -- ``difflib.SequenceMatcher`` for the
character-level similarity, no third-party or embedding-based comparison
(spec 2.2 puts vector search out of scope).
"""
from difflib import SequenceMatcher

from scripts.lib.hashing import content_hash
from scripts.lib.urlnorm import url_identity

# A pair of documents must be this similar, character for character, before
# layer 3 reports it as a suspected near-duplicate. The default is deliberately
# high: the cost of a missed suspicion is one unread pair, while the cost of a
# noisy list is a report nobody reads.
DEFAULT_SIMILARITY_THRESHOLD = 0.9


def dedupe(documents):
    """Collapse the duplicate records in ``documents`` and report what went.

    Layers 1 and 2 remove records; layer 3 only reports. A document is dropped
    when an earlier document in the list shares its URL identity (layer 1) or
    its content fingerprint (layer 2). The earlier record wins in both cases:
    the input is in discovery order, so the first sighting of a document is
    the one kept, and the outcome does not depend on how the list happens to
    be ordered beyond that.

    Returns ``(kept, report)``, where ``kept`` is the surviving documents in
    their original order and ``report`` holds:

    * ``url_dupes`` -- how many records layer 1 removed.
    * ``content_dupes`` -- how many records layer 2 removed.
    * ``semantic_candidates`` -- the pairs layer 3 suspects, as
      ``(document_id, document_id)``, computed over the surviving documents.
      Nothing is removed on this ground: every id in a pair is also in
      ``kept`` (spec 10.1).

    The counts are records removed, not records seen. A document that states no
    ``canonical_url`` takes part in layer 2 only, because an address it does
    not have cannot collide with another. A document with no ``text`` takes
    part in layer 1 only, and one that states neither is kept as it stands.
    Layer 3 compares bodies, so a record without one is never suspected.
    """
    kept = []
    url_dupes = 0
    content_dupes = 0
    seen_identities = set()
    seen_hashes = set()

    for document in documents:
        identity = _url_key(document)
        if identity is not None:
            if identity in seen_identities:
                url_dupes += 1
                continue
            seen_identities.add(identity)

        fingerprint = _content_key(document)
        if fingerprint is not None:
            if fingerprint in seen_hashes:
                content_dupes += 1
                continue
            seen_hashes.add(fingerprint)

        kept.append(document)

    report = {
        "url_dupes": url_dupes,
        "content_dupes": content_dupes,
        # Layer 3 reports and does not merge: the list is evidence for a
        # later judgement, never a reason to drop a document (spec 10.1).
        "semantic_candidates": find_semantic_candidates(
            kept, DEFAULT_SIMILARITY_THRESHOLD
        ),
    }
    return kept, report


def find_semantic_candidates(documents, threshold=DEFAULT_SIMILARITY_THRESHOLD):
    """Return the pairs of documents that look like near-duplicates.

    Similarity is character-level over the body text, via the standard
    library's ``difflib.SequenceMatcher``: two texts are compared as sequences
    of characters and scored by how much of them matches. A pair is reported
    when its score is at least ``threshold``.

    This function is the whole of layer 3, and it never merges anything. Two
    documents can be about the same subject, share vocabulary and still carry
    different evidence or a different point of view -- a vendor's announcement
    and a critic's rebuttal of the same product, a paper and the article
    reporting on it. Spec 10.1 requires that such documents stay separate, so
    this function answers "which pairs should a human or the host agent look
    at?" and leaves the decision there.

    ``threshold`` is the similarity at which a pair is worth reporting. Its
    default is deliberately high, because a suspected pair costs someone a
    look while a noisy report is one nobody reads.

    Returns ``(document_id, document_id)`` pairs in input order, each pair
    with its first document earlier in the list than its second, and each pair
    reported once. A document whose body text is missing or empty, or which
    states no id, is skipped: a pair can only be acted on when both of its
    members can be named in the archive. An empty text would also match every
    other empty text, turning the report into noise.
    """
    comparable = []
    for document in documents:
        identifier = _document_id(document)
        text = _document_text(document)
        if identifier and text:
            comparable.append((identifier, text))

    candidates = []
    for position, (left_id, left_text) in enumerate(comparable):
        for right_id, right_text in comparable[position + 1:]:
            if _similarity(left_text, right_text) >= threshold:
                candidates.append((left_id, right_id))
    return candidates


def _similarity(left, right):
    """Return how alike two texts are, from 0.0 to 1.0.

    The ratio ``difflib`` computes is twice the number of matching characters
    over the combined length of both texts, which is 1.0 for identical texts
    and 0.0 for texts with nothing in common.
    """
    return SequenceMatcher(None, left, right).ratio()


def _document_id(document):
    """Return a document's id, or ``None`` when it states none.

    A record without an id cannot be named in a report, so it is not a
    candidate pair's member. The id is not invented here: inventing one would
    put a value in the report that no archive record carries.
    """
    value = document.get("document_id")
    if isinstance(value, str) and value:
        return value
    return None


def _document_text(document):
    """Return a document's body text as a string, empty when it states none."""
    value = document.get("text")
    if isinstance(value, str):
        return value
    return ""


def _url_key(document):
    """Return the layer-1 key for a document, or ``None`` if it has no URL.

    The key is the URL's identity (spec 10.1): two addresses that normalize to
    the same identity are the same document.
    """
    url = document.get("canonical_url")
    if not isinstance(url, str) or not url:
        return None
    return url_identity(url)


def _content_key(document):
    """Return the layer-2 key for a document, or ``None`` if it has no body.

    An absent or empty body is not a fingerprint. Hashing it would give every
    bodyless record the same key and collapse unrelated documents into one,
    so a record with no text is left out of layer 2 rather than treated as
    empty. A document that carries its own ``content_hash`` uses that value,
    so a fingerprint computed earlier by the collector is not recomputed.
    """
    stored = document.get("content_hash")
    if isinstance(stored, str) and stored:
        return stored
    text = _document_text(document)
    if not text.strip():
        return None
    return content_hash(text)
