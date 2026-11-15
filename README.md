> 原文中文 README：[README_CN.md](README_CN.md)
> 
> Original Chinese README: [README_CN.md](README_CN.md)
>
> This is an English translation fork of the original Chinese project, which can be found on [npmjs.com](https://www.npmjs.com/package/@epiphanye/pi-hud) and [jsdelivr.com](https://www.jsdelivr.com/package/npm/@epiphanye/pi-hud). Only user-facing strings and documentation were translated; no features or code logic were changed.

# @fr0gtastic/pi-hud-en · Rana Desktop Pet

Displays the Pi Coding Agent's status as a Rana desktop pet that stays on your desktop: transparent character animation, status bubble, always-on-top, and a draggable position. It is a standard Pi Package: the TypeScript extension receives Pi lifecycle events, and the Python / PySide6 client renders the native desktop pet.

## Installation

First, install PySide6:

```powershell
# Windows / WSL2: install using native Windows Python
py -3 -m pip install PySide6
```

```bash
# Linux desktop
python3 -m pip install --user PySide6
```

Install the published version:

```bash
pi install npm:@fr0gtastic/pi-hud-en
```

For local development, install from the repo root:

```bash
pi install .
```

Changes will take effect after restarting Pi or running `/reload`. To avoid port conflicts, please delete any previous manual installations of the extension:

```bash
rm -f ~/.pi/agent/extensions/hud.ts ~/.pi/agent/extensions/hud_client_windows.py
```

## Platforms

| Where Pi runs | Where the pet appears |
| --- | --- |
| WSL2 Ubuntu | Windows (native Windows Python, always-on-top) |
| Windows | Windows (native Windows Python) |
| Linux desktop | Linux (system Python / PySide6) |

On WSL2, the pet opens as a native Windows window by default (even with WSLg enabled) so it can sit on top of regular Windows applications.

## States and Actions

Rana uses the sprite-sheet animations shipped with the package. Each state loops continuously until a Pi event or command changes the state.

| Pi state | Animation row |
| --- | --- |
| Idle | Row 1 or Row 7 (picked at random on each entry, then looped) |
| Working | Row 9 |
| Goal achieved | Row 4 (Pi returns to idle automatically afterwards) |
| Error | Row 6 |
| Waiting for approval | Row 8 |
| Dragging right | Row 2 |
| Dragging left | Row 3 |
| Dragging up/down | Row 5 |

The status bubble is shown in every state by default and can only be hidden from the right-click menu ("Hide Bubble"). It sizes itself to the current status text, sits directly above the character, truncates overly long text, and moves and scales with the pet.

The sprite sheet is a fixed grid of 8 columns × 9 rows (1536×1872 px, 192×208 px per cell). Rows with fewer than 8 frames just skip the empty cells on the right, the frames aren't stretched or redistributed. The idle, goal-achieved, and error animations run at about **3 FPS**; working, waiting-for-approval, and dragging run at about **4 FPS**.

The default character height is about **160px**. The pet's position, bubble preference, and size are saved to `~/.pi/hud-pet.json`; on first run, the position from the older `~/.pi/hud-position.json` is migrated.

## Commands

```text
/hud             # show / hide the desktop pet
/hud show        # show the desktop pet
/hud hide        # hide the desktop pet
/hud restart     # restart the HUD service and the pet
/hud waiting     # explicitly enter the "waiting for approval" state
/hud idle        # return to idle immediately
/hud status      # show port, heartbeat, assets, and current animation diagnostics
```

Waiting-for-confirmation is not inferred from the Agent's text: use `/hud waiting` when needed; once confirmation is done, use `/hud idle`, or just let Pi continue with the next round of tasks.

## Controls

- **Left-click drag**: The character and the bubble are both draggable; clicks on the transparent area around them pass through to the window underneath. Dragging right, left, or up/down plays row 2, 3, or 5, and keeps looping that row while you keep dragging the same way -- the animation only switches if you change direction mid-drag. A click without movement is not a drag; on release the position is saved and the animation returns to the current Pi state.
- **Ctrl + mouse wheel**: Resizes the character in ~16px steps (80-320px tall). The bubble, its text, and the clickable area scale with it. The right-click "Size" menu offers Small / Default / Large / Reset.
- **Left double-click** (Windows / WSL only): Focuses the Edge, Windows Terminal, or VS Code window that was in front before the pet started, restoring it first if minimized.
- **Right-click menu**: Show/hide the bubble, view the current state and animation frame, resize, re-apply always-on-top ("Re-Pin"), hide the pet, or quit.

> On Windows, double-click can only target the top-level window of Edge, VS Code, or the terminal -- not a specific Edge tab at `http://127.0.0.1:30141/`, nor a specific Terminal or VS Code terminal tab.

**Quitting and restarting.** Quitting from the menu closes only the pet window; it does not stop the Pi service, and the health check will usually relaunch it automatically. `/hud restart` takes about a second because it waits for the previous instance to fully release its connection and port; run `/hud status` to check on it.

## Lifecycle

- The pet closes immediately when the Pi CLI or Pi Web **service** exits normally, when the session switches, or when you run `/reload`.
- Closing a Pi Web browser tab is not the same as stopping the Pi Web backend, so the pet stays until the service stops.
- If the pet receives no valid message from Pi for about **12 seconds**, it exits on its own -- for example when Pi is force-killed, WSL shuts down, or local communication is interrupted. **The extension's health check (every 2s) will then usually relaunch it automatically, so a force-kill can mean a ~14s round trip before the pet returns.**
- Windows enforces a single instance (the same single-instance lock `/hud restart` waits on), so repeated launches never stack duplicate pets.

## Limitations

- The pet periodically re-applies the Windows always-on-top flag **(every ~1.5s)**, so it usually reappears above borderless-windowed and windowed-fullscreen games.
- Exclusive-fullscreen games, the UAC secure desktop, the lock screen, protected content, and windows running at a different privilege level are controlled by Windows: a normal desktop app cannot guarantee always-on-top or window activation in those situations.

## Diagnostics

Run `/hud status` inside Pi. The output covers four areas:

- **Restart and connection**: restart phase, bound port, last start time, and any error from the last attempt.
- **Client**: whether the pet is sending heartbeats.
- **Assets**: whether the sprite sheet loaded (`ok` or an error message like missing file / unreadable / wrong grid size).
- **Animation**: current clip, sprite row and frame, measured FPS, bubble text, and scaling values.

Reach for it after `/hud restart` to confirm the new instance came up, or whenever the pet is missing, frozen, or stuck on the wrong animation.

In WSL you can check the Windows Python launcher directly:

```bash
/mnt/c/Windows/pyw.exe -3 --version
```

If that path doesn't exist, set **PI_HUD_PYW** -- the environment variable the extension uses to locate the launcher -- to the real path before starting Pi:

```bash
# Example: Python installed to a non-standard location
export PI_HUD_PYW="/mnt/c/Python312/pythonw.exe"
pi
```
