#!/usr/bin/env python3
"""
KrypBot — animated Discord status + dynamic rotation + sleep schedule
+ web dashboard + VPS auto-renew.

.env:
  DISCORD_TOKEN=...
  DASH_TOKEN=...
  DASH_PORT=8000
"""
import os
import re
import time
import asyncio
import discord
import uvicorn
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from discord.ext import tasks
from fastapi import FastAPI, Request, HTTPException, Cookie
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

load_dotenv()

TOKEN      = os.getenv("DISCORD_TOKEN")
DASH_TOKEN = os.getenv("DASH_TOKEN", "changeme")
DASH_PORT  = int(os.getenv("DASH_PORT", 8000))
if not TOKEN:
    raise SystemExit("[x] DISCORD_TOKEN missing from .env")

# ── VPS auto-renew ──────────────────────────────────────
VPS_BOT_ID           = 1558466289092001802
RENEW_THRESHOLD_DAYS = 1.0
VPS_CHECK_INTERVAL_H = 6
VPS_QUERY_CMD        = "1manage"
VPS_RENEW_CMD        = "1vps renew"

# ── visuals ─────────────────────────────────────────────
BAR_FULL, BAR_EMPTY, BAR_LEN, STEP = "▓", "░", 12, 10
SPIN = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
SLEEP_PULSE = ["● away · sleeping", "○ away · sleeping", "· away · sleeping"]


def parse_days_left(text: str):
    """'Active (5d left) • in 6 days' → 5.0"""
    for unit, div in (("d", 1), ("h", 24), ("m", 1440)):
        m = re.search(rf"\(\s*(\d+(?:\.\d+)?)\s*{unit}\s+left", text, re.IGNORECASE)
        if m:
            return float(m.group(1)) / div
    return None


