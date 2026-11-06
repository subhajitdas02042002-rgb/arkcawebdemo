# Smart Print Hub (runs fully on GitHub Pages)

No server needed. The counter dashboard and the customer page are static files.
Files travel straight from the customer's phone to the counter browser (peer-to-peer, WebRTC).

- `index.html`    Counter dashboard (login: User ID `subhajit`)
- `customer.html` Customer upload page (opened from the QR / link)

## Put it online (5 minutes)
1. github.com -> New repository (e.g. `smart-print-hub`) -> Public.
2. Upload every file from this folder (keep `index.html` and `customer.html` in the root).
3. Settings -> Pages -> Source: `main` branch, `/ (root)` -> Save.
4. Open `https://<your-username>.github.io/smart-print-hub/` and log in.

## Using it
- Keep the dashboard open on the counter computer/phone. That is what makes the customer link active.
- Tap **View / Download QR** or **QR Poster (A4)** and put it on your counter.
- Customer scans -> picks a file -> it appears in the queue and prints (Auto-Print on) or waits for you (Permission Mode on).

## Notes
- Accepted files: pdf, png, jpg, jpeg, webp, gif, txt (Word files cannot be printed from a browser).
- Auto-Print opens the browser print dialog. For fully silent printing on a computer, start Chrome
  with `--kiosk-printing` and set your shop printer as default.
- The login is a light lock (the password is stored only as a hash in `index.html`). Do not reuse
  this password anywhere important. Change it by editing `SALT`/`HASH` in `index.html`.
- Uses the free public PeerJS broker for the handshake. If it is down, the page shows "Offline".
- `optional-python-server/` is the older Python server version, not needed for GitHub Pages.
