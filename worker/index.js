/**
 * Opportunity Scout — Cloudflare Worker (free plan).
 *
 * GitHub starts scheduled workflows late (often by hours) and drops many of them. This Worker
 * fixes both problems:
 *   1. Cron Triggers start the "Daily digest" workflow on time (09:15 IST, fallback 10:45 IST).
 *   2. A Telegram webhook answers bot commands within seconds, using replies.json, which every
 *      daily run saves on the scout-data branch.
 *
 * Settings (Worker → Settings → Variables and Secrets):
 *   TELEGRAM_BOT_TOKEN  secret  bot token from @BotFather
 *   TELEGRAM_CHAT_ID    secret  the only chat the bot answers (leave unset to make the bot reply
 *                               to anyone with their chat ID, for setup only)
 *   GITHUB_TOKEN        secret  fine-grained token for this repository only, with
 *                               Actions: read and write, Contents: read-only
 *   GITHUB_REPO         text    owner/repository, e.g. victorsimba189-lab/Opportunity-Ai-agent.
 *   WEBHOOK_SECRET      secret  16+ random letters/digits; protects /telegram and /setup
 *   GITHUB_BRANCH       text    optional, branch the workflow runs on (default "main")
 *   DATA_BRANCH         text    optional, branch holding replies.json (default "scout-data")
 * Cron Triggers (UTC): "45 3 * * *" (09:15 IST) and "15 5 * * *" (10:45 IST fallback).
 *
 * After deploying, open https://<worker-url>/setup?secret=<WEBHOOK_SECRET> once.
 */

const DAILY_WORKFLOW = "daily.yml";
// Same as ticking "wait" and unticking "dry run" in the Run workflow form.
const DAILY_INPUTS = { dry_run: "false", wait: "true" };
const REQUIRED = ["TELEGRAM_BOT_TOKEN", "GITHUB_TOKEN", "GITHUB_REPO", "WEBHOOK_SECRET"];
const SECRET_PATTERN = /^[A-Za-z0-9_-]{16,256}$/; // what Telegram accepts as a webhook secret_token

export const settings = { retryDelayMs: 2000 }; // tests set this to 0

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    try {
      if (url.pathname === "/telegram" && request.method === "POST") return await handleWebhook(request, env, ctx);
      if (url.pathname === "/setup" && request.method === "GET") return await handleSetup(url, env);
      if (url.pathname === "/") return text("Opportunity Scout worker is running.");
      return text("Not found", 404);
    } catch (err) {
      console.error("request failed:", err);
      return text("Internal error", 500);
    }
  },

  async scheduled(event, env) {
    await startDailyRun(env);
  },
};

// ---- Telegram commands ---------------------------------------------------------------------

async function handleWebhook(request, env, ctx) {
  const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
  if (!env.WEBHOOK_SECRET || !safeEqual(secret, env.WEBHOOK_SECRET)) return text("Forbidden", 403);
  let update;
  try {
    update = await request.json();
  } catch {
    return text("ok");
  }
  // Acknowledge at once and reply in the background. Telegram resends updates that get no 200,
  // which would produce duplicate replies, so errors are logged instead of returned.
  ctx.waitUntil(answer(update, env).catch((err) => console.error("answer failed:", err.message)));
  return text("ok");
}

export async function answer(update, env) {
  const msg = update && update.message;
  if (!msg || !msg.chat || msg.chat.id === undefined) return "ignored";
  const chatId = String(msg.chat.id);
  const allowed = String(env.TELEGRAM_CHAT_ID || "").trim();
  if (!allowed) {
    await sendMessage(env, chatId, `Your chat ID is <code>${chatId}</code>.\n`
      + "Add it as TELEGRAM_CHAT_ID in GitHub (repository secret) and in the Worker.");
    return "whoami";
  }
  if (chatId !== allowed) return "ignored";

  const command = parseCommand(msg.text);
  if (command === null) {
    await sendMessage(env, chatId, "Send /help to see what I can do.");
    return "hint";
  }
  let data;
  try {
    data = await loadReplies(env);
  } catch (err) {
    await sendMessage(env, chatId, `⚠️ ${escapeHtml(err.message)}`);
    return "error";
  }
  const name = resolveCommand(command, data);
  for (const chunk of data.replies[name]) await sendMessage(env, chatId, chunk);
  return name;
}

