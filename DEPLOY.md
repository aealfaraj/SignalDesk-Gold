# SignalDesk Deployment

SignalDesk is now ready to host as an HTTPS web app/PWA.

## Files

- `server.py` runs the backend and serves the app.
- `trade-signals-app.html` is the app UI.
- `manifest.webmanifest`, `sw.js`, and `icons/` make it Android/PWA-ready.
- `data/signals.json` is the local development database.

## Recommended Hosting

Use a platform that gives HTTPS and persistent storage, such as Render, Railway, Fly.io, or a VPS.

## Environment Variables

- `SIGNALDESK_ADMIN_CODE`: your private admin passcode.
- `SIGNALDESK_DATA_DIR`: folder where `signals.json` is stored.
- `SIGNALDESK_SECURE_COOKIES`: set to `1` when hosted on HTTPS.
- `HOST`: use `0.0.0.0` on hosting.
- `PORT`: hosting platforms usually set this automatically.

## Render Setup

1. Upload this `outputs` folder to a GitHub repository.
2. Create a new Render Web Service from that repository.
3. Set root directory to `outputs` if the repository has other folders.
4. Build command: `pip install -r requirements.txt`
5. Start command: `python server.py`
6. Add a persistent disk mounted at `/var/data`.
7. Add environment variables:
   - `SIGNALDESK_ADMIN_CODE`
   - `SIGNALDESK_DATA_DIR=/var/data`
   - `SIGNALDESK_SECURE_COOKIES=1`
   - `HOST=0.0.0.0`

After deployment, open the HTTPS URL on Android Chrome and choose **Add to Home screen**.

## Local Run

```powershell
cd outputs
$env:SIGNALDESK_ADMIN_CODE="1234"
python server.py
```

Then open:

```text
http://127.0.0.1:4174/trade-signals-app.html
```
