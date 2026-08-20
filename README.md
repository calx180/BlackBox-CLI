# BlackBox ⚠️

A terminal screensaver for staring at while doing absolutely nothing —
in the spirit of `cmatrix`, `pipes.sh`, and `cbonsai`, but themed
around the little floating drone companions from *NieR: Automata*.

A cube tumbles slowly in place, its faces etched with a stepped
circuit-trace pattern (modeled on the physical Blackbox replica), all
rendered with Unicode braille sub-cell dots for fine, near-vector line
detail instead of blocky ASCII. Scan particles drift around it, static
flickers across the surface every so often, and — if you're running
[Hyprland](https://hyprland.org) — two instances can notice each other
getting close on screen and burst into a shared white flash at once.

Pure Python stdlib. No dependencies to install.

## Requirements

- Python 3.8+
- A terminal with a UTF-8 locale and a font that covers Unicode
  braille (U+2800–U+28FF) — true of essentially every modern terminal
  font
- *(optional)* [Hyprland](https://hyprland.org), for the cross-window
  burst effect — everything else works on any OS/WM/terminal that
  supports `curses`

## Usage

```sh
python3 blackbox.py
```

Press `q` or `Esc` to quit. Press `b` at any time to trigger the
burst effect manually (handy for previewing it without needing a
second window nearby).

### Options

| Flag | Default | Description |
|---|---|---|
| `--fps` | `30` | Frames per second |
| `--speed` | `1.0` | Rotation speed multiplier |
| `--particles` | `10` | Number of orbiting scan particles |
| `--no-glitch` | off | Disable the occasional static-glitch flicker |
| `--hud` | off | Show the status-line / rotation-coordinate readout |
| `--no-proximity` | off | Disable the cross-window burst detection |
| `--proximity-threshold` | `80` | Pixel gap between windows that triggers a burst |
| `--poll-interval` | `0.4` | Seconds between window-position checks |
| `--burst-cooldown` | `6.0` | Minimum seconds between bursts |

## The cross-window burst

Run two copies of the script in separate terminal windows and drag or
tile them near each other. Each instance tags its own terminal window
title and periodically asks `hyprctl` for the position of every other
window tagged the same way. When two Blackbox windows get close
enough on screen, both burst into a bright, ragged-edged white flash
at the same moment — with no direct connection between the two
processes, since they're just reading the same live window layout
from the compositor and reacting to it independently.

On anything other than Hyprland this feature simply stays inactive
(everything else runs the same); the `b` key always works as a manual
trigger regardless of window manager.

## How it renders

Each terminal cell holds a 2×4 grid of sub-cell dots via Unicode
braille characters, giving roughly 8x the effective resolution of a
plain one-glyph-per-cell renderer. The cube's structural edges and its
surface trace pattern are each drawn onto their own dot canvas, then
combined per cell — with the trace pattern taking rendering priority
so the surface artwork reads as the dominant visual, closer to the
physical object than a plain wireframe.

## Inspiration

Modeled after the black cube "Blackbox" companion drones from
*NieR: Automata*, and specifically after a physical replica of one —
this is a fan-made, unofficial tribute and isn't affiliated with or
endorsed by the game's creators or publisher.
