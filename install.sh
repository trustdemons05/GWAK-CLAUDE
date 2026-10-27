#!/usr/bin/env sh
# Install the cursor-delegate skill into Claude Code's personal skills folder.
set -e
dest="$HOME/.claude/skills/cursor-delegate"
mkdir -p "$dest"
cp -R "$(dirname "$0")/cursor-delegate/." "$dest/"
echo "Installed to $dest"
echo "Next: python3 \"$dest/scripts/cursor_task.py\" doctor   (then 'login' if not logged in)"
