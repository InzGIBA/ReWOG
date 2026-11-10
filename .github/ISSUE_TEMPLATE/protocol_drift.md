---
name: Protocol drift
about: The tool broke after a game update (login fails, downloads or keys stop working)
title: "Protocol drift: <what broke>"
labels: protocol-drift
---

A protocol drift report needs the probe output first: everything else
guesses.

## 1. Probe output

```
wog-dump info --protocol-probe
```

Paste the full table (or run with `--probe-json` for machine output).
Any FAIL line is the interesting part.

## 2. Environment

- WOG Dump version (`wog-dump --version`):
- Game version installed:
- OS:

## 3. What happened

- Which command broke: (login / download-assets / decrypt-assets / ...)
- The exact error message:
- Did the game update recently? (Steam build number if visible)

## 4. Captures (optional but decisive)

If you can run mitmproxy against the standalone game client, attach the
capture per docs/mitm-capture.md. **Scrub credentials** before
attaching: remove `pass=`, `hash=`, `session=`, `acc_id=` values.
