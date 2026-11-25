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
#   MOVED <path>        an entry relocated from /root
#   KEPT  <path>        an existing agent file preserved as <path>.pre-migration
#   REF   <path>        a moved text file that still mentions /root/
set -eu
SRC=/root
DST=/home/agent

move_entry() {   # move_entry <relative-path>
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

chown -R 1000:1000 "$DST"

# Report moved text files that still hard-code /root/, the one way a relocated
# config can silently break. Caps the scan so a huge cache cannot stall it.
grep -rIl --exclude-dir=.cache --exclude-dir=.npm --exclude-dir=node_modules \
  --exclude-dir=target --exclude-dir=.cargo -e '/root/' "$DST" 2>/dev/null \
  | head -50 | sed 's/^/REF /' || true
