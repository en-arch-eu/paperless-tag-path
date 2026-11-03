import unicodedata

import pytest

from paperless_tag_path.core import (
    EMPTY_SEGMENT,
    TagNode,
    parse_roots,
    resolve,
    sanitize_segment,
)


def tree(*rows: tuple[int, str, int | None]) -> dict[int, TagNode]:
    return {i: TagNode(i, name, parent) for i, name, parent in rows}


TAGS = tree(
    (1, "things", None),
    (2, "computer", 1),
    (3, "monitor", 1),
    (4, "events", None),
    (5, "2025-01-01 - bought a computer", 4),
    (6, "thing", None),
    (7, "company", None),
    (8, "ACME", 7),
    (9, "accounting", 8),
    (10, "cost invoices", 9),
    (11, "health", None),
    (12, "people", None),
    (13, "Alice", 12),
    (14, "Bob", 12),
    (15, "Hypertension", 11),
    (16, "inbox", None),
)


# --- examples from the feature request (#13886) ------------------------------


def test_argument_order_decides():
    doc = [1, 2, 4, 5]
    assert resolve(doc, TAGS, ["things", "events"]) == "things/computer"
    assert resolve(doc, TAGS, ["events", "things"]) == "events/2025-01-01 - bought a computer"


def test_branching_stop_and_first():
    doc = [1, 2, 3]
    assert resolve(doc, TAGS, "things", on_branch="stop") == "things"
    assert resolve(doc, TAGS, "things", on_branch="first") == "things/computer"


def test_no_match_returns_none():
    assert resolve([16], TAGS, ["things", "events"]) is None


def test_segment_boundary():
    # "thing" is its own tag; it must not match "things/..."
    assert resolve([1, 2], TAGS, "thing") is None
    assert resolve([6], TAGS, "things") is None


# --- roots ---------------------------------------------------------------------


def test_root_given_as_path_and_as_segments():
    doc = [7, 8, 9, 10]
    expected = "company/ACME/accounting/cost invoices"
    assert resolve(doc, TAGS, "company/ACME/accounting") == expected
    assert resolve(doc, TAGS, [["company", "ACME", "accounting"]]) == expected
    assert resolve(doc, TAGS, [["", "company", "ACME", "accounting"]]) == expected


@pytest.mark.parametrize(
    "root",
    ["/company/ACME/accounting", "company/ACME/accounting", "ACME/accounting", "accounting"],
)
def test_root_may_start_at_any_depth(root):
    # the result always starts at the top level
    assert resolve([7, 8, 9, 10], TAGS, root) == "company/ACME/accounting/cost invoices"


@pytest.mark.parametrize("root", ["/ACME/accounting", "/accounting", "/ACME"])
def test_leading_slash_anchors_at_top_level(root):
    assert resolve([7, 8, 9, 10], TAGS, root) is None


def test_relative_root_needs_the_whole_chain():
    # "company/accounting" skips ACME: not a chain of parent and child
    assert resolve([7, 8, 9, 10], TAGS, "company/accounting") is None
    assert resolve([7, 8, 9, 10], TAGS, "ACME/cost invoices") is None


def test_relative_root_on_segment_boundaries():
    assert resolve([1, 2], TAGS, "computer") == "things/computer"
    assert resolve([1, 2], TAGS, "omputer") is None
    assert resolve([1, 2], TAGS, "things/comp") is None


def test_same_name_twice_shallowest_wins():
    # only possible with several owners; deterministic anyway
    tags = tree((1, "a", None), (2, "x", 1), (3, "b", None), (4, "deep", 3), (5, "x", 4), (6, "x", None))
    assert resolve([1, 2, 3, 4, 5], tags, "x") == "a/x"
    assert resolve([1, 2, 3, 4, 5, 6], tags, "x") == "x"
    assert resolve([3, 4, 5], tags, "x") == "b/deep/x"


def test_root_path_must_be_present_on_document():
    # document has company/ACME but not accounting
    assert resolve([7, 8], TAGS, "company/ACME/accounting") is None
    assert resolve([7, 8], TAGS, ["company/ACME/accounting", "company"]) == "company/ACME"


def test_root_matching_ignores_case_and_normalisation():
    tags = tree((1, "księgowość", None), (2, "x", 1))
    nfd = unicodedata.normalize("NFD", "KSIĘGOWOŚĆ")
    assert resolve([1, 2], tags, nfd) == "księgowość/x"


def test_parse_roots_anchoring():
    from paperless_tag_path.core import Root

    assert parse_roots("/a/B") == [Root(("a", "b"), True)]
    assert parse_roots(["a", "x/y"]) == [Root(("a",), False), Root(("x", "y"), False)]
    assert parse_roots([["", "a/b"]]) == [Root(("a/b",), True)]


@pytest.mark.parametrize("bad", [[], "", "/", "a//b", "//a", "a/", ["a", ""], [["a", ""]], [[]], [[""]]])
def test_parse_roots_rejects_empty(bad):
    with pytest.raises(ValueError):
        parse_roots(bad)


