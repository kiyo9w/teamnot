import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";
import os from "node:os";
import readline from "node:readline";
import { createRequire } from "node:module";

let chromium;
try {
  ({ chromium } = await import("playwright-core"));
} catch {
  const moduleBase = process.env.TEAMNOT_PLAYWRIGHT_REQUIRE_FROM
    || `file:///C:/Users/${os.userInfo().username}/OpenClawTools/browser-control.mjs`;
  const requireFromOpenClawTools = createRequire(moduleBase);
  ({ chromium } = requireFromOpenClawTools("playwright-core"));
}

const args = process.argv.slice(2);
const getArg = (name, fallback = "") => {
  const i = args.indexOf(name);
  return i >= 0 ? (args[i + 1] ?? fallback) : fallback;
};

const cdp = getArg("--cdp", process.env.TEAMNOT_CDP_URL || "http://127.0.0.1:18801");
const browserName = getArg("--browser", "chrome").toLowerCase();
const userDataDir = getArg(
  "--user-data-dir",
  path.join(os.homedir(), "OpenClawTools", "teamnot-chrome-cdp-profile"),
);
const sessionId = getArg("--session-id", `teamnot-${Date.now()}`);

let browser = null;
let context = null;
let page = null;

function write(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

function cdpVersionUrl() {
  return cdp.replace(/\/$/, "") + "/json/version";
}

async function probeCdp() {
  try {
    const res = await fetch(cdpVersionUrl(), { signal: AbortSignal.timeout(1500) });
    if (!res.ok) return { ok: false, status: res.status };
    return { ok: true, info: await res.json() };
  } catch (err) {
    return { ok: false, error: String(err?.message || err) };
  }
}

function browserExe() {
  if (browserName === "brave") return "C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe";
  if (browserName === "edge") return "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe";
  return "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
}

function cdpPort() {
  try {
    return new URL(cdp).port || "18800";
  } catch {
    return "18800";
  }
}

async function startBrowser() {
  await fs.mkdir(userDataDir, { recursive: true });
  const child = spawn(browserExe(), [
    "--remote-debugging-address=127.0.0.1",
    `--remote-debugging-port=${cdpPort()}`,
    `--user-data-dir=${userDataDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    "about:blank",
  ], { detached: true, stdio: "ignore" });
  child.unref();
  return { pid: child.pid, userDataDir };
}

async function ensureCdp() {
  let probe = await probeCdp();
  if (probe.ok) return { started: false, probe };
  const started = await startBrowser();
  const deadline = Date.now() + Number(getArg("--startup-timeout-ms", "12000"));
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    probe = await probeCdp();
    if (probe.ok) return { started, probe };
  }
  throw new Error(`Browser started but CDP did not become ready: ${JSON.stringify({ cdp, started, lastProbe: probe })}`);
}

async function ensureSession() {
  const ensured = await ensureCdp();
  if (!browser) {
    browser = await chromium.connectOverCDP(cdp);
  }
  context = browser.contexts()[0] || await browser.newContext();
  if (!page || page.isClosed()) {
    page = await context.newPage();
    await page.addInitScript((id) => {
      window.__TEAMNOT_BROWSER_SESSION_ID__ = id;
    }, sessionId).catch(() => {});
    await page.goto("about:blank", { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => {});
  }
  return ensured;
}

async function currentPage() {
  await ensureSession();
  return page;
}

async function settlePage(p, timeout = 5000) {
  await p.waitForLoadState("domcontentloaded", { timeout }).catch(() => {});
  await p.waitForLoadState("networkidle", { timeout }).catch(() => {});
  await p.waitForTimeout(500).catch(() => {});
}

async function screenshot(p, out, fullPage) {
  await fs.mkdir(path.dirname(out), { recursive: true });
  try {
    await p.screenshot({ path: out, fullPage, timeout: 30000, caret: "hide" });
    return { path: out, method: "playwright" };
  } catch (err) {
    const client = await p.context().newCDPSession(p);
    const capture = await client.send("Page.captureScreenshot", {
      format: "png",
      fromSurface: true,
      captureBeyondViewport: fullPage,
    });
    await fs.writeFile(out, Buffer.from(capture.data, "base64"));
    return {
      path: out,
      method: "cdp-fallback",
      fallbackReason: String(err?.message || err).slice(0, 300),
    };
  }
}

async function handle(command) {
  const action = command.action || "status";
  const ensured = await ensureSession();
  const p = await currentPage();

  if (action === "status") {
    const pages = browser.contexts().flatMap((ctx) => ctx.pages());
    return { ok: true, action, cdp, sessionId, dedicatedUrl: p.url(), contexts: browser.contexts().length, pages: pages.length, ensured };
  }
  if (action === "navigate") {
    if (!command.url) throw new Error("Missing url");
    await p.goto(command.url, { waitUntil: "domcontentloaded", timeout: Number(command.timeout || 30000) });
    await settlePage(p);
    return { ok: true, action, cdp, sessionId, url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "screenshot") {
    const out = command.out || path.join(process.env.TEMP || "C:\\Windows\\Temp", `teamnot-browser-${Date.now()}.png`);
    const captured = await screenshot(p, out, Boolean(command.fullPage));
    return { ok: true, action, cdp, sessionId, url: p.url(), title: await p.title().catch(() => ""), ensured, ...captured };
  }
  if (action === "viewport") {
    await p.setViewportSize({ width: Number(command.width || 390), height: Number(command.height || 844) });
    return { ok: true, action, cdp, sessionId, viewport: p.viewportSize(), url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "upload") {
    if (!command.selector) throw new Error("Missing selector");
    if (!command.file) throw new Error("Missing file");
    await p.setInputFiles(command.selector, command.file, { timeout: Number(command.timeout || 30000) });
    return { ok: true, action, cdp, sessionId, selector: command.selector, files: [command.file], url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "cookies") {
    const urls = Array.isArray(command.urls) ? command.urls : [];
    const cookies = await context.cookies(urls.length ? urls : undefined);
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      ensured,
      cookies: cookies.map((c) => ({
        name: c.name,
        domain: c.domain,
        expires: c.expires,
        httpOnly: c.httpOnly,
        secure: c.secure,
        sameSite: c.sameSite,
      })),
    };
  }
  if (action === "eval") {
    if (!command.expr) throw new Error("Missing expr");
    await settlePage(p, Number(command.settleTimeout || 3000));
    const result = await p.evaluate(command.expr);
    return { ok: true, action, cdp, sessionId, result, url: p.url(), title: await p.title().catch(() => ""), ensured };
  }
  if (action === "reset") {
    await p.goto("about:blank", { waitUntil: "domcontentloaded", timeout: 10000 });
    return { ok: true, action, cdp, sessionId };
  }
  if (action === "close") {
    if (page && !page.isClosed()) await page.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    browser = null;
    context = null;
    page = null;
    return { ok: true, action, cdp, sessionId };
  }
  throw new Error(`Unknown action: ${action}`);
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  let command = {};
  try {
    command = JSON.parse(line);
    const result = await handle(command);
    write({ id: command.id, ...result });
    if (command.action === "close") process.exit(0);
  } catch (err) {
    write({
      id: command.id,
      ok: false,
      action: command.action || "unknown",
      error: String(err?.message || err),
      stack: String(err?.stack || "").split("\n").slice(0, 6).join("\n"),
    });
  }
}
