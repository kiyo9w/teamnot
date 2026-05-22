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

const userDataDir = getArg(
  "--user-data-dir",
  path.join(os.homedir(), "OpenClawTools", `teamnot-chrome-cdp-profile-${cdpPort()}`),
);

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
    browser = await chromium.connectOverCDP(cdp, {
      timeout: Number(getArg("--connect-timeout-ms", "15000")),
    });
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
  await p.waitForFunction(() => {
    const root = document.querySelector("#root, [data-reactroot], main, body");
    const text = (root?.innerText || document.body?.innerText || "").trim();
    return document.readyState !== "loading" && text.length > 0;
  }, { timeout }).catch(() => {});
  await p.waitForTimeout(750).catch(() => {});
}

async function screenshot(p, out, fullPage) {
  await fs.mkdir(path.dirname(out), { recursive: true });
  let retryCount = 0;
  try {
    await p.screenshot({ path: out, fullPage, timeout: 30000, caret: "hide" });
    return { path: out, method: "playwright", retryCount };
  } catch (err) {
    retryCount += 1;
    await settlePage(p, 3000);
    try {
      await p.screenshot({ path: out, fullPage, timeout: 15000, caret: "hide", animations: "disabled" });
      return {
        path: out,
        method: "playwright-retry",
        retryCount,
        fallbackReason: String(err?.message || err).slice(0, 300),
      };
    } catch (retryErr) {
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
        retryCount,
        failedPrimitive: "playwright.screenshot",
        fallbackReason: String(retryErr?.message || retryErr).slice(0, 300),
      };
    }
  }
}

async function importStorageState(command) {
  if (!command.path) throw new Error("Missing path");
  let parsed;
  try {
    parsed = JSON.parse(await fs.readFile(command.path, "utf8"));
  } catch (err) {
    return {
      ok: false,
      action: "importStorageState",
      cdp,
      sessionId,
      unsupportedBlocker: `storageState could not be read safely: ${String(err?.message || err).slice(0, 240)}`,
    };
  }
  const cookies = Array.isArray(parsed.cookies) ? parsed.cookies : [];
  if (cookies.length) await context.addCookies(cookies);
  const origins = Array.isArray(parsed.origins) ? parsed.origins : [];
  for (const origin of origins) {
    if (!origin.origin || !Array.isArray(origin.localStorage)) continue;
    await pGotoOrigin(origin.origin);
    await page.evaluate((entries) => {
      for (const entry of entries) localStorage.setItem(entry.name, entry.value);
    }, origin.localStorage).catch(() => {});
  }
  return {
    ok: true,
    action: "importStorageState",
    cdp,
    sessionId,
    seededStateApplied: true,
    cookiesApplied: cookies.length,
    localStorageOriginsApplied: origins.length,
    url: page?.url() || "",
  };
}