# ── the bot ─────────────────────────────────────────────
class AnimBot(discord.Client):
    def __init__(self, **kw):
        super().__init__(**kw)

        # ── status rotation ─────────────────────────────
        self.statuses = [
            ("building · local",      "progress",   1),
            ("running pipeline v2.4", "typewriter", 2),
            ("syncing · cache",       "dots",       1),
            ("● api · healthy",       "spinner",    2),
            ("available · 24/7",      "static",     1),
        ]
        self.static_bio    = "local dev · pipelines · 24/7"
        self.sleep_bio     = "offline · resumes 07:00"
        self.sleep_hour    = 22
        self.wake_hour     = 7
        self.timezone      = "Asia/Ho_Chi_Minh"
        self.frame_delay   = 3.5
        self.pause_between = 3.0
        self.sleep_delay   = 3.5
        self.min_gap       = 3.4

        # ── runtime state ───────────────────────────────
        self.sleeping       = False
        self.manual         = None       # None=auto, True=sleep, False=wake
        self._awake_applied = False
        self._lock          = asyncio.Lock()
        self._last_write    = 0.0
        self.current_index  = 0
        self.recent_log     = []

        # ── vps state ───────────────────────────────────
        self.vps_days_left   = None
        self.vps_last_check  = None
        self.vps_last_action = None

    # ── logging ─────────────────────────────────────────
    def log(self, msg):
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line)
        self.recent_log.append(line)
        self.recent_log = self.recent_log[-200:]

    # ── boot ────────────────────────────────────────────
    async def on_ready(self):
        self.log(f"[*] logged in as {self.user}")
        await self.try_set_bio(self.static_bio)
        self.schedule_check.start()
        self.vps_check_loop.start()
        asyncio.create_task(self.anim_loop())

    # ── bio ─────────────────────────────────────────────
    async def try_set_bio(self, text):
        try:
            await self.user.edit(bio=text[:190])
            self.log(f"[+] bio: {text[:60]!r}")
        except discord.HTTPException as e:
            self.log(f"[!] bio skipped ({e.status})")

    # ── throttled status write ──────────────────────────
    async def status(self, text, status=discord.Status.online):
        async with self._lock:
            gap = time.monotonic() - self._last_write
            if gap < self.min_gap:
                await asyncio.sleep(self.min_gap - gap)
            for _ in range(4):
                try:
                    await self.change_presence(
                        status=status,
                        activity=discord.CustomActivity(name=text[:128]),
                    )
                    self._last_write = time.monotonic()
                    return
                except discord.HTTPException as e:
                    if e.status == 429:
                        wait = getattr(e, "retry_after", None) or 10
                        self.log(f"  [429] server says wait {wait:.1f}s")
                        await asyncio.sleep(wait + 0.5)
                        continue
                    self.log(f"  [!] status {e.status}")
                    return

    # ── sleep / wake ────────────────────────────────────
    async def apply_sleep(self):
        if self.sleeping:
            return
        self.sleeping = True
        self._awake_applied = False
        self.log("[*] → SLEEP")
        await self.try_set_bio(self.sleep_bio)

    async def apply_awake(self):
        if not self.sleeping and self._awake_applied:
            return
        self.sleeping = False
        self._awake_applied = True
        self.log("[*] → AWAKE")
        await self.try_set_bio(self.static_bio)

    @tasks.loop(seconds=60)
    async def schedule_check(self):
        if self.manual is True:
            await self.apply_sleep()
            return
        if self.manual is False:
            await self.apply_awake()
            return
        try:
            h = datetime.now(ZoneInfo(self.timezone)).hour
        except Exception:
            h = datetime.utcnow().hour
        in_sleep = h >= self.sleep_hour or h < self.wake_hour
        await (self.apply_sleep() if in_sleep else self.apply_awake())

    @schedule_check.before_loop
    async def _wait_s(self):
        await self.wait_until_ready()

    # ── VPS auto-renew ──────────────────────────────────
    async def check_vps(self):
        try:
            user = await self.fetch_user(VPS_BOT_ID)
            dm = await user.create_dm()

            self.log("[vps] sending '1manage'")
            await dm.send(VPS_QUERY_CMD)
            await asyncio.sleep(8)

            days = None
            async for msg in dm.history(limit=10):
                if msg.author.id != VPS_BOT_ID:
                    continue
                d = parse_days_left(msg.content)
                if d is not None:
                    days = d
                    self.log(f"[vps] raw: {msg.content[:80]!r}")
                    break

            if days is None:
                self.log("[vps] couldn't parse expiry — no reply or unknown format")
                self.vps_last_check = datetime.now(timezone.utc)
                return

            self.vps_days_left = days
            self.vps_last_check = datetime.now(timezone.utc)
            self.log(f"[vps] {days:.2f} days left")

            if days < RENEW_THRESHOLD_DAYS:
                self.log("[vps] below threshold → sending '1vps renew'")
                sent_at = datetime.now(timezone.utc)
                await dm.send(VPS_RENEW_CMD)
                self.vps_last_action = "renewed"
                await asyncio.sleep(10)

                async for msg in dm.history(limit=20, after=sent_at):
                    if msg.author.id != VPS_BOT_ID:
                        continue
                    d = parse_days_left(msg.content)
                    if d is not None:
                        self.vps_days_left = d
                        self.log(f"[vps] renewed → now {d:.2f} days left")
                        break
        except Exception as e:
            self.log(f"[vps] error: {e}")

    @tasks.loop(hours=VPS_CHECK_INTERVAL_H)
    async def vps_check_loop(self):
        await self.check_vps()

    @vps_check_loop.before_loop
    async def _wait_v(self):
        await self.wait_until_ready()

    # ── animation modes ─────────────────────────────────
    async def anim_progress(self, text):
        for pct in range(0, 101, STEP):
            filled = round(BAR_LEN * pct / 100)
            bar = BAR_FULL * filled + BAR_EMPTY * (BAR_LEN - filled)
            await self.status(f"[{bar}] {pct:>3}%  ·  {text}")
            await asyncio.sleep(self.frame_delay)

    async def anim_typewriter(self, text):
        for i in range(0, len(text) + 1):
            await self.status(text[:i] + " ▌")
            await asyncio.sleep(self.frame_delay)
        for _ in range(2):
            await self.status(text + " ▌")
            await asyncio.sleep(self.frame_delay)
            await self.status(text + "  ")
            await asyncio.sleep(self.frame_delay)

    async def anim_dots(self, text):
        for n in range(0, 4):
            await self.status(text + "." * n)
            await asyncio.sleep(self.frame_delay)

    async def anim_spinner(self, text):
        for f in SPIN:
            await self.status(f"{f}  {text}")
            await asyncio.sleep(self.frame_delay)

    async def anim_static(self, text):
        await self.status(text)
        await asyncio.sleep(self.frame_delay * 2)

    async def run_mode(self, mode, text):
        if mode == "progress":
            await self.anim_progress(text)
        elif mode == "typewriter":
            await self.anim_typewriter(text)
        elif mode == "dots":
            await self.anim_dots(text)
        elif mode == "spinner":
            await self.anim_spinner(text)
        else:
            await self.anim_static(text)

    # ── sleep animation ─────────────────────────────────
    async def sleep_anim(self):
        i = 0
        while self.sleeping:
            await self.status(SLEEP_PULSE[i % len(SLEEP_PULSE)],
                              status=discord.Status.dnd)
            i += 1
            await asyncio.sleep(self.sleep_delay)

    # ── switcher loop ───────────────────────────────────
    async def anim_loop(self):
        await asyncio.sleep(3)
        while True:
            try:
                if self.sleeping:
                    await self.sleep_anim()
                    continue
                if not self.statuses:
                    await asyncio.sleep(5)
                    continue

                self.current_index %= len(self.statuses)
                text, mode, cycles = self.statuses[self.current_index]

                for _ in range(max(1, int(cycles))):
                    if self.sleeping:
                        break
                    self.log(f"  [{self.current_index}] {mode}: {text!r}")
                    await self.run_mode(mode, text)
                    if self.sleeping:
                        break

                if not self.sleeping:
                    await self.status("")
                    await asyncio.sleep(self.pause_between)

                self.current_index += 1
            except Exception as e:
                self.log(f"[!] loop: {e}")
                await asyncio.sleep(15)


