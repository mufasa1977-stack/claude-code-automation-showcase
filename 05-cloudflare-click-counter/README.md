# 05 - Click-counting redirect on Cloudflare Pages (bonus)

**Problem.** You post a link in thirty videos and a payment page. The payment provider tells you
about sales; nobody tells you about clicks. Between "views" and "sales" the funnel is blind, so you
cannot tell whether the offer is failing or the call-to-action is.

**What it does.** A 60-line Cloudflare Pages Function: `/go/<slug>?s=<source>` counts the click in
Workers KV (total, per day, per source tag) and redirects. `go_stats.py` prints the counters.
Every posted link gets tagged with the post it came from, so you learn which video actually sends
buyers. Deploys with a scoped API token from the CLI - no browser OAuth in the loop.

**Stack.** Cloudflare Pages Functions, Workers KV, wrangler, Python 3.

**Proof.** In production since September 2026 as the CTA sensor for a content-to-store funnel.
