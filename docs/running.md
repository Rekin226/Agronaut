# Install and run: all the options

The [README](../README.md) has the short path. This page covers working from a checkout, Docker, a hosted demo, every CLI command, and keeping a bot running.

### From source

To change Agronaut rather than just use it:

```bash
git clone https://github.com/Rekin226/Agronaut.git && cd Agronaut
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
python3 -m pytest        # no model server needed
```

### Docker (one command)

```bash
docker compose up web            # Streamlit at http://localhost:8501
docker compose --profile bot up  # web + the Telegram bot (needs .env)
```

The web app's Design section works immediately. The Assistant and the bot need an
LLM provider configured in a local `.env` (see [configuration.md](configuration.md)). The SQLite memory DB persists in a
named volume, shared between web and bot.

(`pip install -r requirement.txt` still works if you only want the libraries — `pip install -e .`
installs the same list and adds the command below. For a **deterministic-only** install with no
chat stack, `requirements.txt` is a light manifest covering just the calculator and optimizer.)

### Deploy a hosted demo (free, ~2 min)

The deterministic modes deploy to [Streamlit Community Cloud](https://share.streamlit.io) with
zero config — the light `requirements.txt` keeps the build fast and key-free:

1. Fork this repo (or use your own).
2. Streamlit Community Cloud → **New app** → pick the repo, branch `main`, main file `app.py`.
3. Deploy. Chat mode shows a friendly "needs the chat stack" note; the calculator and
   optimizer are fully live.

See [`docs/demo.md`](demo.md) for the deployment details and for recording a README GIF.

### The `agronaut` command

One front door over everything the project ships. It works from any directory once
installed; bare `agronaut` is the chat REPL, because that is what most people want.

```bash
agronaut                         # chat with the agent in the terminal
agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000
agronaut size-hydro --crop lettuce --area 10 --temp 22 --water 500
agronaut optimize --area 10 --temp 28 --water 5000 --objective food
agronaut list                    # supported species, crops, objectives
agronaut web                     # the Streamlit app (trailing flags go to streamlit,
                                 #   e.g. agronaut web --server.port=9000)
agronaut bot                     # the Telegram bot
agronaut review                  # approve/reject pending community insights
agronaut analytics               # usage summary: latency p50/p95, tokens, feedback
agronaut traces                  # recent turns as pipeline traces (no message text)
agronaut --version               # version, the code path it loaded, and the .env it reads
agronaut doctor                  # check the install, config, provider, corpus, data, channels
agronaut update                  # check PyPI for a newer release and install it
```

`--version` prints where the code came from, not just a number. If you have both a checkout
and a `pip install`, that path is the only way to tell which one you are running, and getting
it wrong is how you end up debugging a bug you already fixed.

**When something is off, run `agronaut doctor`.** It checks the install (including whether a
stale copy is shadowing it), the config, the model provider, the knowledge corpus, the seven
reference tables, the validation record, the database and the channels, and every failure
comes with a `fix:` line. It exits 0 when nothing is broken and 1 when something is, so it
works in a script. Being offline shows as skipped, not failed. It checks your setup, not the
accuracy of the advice: for that, see `data/twin_validation.json`.

**Scripting it.** `size`, `size-hydro`, `optimize` and `list` take `--json`. A sizing result
carries the same `coefficients_used`, `assumptions`, `warnings` and `not_modeled` as the human
report, so the caveats travel with the numbers, and `optimize` carries its `assumptions` and
`not_modeled`. `optimize --json` includes 5 ranked alternatives and `--top N` changes that.
Input the trust gate rejects exits 2 and is JSON too, `{"error": "VALIDATION_FAILED",
"errors": [...]}`, so check for `error` before reading a result.

```bash
agronaut size --fish tilapia --crop lettuce --area 12 --temp 27 --water 3000 --json > design.json
```

**Where it keeps things.** In a checkout, the knowledge base, the reference tables, the
fetched-page cache and the SQLite memory DB all sit beside the source, as before. Installed
non-editably, the cited corpus and the reference tables (price book, growth calibration, the
twin's validation record) are read from `<prefix>/share/agronaut` and state goes to your XDG
directories rather than into `site-packages` — override any of it with `AGRONAUT_CORPUS_DIR`,
`AGRONAUT_REFERENCE_DIR`, `AGRONAUT_CACHE_DIR`, or `AGRONAUT_DATA_DIR`.

| Command | Needs an LLM? |
|---|---|
| `size` / `size-hydro` / `optimize` / `list` | **No.** Pure `aqua_model` — deterministic, offline, cited. Bad input exits non-zero at the trust gate rather than guessing. |
| `doctor` / `--version` / `update` | **No.** `--version` is offline. `doctor` makes one call per configured provider or channel to check the key actually works, and `update` asks PyPI. |
| `chat` (the default) / `bot` | Yes — a tool-calling provider (see [configuration.md](configuration.md)). |
| `web` | Only for the app's chat mode; the calculator and optimizer run without one. |

### Run the Telegram bot

The consultative agent is reachable over Telegram. **This is the recommended way to talk to
Agronaut from a phone**: one token from BotFather, it works on a laptop behind any router,
and nothing expires. `agronaut setup` walks you through it in about two minutes.


```bash
source .venv/bin/activate
agronaut bot             # long-polls Telegram; Ctrl-C to stop (same as `python bot.py`)
```

### Run on WhatsApp (Cloud API)

Agronaut also speaks WhatsApp, the channel most smallholder-facing programs reach farmers
on. It is optional and takes more setup than Telegram: Meta's developer dashboard, two
different tokens, and a public HTTPS address, because Meta calls your machine rather than
the other way round. **[docs/whatsapp_setup.md](whatsapp_setup.md) walks through
every screen.** The short version:

```bash
agronaut setup                 # choose WhatsApp; paste the values from Meta's Step 1
agronaut whatsapp --tunnel     # starts the bot + a public address, prints what to paste
```

`--tunnel` needs [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
(`brew install cloudflared` on a Mac). It prints the **Callback URL** and **verify token** to
paste into Meta at *Step 2. Production setup > Configure Webhooks*, then you message the test
number from WhatsApp on your phone.

When it does not answer, `agronaut whatsapp --check --url <callback url>` says which link is
broken, and the bot window says why it refused or dropped anything. Meta's test access token
is short-lived (one lasted about an hour): once the bot answers, make a permanent System
User token ([steps](whatsapp_setup.md#a-permanent-token-do-this-once)) and save it with
`agronaut whatsapp --token`.

Two things worth knowing before you start:

- **This is not your personal WhatsApp.** The Cloud API is for WhatsApp *Business*. Start
  with the free test number Meta gives you and message it *from* your personal phone.
- **Two things make a laptop setup temporary.** The test token dies quickly (fix: a
  permanent System User token, once) and the quick-tunnel address changes on every start
  (fix: a server or a named tunnel with a fixed address). Both are covered in the guide.

The same brain, memory, tools, and follow-ups as Telegram.

#### Keep it running (systemd)

For an always-on bot that survives crashes and reboots, run it as a **`systemd --user`
service**. Create `~/.config/systemd/user/agronaut-bot.service`:

```ini
[Unit]
Description=Agronaut Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/Agronaut
ExecStart=/path/to/Agronaut/.venv/bin/python bot.py
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=default.target
```

```bash
loginctl enable-linger "$USER"          # run even when you're not logged in
systemctl --user daemon-reload
systemctl --user enable --now agronaut-bot     # start now + on boot
```

Manage it:

```bash
systemctl --user status agronaut-bot     # is it up?
systemctl --user restart agronaut-bot    # after pulling/changing code
journalctl --user -u agronaut-bot -f     # live logs
```

> Only **one** poller may run at a time — a manual `python bot.py` and the service will
> conflict on Telegram's `getUpdates`. When the service owns the bot, restart it after code
> changes (`systemctl --user restart agronaut-bot`) instead of running the script directly.