/** "/Internships@MyBot now" → "internships"; plain text → null. Mirrors scout/bot.py. */
export function parseCommand(text) {
  if (typeof text !== "string" || !text.startsWith("/")) return null;
  return text.trim().split(/\s+/)[0].slice(1).split("@")[0].toLowerCase();
}

function resolveCommand(command, data) {
  const name = (data.aliases || {})[command] || command;
  return Array.isArray(data.replies[name]) ? name : "help";
}

async function loadReplies(env) {
  const branch = encodeURIComponent(env.DATA_BRANCH || "scout-data");
  const res = await github(env, `/contents/replies.json?ref=${branch}`, {
    headers: { Accept: "application/vnd.github.raw+json" },
  });
  if (res.status === 401) {
    throw new Error("GitHub rejected the Worker's GITHUB_TOKEN (expired or revoked?). "
      + "Create a new token and update the Worker secret.");
  }
  if (res.status === 404) {
    throw new Error("No scan results yet. They appear after the first Daily digest run "
      + "(or the Worker's GITHUB_TOKEN can't see the repository).");
  }
  if (!res.ok) throw new Error(`Could not load the latest scan from GitHub (HTTP ${res.status}). Try again shortly.`);
  const data = await res.json();
  if (!data || typeof data.replies !== "object" || !Array.isArray(data.replies.help)) {
    throw new Error("The saved scan results are in an unexpected format.");
  }
  return data;
}

// ---- Daily run -----------------------------------------------------------------------------

export async function startDailyRun(env) {
  const ref = env.GITHUB_BRANCH || "main";
  const attempts = 4;
  let error = "";
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      // Re-enable first: GitHub disables workflows in public repos after 60 days without commits.
      await github(env, `/actions/workflows/${DAILY_WORKFLOW}/enable`, { method: "PUT" });
      const res = await github(env, `/actions/workflows/${DAILY_WORKFLOW}/dispatches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ref, inputs: DAILY_INPUTS }),
      });
      if (res.ok) {
        console.log(`started ${DAILY_WORKFLOW} on ${ref}`);
        return { ok: true };
      }
      error = `GitHub HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`;
      if (res.status < 500 && res.status !== 429) break; // token or settings problem: retrying won't help
    } catch (err) {
      error = err.message;
    }
    if (attempt < attempts) await sleep(settings.retryDelayMs * 2 ** (attempt - 1));
  }
  console.error("could not start the daily run:", error);
  if (env.TELEGRAM_CHAT_ID) {
    try {
      await sendMessage(env, env.TELEGRAM_CHAT_ID, "⚠️ Opportunity Scout could not start today's digest run on GitHub "
        + `(${escapeHtml(error)}). GitHub's own schedule will still try later today. If this repeats, `
        + "check the Worker's GITHUB_TOKEN.");
    } catch (err) {
      console.error("could not report the failure:", err.message);
    }
  }
  return { ok: false, error };
}

// ---- Setup page ----------------------------------------------------------------------------

