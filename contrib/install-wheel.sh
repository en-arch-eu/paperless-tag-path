#!/bin/bash
# Install one version of a plugin wheel into the Paperless plugins directory
# and point a stable link at it. Run on the host (TrueNAS) as root.
#
#   install-wheel.sh --dir <plugins dir> [--sha256 <hex>] <wheel URL or file> <link name>
#   (or PLUGINS_DIR=<plugins dir> in the environment instead of --dir)
#
# Examples:
#   install-wheel.sh --dir /mnt/tank/apps/paperless/plugins https://github.com/<owner>/<repo>/releases/download/<tag>/paperless_tag_path-0.1.1-py3-none-any.whl paperless_tag_path.whl
#   install-wheel.sh --dir /mnt/tank/apps/paperless/plugins ./paperless_tag_path-0.1.1-py3-none-any.whl paperless_tag_path.whl --sha256 <hex>
#   install-wheel.sh --dir /mnt/tank/apps/paperless/plugins paperless_tag_path-0.1.0-py3-none-any.whl paperless_tag_path.whl   # rollback: file already there
#
# Paperless reads the link, never the versioned file:
#   PYTHONPATH=/plugins/paperless_tag_path.whl   (tag_path, imported from the zip)
#
# What it does:
#   1. downloads (or takes) the wheel; the file name must be
#      <name>-<MAJOR.MINOR.PATCH>-py3-none-any.whl (SemVer release, no suffix);
#   2. checks the SHA-256: against --sha256 if given, and for a URL also
#      against SHA256SUMS published in the same release; a mismatch stops
#      before anything in the plugins directory changes;
#   3. checks that the file is a readable zip;
#   4. copies it into the plugins directory (root:root, 644), never
#      overwriting a different file with the same name;
#   5. switches the link in one step (new link + rename), relative, so it
#      also resolves inside the container where the directory is /plugins;
#   6. prints the previous target, i.e. the rollback command.
# It does not restart Paperless: do that yourself, then check the log.
set -euo pipefail

PLUGINS_DIR="${PLUGINS_DIR:-}"
EXPECTED_SHA=""

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
die() { echo "ERROR: $*" >&2; exit 1; }

ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --sha256) EXPECTED_SHA="${2:-}"; shift 2 ;;
        --dir) PLUGINS_DIR="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) ARGS+=("$1"); shift ;;
    esac
done
[[ ${#ARGS[@]} -eq 2 ]] || usage
SOURCE="${ARGS[0]}"
LINK="${ARGS[1]}"

[[ -n "$PLUGINS_DIR" ]] || die "plugins directory not given (--dir or PLUGINS_DIR)"
[[ -d "$PLUGINS_DIR" ]] || die "plugins directory not found: $PLUGINS_DIR"
[[ "$LINK" == *.whl && "$LINK" != */* ]] || die "link name must be a bare *.whl name: $LINK"

WHEEL="$(basename "$SOURCE")"
WHEEL="${WHEEL%%\?*}"
[[ "$WHEEL" =~ ^[A-Za-z0-9_]+-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-py3-none-any\.whl$ ]] \
    || die "not a SemVer release wheel name: $WHEEL"
[[ "$WHEEL" != "$LINK" ]] || die "link name equals the wheel name"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 1. get the file
if [[ "$SOURCE" =~ ^https:// ]]; then
    echo "Downloading $SOURCE"
    curl -fsSL --proto '=https' -o "$TMP/$WHEEL" "$SOURCE"
    SUMS_URL="${SOURCE%/*}/SHA256SUMS"
    echo "Downloading $SUMS_URL"
    curl -fsSL --proto '=https' -o "$TMP/SHA256SUMS" "$SUMS_URL" || die "no SHA256SUMS in the release"
    RELEASE_SHA="$(awk -v f="$WHEEL" '$2 == f || $2 == "*"f {print $1}' "$TMP/SHA256SUMS")"
    [[ -n "$RELEASE_SHA" ]] || die "$WHEEL not listed in SHA256SUMS"
elif [[ -f "$SOURCE" ]]; then
    cp -- "$SOURCE" "$TMP/$WHEEL"
    RELEASE_SHA=""
elif [[ -f "$PLUGINS_DIR/$WHEEL" ]]; then
    # rollback / re-point to a version already installed
    cp -- "$PLUGINS_DIR/$WHEEL" "$TMP/$WHEEL"
    RELEASE_SHA=""
else
    die "no such file or URL: $SOURCE"
fi

# 2. checksum
ACTUAL_SHA="$(sha256sum "$TMP/$WHEEL" | awk '{print $1}')"
echo "SHA-256 $ACTUAL_SHA  $WHEEL"
if [[ -n "$RELEASE_SHA" && "$RELEASE_SHA" != "$ACTUAL_SHA" ]]; then
    die "checksum differs from SHA256SUMS ($RELEASE_SHA)"
fi
if [[ -n "$EXPECTED_SHA" && "${EXPECTED_SHA,,}" != "$ACTUAL_SHA" ]]; then
    die "checksum differs from --sha256 ($EXPECTED_SHA)"
fi
if [[ -z "$RELEASE_SHA" && -z "$EXPECTED_SHA" ]]; then
    echo "WARNING: checksum not verified (local file, no --sha256)"
fi

# 3. readable zip with a .dist-info inside
python3 - "$TMP/$WHEEL" <<'EOF' || die "not a valid wheel"
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
bad = z.testzip()
assert bad is None, f"corrupt member {bad}"
assert any(n.endswith(".dist-info/METADATA") for n in z.namelist()), "no METADATA"
EOF

# 4. copy into the plugins directory
TARGET="$PLUGINS_DIR/$WHEEL"
if [[ -e "$TARGET" ]]; then
    EXISTING_SHA="$(sha256sum "$TARGET" | awk '{print $1}')"
    [[ "$EXISTING_SHA" == "$ACTUAL_SHA" ]] \
        || die "$TARGET exists with different content ($EXISTING_SHA); a released version never changes"
    echo "Already present: $TARGET"
else
    install -m 644 "$TMP/$WHEEL" "$TARGET.part"
    if [[ $EUID -eq 0 ]]; then chown root:root "$TARGET.part"; else echo "WARNING: not root, owner left as $(id -un)"; fi
    mv -f -- "$TARGET.part" "$TARGET"
    echo "Installed: $TARGET"
fi

# 5. switch the link
PREVIOUS=""
if [[ -L "$PLUGINS_DIR/$LINK" ]]; then
    PREVIOUS="$(readlink "$PLUGINS_DIR/$LINK")"
elif [[ -e "$PLUGINS_DIR/$LINK" ]]; then
    die "$PLUGINS_DIR/$LINK exists and is not a link"
fi
rm -f -- "$PLUGINS_DIR/.$LINK.new"
ln -s -- "$WHEEL" "$PLUGINS_DIR/.$LINK.new"
mv -T -- "$PLUGINS_DIR/.$LINK.new" "$PLUGINS_DIR/$LINK"
echo "Link: $LINK -> $WHEEL"

# 6. rollback hint
if [[ -n "$PREVIOUS" && "$PREVIOUS" != "$WHEEL" ]]; then
    echo "Previous: $PREVIOUS"
    echo "Rollback: $0 --dir $PLUGINS_DIR $PREVIOUS $LINK"
fi
echo "Restart Paperless to load it."
