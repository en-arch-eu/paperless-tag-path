"""Django app that registers ``tag_path`` in Paperless' template environment.

Enable with ``PAPERLESS_APPS=paperless_tag_path.apps.TagPathConfig``.

If this module cannot be imported, Django does not start (fail-closed): a
missing filter is loud and leaves files where they are, instead of templates
silently failing and documents moving to the default location.
"""

from __future__ import annotations

import logging

from django.apps import AppConfig

from ._version import __version__
from .core import TagNode
from .jinja_filter import make_tag_path_filter

logger = logging.getLogger("paperless.tag_path")


def load_tags_from_db() -> dict[int, TagNode]:
    """The whole tag table as ``id -> TagNode`` (one query, a few hundred rows)."""
    from documents.models import Tag

    return {
        pk: TagNode(id=pk, name=name, parent_id=parent)
        for pk, name, parent in Tag.objects.values_list("id", "name", "tn_parent")
    }


class TagPathConfig(AppConfig):
    name = "paperless_tag_path"
    label = "paperless_tag_path"
    verbose_name = "tag_path template filter"

    def ready(self) -> None:
        # Private Paperless API (documents.templating.environment); checked
        # after every Paperless upgrade. An ImportError here stops startup.
        from documents.templating.environment import _template_environment

        _template_environment.filters["tag_path"] = make_tag_path_filter(load_tags_from_db)
        logger.info("[tag-path] filter registered, version %s", __version__)
