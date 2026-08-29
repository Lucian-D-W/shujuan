# Windows workbench launcher

The live workbench is not an always-on service by itself. This repository now
includes a Windows launcher that makes it user-clickable:

- If the configured local port already serves the requested endpoint, the
  launcher reuses that service.
- If the configured local port is occupied by an unrelated or unhealthy
  service, the launcher automatically chooses a fallback port and opens that
  healthy workbench URL instead.
- If nothing healthy is running, the launcher starts
  `python -m shujuan workbench serve` in the background.
- If no endpoint is passed, the launcher resolves the current project workbench
  route at click time. For this repository that project route is
  `shujuan-endpoint-workbench`.
- Project roadmap shortcuts default to `-Mode all`, which opens the non-empty
  `all_route` roadmap view. Use `-Mode active` only when you deliberately want
  the active-obligation attention route, which can be empty for a fully closed
  or currently quiet workbench endpoint.
- The opened page is `/workbench`, and the page fetches `/api/projection` with
  `Cache-Control: no-store`, so each click reaches the DB-backed live view
  instead of a stale static export.

## Open directly

Double-click:

```text
scripts\windows\open-shujuan-workbench.cmd
```

Or run:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\windows\open-shujuan-workbench.ps1
```

## Create a desktop shortcut

Run once:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install-shujuan-workbench-shortcut.ps1 -Port 8876
```

This creates a desktop shortcut named from the project directory, for example
`shujuan Roadmap Workbench`. Clicking it opens the live DB-backed project
workbench on local port `8876`, reusing an existing healthy service, starting
one when needed, or switching to a fallback port if that port is unexpectedly
occupied. The default shortcut arguments intentionally omit `-Endpoint`, so the
current project route is resolved at click time instead of baking a transient
endpoint name into the `.lnk`. They intentionally include `-Mode all`, so the
desktop roadmap opens a visible, non-empty project route even when active mode
has no visible route nodes.

Useful options:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\windows\install-shujuan-workbench-shortcut.ps1 -Port 8876 -Mode all -Limit 120
```

Pass `-Endpoint <name>` only for deliberate historical inspection. When an old
`shujuan Live Workbench` shortcut already exists, the installer updates it as a
compatibility shortcut to the same live project route unless
`-SkipLegacyShortcutCleanup` is passed. Use `-UpdateLegacyShortcut` to create or
refresh that legacy compatibility shortcut on demand.

Set `SHUJUAN_PYTHON` when the launcher should use a specific Python:

```powershell
$env:SHUJUAN_PYTHON = "C:\Path\To\python.exe"
```
