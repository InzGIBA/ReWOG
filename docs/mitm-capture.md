# Capturing Keys and Identity via MITM

The game's key API (`/v/query2026.php` on `eu1`/`us4.ultimate-disassembly.com`)
only serves authenticated clients. This repo ships no credentials: you harvest
your own from your own game traffic with a TLS-intercepting (MITM) proxy, then
import both the **decryption keys** and your **client identity**
(`id`/`session`/`dev`) in one step.

> **Warning.** You are intercepting **your own** traffic from **your own** game account.
> The capture contains your auth session and your Steam friends list. The
> extraction script only reads model/key and id/session/dev pairs, but do not
> share the raw capture publicly.

## What was verified against the real client (Nov 2026, `disasm64.exe`)

Facts below come from an actual capture of the Windows Steam client and were
confirmed with live requests:

- The client talks to `/v/query2026.php` (the old `query2018.php` endpoint used
  by early versions of this tool is dead: every request gets `result=1000`)
- Client versions sent with each query: `ver=2.2.3u9`, `uver=6000.0.84f1`
  (Unity 6), `User-Agent: UnityPlayer/6000.0.84f1 (... libcurl/8.10.1-DEV)`
- The client connects by **raw IP address** (the TLS SNI/Host header carries
  `eu1`/`us4.ultimate-disassembly.com`). `eu1.ultimate-disassembly.com`
  resolves to the same server and works fine for the tool
- Login is Steam SSO (`query=2&client=steam&user=<steamid>&auth=<steam
  ticket>`). The ticket is not replayable, but the resulting `id`+`session`
  pair is long-lived and is all the tool needs
- The client signs requests with a `hash=...` parameter, but the server does
  **not** require the signature (verified live: requests with no/arbitrary hash succeed)
- The `dev` (device id) is not validated: any value works with a valid
  `id`/`session`, so the tool generates one per installation
- A rejected/expired session produces `result=1000`, which is exactly the
  "Server error" symptom reported in the issues about the old endpoint

## Quick start (mitmproxy, tested with disasm64.exe)

### 1. Start the capture

```bash
# install: https://mitmproxy.org (pip install mitmproxy / choco / brew)
mitmdump -w wog-keys.flows
```

Listens on `127.0.0.1:8080`, writes everything to `wog-keys.flows`. The first
run creates the CA certificate in `~/.mitmproxy/`
(Windows: `%USERPROFILE%\.mitmproxy\`).

### 2. Point Windows at the proxy

*Settings → Network & Internet → Proxy → Use a proxy server*:
`127.0.0.1`, port `8080`.

### 3. Trust the mitmproxy certificate

Administrator Command Prompt:

```bat
certutil -addstore -f Root "%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.cer"
```

Verify by opening <http://mitm.it> in a browser before starting the game.

### 4. Run the game and open models

Launch the game (`disasm64.exe`), log in with Steam, and **open the weapon
models you need**. The client exchanges a `sync` key for a model when the
model loads. Models you never open in game will not appear in the capture.

### 5. Stop and import

Close the game, `Ctrl+C` mitmproxy, then from the repository root:

```bash
# import keys AND your client identity in one go
uv run python scripts/extract_keys.py wog-keys.flows --identity --merge
```

The script prints the captured keys, stores them in the local database and
shows the identity values for your `.env`:

```
Imported client identity (id=5684358):
  WOG_AUTH_ID=5684358
  WOG_AUTH_SESSION=27
  WOG_DEVICE_ID=707e9256...
```

Copy the values into `.env` (see `.env.example`) so key fetching works
directly:

```dotenv
WOG_AUTH_ID=...
WOG_AUTH_SESSION=...
```

With the identity in place, `wog-dump download-assets --update-keys` fetches
keys for any model **your account has access to** on its own. No per-model
capture needed. Capture again only when the session eventually expires.

### Optional: export a key bundle

```bash
uv run python scripts/extract_keys.py wog-keys.flows -o new_keys.txt
```

## Fiddler alternative

1. Run Fiddler, enable *Tools → Options → HTTPS → Decrypt HTTPS traffic*.
2. Launch the game and open the weapon models you need.
3. Select the `query2026.php` sessions, then
   *File → Export Sessions → Selected Sessions → ZIP* (a `.saz` archive).
4. `uv run python scripts/extract_keys.py capture.saz --merge`
   (identity import works with mitmproxy `.flows` captures only).

## Live tests

With credentials in `.env`, the live tests verify the whole pipeline against
the real servers (fetch a key, download an asset, decrypt the file, load the
bundle in UnityPy):

```bash
uv sync --extra dev
pytest -m live          # skipped automatically when .env has no credentials
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Browser certificate warnings / game fails to connect | The CA certificate is not trusted. Redo step 3 (administrator shell required). |
| No keys found in the capture | The game did not talk to the API through the proxy: verify <http://mitm.it> loads through the proxy, open more models in game, start the game *after* configuring the proxy. |
| `result=1000` when fetching keys | The session expired or was replaced. Capture again and update `WOG_AUTH_SESSION` in `.env` (the server bumps the session number over time). |
| `error: Reading mitmproxy captures requires the mitmproxy package` | Run `uv sync --extra mitm` (or `pip install mitmproxy`). |