@pytest.mark.parametrize("bad", [None, 3, {"a": 1}, [3], [["a", 3]]])
def test_parse_roots_rejects_wrong_types(bad):
    with pytest.raises(TypeError):
        parse_roots(bad)


def test_bad_on_branch():
    with pytest.raises(ValueError):
        resolve([1], TAGS, "things", on_branch="last")


# --- strip_root ----------------------------------------------------------------


def test_strip_root():
    assert resolve([11, 15], TAGS, "health", strip_root=True) == "Hypertension"
    assert resolve([12, 13], TAGS, "people", strip_root=True) == "Alice"
    assert (
        resolve([7, 8, 9, 10], TAGS, "company/ACME/accounting", strip_root=True)
        == "cost invoices"
    )
    # relative root: everything down to the matched tag is stripped
    assert resolve([7, 8, 9, 10], TAGS, "accounting", strip_root=True) == "cost invoices"
    assert resolve([7, 8, 9, 10], TAGS, "ACME", strip_root=True) == "accounting/cost invoices"


def test_strip_root_nothing_below_gives_empty_string():
    # root present, no child: "" (not None, so the default is not used)
    assert resolve([11], TAGS, "health", strip_root=True) == ""
    # two people and on_branch=stop: stops at the root
    assert resolve([12, 13, 14], TAGS, "people", strip_root=True) == ""


def test_strip_root_root_absent_gives_none():
    assert resolve([16], TAGS, "people", strip_root=True) is None


# --- descending ----------------------------------------------------------------


def test_first_is_alphabetical_case_insensitive():
    tags = tree((1, "r", None), (2, "beta", 1), (3, "Alpha", 1), (4, "gamma", 1))
    assert resolve([1, 2, 3, 4], tags, "r", on_branch="first") == "r/Alpha"


def test_descends_to_depth_five():
    tags = tree((1, "a", None), (2, "b", 1), (3, "c", 2), (4, "d", 3), (5, "e", 4))
    assert resolve([5], tags, "a") == "a/b/c/d/e"


def test_branching_below_a_single_child():
    tags = tree((1, "a", None), (2, "b", 1), (3, "c", 2), (4, "d", 2))
    assert resolve([1, 2, 3, 4], tags, "a") == "a/b"
    assert resolve([1, 2, 3, 4], tags, "a", on_branch="first") == "a/b/c"


def test_missing_ancestor_on_document_is_restored():
    # only the leaf assigned; parents come from the tag table
    assert resolve([10], TAGS, "company") == "company/ACME/accounting/cost invoices"


def test_unknown_ids_are_ignored():
    assert resolve([999, 2], TAGS, "things") == "things/computer"


def test_tag_with_unknown_parent_is_not_a_root():
    tags = tree((1, "things", 99), (2, "computer", 1))
    assert resolve([1, 2], tags, "things") is None


def test_cycle_in_data_does_not_hang():
    tags = tree((1, "a", 2), (2, "b", 1), (3, "root", None), (4, "x", 3))
    assert resolve([1, 2, 3, 4], tags, "root") == "root/x"
    assert resolve([1, 2], tags, "a") is None


# --- sanitisation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("plain name", "plain name"),
        ("a/b", "a-b"),
        ("a\\b", "a-b"),
        ("x:y", "x-y"),
        ('<>"|?*', "------"),
        ("tab\tand\x00nul", "tab-and-nul"),
        ("AUX", "AUX_"),
        ("com1", "com1_"),
        ("..", EMPTY_SEGMENT),
        (".", EMPTY_SEGMENT),
        ("...", EMPTY_SEGMENT),
        ("", EMPTY_SEGMENT),
        ("  ", EMPTY_SEGMENT),
        ("trailing dot.", "trailing dot"),
        (" spaces ", "spaces"),
    ],
)
def test_sanitize_segment(name, expected):
    assert sanitize_segment(name) == expected


def test_sanitize_segment_length_in_bytes():
    assert len(sanitize_segment("a" * 300).encode()) <= 255
    long_pl = sanitize_segment("ż" * 300)
    assert len(long_pl.encode()) <= 255
    long_pl.encode().decode()  # not cut in the middle of a character


def test_sanitize_segment_nfc():
    nfd = unicodedata.normalize("NFD", "Zażółć")
    assert sanitize_segment(nfd) == unicodedata.normalize("NFC", "Zażółć")


def test_separators_in_names_cannot_add_levels_or_escape():
    tags = tree((1, "root", None), (2, "../../etc", 1), (3, "a/b\\c", 2))
    result = resolve([3], tags, "root")
    assert result == "root/..-..-etc/a-b-c"
    assert ".." not in result.split("/")


def test_dot_tag_becomes_placeholder_segment():
    tags = tree((1, "root", None), (2, "..", 1), (3, "leaf", 2))
    assert resolve([3], tags, "root") == f"root/{EMPTY_SEGMENT}/leaf"
