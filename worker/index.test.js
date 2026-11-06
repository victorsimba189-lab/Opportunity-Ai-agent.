// Offline tests for the Worker: `node --test` in this folder (Node 18+, no dependencies).
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import worker, { parseCommand, settings, stripHtml } from "./index.js";

const ENV = {
  TELEGRAM_BOT_TOKEN: "123:SECRET",
  TELEGRAM_CHAT_ID: "42",
  GITHUB_TOKEN: "github_pat_x",
  GITHUB_REPO: "owner/repo.",
  WEBHOOK_SECRET: "s3cret_s3cret_s3cret",
};
const REPLIES = {
  version: 1,
  generated_at: "2026-11-04T04:31:00+00:00",
  aliases: { start: "help", programmes: "courses" },
  replies: {
    internships: ["<b>Internships</b> part 1", "part 2"],
    courses: ["<b>Courses</b>"],
    status: ["📋 <b>Status</b>"],
    help: ["🤖 help text"],
  },
};

let calls;
let routes;

// Replaces fetch: each route is [predicate(url, init), handler(url, init) → Response].
beforeEach(() => {
  calls = [];
  routes = [];
  settings.retryDelayMs = 0;
  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    calls.push({ url, init, body: init.body ? JSON.parse(init.body) : undefined });
    for (const [match, handle] of routes) if (match(url, init)) return handle(url, init);
    if (url.includes("api.telegram.org")) return json({ ok: true, result: { message_id: calls.length } });
    throw new Error(`unexpected fetch ${url}`);
  };
});

function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serveReplies(body = REPLIES, status = 200) {
  routes.push([(url) => url.includes("/contents/replies.json"), () => json(body, status)]);
}

const sent = () => calls.filter((c) => c.url.endsWith("/sendMessage")).map((c) => c.body);

async function webhook(update, { secret = ENV.WEBHOOK_SECRET, env = ENV } = {}) {
  const pending = [];
  const ctx = { waitUntil: (p) => pending.push(p) };
  const req = new Request("https://scout.example.workers.dev/telegram", {
    method: "POST",
    headers: { "X-Telegram-Bot-Api-Secret-Token": secret, "Content-Type": "application/json" },
    body: JSON.stringify(update),
  });
  const res = await worker.fetch(req, env, ctx);
  await Promise.all(pending);
  return res;
}

const message = (text, chatId = 42) => ({ update_id: 1, message: { chat: { id: chatId }, text } });

test("parseCommand mirrors the Python bot", () => {
  assert.equal(parseCommand("/Internships@MyScoutBot now"), "internships");
  assert.equal(parseCommand("/status"), "status");
  assert.equal(parseCommand("hello"), null);
  assert.equal(parseCommand(undefined), null);
});

test("webhook rejects requests without the secret token", async () => {
  const res = await webhook(message("/status"), { secret: "wrong" });
  assert.equal(res.status, 403);
  assert.equal(calls.length, 0);
});

test("answers the allowed chat with every chunk of the saved reply", async () => {
  serveReplies();
  const res = await webhook(message("/internships"));
  assert.equal(res.status, 200);
  assert.deepEqual(sent().map((m) => m.text), ["<b>Internships</b> part 1", "part 2"]);
  assert.ok(sent().every((m) => m.chat_id === "42" && m.parse_mode === "HTML"));
  const gh = calls.find((c) => c.url.includes("/contents/"));
  assert.equal(gh.url, "https://api.github.com/repos/owner/repo./contents/replies.json?ref=scout-data");
  assert.equal(gh.init.headers.Authorization, "Bearer github_pat_x");
  assert.equal(gh.init.headers.Accept, "application/vnd.github.raw+json");
});

test("aliases and unknown commands", async () => {
  serveReplies();
  await webhook(message("/programmes"));
  await webhook(message("/start"));
  await webhook(message("/whatever"));
  assert.deepEqual(sent().map((m) => m.text), ["<b>Courses</b>", "🤖 help text", "🤖 help text"]);
});

test("ignores other chats and hints on plain text", async () => {
  serveReplies();
  await webhook(message("/internships", 999));
  assert.equal(sent().length, 0);
  await webhook(message("hi"));
  assert.deepEqual(sent().map((m) => m.text), ["Send /help to see what I can do."]);
});

test("without TELEGRAM_CHAT_ID the bot replies with the sender's chat id", async () => {
  await webhook(message("/start", 777), { env: { ...ENV, TELEGRAM_CHAT_ID: "" } });
  assert.equal(sent().length, 1);
  assert.match(sent()[0].text, /<code>777<\/code>/);
});

