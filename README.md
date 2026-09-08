# Claude Code automation showcase

Production automation built with **Claude Code** for a small software studio
([Xionprotech](https://xionprotech.com), Pottstown, PA). Every piece here is a sanitized copy of
something that runs today: a phone-to-agent control plane, a social comment monitor, a YouTube
publishing pipeline, a fail-closed quality-gate framework, and a click-counting redirect that
turned a blind funnel into a measured one.

The repo is the authority: proposals only claim what is shown here.

| # | Piece | Business problem it solves | Stack |
|---|-------|----------------------------|-------|
| [01](01-telegram-agent-control-plane/) | **Telegram control plane for a headless AI agent** | Run a workstation-bound AI coding agent from a phone or a locked-down office network; no open ports, no "allow?" babysitting, conversation memory kept | Python, Telegram Bot API, Claude Code CLI |
| [02](02-social-comment-monitor/) | **Social comment monitor + alerting** | Know within 30 minutes when a customer comments on an ad; free traffic time-series as a by-product | Python stdlib, Telegram, Task Scheduler / cron |
| [03](03-youtube-publish-automation/) | **YouTube publishing automation** | Batch-post a folder of clips with OAuth, a wrong-channel guard, pluggable pre-publish checks and an idempotent ledger | Python, Google OAuth 2.0, YouTube Data API v3 |
| [04](04-ai-pipeline-quality-gates/) | **Fail-closed quality gates for AI pipelines** | Make "nobody checked" impossible: a declaration + law registry + hash-bound certificate before anything publishes; plus a sensor for hooks that silently stopped running | Python stdlib |
| [05](05-cloudflare-click-counter/) | **Click-counting redirect** (bonus) | See which post actually sends buyers, not just views and sales | Cloudflare Pages Functions, Workers KV |

## How these were built

Claude Code did the typing; the engineering came from running the systems and feeding every
failure back into the code as a check. A few of the rules that came out of that, visible across
the pieces:

- **A check must judge what is being shipped**, never the history it cannot change. Re-judging
  already-live items produces false blocks, and false blocks teach operators to bypass the check.
- **The absence of a check is a block, never a pass.** An applicable rule with no executable
  gate refuses the publish (04).
- **A sensor without a drain is noise.** The comment monitor alerts once when it goes blind
  instead of returning zeros forever (02).
- **Idempotency and guards before features.** The uploader prints the channel it resolved
  before touching a file and keeps a ledger so re-runs never double-post (03).
- **Never trap the operator.** Duplicate bridge launches exit quietly; a broken sensor exits 0
  with a message rather than locking the session (01, 04).

## Running the proofs

Each piece has an offline `--selftest` that exercises its logic against real specimens, so you
can verify the behaviour without any credentials:

```
python 01-telegram-agent-control-plane/agent_telegram_bridge.py --selftest
python 02-social-comment-monitor/comment_monitor.py --selftest
python 03-youtube-publish-automation/yt_publish.py --selftest
python 04-ai-pipeline-quality-gates/ship_door.py --selftest
python 04-ai-pipeline-quality-gates/hook_regression_gate.py --selftest
python tools/secrets_scan.py --path .
```

Screenshots of those runs are in [`docs/`](docs/).

## Services

Available for: MCP server builds, Claude Code / agent automation for a business process,
Telegram / Slack control planes, OAuth publishing pipelines, and audits of existing AI
automations (where does it fail silently, what is unchecked, what is un-idempotent).

Contact: tariq@xionprotech.com

## License

MIT - see [LICENSE](LICENSE).
