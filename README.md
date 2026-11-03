# paperless-tag-path

A `tag_path` Jinja filter for [Paperless-ngx](https://github.com/paperless-ngx/paperless-ngx)
storage paths: it builds a directory path from the **tag hierarchy** (nested
tags, Paperless 2.19+). Implements the proposal in
[discussion #13886](https://github.com/paperless-ngx/paperless-ngx/discussions/13886)
as a Django app, without changes to Paperless itself.

Written for Paperless-ngx **3.4.x**. Third-party extensions are not supported
by the Paperless team; do not report problems with this filter there.

## Usage

```jinja
{{ tag_name_list | tag_path(["things", "events"], default="unsorted") }}/{{ created }} - {{ title }}
```

The input is the document's tags, in either form Paperless puts in the
template context (there is no top-level `tags` variable in 3.4):

- `tag_name_list`: the documented list of tag names; use this one;
- `document.tags`: the same tags with ids. Tag names are unique per owner, so
  on an instance where two owners have tags with the same name, use this form
  to avoid matching the other owner's tag.

`tag_list` (one comma-separated string) is rejected.

| Argument | Default | Meaning |
| --- | --- | --- |
| `roots` | required | One root or a list of roots; with a list, **the first root present on the document wins**. A root is a chain of tag names: `"accounting"`, `"ACME/accounting"`, `"company/ACME/accounting"` match anywhere in the tree; a leading `/` (`"/company/ACME"`) anchors it at the top level. For tag names containing `/`, give a list of segments: `["company", "A/B"]` (a leading `""` anchors it). |
| `on_branch` | `"stop"` | When the current tag has more than one child tag on the document: `"stop"` stops there, `"first"` takes the alphabetically first child and keeps descending. |
| `default` | `"."` | Returned when no root is present on the document, and when resolving fails. |
| `strip_root` | `False` | Leave out everything down to and including the matched root tag. If nothing lies below it, the result is `""` (not `default`). |

The result always starts at the top level of the tree, wherever the root
matched. Matching is on whole segments (`"thing"` does not match `things`,
`"company/accounting"` does not match `company/ACME/accounting`),
case-insensitive, after NFC normalisation. If a root matches several tags
(possible only with several owners), the shallowest wins, then alphabetical
order. Ancestors are taken from the tag table, so a document carrying only a
leaf tag still matches its root.

With `company/ACME/accounting/cost invoices` on a document:

```jinja
{{ tag_name_list | tag_path("accounting") }}               → company/ACME/accounting/cost invoices
{{ tag_name_list | tag_path("/company/ACME/accounting") }} → company/ACME/accounting/cost invoices
{{ tag_name_list | tag_path("/ACME/accounting") }}         → .  (ACME is not at the top level)
{{ tag_name_list | tag_path("ACME", strip_root=True) }}    → accounting/cost invoices
```

### Examples

A document tagged `events/2025-01-01 - bought a computer` and `things/computer`:

```jinja
{{ tag_name_list | tag_path(["things", "events"]) }}   → things/computer
{{ tag_name_list | tag_path(["events", "things"]) }}   → events/2025-01-01 - bought a computer
```

A document tagged `things/computer` and `things/monitor`:

```jinja
{{ tag_name_list | tag_path("things") }}                    → things
{{ tag_name_list | tag_path("things", on_branch="first") }} → things/computer
```

Two calls in one template, with `strip_root` and a fallback for documents
concerning several people:

```jinja
health/{{ tag_name_list | tag_path("people", strip_root=True, default="general") or "shared" }}/{{ tag_name_list | tag_path("health", strip_root=True, default="") }}
```

## Safety

- **Every segment is sanitised before joining**, with the same function
  Paperless uses for placeholders (`pathvalidate.sanitize_filename`, `-` as
  replacement, NFC): `/`, `\`, `:` and other characters rejected by Windows or
  SMB, control characters, reserved names (`AUX` → `AUX_`), trailing dots and
  spaces; at most 255 bytes per segment. A tag name can never add a level.
- Segments consisting only of dots (`.`, `..`) or empty after cleaning become `_`.
- **Errors in the template raise, problems with the data do not.** An
  undefined variable (`{{ tags | ... }}`), a string as input (`tag_list`) or a wrong argument
  (`on_branch="nope"`, `roots="a//b"`) raises, so Paperless rejects the
  template when the storage path is saved ("Invalid variable detected"). These
  depend only on the template, so a saved template never hits them later.
  Anything that goes wrong with the data (unknown tags, a database error) is
  logged (logger `paperless.tag_path`) and `default` is returned: a failing
  template would make Paperless fall back to the default file name and move
  the file.
- Paperless still checks the whole rendered path (`is_safe_relative_path`).

## Installation (Docker / TrueNAS app)

1. Put the wheel in the directory mounted as `/plugins`, with a stable link
   pointing at it. `contrib/install-wheel.sh` does that (download from a release,
   SHA-256 check, link switch, rollback hint):

   ```
   plugins/
     paperless_tag_path-0.1.0-py3-none-any.whl
     paperless_tag_path.whl -> paperless_tag_path-0.1.0-py3-none-any.whl
   ```

   The link must be relative (it is resolved inside the container).

   On the host, as root, for example:

   ```
   ./install-wheel.sh --dir /mnt/tank/apps/paperless/plugins \
     https://github.com/en-arch-eu/paperless-tag-path/releases/download/v0.1.0/paperless_tag_path-0.1.0-py3-none-any.whl \
     paperless_tag_path.whl
   ```

   The script checks the file against `SHA256SUMS` from the same release.

2. Set two environment variables once and restart:

   ```
   PYTHONPATH=/plugins/paperless_tag_path.whl
   PAPERLESS_APPS=paperless_tag_path.apps.TagPathConfig
   ```

3. Check the log for `[tag-path] filter registered, version 0.1.0`.

Updating is: install the new wheel (the link moves), restart. Rolling back is
the same with the previous file.

The wheel is imported directly as a zip (the package is pure Python), not
installed with `pip`. A `/custom-cont-init.d` script cannot install it: in the
3.4 image the custom init scripts run after `init-migrations`,
`init-superuser` and `init-system-checks`, and those already start Django with
`PAPERLESS_APPS`, so the app has to be importable before any init script runs.

If the package cannot be imported, Django does not start. This is deliberate:
a missing filter would otherwise make every template using it fail, and
Paperless would move files to default locations.

**Rollback:** remove the app from `PAPERLESS_APPS` (and `PYTHONPATH`), restart. Storage
paths that use `tag_path` must be switched back first, otherwise the next
save or `document_renamer` moves the files.

The filter uses a private Paperless API
(`documents.templating.environment._template_environment`). Check it after
every Paperless upgrade.

## Development

```
uv run pytest
```

A throwaway Paperless with the filter, for trying it by hand:

```
uv build
# dev/.env: PAPERLESS_SECRET_KEY=<random>
docker compose -f dev/compose.yml --env-file dev/.env up -d
uv run --no-project --with httpx python dev/seed.py
```

UI on http://localhost:8010 (admin / admin), stored files in `dev/media`.

The logic (`core.py`) and the filter (`jinja_filter.py`) have no Django
imports; only `apps.py` touches Paperless.

## Versioning

Releases follow [Semantic Versioning 2.0.0](https://semver.org/):
`MAJOR.MINOR.PATCH`, released versions only (no pre-release suffixes, so the
version is the same in the git tag, the wheel name and PEP 440). While the
major version is 0, a breaking change raises MINOR. A released version is
never rebuilt or changed. Changes are listed in `CHANGELOG.md`.

The public API covered by the version is: the filter name `tag_path`, its arguments and their meaning (including which input forms are accepted and what is a template error), the format of the returned path, and the installation interface (`PYTHONPATH`, `PAPERLESS_APPS=paperless_tag_path.apps.TagPathConfig`).

## Licence

GPL-3.0-or-later (same as Paperless-ngx).
