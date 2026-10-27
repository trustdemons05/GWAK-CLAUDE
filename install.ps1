# Install the cursor-delegate skill into Claude Code's personal skills folder.
$dest = Join-Path $env:USERPROFILE ".claude\skills\cursor-delegate"
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Recurse -Force (Join-Path $PSScriptRoot "cursor-delegate\*") $dest
Write-Host "Installed to $dest"
Write-Host "Next: python `"$dest\scripts\cursor_task.py`" doctor   (then 'login' if not logged in)"
