"""
agent_telegram_bridge.py - drive a headless AI coding agent (Claude Code) from Telegram.

WHAT IT DOES
  You text a Telegram bot from your phone. The bridge runs ONE headless agent turn inside a
  project directory (full tools, memory, MCP servers) and texts the result back. Conversation
  context persists across messages via the agent's --resume <session_id>, so it feels like one
  continuous chat. Photos you send are downloaded and handed to the agent as a file path.
  Optional: voice notes in (local speech-to-text) and voice replies out (local text-to-speech).

WHY IT IS BUILT THIS WAY
  * Long-polls getUpdates (outbound HTTPS only) -> works behind a corporate firewall / home NAT
    with no port-forwarding and no inbound webhook.
  * Locked to an allow-list of chat ids. Unknown senders are LOGGED, never served.
  * Sequential: one turn at a time, so concurrent texts never clash on --resume.
  * Singleton lock (exclusive localhost bind) -> duplicate launches exit quietly in milliseconds.
    Overlapping pollers otherwise eat each other's updates.
  * Auto-approve is OPT-IN (AGENT_AUTO_APPROVE=1). Even then, the agent's own PreToolUse safety
    hooks still fire, so destructive commands stay blocked; only the "allow?" prompt goes away.

CONFIG (environment variables; see .env.example)
  TELEGRAM_BOT_TOKEN   bot token from @BotFather                      (required)
  TELEGRAM_CHAT_IDS    comma-separated authorized chat ids            (required)
  AGENT_PROJECT_DIR    directory the agent works in                   (default: cwd)
  AGENT_CMD            agent executable                               (default: claude)
  AGENT_AUTO_APPROVE   "1" to pass --dangerously-skip-permissions     (default: off)
  AGENT_TURN_TIMEOUT   seconds per turn                               (default: 1800)
  BRIDGE_CLAIM_CODE    if set, an unknown sender who texts this exact code is added to the
                       allow-list (multi-device onboarding without editing config)
  TRANSCRIBE_CMD       optional: command that prints a transcript for an audio file path
  SPEAK_CMD            optional: command that speaks a text argument (voice replies)

RUN
  python agent_telegram_bridge.py             # start the bridge
  python agent_telegram_bridge.py --selftest  # offline logic checks (no network, no agent)
  python agent_telegram_bridge.py --live-test # one real agent turn + one Telegram message

CHAT COMMANDS
  /new  reset conversation    /ping  health    /whoami  identity    /stop  shut down
"""
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:  # keep --selftest runnable without the dependency installed
    requests = None

# ---- configuration ---------------------------------------------------------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_IDS = {c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()}
ROOT = Path(os.environ.get("AGENT_PROJECT_DIR", os.getcwd())).resolve()
AGENT = shutil.which(os.environ.get("AGENT_CMD", "claude")) or os.environ.get("AGENT_CMD", "claude")
AUTO_APPROVE = os.environ.get("AGENT_AUTO_APPROVE", "0") == "1"
TURN_TIMEOUT = int(os.environ.get("AGENT_TURN_TIMEOUT", "1800"))
CLAIM_CODE = os.environ.get("BRIDGE_CLAIM_CODE", "")
TRANSCRIBE_CMD = os.environ.get("TRANSCRIBE_CMD", "")
SPEAK_CMD = os.environ.get("SPEAK_CMD", "")

STATE_DIR = Path(__file__).resolve().parent / "_state"
STATE_DIR.mkdir(exist_ok=True)
STATE = STATE_DIR / "bridge_state.json"
LOG = STATE_DIR / "bridge.log"
EXTRA_IDS_FILE = STATE_DIR / "extra_chat_ids.txt"
MEDIA_DIR = STATE_DIR / "media"
MEDIA_DIR.mkdir(exist_ok=True)

