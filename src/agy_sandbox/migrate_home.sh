#!/bin/sh
# Move the old root session's home into the agent's home, in place, inside a sandbox.
#
# A sandbox built by agy-sandbox 0.5 started its sessions as root, so everything an
# agent wrote lives under /root. After the snapshot's start-user is flipped to agent
# (see snapshot.py), this runs once as root inside the new sandbox to relocate that
# state to /home/agent, where the 0.6 tools and kits expect it.
#
# It never deletes a user's data. When a name already exists in the destination, the
# existing file is renamed aside with a .pre-migration suffix and the old one takes its
# place. Directories are merged entry by entry so volume mountpoints under the
# destination (e.g. ~/.claude/projects) receive the moved files instead of being hidden.
# Every line it prints is a record for the migration report:
#   MOVED  <path>       an entry relocated from /root
#   KEPT   <path>       an existing agent file preserved as <path>.pre-migration
#   RELINK <path>       a symlink that pointed inside /root, repointed into /home/agent
#   FIXVENV <path>      a relocated virtualenv file whose /root path was rewritten to /home/agent
#   REF    <path>       a moved text file that still mentions /root/
set -eu
SRC=${MIGRATE_SRC:-/root}
DST=${MIGRATE_DST:-/home/agent}
OWNER=${MIGRATE_OWNER:-1000:1000}   # the agent user; overridable only so tests can run off-sandbox

move_entry() {   # move_entry <relative-path>
  # rel/s/d/name/child are declared local: this function recurses, and without local a
  # child call would clobber the parent loop's rel and derail the walk (POSIX sh has no
  # block scope). dash, busybox ash and bash — every /bin/sh these images ship — honour it.
  local rel s d name child
  rel=$1
  s="$SRC/$rel"
  d="$DST/$rel"
  if [ ! -e "$d" ] && [ ! -L "$d" ]; then
    mkdir -p "$(dirname "$d")"
    mv "$s" "$d"
    echo "MOVED $rel"
    return
  fi
  if [ -d "$s" ] && [ ! -L "$s" ] && [ -d "$d" ] && [ ! -L "$d" ]; then
    # Merge: recurse into every child so a mounted volume under $d keeps its mount.
    for child in "$s"/.* "$s"/*; do
      [ -e "$child" ] || [ -L "$child" ] || continue
      name=$(basename "$child")
      [ "$name" = "." ] && continue
      [ "$name" = ".." ] && continue
      move_entry "$rel/$name"
    done
    return
  fi
  # A name clash that is not two directories: preserve the agent's file, move ours in.
  mv "$d" "$d.pre-migration"
  echo "KEPT $rel.pre-migration"
  mv "$s" "$d"
  echo "MOVED $rel"
}

for entry in "$SRC"/.* "$SRC"/*; do
  [ -e "$entry" ] || [ -L "$entry" ] || continue
  name=$(basename "$entry")
  [ "$name" = "." ] && continue
  [ "$name" = ".." ] && continue
  move_entry "$name"
done

# Repoint symlinks whose target was inside the old home: their target has just moved
# from $SRC to $DST, so an absolute link into $SRC now dangles. A relative link (e.g.
# one cpython dir to another) moved together with its target and is left alone.
find "$DST" -type l 2>/dev/null | while IFS= read -r link; do
  target=$(readlink "$link")
  case "$target" in
    "$SRC" | "$SRC"/*)
      # A link that cannot be rewritten (e.g. on a read-only mount) is reported, not fatal:
      # everything has already moved and the person can fix one link by hand.
      if ln -sfn "$DST${target#"$SRC"}" "$link" 2>/dev/null; then
        echo "RELINK ${link#"$DST"/}"
      else
        echo "could not repoint symlink $link -> $target" >&2
      fi
      ;;
  esac
done

# Relocate Python virtualenvs. A venv bakes its own absolute path into pyvenv.cfg, every
# bin/ entry-point's shebang and the activate scripts, so a moved venv (e.g. a `uv tool`
# like architecture-registry) points its interpreter back at the now-empty $SRC. Rewrite
# $SRC -> $DST inside each venv only (a dir with a pyvenv.cfg), where every such reference
# is machine-generated — never touching session logs, audit trails or other user data.
find "$DST" -name pyvenv.cfg -type f 2>/dev/null | while IFS= read -r cfg; do
  venv=$(dirname "$cfg")
  # The venv's top-level metadata (pyvenv.cfg, uv-receipt.toml, ...) and every bin/ entry
  # point — but never lib/, where a package's own data may legitimately contain the string.
  for f in "$venv"/* "$venv"/bin/*; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue    # never write through a symlink into some other file
    grep -IFq -- "$SRC/" "$f" 2>/dev/null || continue
    tmp="$f.migtmp"
    if sed "s#$SRC/#$DST/#g" "$f" > "$tmp" && cat "$tmp" > "$f"; then
      echo "FIXVENV ${f#"$DST"/}"
    fi
    rm -f "$tmp"
  done
done

chown -R "$OWNER" "$DST"

# Report moved text files that still hard-code /root/, the one way a relocated
# config can silently break. Caps the scan so a huge cache cannot stall it.
grep -rIl --exclude-dir=.cache --exclude-dir=.npm --exclude-dir=node_modules \
  --exclude-dir=target --exclude-dir=.cargo -e '/root/' "$DST" 2>/dev/null \
  | head -50 | sed 's/^/REF /' || true
