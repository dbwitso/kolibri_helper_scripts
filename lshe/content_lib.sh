# Shared by get_content.sh and export_channels.sh: reading and checking a
# channel pack. Source it; it defines functions only.
#
# A channel pack is the folder Kolibri's exportchannel/exportcontent write
# (and build_offline_channels.sh, or Device > Channels > Export):
#
#   <pack>/channels.conf                 channel ids, one per line (# comments)
#   <pack>/content/databases/<id>.sqlite3  one per channel
#   <pack>/content/storage/a/b/<md5>.<ext>  the files, named by their MD5
#
# Kolibri names every content file by the MD5 of its bytes, so a file can be
# checked against its own name: no separate checksum list is needed.

# Channel ids in a pack: from channels.conf, else from its databases.
pack_channel_ids() {
  local pack="$1" line id
  if [[ -r "$pack/channels.conf" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      id="${line%%#*}"
      id="${id//[[:space:]]/}"
      [[ -n "$id" ]] && echo "$id"
    done <"$pack/channels.conf"
  else
    find "$pack/content/databases" -maxdepth 1 -name '*.sqlite3' -printf '%f\n' 2>/dev/null |
      sed 's/\.sqlite3$//'
  fi
}

# Size of a pack's files, in MB.
pack_size_mb() {
  du -sm "$1/content" 2>/dev/null | cut -f1
}

# Check a pack. With "full", also check every file against its MD5 name
# (slow for large packs: about a minute per few GB); with "repair", do that
# and delete each damaged file, so copying again replaces it (rsync would
# otherwise skip a damaged file of the right size and date). Prints what it
# finds; returns 0 if the pack is complete, 1 if anything is missing or
# damaged.
verify_pack() {
  local pack="$1" mode="${2:-quick}" problems=0 id db files bad=0 checked=0 f name sum
  if [[ ! -d "$pack/content/databases" ]]; then
    echo "  NOT A CHANNEL PACK: $pack/content/databases is missing"
    return 1
  fi
  local ids
  ids="$(pack_channel_ids "$pack")"
  if [[ -z "$ids" ]]; then
    echo "  NO CHANNELS: no channels.conf and no channel databases in $pack"
    return 1
  fi
  [[ -r "$pack/channels.conf" ]] || echo "  (no channels.conf: using the channel databases found)"
  for id in $ids; do
    if ! [[ "${id//-/}" =~ ^[0-9a-fA-F]{32}$ ]]; then
      echo "  BAD CHANNEL ID in channels.conf: $id"
      problems=$((problems + 1))
      continue
    fi
    db="$pack/content/databases/${id}.sqlite3"
    if [[ ! -s "$db" ]]; then
      echo "  MISSING channel database for $id"
      problems=$((problems + 1))
    elif [[ "$(head -c 15 "$db")" != "SQLite format 3" ]]; then
      echo "  DAMAGED channel database for $id (not an SQLite file)"
      problems=$((problems + 1))
    else
      echo "  channel $id: database ok ($(du -h "$db" | cut -f1))"
    fi
  done
  files=$(find "$pack/content/storage" -type f 2>/dev/null | wc -l)
  echo "  content files: $files ($(pack_size_mb "$pack") MB in total)"
  if [[ "$files" -eq 0 ]]; then
    echo "  NO CONTENT FILES in $pack/content/storage"
    problems=$((problems + 1))
  fi
  if [[ "$mode" == "full" || "$mode" == "repair" ]] && [[ "$files" -gt 0 ]]; then
    echo "  checking every file against its checksum..."
    while IFS= read -r -d '' f; do
      name="$(basename "$f")"
      name="${name%%.*}"
      checked=$((checked + 1))
      # Only files named by an MD5 can be checked this way.
      [[ "$name" =~ ^[0-9a-f]{32}$ ]] || continue
      sum="$(md5sum "$f" | cut -d' ' -f1)"
      if [[ "$sum" != "$name" ]]; then
        if [[ "$mode" == "repair" ]]; then
          rm -f "$f"
          echo "  DAMAGED file removed (copy again to replace it): ${f#"$pack"/}"
        else
          echo "  DAMAGED file: ${f#"$pack"/}"
        fi
        bad=$((bad + 1))
      fi
    done < <(find "$pack/content/storage" -type f -print0)
    echo "  checked $checked file(s): $bad damaged"
    problems=$((problems + bad))
  fi
  if [[ "$problems" -gt 0 ]]; then
    echo "  => $problems problem(s): this pack is not complete"
    return 1
  fi
  echo "  => pack ok"
  return 0
}