# ── dashboard ───────────────────────────────────────────
app = FastAPI()
bot_ref: AnimBot = None

LOGIN_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>login</title>
<style>
body{background:#0f172a;color:#e2e8f0;font-family:system-ui;display:flex;
align-items:center;justify-content:center;height:100vh;margin:0}
.card{background:#1e293b;padding:32px;border-radius:12px;width:320px}
input,button{width:100%;padding:10px;border-radius:8px;border:1px solid #334155;
background:#0f172a;color:#e2e8f0;font-size:14px;box-sizing:border-box;margin-top:8px}
button{background:#38bdf8;color:#0f172a;font-weight:700;cursor:pointer;border:none}
</style></head><body>
<div class="card"><h3>KrypBot</h3>
<input id="t" type="password" placeholder="token">
<button onclick="go()">log in</button></div>
<script>
async function go(){
  const r = await fetch('/login', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({token: document.getElementById('t').value})});
  if(r.ok) location.reload();
  else alert('wrong token');
}
</script></body></html>"""

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>KrypBot</title>
<style>
*{box-sizing:border-box}
body{background:#0f172a;color:#e2e8f0;font-family:system-ui;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:#64748b;font-size:13px;margin-bottom:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1100px}
.card{background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155}
.card h3{margin:0 0 12px;font-size:12px;text-transform:uppercase;color:#94a3b8;letter-spacing:.8px}
.row{display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid #253046}
.row:last-child{border-bottom:none}
.row span:last-child{color:#38bdf8;font-weight:600}
button{padding:8px 14px;border-radius:8px;border:none;background:#38bdf8;color:#0f172a;
font-weight:700;cursor:pointer;margin:4px 4px 0 0;font-size:13px}
button.alt{background:#334155;color:#e2e8f0}
button.warn{background:#f59e0b}
textarea{width:100%;height:180px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;
border-radius:8px;padding:10px;font-family:ui-monospace,monospace;font-size:12px;resize:vertical}
input{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:8px;
padding:6px 8px;font-size:13px;width:80px}
.log{background:#0f172a;border:1px solid #334155;border-radius:8px;padding:10px;
height:260px;overflow-y:auto;font-family:ui-monospace,monospace;font-size:11px;
line-height:1.5;white-space:pre-wrap}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700}
.awake{background:#065f46;color:#6ee7b7}
.asleep{background:#7c2d12;color:#fed7aa}
.ok{color:#6ee7b7}
.warn-text{color:#fbbf24}
</style></head><body>
<h1>KrypBot</h1>
<div class="sub" id="who"></div>
<div class="grid">

<div class="card">
  <h3>State</h3>
  <div class="row"><span>status</span><span id="st"></span></div>
  <div class="row"><span>current index</span><span id="idx"></span></div>
  <div class="row"><span>schedule</span><span id="sch"></span></div>
  <div class="row"><span>timezone</span><span id="tz"></span></div>
  <div style="margin-top:12px">
    <button onclick="act('/api/sleep')" class="warn">force sleep</button>
    <button onclick="act('/api/wake')">force wake</button>
    <button onclick="act('/api/auto')" class="alt">auto</button>
  </div>
</div>

<div class="card">
  <h3>VPS</h3>
  <div class="row"><span>days left</span><span id="vdays"></span></div>
  <div class="row"><span>last check</span><span id="vcheck"></span></div>
  <div class="row"><span>last action</span><span id="vaction"></span></div>
  <div style="margin-top:12px"><button onclick="act('/api/vps-check')">check now</button></div>
</div>

<div class="card">
  <h3>Config</h3>
  <div class="row"><span>sleep hour</span><input id="sh"></div>
  <div class="row"><span>wake hour</span><input id="wh"></div>
  <div class="row"><span>frame delay (s)</span><input id="fd" step="0.1"></div>
  <div class="row"><span>min gap (s)</span><input id="mg" step="0.1"></div>
  <div class="row"><span>timezone</span><input id="tzin" style="width:180px"></div>
  <div style="margin-top:12px"><button onclick="saveCfg()">save</button></div>
</div>

<div class="card" style="grid-column:1/-1">
  <h3>Status rotation</h3>
  <textarea id="stlist"></textarea>
  <div style="margin-top:8px"><button onclick="saveSt()">save rotation</button>
  <span style="color:#64748b;font-size:12px;margin-left:10px">
  [["text","mode",cycles], ...]  modes: progress, typewriter, dots, spinner, static
  </span></div>
</div>

<div class="card" style="grid-column:1/-1">
  <h3>Log</h3>
  <div class="log" id="log"></div>
</div>

</div>
<script>
async function j(u,o){const r=await fetch(u,o);if(!r.ok)throw 0;return r.json()}
async function refresh(){
  try{
    const s = await j('/api/state');
    document.getElementById('who').textContent = 'logged in as ' + (s.user || '…');
    document.getElementById('st').innerHTML = s.sleeping
      ? '<span class="badge asleep">SLEEP</span>'
      : '<span class="badge awake">AWAKE</span>';
    document.getElementById('idx').textContent = s.current_index;
    document.getElementById('sch').textContent = s.sleep_hour + ' → ' + s.wake_hour;
    document.getElementById('tz').textContent = s.timezone;
    const vd = s.vps_days_left;
    document.getElementById('vdays').innerHTML = vd === null ? '—'
      : (vd < 1
          ? '<span class="warn-text">' + vd.toFixed(2) + ' d</span>'
          : '<span class="ok">' + vd.toFixed(2) + ' d</span>');
    document.getElementById('vcheck').textContent = s.vps_last_check || '—';
    document.getElementById('vaction').textContent = s.vps_last_action || '—';
    document.getElementById('sh').value = s.sleep_hour;
    document.getElementById('wh').value = s.wake_hour;
    document.getElementById('fd').value = s.frame_delay;
    document.getElementById('mg').value = s.min_gap;
    document.getElementById('tzin').value = s.timezone;
    document.getElementById('stlist').value = JSON.stringify(s.statuses, null, 2);
    document.getElementById('log').textContent = s.log.join('\n');
    const l = document.getElementById('log'); l.scrollTop = l.scrollHeight;
  }catch(e){}
}
async function act(u){ await fetch(u, {method:'POST'}); refresh(); }
async function saveCfg(){
  await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      sleep_hour: +document.getElementById('sh').value,
      wake_hour: +document.getElementById('wh').value,
      frame_delay: +document.getElementById('fd').value,
      min_gap: +document.getElementById('mg').value,
      timezone: document.getElementById('tzin').value
    })});
  refresh();
}
async function saveSt(){
  try{
    const arr = JSON.parse(document.getElementById('stlist').value);
    await fetch('/api/statuses', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({statuses: arr})});
    refresh();
  }catch(e){ alert('invalid JSON') }
}
refresh();
setInterval(refresh, 3000);
</script></body></html>"""


def _auth(session):
    if session != DASH_TOKEN:
        raise HTTPException(401)


@app.get("/", response_class=HTMLResponse)
async def index(session: str = Cookie(None)):
    return DASHBOARD_HTML if session == DASH_TOKEN else LOGIN_HTML


@app.post("/login")
async def login(req: Request):
    data = await req.json()
    if data.get("token") != DASH_TOKEN:
        raise HTTPException(401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie("session", DASH_TOKEN, httponly=True, samesite="strict")
    return resp


@app.get("/api/state")
async def api_state(session: str = Cookie(None)):
    _auth(session)
    return {
        "user": str(bot_ref.user) if bot_ref and bot_ref.user else None,
        "sleeping": bot_ref.sleeping,
        "current_index": bot_ref.current_index,
        "statuses": bot_ref.statuses,
        "sleep_hour": bot_ref.sleep_hour,
        "wake_hour": bot_ref.wake_hour,
        "timezone": bot_ref.timezone,
        "frame_delay": bot_ref.frame_delay,
        "min_gap": bot_ref.min_gap,
        "vps_days_left": bot_ref.vps_days_left,
        "vps_last_check": bot_ref.vps_last_check.strftime("%Y-%m-%d %H:%M")
                          if bot_ref.vps_last_check else None,
        "vps_last_action": bot_ref.vps_last_action,
        "log": bot_ref.recent_log,
    }


@app.post("/api/sleep")
async def api_sleep(session: str = Cookie(None)):
    _auth(session)
    bot_ref.manual = True
    return {"ok": True}


@app.post("/api/wake")
async def api_wake(session: str = Cookie(None)):
    _auth(session)
    bot_ref.manual = False
    return {"ok": True}


@app.post("/api/auto")
async def api_auto(session: str = Cookie(None)):
    _auth(session)
    bot_ref.manual = None
    return {"ok": True}


@app.post("/api/vps-check")
async def api_vps(session: str = Cookie(None)):
    _auth(session)
    asyncio.create_task(bot_ref.check_vps())
    return {"ok": True}


@app.post("/api/statuses")
async def api_statuses(req: Request, session: str = Cookie(None)):
    _auth(session)
    bot_ref.statuses = (await req.json())["statuses"]
    return {"ok": True}


@app.post("/api/config")
async def api_config(req: Request, session: str = Cookie(None)):
    _auth(session)
    d = await req.json()
    for k in ("sleep_hour", "wake_hour", "frame_delay", "min_gap"):
        if k in d:
            setattr(bot_ref, k, type(getattr(bot_ref, k))(d[k]))
    if "timezone" in d:
        bot_ref.timezone = d["timezone"]
    return {"ok": True}


# ── boot ────────────────────────────────────────────────
async def run():
    global bot_ref
    bot_ref = AnimBot()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=DASH_PORT, log_level="warning")
    srv = uvicorn.Server(cfg)
    await asyncio.gather(bot_ref.start(TOKEN), srv.serve())


if __name__ == "__main__":
    asyncio.run(run())
