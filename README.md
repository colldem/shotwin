# ShotWin

Portable screenshot tool for Windows.

## Download & run

1. **Code → Download ZIP**, unpack anywhere.
2. Run `bin\ShotWin.exe` — no installation, no Python needed.
3. First launch: if Windows shows *"Windows protected your PC"*, click **More info → Run anyway** (the exe is not code-signed).

The app lives in the system tray. `bin/ShotWin.exe` is rebuilt automatically from the latest `main` on every push.

## Hotkeys

| Key | Action |
|---|---|
| `Ctrl+Shift+1` | Area capture |
| `Ctrl+Shift+2` | Screen under the mouse |
| `Ctrl+Shift+3` | All screens (one wide image) |

Editor: `A` arrow, `R` shapes (rectangle / rounded / ellipse / line, Shift = square / circle), `T` text, `N` numbered steps, `B` blur,
`P` pen, `M` marker, `C` color, `Ctrl+Z` undo, `Ctrl+C` copy, `Ctrl+S` save, `Esc` close.
Line width (Thin / Medium / Thick): ▾ next to Arrow, Shapes, Step, Pen, Marker — or keys `1`/`2`/`3` for the current tool.
Text: drag to move, double-click to edit (font, size, bold / italic / underline).
Steps: each click adds the next number (1, 2, 3…); drag to move, double-click to change the number, Undo removes the last one.

Only one copy can run at a time — if hotkeys "are already used", ShotWin is probably already in the tray.

## Build from source

```bat
build.bat
```
Result: `dist\ShotWin.exe`.
