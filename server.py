from http import cookies
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("SIGNALDESK_DATA_DIR", ROOT / "data"))
DATA_FILE = DATA_DIR / "signals.json"
SUBSCRIPTIONS_FILE = DATA_DIR / "push_subscriptions.json"
ADMIN_CODE = os.environ.get("SIGNALDESK_ADMIN_CODE", "1234")
COOKIE_SECURE = os.environ.get("SIGNALDESK_SECURE_COOKIES", "0") == "1"
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "").replace("\\n", "\n")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")
LAST_PUSH_RESULT = {"attempted": 0, "sent": 0, "removed": 0, "error": ""}
SESSIONS = {}

INITIAL_SIGNALS = [
    {
        "id": "demo-active",
        "symbol": "XAUUSD",
        "direction": "BUY",
        "entry": "2338.50",
        "stopLoss": "2328.00",
        "takeProfit": "2355.00",
        "closePrice": "",
        "status": "active",
        "notes": "Liquidity sweep confirmed. Keep risk tight and move stop after partials.",
        "createdAt": "2026-06-15T16:30:00.000Z",
    },
    {
        "id": "demo-win",
        "symbol": "XAUUSD",
        "direction": "SELL",
        "entry": "2364.20",
        "stopLoss": "2372.10",
        "takeProfit": "2348.50",
        "closePrice": "2348.50",
        "status": "win",
        "notes": "London rejection from supply. TP reached cleanly.",
        "createdAt": "2026-06-15T12:30:00.000Z",
    },
    {
        "id": "demo-closed",
        "symbol": "XAUUSD",
        "direction": "BUY",
        "entry": "2312.40",
        "stopLoss": "2304.30",
        "takeProfit": "2329.00",
        "closePrice": "2320.80",
        "status": "closed",
        "notes": "Closed manually before news. No new entry until volatility cools.",
        "createdAt": "2026-06-14T16:30:00.000Z",
    },
]

SERVICE_WORKER_FALLBACK = """const CACHE_NAME = "signaldesk-v4";
const APP_SHELL = [
  "/",
  "/trade-signals-app.html",
  "/manifest.webmanifest",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", event => {
  const requestUrl = new URL(event.request.url);

  if (requestUrl.pathname.startsWith("/api/")) {
    event.respondWith(fetch(event.request));
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request).catch(() => caches.match("/trade-signals-app.html"))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(cached => cached || fetch(event.request))
  );
});

self.addEventListener("push", event => {
  let data = {
    title: "SignalDesk",
    body: "New XAUUSD signal",
    url: "/trade-signals-app.html",
    tag: "signaldesk"
  };

  if (event.data) {
    try {
      data = { ...data, ...event.data.json() };
    } catch {
      data.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      tag: data.tag,
      data: { url: data.url }
    })
  );
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const url = event.notification.data?.url || "/trade-signals-app.html";

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then(clientList => {
      for (const client of clientList) {
        if ("focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
"""


def ensure_data_file():
    DATA_DIR.mkdir(exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text(json.dumps(INITIAL_SIGNALS, indent=2), encoding="utf-8")
    if not SUBSCRIPTIONS_FILE.exists():
        SUBSCRIPTIONS_FILE.write_text("[]", encoding="utf-8")


def read_signals():
    ensure_data_file()
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def write_signals(signals):
    ensure_data_file()
    DATA_FILE.write_text(json.dumps(signals, indent=2), encoding="utf-8")


def read_subscriptions():
    ensure_data_file()
    return json.loads(SUBSCRIPTIONS_FILE.read_text(encoding="utf-8"))


def write_subscriptions(subscriptions):
    ensure_data_file()
    SUBSCRIPTIONS_FILE.write_text(json.dumps(subscriptions, indent=2), encoding="utf-8")


def subscription_key(subscription):
    return subscription.get("endpoint", "")


def notification_payload(signal):
    entry = signal.get("entry") or "entry pending"
    return {
        "title": f"XAUUSD {signal.get('direction', 'Signal')}",
        "body": f"Entry: {entry}. Tap to open SignalDesk.",
        "url": "/trade-signals-app.html",
        "tag": f"signal-{signal.get('id')}",
    }


def send_push_notifications(signal):
    global LAST_PUSH_RESULT
    LAST_PUSH_RESULT = {"attempted": 0, "sent": 0, "removed": 0, "error": ""}

    if not VAPID_PUBLIC_KEY or not VAPID_PRIVATE_KEY:
        LAST_PUSH_RESULT["error"] = "VAPID keys are missing"
        return

    try:
        from pywebpush import WebPushException, webpush
    except Exception as error:
        LAST_PUSH_RESULT["error"] = f"pywebpush unavailable: {error}"
        return

    subscriptions = read_subscriptions()
    next_subscriptions = []
    payload = json.dumps(notification_payload(signal))
    LAST_PUSH_RESULT["attempted"] = len(subscriptions)

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info=subscription,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_CLAIM_EMAIL},
            )
            LAST_PUSH_RESULT["sent"] += 1
            next_subscriptions.append(subscription)
        except WebPushException as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            if status not in {404, 410}:
                next_subscriptions.append(subscription)
                LAST_PUSH_RESULT["error"] = str(error)
            else:
                LAST_PUSH_RESULT["removed"] += 1

    write_subscriptions(next_subscriptions)


