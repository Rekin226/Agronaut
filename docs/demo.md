# Hosting the live demo & recording the README GIF

This is the playbook for the public demo linked at the top of the README. The deterministic
modes (Design Calculator, Optimize Ratio) need no API keys and run within Streamlit
Community Cloud's free tier, because the demo installs the light **`requirements.txt`**
(streamlit + pandas + a couple of small libs) — not the full chat/ML stack.

## 1. Deploy to Streamlit Community Cloud

1. Sign in at <https://share.streamlit.io> with the GitHub account that owns the repo and
   authorize Streamlit (one-time).
2. **Create app → Deploy a public app from GitHub.**
   - Repository: `Rekin226/Agronaut`
   - Branch: `main`
   - Main file path: `app.py`
3. **Advanced settings → Custom subdomain:** enter `agronaut` so the URL becomes
   **`https://agronaut.streamlit.app`** (the link already wired into the README). If that
   subdomain is taken, pick another and update the two links at the top of `README.md`.
4. **Deploy.** First build takes ~1–2 min. The Calculator and Optimizer are fully
   interactive; "Assistant (chat)" shows a friendly note that the optional chat stack
   isn't installed — expected and intentional for the demo.

No secrets are required. (If you ever want chat in the hosted app, add an `NVIDIA_API_KEY`
under the app's **Secrets** and switch the deploy to install `requirement.txt` — but that's
heavier and not needed for the demo.)

## 2. Record the README GIF (15–20s)

Keep it short and legible — it autoplays in the README, so it should tell the story in one
loop:

1. Open the live app (or `streamlit run app.py` locally).
2. **Design Calculator:** enter a small system (e.g. tilapia + lettuce, a modest water
   budget) → scroll the result so the **bill of materials**, **operating envelope**, and the
   **"what's NOT modeled"** honesty list are all visible.
3. Switch to **Optimize Ratio** → run a search → let the best fish/crop ratio and the gain
   over the even-split baseline land on screen.
4. Stop the recording.

**Tools:** [Kap](https://getkap.co) (macOS) or [Peek](https://github.com/phw/peek) (Linux)
export GIF directly. Aim for ≤ 1280px wide and < 5 MB so it loads fast on GitHub.

## 3. Wire it in

1. Save the file as `docs/agronaut-demo.gif`.
2. In `README.md`, replace the `<!-- TODO: replace with a … GIF … -->` line with:
   ```markdown
   ![Agronaut demo](docs/agronaut-demo.gif)
   ```
3. Commit. The GIF now plays right under the demo link — the first thing a visitor sees.
