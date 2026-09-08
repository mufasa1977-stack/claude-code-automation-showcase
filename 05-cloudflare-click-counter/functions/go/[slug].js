// /go/<slug> - a counting redirect for every call-to-action link you post.
//
// Payment links and store listings usually report sales but not CLICKS, so the funnel is blind
// between "views" and "sales". This Cloudflare Pages Function counts the click in KV (per slug,
// per day, per source tag) and 302s to the destination. Add a destination below; nothing else changes.
//
//   https://your-domain.com/go/offer            -> destination
//   https://your-domain.com/go/offer?s=yt_abc   -> same, tagged with the post it came from
//   https://your-domain.com/go/_stats?key=...   -> JSON counters (key = GO_STATS_KEY secret)
//
// Bindings (wrangler.toml / Pages project settings): KV namespace GO_COUNTS, secret GO_STATS_KEY.

const DEST = {
  offer: "https://example.com/your-offer-page",
  guide: "https://example.com/your-guide",
};

function day() {
  return new Date().toISOString().slice(0, 10);
}

async function bump(kv, key) {
  if (!kv) return;
  const cur = parseInt((await kv.get(key)) || "0", 10);
  await kv.put(key, String(cur + 1));
}

export async function onRequest(context) {
  const { request, env, params } = context;
  const slug = String(params.slug || "").toLowerCase();
  const url = new URL(request.url);

  if (slug === "_stats") {
    if (!env.GO_STATS_KEY || url.searchParams.get("key") !== env.GO_STATS_KEY) {
      return new Response("forbidden", { status: 403 });
    }
    const list = env.GO_COUNTS ? await env.GO_COUNTS.list({ prefix: "c:" }) : { keys: [] };
    const out = {};
    for (const k of list.keys) out[k.name.slice(2)] = parseInt((await env.GO_COUNTS.get(k.name)) || "0", 10);
    return new Response(JSON.stringify(out, null, 1), { headers: { "content-type": "application/json" } });
  }

  const dest = DEST[slug];
  if (!dest) return new Response("unknown link", { status: 404 });

  const src = (url.searchParams.get("s") || "direct").replace(/[^a-zA-Z0-9_\-]/g, "").slice(0, 40);
  const d = day();
  context.waitUntil(Promise.all([
    bump(env.GO_COUNTS, `c:${slug}:total`),
    bump(env.GO_COUNTS, `c:${slug}:${d}`),
    bump(env.GO_COUNTS, `c:${slug}:src:${src}`),
  ]));
  return Response.redirect(dest, 302);
}