def clean_signal(signal):
    return {
        "id": str(signal.get("id") or secrets.token_hex(8)),
        "symbol": "XAUUSD",
        "direction": "SELL" if signal.get("direction") == "SELL" else "BUY",
        "entry": str(signal.get("entry") or ""),
        "stopLoss": str(signal.get("stopLoss") or ""),
        "takeProfit": str(signal.get("takeProfit") or ""),
        "closePrice": str(signal.get("closePrice") or ""),
        "status": signal.get("status") if signal.get("status") in {"active", "win", "loss", "closed"} else "active",
        "notes": str(signal.get("notes") or ""),
        "createdAt": str(signal.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
    }


class SignalDeskHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def send_json(self, data, status=200, extra_headers=None):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text, content_type="text/plain; charset=utf-8", status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def session_token(self):
        header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(header)
        morsel = jar.get("signaldesk_session")
        return morsel.value if morsel else ""

    def is_admin(self):
        token = self.session_token()
        expires = SESSIONS.get(token, 0)
        if expires < time.time():
            SESSIONS.pop(token, None)
            return False
        return True

    def require_admin(self):
        if self.is_admin():
            return True
        self.send_json({"error": "Admin login required"}, status=401)
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/api/files":
            self.send_json({
                "root": str(ROOT),
                "files": sorted(item.name for item in ROOT.iterdir()),
                "swExists": (ROOT / "sw.js").exists(),
                "manifestExists": (ROOT / "manifest.webmanifest").exists(),
            })
            return
        if path == "/api/signals":
            self.send_json({"signals": read_signals(), "isAdmin": self.is_admin()})
            return
        if path == "/api/session":
            self.send_json({"isAdmin": self.is_admin()})
            return
        if path == "/api/push/public-key":
            self.send_json({"publicKey": VAPID_PUBLIC_KEY, "enabled": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY)})
            return
        if path == "/api/push/status":
            self.send_json({
                "enabled": bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY),
                "subscriptions": len(read_subscriptions()),
                "lastPush": LAST_PUSH_RESULT,
            })
            return
        if path == "/":
            self.path = "/trade-signals-app.html"
        if path == "/sw.js" and not (ROOT / "sw.js").exists():
            self.send_text(SERVICE_WORKER_FALLBACK, "application/javascript; charset=utf-8")
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/login":
            data = self.read_json_body()
            if str(data.get("passcode", "")) != ADMIN_CODE:
                self.send_json({"error": "Incorrect passcode"}, status=403)
                return
            token = secrets.token_urlsafe(32)
            SESSIONS[token] = time.time() + 60 * 60 * 12
            self.send_json(
                {"ok": True, "isAdmin": True},
                extra_headers={
                    "Set-Cookie": self.cookie_header(token, 60 * 60 * 12)
                },
            )
            return

        if path == "/api/logout":
            SESSIONS.pop(self.session_token(), None)
            self.send_json(
                {"ok": True},
                extra_headers={"Set-Cookie": self.cookie_header("", 0)},
            )
            return

        if path == "/api/signals":
            if not self.require_admin():
                return
            data = self.read_json_body()
            signal = clean_signal(data)
            signals = read_signals()
            signals.insert(0, signal)
            write_signals(signals)
            send_push_notifications(signal)
            self.send_json({"signal": signal})
            return

        if path == "/api/push/subscribe":
            subscription = self.read_json_body()
            if not subscription.get("endpoint"):
                self.send_json({"error": "Invalid push subscription"}, status=400)
                return
            subscriptions = read_subscriptions()
            existing = {subscription_key(item): item for item in subscriptions}
            existing[subscription_key(subscription)] = subscription
            write_subscriptions(list(existing.values()))
            self.send_json({"ok": True, "count": len(existing)})
            return

        if path == "/api/push/test":
            if not self.require_admin():
                return
            send_push_notifications({
                "id": f"test-{secrets.token_hex(4)}",
                "symbol": "XAUUSD",
                "direction": "TEST",
                "entry": "notification test",
            })
            self.send_json({"ok": True, "result": LAST_PUSH_RESULT})
            return

        if path == "/api/reset-demo":
            if not self.require_admin():
                return
            signals = [dict(signal, id=f"{signal['id']}-{secrets.token_hex(3)}") for signal in INITIAL_SIGNALS]
            write_signals(signals)
            self.send_json({"signals": signals})
            return

        self.send_error(404)

    def do_PATCH(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/signals/"):
            self.send_error(404)
            return
        if not self.require_admin():
            return
        signal_id = path.rsplit("/", 1)[-1]
        data = self.read_json_body()
        signals = read_signals()
        for index, signal in enumerate(signals):
            if signal.get("id") == signal_id:
                updated = clean_signal({**signal, **data, "id": signal_id})
                signals[index] = updated
                write_signals(signals)
                self.send_json({"signal": updated})
                return
        self.send_json({"error": "Signal not found"}, status=404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/signals/"):
            self.send_error(404)
            return
        if not self.require_admin():
            return
        signal_id = path.rsplit("/", 1)[-1]
        signals = read_signals()
        next_signals = [signal for signal in signals if signal.get("id") != signal_id]
        if len(next_signals) == len(signals):
            self.send_json({"error": "Signal not found"}, status=404)
            return
        write_signals(next_signals)
        self.send_json({"ok": True})

    def cookie_header(self, value, max_age):
        secure = "; Secure" if COOKIE_SECURE else ""
        return f"signaldesk_session={value}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}"


if __name__ == "__main__":
    ensure_data_file()
    port = int(os.environ.get("PORT", "4174"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), SignalDeskHandler)
    print(f"SignalDesk running at http://{host}:{port}")
    server.serve_forever()
