# 01 - Telegram control plane for a headless AI agent

**Problem.** A developer runs an AI coding agent (Claude Code) on a workstation full of local tools,
credentials and project context. They need to drive it from a phone or from a locked-down office
network - without exposing a port, without babysitting "allow?" prompts, and without losing the
conversation between messages.

**What it does.**
- Text a Telegram bot; the bridge runs one headless agent turn in the project and texts back the result.
- Conversation memory persists across messages (`--resume <session_id>`), so it is one running chat.
- Photos are downloaded and handed to the agent by file path. Voice notes can be transcribed locally
  and answered by voice (pluggable `TRANSCRIBE_CMD` / `SPEAK_CMD`).
- Chat commands: `/new` `/ping` `/whoami` `/stop`.

**Engineering that made it reliable in production (running since June 2026):**
- Outbound-only long polling -> works behind corporate firewalls and home NAT; no webhook, no port-forward.
- Allow-listed chat ids; unknown senders are logged, never served; optional one-time claim code for onboarding a second device.
- Singleton lock via exclusive localhost bind -> duplicate launches (startup-folder + watchdog races) exit in milliseconds instead of stealing each other's updates.
- Sequential turn processing so concurrent texts never clash on the same session.
- Auto-approve is opt-in, and the agent's own pre-tool safety hooks still fire underneath it.
- Replies chunked under Telegram's 4096-char limit; long spoken replies capped with the text carrying the rest.

**Stack.** Python 3, `requests`, Telegram Bot API, Claude Code CLI (`claude -p --output-format json`).
Boot persistence on Windows = a shortcut to a restart-loop `.bat` in the Startup folder (not included).

**Run.**
```
pip install requests
cp .env.example .env   # fill in token + chat ids
python agent_telegram_bridge.py --selftest   # offline logic checks
python agent_telegram_bridge.py --live-test  # one real turn + one Telegram message
python agent_telegram_bridge.py              # start
```

**Proof.** `../docs/01_bridge_selftest.png` (offline selftest output). A live chat screenshot is
available on request (it contains the operator's private chat).