LOCK_PORT = 48732            # singleton lock port (any free localhost port works)
TG_CHUNK = 3800              # Telegram hard limit is 4096 chars per message
SPOKEN_REPLY_CHARS = 700     # a long voice note is useless; the text carries the rest
API = f"https://api.telegram.org/bot{TOKEN}"


def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG.open("a", encoding="utf-8").write(line + "\n")
    except Exception:
        pass


# ---- state -----------------------------------------------------------------
def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"offset": 0, "session_id": None}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def load_extra_ids() -> set:
    try:
        if EXTRA_IDS_FILE.exists():
            return {ln.strip() for ln in EXTRA_IDS_FILE.read_text().splitlines() if ln.strip()}
    except Exception:
        pass
    return set()


CHAT_IDS |= load_extra_ids()


# ---- pure helpers (covered by --selftest) ----------------------------------
def chunk_text(text: str, size: int = TG_CHUNK):
    """Split a reply into Telegram-sized pieces; an empty reply still sends one message."""
    if not text:
        return [" "]
    return [text[i:i + size] for i in range(0, len(text), size)]


def is_authorized(sender_id: str, allowed: set) -> bool:
    return bool(sender_id) and sender_id in allowed


def build_agent_cmd(prompt: str, session_id, auto_approve: bool = AUTO_APPROVE):
    cmd = [AGENT, "-p", prompt, "--output-format", "json"]
    if auto_approve:
        cmd.append("--dangerously-skip-permissions")
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


def parse_agent_output(stdout: str, fallback_session):
    """Agent JSON -> (result_text, session_id, ok). Non-JSON output is passed through."""
    try:
        data = json.loads(stdout)
        return (data.get("result") or "(no text result)",
                data.get("session_id") or fallback_session,
                not data.get("is_error", False))
    except Exception:
        return (stdout[-3500:] if stdout else "(empty output)", fallback_session, True)


def clean_for_speech(text: str, limit: int = SPOKEN_REPLY_CHARS) -> str:
    clean = re.sub(r"[*_`#>|]+", "", text)
    clean = re.sub(r"\s*\n\s*", ". ", clean).strip()
    if len(clean) > limit:
        clean = clean[:limit].rsplit(".", 1)[0] + ". Full details are in the text above."
    return clean


# ---- Telegram I/O ----------------------------------------------------------
def send(text: str) -> None:
    for cid in CHAT_IDS:
        for chunk in chunk_text(text):
            try:
                requests.post(f"{API}/sendMessage", data={"chat_id": cid, "text": chunk}, timeout=30)
            except Exception as e:
                log(f"send fail ({cid}): {e}")


def get_updates(offset: int):
    try:
        r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=60)
        return r.json().get("result", []) if r.status_code == 200 else None
    except Exception as e:
        log(f"getUpdates fail: {e}")
        return None


def download_file(file_id: str, update_id: int, suffix: str):
    try:
        gf = requests.get(f"{API}/getFile", params={"file_id": file_id}, timeout=30).json()
        file_path = gf["result"]["file_path"]
        data = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}", timeout=90).content
        out = MEDIA_DIR / f"tg_{datetime.now():%Y%m%d_%H%M%S}_{update_id}{suffix}"
        out.write_bytes(data)
        log(f"downloaded -> {out.name} ({len(data)} bytes)")
        return str(out)
    except Exception as e:
        log(f"download FAILED: {e}")
        return None


# ---- optional voice hooks --------------------------------------------------
def transcribe(audio_path: str):
    if not TRANSCRIBE_CMD:
        return None
    try:
        p = subprocess.run(shlex.split(TRANSCRIBE_CMD) + [audio_path],
                           capture_output=True, text=True, timeout=180)
        return (p.stdout or "").strip() or None
    except Exception as e:
        log(f"transcribe FAILED: {e}")
        return None


