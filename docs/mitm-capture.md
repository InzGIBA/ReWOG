# Capturing Decryption Keys via MITM

The key API (`eu1.ultimate-disassembly.com/v/query2018.php`) only serves
authenticated clients. The primary way to fetch keys is logging in with
your own game account (`wog-dump login`) - the tool then gets a fresh
session on every run.

If you cannot use your account (or want keys the account flow cannot
fetch), capture them from your own game traffic with a TLS-intercepting
(MITM) proxy and import them with the extraction script.

> ⚠️ You are intercepting **your own** traffic from **your own** game
> account. The capture contains your auth session - the extraction script
> only reads model/key pairs, but do not share the raw capture publicly.
> Only share the extracted `model key` lines.

## What you need

- [World of Guns: Gun Disassembly](https://store.steampowered.com/app/262410/World_of_Guns_Gun_Disassembly/) (Steam, running on Windows)
- [mitmproxy](https://mitmproxy.org) (free, recommended) or [Fiddler](https://www.telerik.com/fiddler)

## Option A: mitmproxy (recommended)

### 1. Start the capture

```bash
# install: https://mitmproxy.org (pip install mitmproxy, choco, or brew)
mitmdump -w wog-keys.flows
```

This listens on `127.0.0.1:8080` and writes everything to
`wog-keys.flows`. The first run creates the mitmproxy CA certificate in
`~/.mitmproxy/` (on Windows: `%USERPROFILE%\.mitmproxy\`).

### 2. Point Windows at the proxy

*Settings → Network & Internet → Proxy → Use a proxy server*:
address `127.0.0.1`, port `8080`.

### 3. Trust the mitmproxy certificate

Open an **administrator** Command Prompt and run:

```bat
certutil -addstore -f Root "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"
```

Verify: open <http://mitm.it> in a browser - it should show the
mitmproxy certificate installation page (do this check before starting
the game).

### 4. Run the game and open models

Launch World of Guns, log in with your account and **open the weapon
models you need** (in the game's model list / disassembly view). The
client exchanges a `sync` key for a model when it loads it - a model you
never open in game may not show up in the capture.

### 5. Stop the capture

Close the game, then press `Ctrl+C` in the mitmproxy terminal.

### 6. Extract the keys

From the repository root:

```bash
# preview the captured keys
uv run python scripts/extract_keys.py wog-keys.flows

# merge them into your local key store (runtime/data.json + runtime/keys.txt)
uv run python scripts/extract_keys.py wog-keys.flows --merge

# or export a shareable bundle
uv run python scripts/extract_keys.py wog-keys.flows -o new_keys.txt
```

Reading `.flows` needs the optional `mitm` extra:

```bash
uv sync --extra mitm        # or: pip install mitmproxy
```

## Option B: Fiddler

1. Run Fiddler, enable *Tools → Options → HTTPS → Decrypt HTTPS traffic*
   (accept its certificate prompt).
2. Launch the game and open the weapon models you need.
3. Select the `query2018.php` sessions, then
   *File → Export Sessions → Selected Sessions → ZIP*  (this produces a
   `.saz` archive).
4. Extract the keys:

   ```bash
   uv run python scripts/extract_keys.py capture.saz --merge
   ```

## After importing

- `wog-dump decrypt-assets` will now find keys for the imported models.
- To share keys with other users, open a pull request adding the new
  `model key` lines to [`runtime/keys.txt`](../runtime/keys.txt).

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Browser shows certificate warnings / game cannot connect | The CA certificate is not trusted - redo step 3 (administrator shell required). |
| No keys found in the capture | The game did not talk to the key API through the proxy: verify <http://mitm.it> loads through the proxy, open more models in game, and check the game was started *after* the proxy was configured. |
| Game ignores the system proxy | Some clients need a restart after the proxy change; as a fallback use Proxifier or mitmproxy's transparent mode to force the traffic through. |
| `error: Reading mitmproxy captures requires the mitmproxy package` | Run `uv sync --extra mitm` (or `pip install mitmproxy`). |
