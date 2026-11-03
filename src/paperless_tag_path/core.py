"""Pure logic of the ``tag_path`` filter: no Django imports.

The tag tree is passed in as a mapping ``id -> TagNode``; the document's tags
as an iterable of ids. Everything here is deterministic and testable with
plain pytest.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import pathvalidate

ON_BRANCH_VALUES = ("stop", "first")

#: Written in place of a segment that is empty after sanitisation
#: (a tag named ``...``, ``..`` or made only of rejected characters).
EMPTY_SEGMENT = "_"

#: Guard against corrupt data (cycles); Paperless allows depth 5.
_MAX_DEPTH = 64


@dataclass(frozen=True)
class TagNode:
    id: int
    name: str
    parent_id: int | None


def match_key(name: str) -> str:
    """Key used to compare a tag name with a root segment."""
    return unicodedata.normalize("NFC", name).strip().casefold()


def sanitize_segment(name: str) -> str:
    """Clean one tag name so it is safe as a single directory name.

    Same base as Paperless placeholders (NFC + ``pathvalidate.sanitize_filename``
    with ``-`` as replacement): removes ``/``, ``\\``, ``:`` and other characters
    rejected by Windows/SMB, control characters, reserved names (``AUX`` ->
    ``AUX_``), trailing dots and spaces, and caps the length at 255 bytes.
    ``pathvalidate`` lets ``.`` and ``..`` through, so dot-only segments are
    replaced here.
    """
    segment = pathvalidate.sanitize_filename(
        unicodedata.normalize("NFC", name),
        replacement_text="-",
    ).strip()
    if not segment.strip("."):
        return EMPTY_SEGMENT
    return segment


@dataclass(frozen=True)
class Root:
    """A parsed root: segment keys, and whether it starts at the top level."""

    segments: tuple[str, ...]
    anchored: bool


def parse_roots(roots: object) -> list[Root]:
    """Normalise the ``roots`` argument to a list of roots.

    A root is a chain of tag names, matched on whole segments:

    - ``"/company/ACME"``: leading ``/``, the chain starts at the top level;
    - ``"company/ACME"``, ``"ACME/accounting"``, ``"accounting"``: the
      chain may start at any depth (``"ACME/accounting"`` matches
      ``company/ACME/accounting``; ``"/ACME/accounting"`` does not).

    ``roots`` is one root or a list of roots (order = precedence). An element
    may also be a list of segments, ``["company", "A/B"]``, for tag names
    containing ``/``; a leading ``""`` in it anchors it like a leading ``/``.
    """
    if isinstance(roots, str):
        items: list[object] = [roots]
    elif isinstance(roots, (list, tuple)):
        items = list(roots)
    else:
        raise TypeError(f"roots must be a string or a list, got {type(roots).__name__}")

    parsed: list[Root] = []
    for item in items:
        if isinstance(item, str):
            raw_segments: list[object] = list(item.split("/"))
        elif isinstance(item, (list, tuple)):
            raw_segments = list(item)
        else:
            raise TypeError(f"root must be a string or a list of segments, got {item!r}")
        if not all(isinstance(s, str) for s in raw_segments):
            raise TypeError(f"root segments must be strings: {item!r}")
        anchored = len(raw_segments) > 1 and raw_segments[0] == ""
        if anchored:
            raw_segments = raw_segments[1:]
        segments = tuple(match_key(s) for s in raw_segments)  # type: ignore[arg-type]
        if not segments or any(s == "" for s in segments):
            raise ValueError(f"empty segment in root {item!r}")
        parsed.append(Root(segments, anchored))

    if not parsed:
        raise ValueError("roots must not be empty")
    return parsed


def validate_on_branch(on_branch: object) -> str:
    if on_branch not in ON_BRANCH_VALUES:
        raise ValueError(f"on_branch must be one of {ON_BRANCH_VALUES}, got {on_branch!r}")
    return on_branch  # type: ignore[return-value]


def ids_for_names(names: Iterable[str], tags: Mapping[int, TagNode]) -> set[int]:
    """Ids of tags with the given names (``tag_name_list``).

    Names are compared exactly after NFC, as Paperless stores them. A name is
    unique per owner, so with several owners one name can give several ids;
    all of them are kept and resolution picks deterministically.
    """
    wanted = {unicodedata.normalize("NFC", n) for n in names}
    return {t.id for t in tags.values() if unicodedata.normalize("NFC", t.name) in wanted}


def _present_with_ancestors(
    doc_tag_ids: Iterable[int],
    tags: Mapping[int, TagNode],
) -> set[int]:
    """Document tags plus all their ancestors.

    Paperless adds ancestors when a child tag is assigned, but a parent can
    still be removed by hand; the path should not depend on that.
    """
    present: set[int] = set()
    for tag_id in doc_tag_ids:
        node = tags.get(tag_id)
        steps = 0
        while node is not None and node.id not in present and steps < _MAX_DEPTH:
            present.add(node.id)
            node = tags.get(node.parent_id) if node.parent_id is not None else None
            steps += 1
    return present


def _chain_to_top(node: TagNode, tags: Mapping[int, TagNode]) -> list[TagNode] | None:
    """``[top, ..., node]``, or ``None`` if the parent chain is broken or cyclic."""
    chain = [node]
    seen = {node.id}
    while chain[0].parent_id is not None:
        parent = tags.get(chain[0].parent_id)
        if parent is None or parent.id in seen or len(chain) >= _MAX_DEPTH:
            return None
        seen.add(parent.id)
        chain.insert(0, parent)
    return chain


def _sort_key(node: TagNode) -> tuple[str, int]:
    return (match_key(node.name), node.id)


def _chain_keys(chain: list[TagNode]) -> tuple[str, ...]:
    return tuple(match_key(n.name) for n in chain)


def resolve(
    doc_tag_ids: Iterable[int],
    tags: Mapping[int, TagNode],
    roots: object,
    *,
    on_branch: str = "stop",
    strip_root: bool = False,
) -> str | None:
    """Directory path built from the document's tag hierarchy.

    The path always starts at the top level of the tag tree. Returns ``None``
    when none of ``roots`` is present on the document, so the caller can
    substitute its default. Returns ``""`` when ``strip_root`` is set and
    nothing lies below the matched root.
    """
    on_branch = validate_on_branch(on_branch)
    root_specs = parse_roots(roots)

    present = _present_with_ancestors(doc_tag_ids, tags)
    chains: dict[int, list[TagNode]] = {}
    for tag_id in present:
        chain = _chain_to_top(tags[tag_id], tags)
        if chain is not None:  # detached tags (unknown parent, cycle) are ignored
            chains[tag_id] = chain

    children: dict[int, list[TagNode]] = {}
    for tag_id, chain in chains.items():
        if len(chain) > 1:
            children.setdefault(chain[-2].id, []).append(chain[-1])
    for siblings in children.values():
        siblings.sort(key=_sort_key)

    for root in root_specs:
        n = len(root.segments)
        candidates = [
            chain
            for chain in chains.values()
            if len(chain) >= n
            and _chain_keys(chain[-n:]) == root.segments
            and (not root.anchored or len(chain) == n)
        ]
        if not candidates:
            continue
        # Several matches only with several owners sharing a name:
        # the shallowest, then alphabetically, then the lowest id.
        chain = list(min(candidates, key=lambda c: (len(c), _chain_keys(c), c[-1].id)))
        root_depth = len(chain)

        current = chain[-1]
        while len(chain) < _MAX_DEPTH:
            kids = children.get(current.id, [])
            if not kids or (len(kids) > 1 and on_branch == "stop"):
                break
            current = kids[0]  # sorted: alphabetically first
            chain.append(current)

        if strip_root:
            chain = chain[root_depth:]
        return "/".join(sanitize_segment(node.name) for node in chain)

    return None