def speak(text: str) -> bool:
    if not SPEAK_CMD:
        return False
    try:
        p = subprocess.run(shlex.split(SPEAK_CMD) + [clean_for_speech(text)],
                           capture_output=True, text=True, timeout=180)
        return p.returncode == 0
    except Exception as e:
        log(f"speak FAILED: {e}")
        return False


# ---- the agent turn --------------------------------------------------------
def run_agent(prompt: str, session_id):
    cmd = build_agent_cmd(prompt, session_id)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT),
                           timeout=TURN_TIMEOUT, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return (f"(turn hit the {TURN_TIMEOUT // 60}-min timeout; send /new if stuck)", session_id, False)
    if p.returncode != 0:
        return (f"(agent exited {p.returncode})\n{(p.stderr or p.stdout or '')[-1500:]}", session_id, False)
    return parse_agent_output(p.stdout, session_id)


def handle(text: str, state: dict, spoke: bool = False) -> bool:
    """Process one authorized message. Returns False to stop the bridge."""
    low = text.strip().lower()
    if low == "/stop":
        send("Bridge shutting down."); return False
    if low == "/ping":
        send("alive. session=" + (state["session_id"] or "new")); return True
    if low == "/whoami":
        send(f"project={ROOT}\nagent={AGENT}\nauto_approve={AUTO_APPROVE}"); return True
    if low == "/new":
        state["session_id"] = None; save_state(state)
        send("New conversation started (context cleared)."); return True
    send("on it...")
    t0 = time.time()
    result, sid, ok = run_agent(text, state["session_id"])
    state["session_id"] = sid; save_state(state)
    send(f"{'OK' if ok else 'WARN'} ({time.time() - t0:.0f}s)\n\n{result}")
    if spoke and ok:
        speak(result)
    return True


# ---- singleton -------------------------------------------------------------
def acquire_singleton():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
    except OSError:
        log(f"singleton lock busy (port {LOCK_PORT}) - another bridge is alive, exiting quietly")
        sys.exit(0)
    return s


# ---- tests -----------------------------------------------------------------
def selftest() -> int:
    """Offline checks of the logic that must never regress. No network, no agent."""
    failures = []

    def chk(label, cond):
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        if not cond:
            failures.append(label)

    chk("empty reply still sends one message", chunk_text("") == [" "])
    chk("long reply is chunked under Telegram's limit",
        all(len(c) <= TG_CHUNK for c in chunk_text("x" * 10000)) and len(chunk_text("x" * 10000)) == 3)
    chk("unknown sender is refused", not is_authorized("999", {"123"}))
    chk("empty sender is refused", not is_authorized("", {"123"}))
    chk("allow-listed sender is accepted", is_authorized("123", {"123"}))
    chk("auto-approve flag is OFF unless opted in",
        "--dangerously-skip-permissions" not in build_agent_cmd("hi", None, auto_approve=False))
    chk("resume flag carries the session id",
        build_agent_cmd("hi", "abc", auto_approve=False)[-2:] == ["--resume", "abc"])
    r, sid, ok = parse_agent_output('{"result":"PONG","session_id":"s1","is_error":false}', None)
    chk("agent JSON parsed", (r, sid, ok) == ("PONG", "s1", True))
    r, sid, ok = parse_agent_output('{"result":"boom","session_id":"s2","is_error":true}', None)
    chk("agent error flag propagates", ok is False and sid == "s2")
    r, sid, ok = parse_agent_output("plain text", "keep")
    chk("non-JSON output passes through with old session", r == "plain text" and sid == "keep")
    sp = clean_for_speech("**Done.**\nNext: `run tests`\n" + "word " * 400)
    chk("speech text stripped of markdown and capped", "*" not in sp and "`" not in sp and len(sp) < 800)

    print(f"[selftest] {len(failures)} failure(s)")
    return 1 if failures else 0


