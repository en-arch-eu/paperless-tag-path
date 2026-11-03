# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-11-03

### Changed

- `contrib/install-wheel.sh` replaced by `update-tag-path.sh`, which lives in
  the plugins directory and takes no arguments: it installs the latest
  release (or the version given, for a rollback), checks it against
  `SHA256SUMS`, switches the link and updates itself. The script is attached
  to each release. The package itself is unchanged.

## [0.1.0] - 2026-11-03

First version: the `tag_path` filter for storage path templates
(Paperless-ngx 3.4), as proposed in
[discussion #13886](https://github.com/paperless-ngx/paperless-ngx/discussions/13886).
