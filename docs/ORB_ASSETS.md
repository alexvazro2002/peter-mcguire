# Thinking Orbs in the native Peter interface

Peter uses [thinking-orbs](https://github.com/Jakubantalik/thinking-orbs), npm version
0.3.1 under its MIT license. Its exported `thinking-orbs/engine` provides the
upstream geometry and canvas painters. The build renders those painters into
208-pixel padded animated WebP assets using the canonical size-64 presets. Flutter
plays the assets locally at about 24 frames per second. Python changes only the
selected state; there are no animation frame messages through the Flet socket.

State mapping: standby → slow breathing (0.45×), listening → gentle listening
waveform (0.75×), thinking → working/orbiting dots, talking → faster listening
waveform (1.2×) with a speaker indicator. Speaking assets use the actual upstream
listening geometry, recorded in provenance; animation reflects the state rather
than measured audio amplitude. Errors use a static local attention icon. Dark and light
assets are included. A half-second blend at each six-second loop boundary avoids
a reset jump in nonperiodic upstream motion. No React or Node process runs when
Peter starts; Node is needed only when rebuilding the included assets.

Rebuild from the project root:

```bash
(cd scripts/thinking_orbs && pnpm install --frozen-lockfile && pnpm run frames)
.venv/bin/python scripts/thinking_orbs/encode.py
```

The pinned package lock, renderer, encoder, provenance record and upstream MIT
license are included in the source package. Intermediate PNG frames and Node
modules are excluded. The recorded Git commit is the inspected upstream reference;
the npm package version and lock integrity identify the actual renderer used.
