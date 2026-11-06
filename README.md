# Smart Print Hub

Print-shop counter dashboard: customers upload files via a link/QR, the counter prints them.

- `index.html` - browser-only demo. Hosted free on **GitHub Pages**.
- `app.py` - real Flask server (uploads, auto-print, QR, poster).

## Demo on GitHub Pages
Repo -> Settings -> Pages -> Source: `main` branch, `/ (root)` -> Save.
Your demo opens at `https://<username>.github.io/<repo>/`.

## Run the real app (at the shop computer, where the printer is)
    pip install -r requirements.txt
    python app.py
Open http://localhost:5000 and log in. Default User ID `subhajit`; set `HUB_USER` / `HUB_PASSWORD` environment variables to change credentials.

For customers outside your Wi-Fi:
    cloudflared tunnel --url http://localhost:5000
    export PUBLIC_URL=https://xxxx.trycloudflare.com   # Windows: set PUBLIC_URL=...

## Note
GitHub cannot run Python servers. A cloud host (Render, Railway) can run `app.py`
from this repo (the `Procfile` is included), but a cloud server cannot reach the
printer in your shop, so printing must happen on the shop computer.