async function handleSetup(url, env) {
  if (!env.WEBHOOK_SECRET || !safeEqual(url.searchParams.get("secret") || "", env.WEBHOOK_SECRET)) {
    return text("Forbidden. Open /setup?secret=<your WEBHOOK_SECRET>.", 403);
  }
  const lines = ["Opportunity Scout worker setup", ""];
  const check = (ok, message) => lines.push(`${ok ? "✅" : "❌"} ${message}`);
  const missing = REQUIRED.filter((name) => !String(env[name] || "").trim());
  if (missing.length) {
    check(false, `Missing Worker settings: ${missing.join(", ")}`);
    return text(lines.join("\n"));
  }
  if (!SECRET_PATTERN.test(env.WEBHOOK_SECRET)) {
    check(false, "WEBHOOK_SECRET must be 16–256 characters: letters, digits, _ or - only.");
    return text(lines.join("\n"));
  }
  const undo = url.searchParams.has("undo");

  try {
    const me = await telegram(env, "getMe", {});
    check(true, `Telegram bot @${me.username}`);
    if (undo) {
      await telegram(env, "deleteWebhook", { drop_pending_updates: false });
      check(true, "Webhook removed: commands are answered by the GitHub poller again (slow).");
    } else {
      await telegram(env, "setWebhook", {
        url: `${url.origin}/telegram`,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message"],
        max_connections: 5,
      });
      check(true, `Telegram now sends commands to ${url.origin}/telegram`);
    }
  } catch (err) {
    check(false, `Telegram: ${err.message} (check TELEGRAM_BOT_TOKEN)`);
  }

  if (env.TELEGRAM_CHAT_ID) {
    try {
      await sendMessage(env, env.TELEGRAM_CHAT_ID, undo
        ? "Opportunity Scout: the Worker is disconnected. Commands are answered by GitHub again (slow)."
        : "✅ Opportunity Scout Worker connected. Commands are now answered within seconds.");
      check(true, "Confirmation message sent to TELEGRAM_CHAT_ID");
    } catch (err) {
      check(false, `Could not message TELEGRAM_CHAT_ID: ${err.message}`);
    }
  } else {
    check(false, "TELEGRAM_CHAT_ID is not set: the bot replies to anyone with their chat ID until you add it.");
  }

  const repo = await github(env, "");
  if (repo.ok) {
    check(true, `GitHub repository ${(await repo.json()).full_name}`);
  } else {
    check(false, `GitHub repository ${env.GITHUB_REPO}: HTTP ${repo.status} `
      + (repo.status === 401 ? "(token invalid or expired)" : "(check GITHUB_REPO and the token's repository access)"));
  }
  const enable = await github(env, `/actions/workflows/${DAILY_WORKFLOW}/enable`, { method: "PUT" });
  if (enable.ok) {
    check(true, "The token can start workflows (Actions: read and write)");
  } else {
    check(false, `Starting workflows: HTTP ${enable.status} `
      + (enable.status === 403 ? "(give the token Actions: read and write)" : `(is ${DAILY_WORKFLOW} on the default branch?)`));
  }
  try {
    const data = await loadReplies(env);
    check(true, `Latest scan results saved at ${data.generated_at}`);
  } catch (err) {
    check(false, err.message);
  }
  lines.push("", "Also add the Cron Triggers 45 3 * * * and 15 5 * * * (UTC) under Settings → Triggers,",
    "so the digest starts at 09:15 IST and is sent at 10:00 IST.");
  return text(lines.join("\n"));
}

// ---- Helpers -------------------------------------------------------------------------------

async function telegram(env, method, params) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const body = await res.json().catch(() => ({ ok: false, description: `HTTP ${res.status}` }));
  // The error names the method only: the URL contains the bot token.
  if (!body.ok) throw new Error(`Telegram ${method}: ${body.description || `HTTP ${res.status}`}`);
  return body.result;
}

async function sendMessage(env, chatId, message) {
  const base = { chat_id: chatId, link_preview_options: { is_disabled: true } };
  try {
    return await telegram(env, "sendMessage", { ...base, text: message, parse_mode: "HTML" });
  } catch (err) {
    if (!err.message.includes("can't parse entities")) throw err;
    return telegram(env, "sendMessage", { ...base, text: stripHtml(message) });
  }
}

function github(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${String(env.GITHUB_REPO).trim()}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "opportunity-scout-worker",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

function text(body, status = 200) {
  return new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function escapeHtml(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function stripHtml(value) {
  return value
    .replace(/<a href="([^"]+)">([^<]*)<\/a>/g, "$2 ($1)")
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
