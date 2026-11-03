"""The Jinja filter itself, independent of Django.

``make_tag_path_filter(load_tags)`` returns the function registered as
``tag_path``. ``load_tags`` supplies the tag tree; in Paperless it reads the
``Tag`` table (see ``apps.py``), in tests it is a plain function.

Two kinds of problems are treated differently.

**Errors in the template** raise, so Paperless rejects the template when the
storage path is saved ("Invalid variable detected"), the same as an undefined
placeholder anywhere else:

- an undefined variable under ``StrictUndefined`` (Paperless' mode), e.g.
  ``{{ tags | tag_path(...) }}`` instead of ``document.tags``;
- a string as input (``tag_list`` instead of ``tag_name_list``);
- a wrong argument: ``on_branch`` other than ``"stop"``/``"first"``, ``roots``
  of the wrong type or with an empty segment.

They depend only on the template, never on the document, so a template that
passed validation does not hit them later.

**Problems with the data** (unknown tag ids, a database error, anything
unexpected while resolving) do not raise: a failing template makes Paperless
fall back to the default file name and move the file (#14128). They are
logged and ``default`` is returned instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping

from jinja2 import StrictUndefined
from jinja2.exceptions import UndefinedError

from .core import TagNode, ids_for_names, parse_roots, resolve, validate_on_branch

logger = logging.getLogger("paperless.tag_path")

DEFAULT = "."


def check_value(value: object) -> None:
    """The filter input must be a list of tags (``tag_name_list``, ``document.tags``).

    A string is a template error (most likely ``tag_list``, which is one
    comma-separated string): same for every document, so it raises.
    """
    if isinstance(value, (str, bytes)):
        raise TypeError("tag_path expects tag_name_list or document.tags, not a string (tag_list?)")


def tag_refs(value: object) -> tuple[list[int], list[str]]:
    """Tag ids and tag names from the filter input.

    - ``tag_name_list``: names;
    - ``document.tags``: dicts with ``id`` (and ``name``), the id is used;
    - tag objects with ``id``, or plain ints.

    Items without a usable id or name (the dummy tags Paperless uses to
    validate a template have ``id=None``) are skipped.
    """
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return [], []
    ids: list[int] = []
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
            continue
        if isinstance(item, Mapping):
            tag_id = item.get("id")
        elif isinstance(item, int):
            tag_id = item
        else:
            tag_id = getattr(item, "id", None)
        if isinstance(tag_id, int) and not isinstance(tag_id, bool):
            ids.append(tag_id)
    return ids, names


def make_tag_path_filter(
    load_tags: Callable[[], Mapping[int, TagNode]],
) -> Callable[..., object]:
    def tag_path(
        value: object,
        roots: object,
        on_branch: str = "stop",
        default: object = DEFAULT,
        strip_root: bool = False,
    ) -> object:
        # Errors in the template: raise.
        for name, arg in (
            ("value", value),
            ("roots", roots),
            ("on_branch", on_branch),
            ("default", default),
            ("strip_root", strip_root),
        ):
            if isinstance(arg, StrictUndefined):
                logger.warning("tag_path: %s is an undefined variable", name)
                raise UndefinedError(f"tag_path: {name} is an undefined variable")
        try:
            check_value(value)
            validate_on_branch(on_branch)
            parse_roots(roots)
        except (TypeError, ValueError) as exc:
            logger.warning("tag_path: invalid argument: %s", exc)
            raise

        # Problems with the data: log and return default.
        try:
            ids, names = tag_refs(value)
            if not ids and not names:
                return default
            tags = load_tags()
            if names:
                ids = [*ids, *ids_for_names(names, tags)]
            result = resolve(
                ids,
                tags,
                roots,
                on_branch=on_branch,
                strip_root=bool(strip_root),
            )
        except Exception as exc:  # noqa: BLE001 - must not move files
            logger.warning("tag_path: %s; returning default %r", exc, default)
            return default
        return default if result is None else result

    return tag_path
