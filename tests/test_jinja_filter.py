"""The filter as Jinja sees it, in a sandbox like Paperless' ``JinjaEnvironment``."""

import logging
from types import SimpleNamespace

import pytest
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from paperless_tag_path.core import TagNode
from paperless_tag_path.jinja_filter import make_tag_path_filter, tag_refs

TAGS = {
    1: TagNode(1, "things", None),
    2: TagNode(2, "computer", 1),
    3: TagNode(3, "monitor", 1),
    4: TagNode(4, "events", None),
    5: TagNode(5, "2025-01-01 - bought a computer", 4),
    11: TagNode(11, "health", None),
    12: TagNode(12, "people", None),
    13: TagNode(13, "Alice", 12),
    14: TagNode(14, "Bob", 12),
    15: TagNode(15, "Hypertension", 11),
}


def document(*ids):
    # same shape as get_safe_document_context: [{"name": ..., "id": ...}]
    return {"tags": [{"name": TAGS[i].name, "id": i} for i in ids]}


@pytest.fixture
def env():
    e = SandboxedEnvironment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    e.filters["tag_path"] = make_tag_path_filter(lambda: TAGS)
    return e


def render(env, template, **context):
    return env.from_string(template).render(**context)


def test_renders_in_sandbox(env):
    out = render(
        env,
        '{{ document.tags | tag_path(["things", "events"]) }}/{{ title }}',
        document=document(1, 2, 4, 5),
        title="Invoice",
    )
    assert out == "things/computer/Invoice"


def test_keyword_arguments(env):
    doc = document(1, 2, 3)
    assert render(env, '{{ document.tags | tag_path("things", on_branch="first") }}', document=doc) == "things/computer"
    assert render(env, '{{ document.tags | tag_path("events", default="unsorted") }}', document=doc) == "unsorted"


def test_default_is_dot(env):
    assert render(env, '{{ document.tags | tag_path("events") }}', document=document(1)) == "."


def test_health_template_with_two_calls(env):
    template = (
        'health/{{ document.tags | tag_path("people", strip_root=True, default="general") or "shared" }}'
        '/{{ document.tags | tag_path("health", strip_root=True, default="") }}'
    )
    assert render(env, template, document=document(11, 15, 12, 13)) == "health/Alice/Hypertension"
    assert render(env, template, document=document(11, 15, 12, 13, 14)) == "health/shared/Hypertension"
    assert render(env, template, document=document(11, 15)) == "health/general/Hypertension"
    assert render(env, template, document=document(11, 12, 13)) == "health/Alice/"


def test_paperless_dummy_document_gives_default(env):
    # validate_filepath_template_and_render uses Tag(name=...) without ids
    doc = {"tags": [{"name": "Test Tag 1", "id": None}, {"name": "Another Test Tag", "id": None}]}
    assert render(env, '{{ document.tags | tag_path("things", default="x") }}', document=doc) == "x"


@pytest.mark.parametrize(
    "call",
    [
        'tag_path("things", on_branch="last")',
        "tag_path(3)",
        "tag_path([])",
        'tag_path("a//b")',
        'tag_path([["a", 3]])',
    ],
)
def test_bad_arguments_are_template_errors(env, caplog, call):
    # same for any document, so Paperless rejects the template on save
    with caplog.at_level(logging.WARNING, logger="paperless.tag_path"), pytest.raises((TypeError, ValueError)):
        render(env, "{{ document.tags | " + call + " }}", document=document(1, 2))
    assert "invalid argument" in caplog.text


def test_bad_arguments_raise_even_without_tags(env):
    # Paperless validates templates on a dummy document whose tags have no id
    doc = {"tags": [{"name": "Test Tag 1", "id": None}]}
    with pytest.raises(ValueError, match="on_branch"):
        render(env, '{{ document.tags | tag_path("things", on_branch="nope") }}', document=doc)


def test_never_raises_when_loading_tags_fails(caplog):
    def broken():
        raise RuntimeError("database is gone")

    f = make_tag_path_filter(broken)
    with caplog.at_level(logging.WARNING, logger="paperless.tag_path"):
        assert f([{"id": 1}], "things", default="fallback") == "fallback"
    assert "database is gone" in caplog.text


def test_tag_refs_accepts_names_dicts_objects_and_ints():
    value = ["things", {"id": 1, "name": "a"}, SimpleNamespace(id=2), 3, {"id": None}, {"name": "x"}, True]
    assert tag_refs(value) == ([1, 2, 3], ["things"])
    assert tag_refs(None) == ([], [])
    assert tag_refs(42) == ([], [])


def names(*ids):
    # same shape as tag_name_list
    return sorted(TAGS[i].name for i in ids)


def test_tag_name_list_gives_the_same_result(env):
    for template in (
        '{{ tag_name_list | tag_path(["things", "events"]) }}',
        '{{ tag_name_list | tag_path("things", on_branch="first") }}',
        '{{ tag_name_list | tag_path("people", strip_root=True) or "shared" }}',
    ):
        for ids in [(1, 2, 4, 5), (1, 2, 3), (12, 13), (12, 13, 14)]:
            by_name = render(env, template, tag_name_list=names(*ids))
            by_id = render(env, template.replace("tag_name_list", "document.tags"), document=document(*ids))
            assert by_name == by_id, (template, ids)


def test_tag_name_list_only_leaf_needed(env):
    assert render(env, '{{ tag_name_list | tag_path("things") }}', tag_name_list=["computer"]) == "things/computer"


def test_tag_name_list_unknown_names_give_default(env):
    # Paperless validates templates with tags "Test Tag 1", "Another Test Tag"
    out = render(env, '{{ tag_name_list | tag_path("things", default="d") }}', tag_name_list=["Test Tag 1", "Another Test Tag"])
    assert out == "d"


def test_tag_list_string_is_a_template_error(env):
    with pytest.raises(TypeError, match="tag_list"):
        render(env, '{{ tag_list | tag_path("things") }}', tag_list="computer,things")


def test_undefined_variable_is_a_template_error(env):
    # Paperless uses StrictUndefined: {{ tags | ... }} must fail like any other
    # undefined placeholder, so the storage path is rejected when saved.
    from jinja2.exceptions import UndefinedError

    with pytest.raises(UndefinedError):
        render(env, '{{ tags | tag_path("things") }}', document=document(1, 2))
    with pytest.raises(UndefinedError):
        render(env, "{{ document.tags | tag_path(roots) }}", document=document(1, 2))


def test_undefined_in_lenient_environment_gives_default():
    from jinja2 import Environment

    e = Environment()
    e.filters["tag_path"] = make_tag_path_filter(lambda: TAGS)
    assert e.from_string('{{ tags | tag_path("things", default="d") }}').render() == "d"
