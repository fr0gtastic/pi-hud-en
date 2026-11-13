> 原文中文 README：[README_CN.md](README_CN.md)
> Original Chinese README: [README_CN.md](README_CN.md)

# @fr0gtastic/pi-hud-en · Rana Desktop Pet

Displays the Pi Coding Agent's status as a Rana desktop pet that stays on your desktop: transparent character animation, status bubble, always-on-top, and a draggable position. It is a standard Pi Package: the TypeScript extension receives Pi lifecycle events, and the Python / PySide6 client renders the native desktop pet.

## Installation

Install PySide6 first:

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

Takes effect after restarting Pi or running `/reload`. Please delete the old hand-made extensions to avoid contention over the HUD port:

```bash
rm -f ~/.pi/agent/extensions/hud.ts ~/.pi/agent/extensions/hud_client_windows.py
```

## Platforms

| Pi runtime environment | Desktop pet window |
| --- | --- |
| WSL2 Ubuntu | Windows native Python / Win32 Topmost |
| Windows | Windows native Python |
| Linux desktop | Linux Python / PySide6 |

In WSL2, even when WSLg is enabled, a native Windows window is created by default so that it can cover ordinary Windows applications.

## States and Actions

Rana uses the sprite-sheet animations shipped with the package. Each state loops continuously until a Pi event or command changes the state.

| Pi state | Rana animation row | Bubble |
| --- | --- | --- |
| Idle | Row 1 or Row 7 (chosen randomly on entry, then looped) | Visible by default at all times |
| Working | Row 9 | Visible by default at all times |
| Goal achieved | Row 4 | Visible by default at all times; Pi then automatically returns to idle |
| Execution error | Row 6 | Visible by default at all times |
| Waiting for approval | Row 8 | Visible by default at all times |
| Dragging right | Row 2 | Visible by default at all times |
| Dragging left | Row 3 | Visible by default at all times |
| Dragging up/down | Row 5 | Visible by default at all times |

The sprite sheet is sliced into frames on a fixed **8×9** grid (1536×1872, each cell 192×208). Rows with fewer than 8 frames only play the valid frames on the left; the row width is not evenly redistributed across the valid frames. The idle, complete, and error animations run at about **3 FPS**; the working, waiting-for-confirmation, and dragging animations run at about **4 FPS**.

The default character height is about **160px**. The bubble automatically sizes itself to fit the current status text and sits directly above the character; overly long text is truncated. The bubble stays on screen together with the character and scales proportionally; it can be turned off only via the right-click "Hide Bubble" option. The pet's position, bubble preference, and size are saved in:

```text
~/.pi/hud-pet.json
```

The position from the older `~/.pi/hud-position.json` is migrated on first run.

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

## Operations

- **Left-click drag**: The character's visible outline and the bubble are draggable; clicks on the surrounding transparent area are passed through to the window underneath. Dragging right, left, and up/down plays rows 2, 3, and 5 respectively; moving in the same direction plays the same animation continuously, and the animation switches only when the drag direction changes mid-drag. A simple click does not start a drag; on release the position is remembered and the animation returns to the current Pi state.
- **Ctrl + mouse wheel**: Scales the character height in steps of about 16px (80–320px); the bubble, text, and precise click hit area scale along with it. The right-click "Size" menu offers Small / Default / Large / Reset.
- **Left double-click (Windows / WSL)**: Restores the Edge, Windows Terminal, or VS Code host window that was in the foreground before the pet was started; if the window is minimized, it is restored first.
- **Right-click menu**: Show/hide bubble, view the current state and animation frame, adjust size, re-apply always-on-top (Re-Pin), hide the pet, or quit the pet.
- **Quitting the pet** only closes the GUI window and does not stop the Pi service; the health check will try to relaunch the client, or you can use `/hud restart`.
- `/hud restart` first waits for the old connection, the TCP port, and the Windows single-instance lock to be released, then relaunches the client, so there may be a short delay of about 1 second; use `/hud status` to check `phase`, port, last start time, and specific errors.

> Windows can only reliably restore the top-level window of Edge, VS Code, or the terminal; it cannot reliably target a specific Edge tab at `http://127.0.0.1:30141/`, nor select a specific Terminal / VS Code terminal tab.

## Lifecycle and Limitations

- When the Pi CLI or Pi Web **service** exits normally, or when a session switch or `/reload` occurs, the pet closes immediately.
- Closing a single Pi Web browser page is not the same as stopping the Pi Web backend service, so the pet remains until the service stops.
- If Pi is forcibly killed, WSL is shut down, or local communication is interrupted, the pet automatically exits after about **12 seconds** without receiving a valid server message.
- Windows uses a single-instance mutex, so repeated launches will not stack multiple pets.
- The pet periodically re-applies the Windows topmost flag; borderless windowed and windowed-fullscreen games can usually regain visibility.

Exclusive fullscreen games, the UAC secure desktop, the lock screen, protected content, and different administrator privilege levels are managed by Windows; ordinary desktop programs cannot guarantee always-on-top or window activation.

## Diagnostics

Run this inside Pi:

```text
/hud status
```

The output includes the restart phase, port binding, last start time and any errors, client heartbeat, asset status, and the current clip, sprite row/frame, actual FPS, bubble, and scaling diagnostics.

In WSL you can verify the Windows Python launcher:

```bash
/mnt/c/Windows/pyw.exe -3 --version
```

If the launcher is not in the default location, set this before starting Pi:

```bash
export PI_HUD_PYW="/mnt/c/Windows/pyw.exe"
pi
```
