"""Remote access: use NeonClipping from another device (a phone, another PC, a Colab quick tunnel).

Two independent modes, both OFF by default (the server only listens on 127.0.0.1):

1. NEONCLIPPING_REMOTE=1  - token mode (new). Every request, from any address, needs a random
   access token generated at startup. The first URL is printed on the console as
   http://<host>:<port>/?token=<token>; after the first visit the token lives in an HttpOnly
   cookie, so it never appears in later URLs, history or server logs. Comparison is
   constant-time, wrong tries are rate-limited per address, and requests without the token are
   rejected with a plain message. Set NEONCLIPPING_REMOTE_TOKEN to pin your own token.

2. NEONCLIPPING_LAN=1     - phone mode (unchanged). Same-Wi-Fi access to the light mobile page
   with a 6-digit PIN, restricted routes. See the class docstring at the bottom.

Both modes keep all processing on this machine; nothing is uploaded anywhere.
"""
import hmac
import hashlib
import json
import os
import secrets
import socket
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

LOCAL = ('127.0.0.1', '::1', 'localhost')
# Routes the phone (PIN) mode may use. Token mode allows every route after the token check.
PHONE_ALLOW = ('/m', '/manifest.webmanifest', '/phone-icon.svg', '/live', '/live-active', '/library', '/media', '/text', '/make', '/status',
               '/clips', '/feedback', '/creators', '/donate', '/healthz', '/assets')