test("explains when there are no saved results yet or the token is rejected", async () => {
  serveReplies({ message: "Not Found" }, 404);
  await webhook(message("/status"));
  assert.match(sent()[0].text, /No scan results yet/);
  routes = [];
  serveReplies({ message: "Bad credentials" }, 401);
  await webhook(message("/status"));
  assert.match(sent()[1].text, /GITHUB_TOKEN/);
});

test("falls back to plain text when Telegram rejects the HTML", async () => {
  serveReplies({ ...REPLIES, replies: { ...REPLIES.replies, status: ['<a href="https://x.y/?a=1&amp;b=2">A &amp; B</a>'] } });
  routes.push([(url, init) => url.endsWith("/sendMessage") && init.body.includes("parse_mode"),
    () => json({ ok: false, description: "Bad Request: can't parse entities" }, 400)]);
  await webhook(message("/status"));
  const texts = sent().map((m) => m.text);
  assert.equal(texts.at(-1), "A & B (https://x.y/?a=1&b=2)");
  assert.equal(stripHtml("<b>1 &lt; 2</b>"), "1 < 2");
});

test("cron starts the daily workflow with wait on and dry run off", async () => {
  routes.push([(url) => url.endsWith("/enable"), () => new Response(null, { status: 204 })]);
  routes.push([(url) => url.endsWith("/dispatches"), () => new Response(null, { status: 204 })]);
  await worker.scheduled({ cron: "45 3 * * *" }, ENV);
  const dispatch = calls.find((c) => c.url.endsWith("/dispatches"));
  assert.equal(dispatch.url, "https://api.github.com/repos/owner/repo./actions/workflows/daily.yml/dispatches");
  assert.deepEqual(dispatch.body, { ref: "main", inputs: { dry_run: "false", wait: "true" } });
  assert.equal(sent().length, 0);
});

test("cron retries GitHub server errors, then succeeds", async () => {
  let n = 0;
  routes.push([(url) => url.endsWith("/enable"), () => new Response(null, { status: 204 })]);
  routes.push([(url) => url.endsWith("/dispatches"), () => (++n < 3 ? new Response("busy", { status: 502 }) : new Response(null, { status: 204 }))]);
  await worker.scheduled({ cron: "45 3 * * *" }, ENV);
  assert.equal(n, 3);
  assert.equal(sent().length, 0);
});

test("cron reports a rejected token on Telegram once, without retrying", async () => {
  routes.push([(url) => url.includes("api.github.com"), () => new Response('{"message":"Bad credentials"}', { status: 401 })]);
  await worker.scheduled({ cron: "45 3 * * *" }, ENV);
  assert.equal(calls.filter((c) => c.url.endsWith("/dispatches")).length, 1);
  assert.equal(sent().length, 1);
  assert.match(sent()[0].text, /could not start today's digest.*401/);
});

test("setup needs the secret, registers the webhook and checks GitHub access", async () => {
  const forbidden = await worker.fetch(new Request("https://scout.example.workers.dev/setup?secret=nope"), ENV, {});
  assert.equal(forbidden.status, 403);

  routes.push([(url) => url.endsWith("/getMe"), () => json({ ok: true, result: { username: "MyScoutBot" } })]);
  routes.push([(url) => url.endsWith("/repos/owner/repo."), () => json({ full_name: "owner/repo." })]);
  routes.push([(url) => url.endsWith("/enable"), () => new Response(null, { status: 204 })]);
  serveReplies();
  const res = await worker.fetch(
    new Request(`https://scout.example.workers.dev/setup?secret=${ENV.WEBHOOK_SECRET}`), ENV, {});
  const body = await res.text();
  assert.equal(res.status, 200);
  assert.ok(!body.includes("❌"), body);
  const hook = calls.find((c) => c.url.endsWith("/setWebhook"));
  assert.deepEqual(hook.body, {
    url: "https://scout.example.workers.dev/telegram", secret_token: ENV.WEBHOOK_SECRET,
    allowed_updates: ["message"], max_connections: 5,
  });
  assert.equal(sent().length, 1); // confirmation in the chat
  assert.ok(!body.includes(ENV.TELEGRAM_BOT_TOKEN) && !body.includes(ENV.GITHUB_TOKEN));
});

test("setup lists missing settings", async () => {
  const res = await worker.fetch(
    new Request(`https://scout.example.workers.dev/setup?secret=${ENV.WEBHOOK_SECRET}`),
    { ...ENV, GITHUB_TOKEN: "" }, {});
  assert.match(await res.text(), /Missing Worker settings: GITHUB_TOKEN/);
  assert.equal(calls.length, 0);
});
