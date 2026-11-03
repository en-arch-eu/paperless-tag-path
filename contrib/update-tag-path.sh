#!/bin/bash
# Install or update paperless-tag-path in the Paperless plugins directory.
# Keep this script in that directory (root:root, 755) and run it as root:
#
#   sudo ./update-tag-path.sh            # latest release
#   sudo ./update-tag-path.sh 0.1.0      # that version (also: rollback)
#
# Downloads the wheel from the GitHub release, checks it against the
# release's SHA256SUMS, keeps each version as its own file and points the
# stable link at the chosen one (relative, so it also resolves inside the
# container, where the directory is /plugins). Without a version it also
# replaces itself with the copy from the latest release.
# Restart Paperless afterwards.
set -euo pipefail

REPO=en-arch-eu/paperless-tag-path
DIST=paperless_tag_path
INIT_SCRIPT=""          # file from the release to keep in custom-cont-init.d

LINK="$DIST.whl"
SELF="$(readlink -f "$0")"
DIR="${PLUGINS_DIR:-$(dirname "$SELF")}"
INIT_DIR="${INIT_DIR:-$(dirname "$DIR")/custom-cont-init.d}"

die() { echo "ERROR: $*" >&2; exit 1; }
sha_of() { sha256sum "$1" | awk '{print $1}'; }

case "${1:-}" in -h|--help) awk 'NR > 1 && !/^#/ {exit} NR > 1 {sub(/^# ?/, ""); print}' "$0"; exit 0 ;; esac
[[ $# -le 1 ]] || die "usage: $0 [VERSION]"
[[ $EUID -eq 0 ]] || die "run as root (sudo $0 $*)"
[[ -d "$DIR" ]] || die "plugins directory not found: $DIR"
[[ ! -e "$DIR/$LINK" || -L "$DIR/$LINK" ]] || die "$DIR/$LINK exists and is not a link"

SEMVER='(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
VERSION="${1:-}"
VERSION="${VERSION#v}"
if [[ -z "$VERSION" ]]; then
    VERSION="$(curl -fsSL --proto '=https' "https://api.github.com/repos/$REPO/releases/latest" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])')" \
        || die "cannot read the latest release of $REPO"
    VERSION="${VERSION#v}"
    LATEST=1
    echo "Latest release of $REPO: $VERSION"
else
    LATEST=0
fi
[[ "$VERSION" =~ ^$SEMVER$ ]] || die "not a MAJOR.MINOR.PATCH version: $VERSION"

WHEEL="$DIST-$VERSION-py3-none-any.whl"
BASE="https://github.com/$REPO/releases/download/v$VERSION"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
CHANGED=0

get() { curl -fsSL --proto '=https' -o "$TMP/$1" "$BASE/$1"; }
listed() { awk -v f="$1" '$2 == f || $2 == "*"f {print $1}' "$TMP/SHA256SUMS"; }

# put file $1 from the release into directory $2 with mode $3, if it differs
place() {
    local name="$1" dest="$2/$1" mode="$3" want
    want="$(listed "$name")"
    [[ -n "$want" ]] || return 2
    if [[ -f "$dest" && "$(sha_of "$dest")" == "$want" ]]; then
        return 1
    fi
    get "$name" || die "cannot download $BASE/$name"
    [[ "$(sha_of "$TMP/$name")" == "$want" ]] || die "$name: checksum differs from SHA256SUMS"
    install -o root -g root -m "$mode" "$TMP/$name" "$dest.part"
    mv -f -- "$dest.part" "$dest"
}

# 1. the release's checksums (a rollback to a file already here works offline)
if get SHA256SUMS; then
    HAVE_SUMS=1
elif [[ -f "$DIR/$WHEEL" ]]; then
    HAVE_SUMS=0
    echo "WARNING: release v$VERSION not reachable; using $WHEEL already in $DIR, unverified"
else
    die "cannot download $BASE/SHA256SUMS (no release v$VERSION?)"
fi

# 2. the wheel
if (( HAVE_SUMS )); then
    want="$(listed "$WHEEL")"
    [[ -n "$want" ]] || die "$WHEEL is not in release v$VERSION"
    if [[ -f "$DIR/$WHEEL" ]]; then
        [[ "$(sha_of "$DIR/$WHEEL")" == "$want" ]] \
            || die "$DIR/$WHEEL differs from release v$VERSION; a released version never changes"
        echo "Present: $WHEEL"
    else
        echo "Downloading $WHEEL"
        get "$WHEEL" || die "cannot download $BASE/$WHEEL"
        [[ "$(sha_of "$TMP/$WHEEL")" == "$want" ]] || die "$WHEEL: checksum differs from SHA256SUMS"
        python3 - "$TMP/$WHEEL" <<'EOF' || die "$WHEEL is not a valid wheel"
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
assert z.testzip() is None
assert any(n.endswith(".dist-info/METADATA") for n in z.namelist())
EOF
        install -o root -g root -m 644 "$TMP/$WHEEL" "$DIR/$WHEEL.part"
        mv -f -- "$DIR/$WHEEL.part" "$DIR/$WHEEL"
        echo "Installed: $WHEEL"
        CHANGED=1
    fi
fi

# 3. the link
PREVIOUS=""
[[ -L "$DIR/$LINK" ]] && PREVIOUS="$(readlink "$DIR/$LINK")"
if [[ "$PREVIOUS" == "$WHEEL" ]]; then
    echo "Link: $LINK -> $WHEEL (unchanged)"
else
    rm -f -- "$DIR/.$LINK.new"
    ln -s -- "$WHEEL" "$DIR/.$LINK.new"
    mv -T -- "$DIR/.$LINK.new" "$DIR/$LINK"
    echo "Link: $LINK -> $WHEEL"
    CHANGED=1
fi

# 4. the init script, if this plugin has one
if [[ -n "$INIT_SCRIPT" ]]; then
    if [[ ! -d "$INIT_DIR" ]]; then
        echo "NOTE: $INIT_DIR not found; $INIT_SCRIPT not checked (set INIT_DIR)"
    elif (( ! HAVE_SUMS )); then
        echo "NOTE: $INIT_SCRIPT not checked (release not reachable)"
    else
        rc=0; place "$INIT_SCRIPT" "$INIT_DIR" 755 || rc=$?
        case $rc in
            0) echo "Updated: $INIT_DIR/$INIT_SCRIPT"; CHANGED=1 ;;
            1) echo "Init script: $INIT_SCRIPT up to date" ;;
            2) echo "NOTE: release v$VERSION has no $INIT_SCRIPT; left as it is" ;;
        esac
    fi
fi

# 5. this script, from the latest release only
if (( LATEST && HAVE_SUMS )); then
    rc=0; place "$(basename "$SELF")" "$(dirname "$SELF")" 755 || rc=$?
    (( rc == 0 )) && echo "Updated: $SELF (takes effect next run)"
fi

if [[ -n "$PREVIOUS" && "$PREVIOUS" != "$WHEEL" ]]; then
    PREV_VERSION="${PREVIOUS#"$DIST"-}"
    echo "Previous: $PREVIOUS (rollback: sudo $SELF ${PREV_VERSION%-py3-none-any.whl})"
fi
if (( CHANGED )); then
    echo "Restart Paperless to load it."
else
    echo "Nothing to do."
fi