MAX_TRIES, LOCK_SECONDS = 5, 600
COOKIE = 'nc_phone'
REMOTE_COOKIE = 'nc_remote'
# Always reachable without a token: the health probe tunnels use, and the login itself.
REMOTE_OPEN = ('/healthz', '/remote/login', '/manifest.webmanifest', '/phone-icon.svg', '/favicon.svg')

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NeonClipping - access code</title><style>body{margin:0;min-height:100dvh;display:grid;place-items:center;background:#0d0b14;color:#fbf6ff;
font:15px/1.5 system-ui,'Segoe UI',Roboto,sans-serif}.card{background:#17141f;border:1px solid #30263e;border-radius:16px;padding:32px;max-width:420px;margin:16px}
h1{font-size:20px;margin:0 0 8px}p{color:#a99db7;margin:0 0 18px}input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #30263e;
background:#100c17;color:inherit;font:inherit}button{margin-top:12px;width:100%;padding:12px;border:0;border-radius:10px;background:#ff4ba5;color:#14101f;
font-weight:700;font:inherit;cursor:pointer}#msg{margin-top:12px;color:#ff8a5c;min-height:20px}</style></head>
<body><div class="card"><h1>This NeonClipping is protected</h1><p>Enter the access token printed on the server console
(the full link looks like <code>http://.../?token=...</code>).</p>
<input id="token" type="password" autocomplete="off" placeholder="Access token" aria-label="Access token">
<button onclick="go()">Open NeonClipping</button><div id="msg" role="status"></div>
<script>async function go(){const r=await fetch('/remote/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({token:document.getElementById('token').value})});if(r.ok){location.href='/';}else{
document.getElementById('msg').textContent=(await r.json()).detail||'Wrong token.';}}</script>
</div></body></html>"""


def is_local(host):
    return host in LOCAL or str(host).startswith('127.')


def phone_allowed(path):
    return any(path == p or path.startswith(p + '/') for p in PHONE_ALLOW)


def lan_enabled():
    return os.environ.get('NEONCLIPPING_LAN') == '1'


def remote_enabled():
    return os.environ.get('NEONCLIPPING_REMOTE') == '1'


def lan_ips():
    """Private IPv4 addresses of this PC (what a phone on the same Wi-Fi would type)."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))                 # no packet is sent; it only picks the outgoing interface
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        parts = ip.split('.')
        private = ip.startswith(('10.', '192.168.')) or (ip.startswith('172.') and len(parts) > 1 and 16 <= int(parts[1]) <= 31)
        if private and ip not in out:
            out.append(ip)
    return out


def new_token(nbytes=24):
    """Random URL-safe access token (NEONCLIPPING_REMOTE_TOKEN pins a fixed one)."""
    fixed = os.environ.get('NEONCLIPPING_REMOTE_TOKEN', '').strip()
    return fixed if fixed else secrets.token_urlsafe(nbytes)


def token_digest(token, salt):
    """Salted SHA-256 of the token; only the digest+salt touch the disk."""
    return hmac.new(salt.encode(), token.encode(), hashlib.sha256).hexdigest()


def attach(app, data, root):
    store = Path(data) / 'phone.json'
    remote_store = Path(data) / 'remote.json'
    fails = {}          # ip -> [count, first_time]

    # ---------------- remote token mode (NEONCLIPPING_REMOTE=1) ----------------
    remote = {'token': '', 'salt': secrets.token_hex(16)}

    def load_remote():
        """Start a fresh token each launch (or keep the pinned one). Never raises."""
        remote['token'] = new_token()
        try:
            remote_store.parent.mkdir(parents=True, exist_ok=True)
            remote_store.write_text(json.dumps({'digest': token_digest(remote['token'], remote['salt']), 'salt': remote['salt']}), encoding='utf-8')
        except OSError:
            pass

    def remote_ok(request):
        """True when the request carries the valid token as a cookie or a ?token= parameter."""
        got = request.query_params.get('token', '') or request.cookies.get(REMOTE_COOKIE, '')
        want = remote['token']
        return bool(got) and bool(want) and hmac.compare_digest(str(got).encode('utf-8'), want.encode('utf-8'))

    def remote_login_rate_limited(ip):
        count, first = fails.get(ip, [0, time.time()])
        if count >= MAX_TRIES and time.time() - first < LOCK_SECONDS:
            return True
        if time.time() - first >= LOCK_SECONDS:
            fails.pop(ip, None)
        return False

    if remote_enabled():
        load_remote()

    def remote_welcome():
        """The line printed on the console at startup."""
        port = os.environ.get('NEONCLIPPING_PORT', '8770')
        token = remote['token']
        urls = ', '.join(f'http://{ip}:{port}/?token={token}' for ip in lan_ips()) or f'http://<this-computer>:{port}/?token={token}'
        return ('Remote access is ON. Open one of these links on any device:\n  ' + urls +
                '\nKeep the link private: anyone with it can use NeonClipping on this machine.')

    @app.middleware('http')
    async def gate(request: Request, call_next):
        host = request.client.host if request.client else ''
        path = request.url.path
        if remote_enabled():
            # Token mode: every route (including requests that arrive through a local tunnel
            # such as cloudflared and therefore look like 127.0.0.1) needs the token.
            if path in REMOTE_OPEN:
                return await call_next(request)
            if remote_ok(request):
                response = await call_next(request)
                # First visit with ?token=...: move the token into an HttpOnly cookie and
                # redirect so the token leaves the address bar and the browser history.
                if request.query_params.get('token') and REMOTE_COOKIE not in request.cookies:
                    response = RedirectResponse('/', status_code=303)
                    response.set_cookie(REMOTE_COOKIE, remote['token'], httponly=True, samesite='lax', max_age=7 * 24 * 3600)
                return response
            if path == '/':
                return HTMLResponse(LOGIN_PAGE, status_code=401)
            return JSONResponse({'detail': 'This NeonClipping is protected. Open the link with ?token=... from the server console.'},
                                status_code=401)
        if is_local(host):
            return await call_next(request)
        # Phone (LAN PIN) mode for same-Wi-Fi use; everything else stays off.
        if not lan_enabled():
            return JSONResponse({'detail': 'Phone access is off on this computer. Start with NEONCLIPPING_REMOTE=1 (token link) or NEONCLIPPING_LAN=1 (phone PIN).'}, status_code=403)
        if path in ('/m', '/m/login', '/manifest.webmanifest', '/phone-icon.svg'):
            return await call_next(request)                 # the page itself and the PIN form carry no data
        if not phone_allowed(path) or path.startswith('/phone'):
            return JSONResponse({'detail': 'This is only available on the PC.'}, status_code=403)
        if not phone_authed(request):
            return JSONResponse({'detail': 'Enter the PIN shown on the PC.'}, status_code=401)
        return await call_next(request)

    @app.get('/remote/status')
    def remote_status(request: Request):
        """Whether token mode is on (the token itself is only printed on the PC console)."""
        host = request.client.host if request.client else ''
        if not is_local(host) and not remote_ok(request):
            raise HTTPException(403, 'Not available.')
        port = os.environ.get('NEONCLIPPING_PORT') or str(request.url.port or 8770)
        on = remote_enabled()
        return {'enabled': on, 'port': port,
                'urls': [f'http://{ip}:{port}/?token=<see console>' for ip in lan_ips()] if on else [],
                'pinned': bool(os.environ.get('NEONCLIPPING_REMOTE_TOKEN', '').strip())}

    @app.post('/remote/login')
    def remote_login(b: dict, request: Request):
        """Exchange the token for an HttpOnly cookie (used when the cookie was lost)."""
        if not remote_enabled():
            raise HTTPException(403, 'Remote access is off on this computer.')
        ip = request.client.host if request.client else '?'
        if remote_login_rate_limited(ip):
            raise HTTPException(429, 'Too many wrong tokens. Try again in a few minutes.')
        if not hmac.compare_digest(str(b.get('token', '')).strip().encode('utf-8'), remote['token'].encode('utf-8')):
            count, first = fails.get(ip, [0, time.time()])
            fails[ip] = [count + 1, first]
            raise HTTPException(401, 'Wrong token. Copy the full link (with ?token=...) from the server console.')
        fails.pop(ip, None)
        resp = JSONResponse({'ok': True})
        resp.set_cookie(REMOTE_COOKIE, remote['token'], httponly=True, samesite='lax', max_age=7 * 24 * 3600)
        return resp

    @app.get('/favicon.svg')
    def favicon():
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='#14101f'/>"
               "<text x='10' y='47' font-family='Arial Black,Arial' font-size='38' font-weight='900' fill='#ff4ba5'>N</text>"
               "<path d='M34 40 L52 22 M40 22 H52 V34' stroke='#58e8f7' stroke-width='5' fill='none' stroke-linecap='round'/></svg>")
        return Response(svg, media_type='image/svg+xml')

    # ---------------- phone PIN mode (NEONCLIPPING_LAN=1, unchanged behaviour) ----------------
    def load():
        try:
            j = json.loads(store.read_text(encoding='utf-8'))
            if isinstance(j, dict) and j.get('pin') and j.get('salt'):
                return j
        except (OSError, ValueError):
            pass
        return new_pin()

    def new_pin():
        j = {'pin': f'{secrets.randbelow(10**6):06d}', 'salt': secrets.token_hex(16)}
        tmp = store.with_suffix('.tmp')
        tmp.write_text(json.dumps(j), encoding='utf-8')
        tmp.replace(store)
        return j

    def phone_token(j):
        return hmac.new(j['salt'].encode(), j['pin'].encode(), hashlib.sha256).hexdigest()

    def phone_authed(request):
        got = request.cookies.get(COOKIE, '')
        return bool(got) and hmac.compare_digest(got, phone_token(load()))

    @app.get('/m')
    def mobile_page():
        return HTMLResponse((Path(root) / 'mobile.html').read_text(encoding='utf-8'))

    @app.post('/m/login')
    def login(b: dict, request: Request):
        ip = request.client.host if request.client else '?'
        count, first = fails.get(ip, [0, time.time()])
        if count >= MAX_TRIES and time.time() - first < LOCK_SECONDS:
            raise HTTPException(429, 'Too many wrong PINs. Try again in a few minutes.')
        if time.time() - first >= LOCK_SECONDS:
            count, first = 0, time.time()
        j = load()
        if not hmac.compare_digest(str(b.get('pin', '')).strip(), j['pin']):
            fails[ip] = [count + 1, first]
            raise HTTPException(401, 'Wrong PIN.')
        fails.pop(ip, None)
        resp = JSONResponse({'ok': True})
        resp.set_cookie(COOKIE, phone_token(j), httponly=True, samesite='lax', max_age=30 * 24 * 3600)
        return resp

    @app.get('/phone/status')
    def status(request: Request):
        host = request.client.host if request.client else ''
        if not is_local(host):
            raise HTTPException(403, 'This is only available on the PC.')
        port = os.environ.get('NEONCLIPPING_PORT') or str(request.url.port or 8770)
        on = lan_enabled()
        return {'enabled': on, 'port': port, 'urls': [f'http://{ip}:{port}/m' for ip in lan_ips()] if on else [], 'pin': load()['pin'] if on else None}

    @app.post('/phone/newpin')
    def newpin(request: Request):
        host = request.client.host if request.client else ''
        if not is_local(host):
            raise HTTPException(403, 'This is only available on the PC.')
        return {'pin': new_pin()['pin']}

    @app.get('/manifest.webmanifest')
    def manifest():
        return JSONResponse({'name': 'NeonClipping', 'short_name': 'NeonClip', 'start_url': '/m', 'scope': '/', 'display': 'standalone',
                             'background_color': '#0b0912', 'theme_color': '#0b0912',
                             'icons': [{'src': '/phone-icon.svg', 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'}]},
                            media_type='application/manifest+json')

    @app.get('/phone-icon.svg')
    def icon():
        svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='#0b0912'/>"
               "<text x='8' y='46' font-family='Impact,Arial Black' font-size='38' fill='#ff4ba5'>N</text>"
               "<path d='M34 40 L52 22 M40 22 H52 V34' stroke='#58e8f7' stroke-width='5' fill='none' stroke-linecap='round'/></svg>")
        return Response(svg, media_type='image/svg+xml')

    return remote_welcome if remote_enabled() else None