async function pGotoOrigin(origin) {
  const p = await currentPage();
  await p.goto(origin, { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => {});
}

async function handle(command) {
  const action = command.action || "status";
  const ensured = await ensureSession();
  const p = await currentPage();

  if (action === "status") {
    const pages = browser.contexts().flatMap((ctx) => ctx.pages());
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: userDataDir,
      dedicatedUrl: p.url(),
      contexts: browser.contexts().length,
      pages: pages.length,
      ensured,
    };
  }
  if (action === "navigate") {
    if (!command.url) throw new Error("Missing url");
    await p.goto(command.url, { waitUntil: "domcontentloaded", timeout: Number(command.timeout || 30000) });
    await settlePage(p);
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: userDataDir,
      url: p.url(),
      title: await p.title().catch(() => ""),
      pages: browser.contexts().flatMap((ctx) => ctx.pages()).length,
      ensured,
    };
  }
  if (action === "screenshot") {
    const out = command.out || path.join(process.env.TEMP || "C:\\Windows\\Temp", `teamnot-browser-${Date.now()}.png`);
    const captured = await screenshot(p, out, Boolean(command.fullPage));
    return {
      ok: true,
      action,
      cdp,
      cdpPort: Number(cdpPort()),
      sessionId,
      profileDir: userDataDir,
      url: p.url(),
      title: await p.title().catch(() => ""),
      ensured,
      ...captured,
    };
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
  if (action === "importStorageState") {
    return await importStorageState(command);
  }
  if (action === "setCookies") {
    const cookies = Array.isArray(command.cookies) ? command.cookies : [];
    if (!cookies.length) {
      return { ok: false, action, cdp, sessionId, unsupportedBlocker: "No cookies were provided." };
    }
    await context.addCookies(cookies);
    return { ok: true, action, cdp, sessionId, seededStateApplied: true, cookiesApplied: cookies.length, url: p.url() };
  }
  if (action === "setLocalStorage") {
    const entries = Array.isArray(command.entries) ? command.entries : [];
    let applied = 0;
    for (const entry of entries) {
      if (!entry.origin || !entry.values || typeof entry.values !== "object") continue;
      await p.goto(entry.origin, { waitUntil: "domcontentloaded", timeout: 10000 }).catch(() => {});
      await p.evaluate((values) => {
        for (const [key, value] of Object.entries(values)) localStorage.setItem(key, String(value));
      }, entry.values).catch(() => {});
      applied += Object.keys(entry.values).length;
    }
    return { ok: true, action, cdp, sessionId, seededStateApplied: applied > 0, localStorageValuesApplied: applied, url: p.url() };
  }
  if (action === "loginHint") {
    return {
      ok: true,
      action,
      cdp,
      sessionId,
      seededStateApplied: false,
      loginHintRecorded: true,
      email: command.email || "",
      loginUrl: command.loginUrl || "",
      workspaceId: command.workspaceId || "",
      unsupportedBlocker: "loginHint records account metadata only; automated credential entry is intentionally not performed.",
      url: p.url(),
    };
  }
  if (action === "login") {
    return await performLogin(command);
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

async function performLogin(command) {
  if (!command.email) throw new Error("Missing email");
  if (!command.password) throw new Error("Missing password");
  const p = await currentPage();
  const targetUrl = command.loginUrl || p.url();
  await p.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: Number(command.timeout || 30000) });
  await settlePage(p);
  const beforeUrl = p.url();
  const beforeTitle = await p.title().catch(() => "");
  const filled = await p.evaluate(({ email, password }) => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const inputs = Array.from(document.querySelectorAll("input,textarea")).filter(visible);
    const labelFor = (el) => [
      el.getAttribute("type") || "",
      el.getAttribute("name") || "",
      el.getAttribute("id") || "",
      el.getAttribute("autocomplete") || "",
      el.getAttribute("placeholder") || "",
      el.getAttribute("aria-label") || "",
    ].join(" ").toLowerCase();
    const emailInput = inputs.find((el) => {
      const label = labelFor(el);
      return label.includes("email") || label.includes("username") || el.type === "email";
    }) || inputs[0];
    const passwordInput = inputs.find((el) => {
      const label = labelFor(el);
      return label.includes("password") || el.type === "password";
    });
    const setValue = (el, value) => {
      if (!el) return false;
      el.focus();
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    };
    return {
      emailFilled: setValue(emailInput, email),
      passwordFilled: setValue(passwordInput, password),
      inputCount: inputs.length,
    };
  }, { email: command.email, password: command.password });
  if (!filled.emailFilled || !filled.passwordFilled) {
    return {
      ok: false,
      action: "login",
      cdp,
      sessionId,
      seededStateApplied: false,
      unsupportedBlocker: `Login form could not be filled: emailFilled=${filled.emailFilled}, passwordFilled=${filled.passwordFilled}, inputCount=${filled.inputCount}`,
      url: p.url(),
      title: await p.title().catch(() => ""),
    };
  }
  const submitted = await p.evaluate(() => {
    const visible = (el) => {
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
    };
    const controls = Array.from(document.querySelectorAll("button,input[type=submit],input[type=button],[role=button]")).filter(visible);
    const submitter = controls.find((el) => {
      const text = `${el.innerText || el.value || ""} ${el.getAttribute("aria-label") || ""}`.toLowerCase();
      return /log.?in|sign.?in|continue|submit|start|register|join/.test(text);
    }) || controls[0];
    if (submitter) {
      submitter.click();
      return { clicked: true, label: submitter.innerText || submitter.value || submitter.getAttribute("aria-label") || "" };
    }
    const form = document.querySelector("form");
    if (form) {
      form.requestSubmit ? form.requestSubmit() : form.submit();
      return { clicked: false, submittedForm: true };
    }
    return { clicked: false, submittedForm: false };
  });
  await p.waitForLoadState("domcontentloaded", { timeout: Number(command.timeout || 30000) }).catch(() => {});
  await p.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
  await p.waitForTimeout(1000).catch(() => {});
  const afterUrl = p.url();
  const afterTitle = await p.title().catch(() => "");
  const authState = await p.evaluate(() => {
    const text = (document.body?.innerText || "").slice(0, 3000);
    const passwordInputs = document.querySelectorAll('input[type="password"]').length;
    return {
      textSample: text,
      passwordInputs,
      hasLogout: /log out|logout|sign out|signout/i.test(text),
      hasDashboardSignal: /dashboard|settings|account|workspace|profile|team|billing|project|admin/i.test(text),
      hasError: /invalid|incorrect|required|error|failed|try again/i.test(text),
    };
  }).catch(() => ({}));
  const successUrl = command.successUrl || "";
  const reachedSuccessUrl = successUrl && afterUrl.startsWith(successUrl);
  const urlChanged = beforeUrl !== afterUrl;
  const seededStateApplied = Boolean(
    reachedSuccessUrl
    || (urlChanged && !authState.hasError)
    || authState.hasLogout
    || (authState.hasDashboardSignal && authState.passwordInputs === 0)
  );
  return {
    ok: seededStateApplied,
    action: "login",
    cdp,
    sessionId,
    seededStateApplied,
    loginAttempted: true,
    loginUrl: targetUrl,
    beforeUrl,
    afterUrl,
    beforeTitle,
    afterTitle,
    submitted,
    authState,
    workspaceId: command.workspaceId || "",
    unsupportedBlocker: seededStateApplied ? "" : "Login was attempted but authenticated state could not be verified.",
  };
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
if (browser) {
  await browser.close().catch(() => {});
}
