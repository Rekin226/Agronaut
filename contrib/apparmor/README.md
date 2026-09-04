# Browser automation on Ubuntu 23.10+

If you run headless-browser QA against the Streamlit app and Chromium dies immediately with:

```
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc: No usable sandbox!
```

your kernel is fine and your install is fine. Ubuntu 23.10 and later ship
`kernel.apparmor_restrict_unprivileged_userns=1`, which stops unconfined binaries from
creating unprivileged user namespaces. Chromium needs one to build **its own** sandbox, so
it refuses to start rather than run unprotected.

Check whether this is what you are hitting:

```bash
sysctl kernel.apparmor_restrict_unprivileged_userns    # 1 means restricted
```

## The fix

`playwright-chromium` in this directory grants that one capability to Playwright's two
Chromium binaries and nothing else:

```bash
sudo cp contrib/apparmor/playwright-chromium /etc/apparmor.d/playwright-chromium
sudo apparmor_parser -r /etc/apparmor.d/playwright-chromium
```

To remove it:

```bash
sudo apparmor_parser -R /etc/apparmor.d/playwright-chromium
sudo rm /etc/apparmor.d/playwright-chromium
```

## Why not `--no-sandbox`

That is the advice you will find first, and it does stop the crash — by removing the
protection whose absence caused it. Chromium then renders untrusted web pages with no
sandbox at all.

This profile does the opposite: it grants the single capability Chromium needs to
**construct** its sandbox, so the sandbox stays on. The system-wide restriction stays on
for every other binary on the machine. It is modelled on Ubuntu's own
`/etc/apparmor.d/chrome`, which does exactly this for `/opt/google/chrome/chrome`.

It is still a system-level security change, and it is yours to make deliberately — that is
why this is a file you install rather than something a setup script does behind your back.

## The version globs

The profile matches `chromium-*` and `chromium_headless_shell-*` rather than today's build
number. Playwright bumps those directory names on upgrade, and a pinned path would quietly
stop matching — putting the crash back weeks later with no clue as to why.

## Nothing in this repo needs it

Browser automation is a QA convenience, not a dependency. The test suite, `scripts.safety_eval`,
the whole of `aqua_model` and the Streamlit app all run without it.