def live_test() -> int:
    assert TOKEN and CHAT_IDS, "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS must be set"
    log("LIVE TEST: one headless agent turn...")
    res, sid, ok = run_agent("Reply with exactly the single word: PONG", None)
    log(f"agent -> ok={ok} session={sid} result={res[:80]!r}")
    send(f"Bridge live test: agent {'OK' if ok else 'FAILED'} -> {res[:80]}")
    return 0 if ok else 1


# ---- main loop -------------------------------------------------------------
def main() -> None:
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if requests is None:
        sys.exit("pip install requests")
    if "--live-test" in sys.argv:
        sys.exit(live_test())
    if not TOKEN or not CHAT_IDS:
        sys.exit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_IDS (see .env.example)")

    _lock = acquire_singleton()  # noqa: F841  (must stay referenced for process lifetime)
    state = load_state()
    log("=" * 56)
    log(f"AGENT TELEGRAM BRIDGE online  project={ROOT}  chats={len(CHAT_IDS)}")
    log("=" * 56)
    send("Agent bridge ONLINE. Text me to work the project remotely.\n"
         "Context persists across messages. Commands: /new /ping /whoami /stop")

    running = True
    while running:
        updates = get_updates(state["offset"] + 1)
        if updates is None:
            time.sleep(10)
            continue
        for upd in updates:
            state["offset"] = max(state["offset"], upd["update_id"]); save_state(state)
            msg = upd.get("message") or upd.get("edited_message") or {}
            sender_id = str(msg.get("from", {}).get("id", ""))
            text = (msg.get("text") or "").strip()

            if not is_authorized(sender_id, CHAT_IDS):
                frm = msg.get("from", {})
                log(f"UNKNOWN SENDER id={sender_id} user={frm.get('username')} text={text[:60]!r}")
                if CLAIM_CODE and text == CLAIM_CODE:
                    EXTRA_IDS_FILE.open("a", encoding="utf-8").write(sender_id + "\n")
                    CHAT_IDS.add(sender_id)
                    log(f"CLAIMED: {sender_id} added to the allow-list")
                    try:
                        requests.post(f"{API}/sendMessage",
                                      data={"chat_id": sender_id, "text": "Device bound."}, timeout=30)
                    except Exception:
                        pass
                continue

            # images -> saved to disk, handed to the agent by path
            caption = (msg.get("caption") or "").strip()
            image_path = None
            if "photo" in msg:
                image_path = download_file(msg["photo"][-1]["file_id"], upd["update_id"], ".jpg")
            elif str(msg.get("document", {}).get("mime_type", "")).startswith("image/"):
                doc = msg["document"]
                image_path = download_file(doc["file_id"], upd["update_id"],
                                           Path(doc.get("file_name", "img")).suffix or ".img")
            prompt = text or caption
            if image_path:
                note = f"[User sent an image via Telegram; open this file to view it: {image_path}]"
                prompt = (prompt + "\n\n" + note) if prompt else note

            # voice notes -> transcribed locally (if TRANSCRIBE_CMD is configured)
            spoke = False
            voice = msg.get("voice") or msg.get("audio") or msg.get("video_note")
            if voice:
                ext = ".ogg" if msg.get("voice") else (".mp4" if msg.get("video_note") else ".mp3")
                apath = download_file(voice["file_id"], upd["update_id"], ext)
                heard = transcribe(apath) if apath else None
                if heard:
                    spoke = True
                    send(f"heard: \"{heard}\"")
                    prompt = ((prompt + "\n\n") if prompt else "") + \
                        "[Sent by voice; the user cannot read a long answer. Keep it tight.]\n" + heard
                else:
                    send("Got a voice note but could not transcribe it; please text it.")
                    continue

            if not prompt:
                continue
            log(f"MSG: {prompt[:100]!r}")
            try:
                running = handle(prompt, state, spoke=spoke)
            except Exception as e:
                log(f"handle error: {e}")
                send(f"error: {str(e)[:300]}")
    log("bridge stopped.")


if __name__ == "__main__":
    main()
