#!/usr/bin/env node

import { createServer } from "node:http";
import { spawn, spawnSync } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdir, open, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";
import { pathToFileURL } from "node:url";

const DEFAULT_AUTHHUB_URL = "https://authhub.cms1.cc/api/v1";
const DEFAULT_CALLBACK_URL = "http://localhost:10086/auth/callback";
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000;
const REFRESH_SKEW_SECONDS = 90;
const DEFAULT_AUTHHUB_TIMEOUT_MS = 60 * 1000;
const DEFAULT_MCP_TIMEOUT_MS = 120 * 1000;

let cachedCredential = null;
let cachedCredentialStamp = null;

class CredentialCorruptError extends Error {
  constructor(message, cause) {
    super(message, { cause });
    this.name = "CredentialCorruptError";
  }
}

class HttpResponseError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "HttpResponseError";
    this.status = status;
  }
}

function parseArgs(argv) {
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) {
      throw new Error("Expected --label <name> --url <upstream-url>");
    }
    values[key.slice(2)] = value;
  }
  if (!values.label || !values.url) {
    throw new Error("Both --label and --url are required");
  }
  return values;
}

function normalizeAuthHubUrl(value) {
  const trimmed = value.replace(/\/$/, "");
  return trimmed.endsWith("/api/v1") ? trimmed : `${trimmed}/api/v1`;
}

function positiveIntegerSetting(name, fallback) {
  const parsed = Number.parseInt(process.env[name] || "", 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function credentialPaths() {
  const base = process.env.LOCALAPPDATA
    ? join(process.env.LOCALAPPDATA, "Tanyue", "FireplayMcpSuite")
    : join(homedir(), ".fireplay-mcp-suite");
  return {
    directory: base,
    credential: join(base, platform() === "win32" ? "auth.dpapi" : "auth.json"),
    loginLock: join(base, "login.lock"),
    refreshLock: join(base, "refresh.lock"),
  };
}

function powershell(script, input) {
  const result = spawnSync(
    "powershell.exe",
    ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
    { input, encoding: "utf8", windowsHide: true, maxBuffer: 1024 * 1024 },
  );
  if (result.status !== 0) {
    throw new Error(`Windows credential protection failed: ${result.stderr.trim()}`);
  }
  return result.stdout.trim();
}

function protectCredential(plainText) {
  if (platform() !== "win32") return plainText;
  return powershell(
    "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();Add-Type -AssemblyName System.Security;" +
      "$v=[Console]::In.ReadToEnd();$b=[Text.Encoding]::UTF8.GetBytes($v);" +
      "$p=[Security.Cryptography.ProtectedData]::Protect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);" +
      "[Console]::Out.Write([Convert]::ToBase64String($p))",
    plainText,
  );
}

function unprotectCredential(cipherText) {
  if (platform() !== "win32") return cipherText;
  return powershell(
    "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();Add-Type -AssemblyName System.Security;" +
      "$v=[Console]::In.ReadToEnd();$b=[Convert]::FromBase64String($v);" +
      "$p=[Security.Cryptography.ProtectedData]::Unprotect($b,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser);" +
      "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($p))",
    cipherText,
  );
}

function clearCredentialCache() {
  cachedCredential = null;
  cachedCredentialStamp = null;
}

async function loadCredential(paths) {
  const resolvedPaths = paths || credentialPaths();
  const useCache = paths === undefined;
  let fileStat;
  try {
    fileStat = await stat(resolvedPaths.credential);
  } catch (error) {
    if (error?.code === "ENOENT") {
      if (useCache) clearCredentialCache();
      return null;
    }
    throw error;
  }

  const stamp = `${fileStat.dev}:${fileStat.ino}:${fileStat.mtimeMs}:${fileStat.size}`;
  if (useCache && cachedCredential && cachedCredentialStamp === stamp) {
    return cachedCredential;
  }

  const stored = await readFile(resolvedPaths.credential, "utf8");
  try {
    const credential = JSON.parse(unprotectCredential(stored));
    if (useCache) {
      cachedCredential = credential;
      cachedCredentialStamp = stamp;
    }
    return credential;
  } catch (error) {
    if (useCache) clearCredentialCache();
    throw new CredentialCorruptError("Saved AuthHub credential cannot be decrypted or parsed", error);
  }
}

async function saveCredential(credential, paths = credentialPaths()) {
  await mkdir(paths.directory, { recursive: true });
  const temporary = `${paths.credential}.${process.pid}.tmp`;
  const stored = protectCredential(JSON.stringify(credential));
  await writeFile(temporary, stored, { encoding: "utf8", mode: 0o600 });
  await rename(temporary, paths.credential);
  clearCredentialCache();
}

async function removeCredential(paths) {
  clearCredentialCache();
  await rm(paths.credential, { force: true });
}

async function quarantineCredential(paths) {
  clearCredentialCache();
  const quarantined = `${paths.credential}.corrupt-${Date.now()}`;
  try {
    await rename(paths.credential, quarantined);
    return quarantined;
  } catch (error) {
    if (error?.code === "ENOENT") return null;
    throw error;
  }
}

function tokenExpiresAt(token) {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return 0;
    const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
    return typeof payload.exp === "number" ? payload.exp : 0;
  } catch {
    return 0;
  }
}

async function fetchText(url, options, timeoutMs, label) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  timer.unref?.();
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    return { response, text };
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`${label} timed out after ${timeoutMs}ms`);
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

async function jsonRequest(url, body) {
  const { response, text } = await fetchText(
    url,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    },
    positiveIntegerSetting("FIREPLAY_AUTHHUB_TIMEOUT_MS", DEFAULT_AUTHHUB_TIMEOUT_MS),
    "AuthHub request",
  );
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`AuthHub returned a non-JSON response (${response.status})`);
  }
  if (!response.ok) {
    const detail = data.detail || data.error || response.statusText;
    throw new HttpResponseError(`AuthHub request failed (${response.status}): ${detail}`, response.status);
  }
  return data;
}

function isRefreshCredentialRejected(error) {
  return error instanceof HttpResponseError && (error.status === 400 || error.status === 401);
}

function openBrowser(url) {
  if (platform() === "win32") {
    const child = spawn("rundll32.exe", ["url.dll,FileProtocolHandler", url], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    });
    child.unref();
    return;
  }
  const command = platform() === "darwin" ? "open" : "xdg-open";
  const child = spawn(command, [url], { detached: true, stdio: "ignore" });
  child.unref();
}

async function acquireLock(path) {
  try {
    const handle = await open(path, "wx", 0o600);
    await handle.writeFile(`${process.pid}\n${Date.now()}\n`, "utf8");
    return handle;
  } catch (error) {
    if (error?.code === "EEXIST") {
      const lockStat = await stat(path).catch(() => null);
      if (lockStat && Date.now() - lockStat.mtimeMs > LOGIN_TIMEOUT_MS) {
        await rm(path, { force: true });
        return acquireLock(path);
      }
      return null;
    }
    throw error;
  }
}

async function releaseLock(handle, path) {
  if (!handle) return;
  await handle.close().catch(() => {});
  await rm(path, { force: true }).catch(() => {});
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function waitForCredential(paths, previousAccessToken = null) {
  const deadline = Date.now() + LOGIN_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const credential = await loadCredential(paths).catch(() => null);
    if (credential?.access_token && credential.access_token !== previousAccessToken) return credential;
    await delay(500);
  }
  throw new Error("Timed out waiting for AuthHub login or token refresh");
}

async function interactiveLogin(authHubUrl, callbackUrl, paths) {
  await mkdir(paths.directory, { recursive: true });
  const lock = await acquireLock(paths.loginLock);
  if (!lock) return waitForCredential(paths);

  try {
    const expectedState = randomBytes(32).toString("base64url");
    const login = await jsonRequest(`${authHubUrl}/auth/sso/login-url`, {
      redirect_uri: callbackUrl,
      state: expectedState,
    });

    const callback = new URL(callbackUrl);
    const result = new Promise((resolve, reject) => {
      const server = createServer(async (request, response) => {
        try {
          const requestUrl = new URL(request.url || "/", callback.origin);
          if (requestUrl.pathname !== callback.pathname) {
            response.writeHead(404).end("Not found");
            return;
          }
          const code = requestUrl.searchParams.get("code");
          const state = requestUrl.searchParams.get("state");
          const error = requestUrl.searchParams.get("error");
          if (error) throw new Error(`Feishu login failed: ${error}`);
          if (!code || state !== expectedState) throw new Error("Invalid AuthHub callback");

          const token = await jsonRequest(`${authHubUrl}/auth/sso/exchange-token`, { code, state });
          if (!token.access_token || !token.refresh_token) {
            throw new Error("AuthHub did not return both access_token and refresh_token");
          }
          const credential = {
            access_token: token.access_token,
            refresh_token: token.refresh_token,
            expires_at: Math.floor(Date.now() / 1000) + Number(token.expires_in || 3600),
            refresh_expires_at: Math.floor(Date.now() / 1000) + Number(token.refresh_expires_in || 2592000),
          };
          await saveCredential(credential, paths);
          response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
          response.end("<!doctype html><meta charset=utf-8><title>登录成功</title><h1>登录成功</h1><p>可以关闭此页面并返回 Codex。</p>");
          server.close(() => resolve(credential));
        } catch (error) {
          response.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
          response.end("登录失败，请返回 Codex 查看错误。");
          server.close(() => reject(error));
        }
      });
      server.on("error", reject);
      server.listen(Number(callback.port || 80), callback.hostname, () => openBrowser(login.login_url));
      server.unref();
      setTimeout(() => {
        server.close();
        reject(new Error("AuthHub login timed out"));
      }, LOGIN_TIMEOUT_MS).unref();
    });
    return await result;
  } finally {
    await releaseLock(lock, paths.loginLock);
  }
}

async function refreshCredential(authHubUrl, current, paths) {
  await mkdir(paths.directory, { recursive: true });
  const lock = await acquireLock(paths.refreshLock);
  if (!lock) return waitForCredential(paths, current.access_token);
  try {
    const token = await jsonRequest(`${authHubUrl}/auth/refresh`, {
      refresh_token: current.refresh_token,
    });
    const credential = {
      access_token: token.access_token,
      refresh_token: token.refresh_token || current.refresh_token,
      expires_at: Math.floor(Date.now() / 1000) + Number(token.expires_in || 3600),
      refresh_expires_at:
        Math.floor(Date.now() / 1000) +
        Number(token.refresh_expires_in || Math.max(0, current.refresh_expires_at - Math.floor(Date.now() / 1000))),
    };
    await saveCredential(credential, paths);
    return credential;
  } finally {
    await releaseLock(lock, paths.refreshLock);
  }
}

async function getAccessToken(authHubUrl, callbackUrl, forceRefresh = false) {
  const paths = credentialPaths();
  let credential;
  try {
    credential = await loadCredential();
  } catch (error) {
    if (!(error instanceof CredentialCorruptError)) throw error;
    const quarantined = await quarantineCredential(paths);
    process.stderr.write(
      `[fireplay-mcp-suite] damaged AuthHub credential isolated${quarantined ? ` at ${quarantined}` : ""}; login is required\n`,
    );
    credential = null;
  }
  if (!credential?.access_token || !credential?.refresh_token) {
    credential = await interactiveLogin(authHubUrl, callbackUrl, paths);
  }

  const now = Math.floor(Date.now() / 1000);
  const expiresAt = tokenExpiresAt(credential.access_token) || credential.expires_at || 0;
  if (forceRefresh || expiresAt <= now + REFRESH_SKEW_SECONDS) {
    try {
      credential = await refreshCredential(authHubUrl, credential, paths);
    } catch (error) {
      const refreshExpired = (credential.refresh_expires_at || 0) <= now;
      if (!refreshExpired && !isRefreshCredentialRejected(error)) throw error;
      await removeCredential(paths).catch(() => {});
      credential = await interactiveLogin(authHubUrl, callbackUrl, paths);
    }
  }
  return credential.access_token;
}

function emit(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function emitError(message, error) {
  if (message?.id === undefined) {
    process.stderr.write(`[fireplay-mcp-suite] ${error.message}\n`);
    return;
  }
  emit({
    jsonrpc: "2.0",
    id: message.id,
    error: { code: -32603, message: error.message || "MCP proxy error" },
  });
}

function parseSse(text) {
  const messages = [];
  for (const block of text.split(/\r?\n\r?\n/)) {
    const data = block
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data || data === "[DONE]") continue;
    messages.push(JSON.parse(data));
  }
  return messages;
}

async function runProxy({ label, url }) {
  const authHubUrl = normalizeAuthHubUrl(process.env.FIREPLAY_AUTHHUB_URL || DEFAULT_AUTHHUB_URL);
  const callbackUrl = process.env.FIREPLAY_AUTH_CALLBACK_URL || DEFAULT_CALLBACK_URL;
  let sessionId = null;

  async function forward(message, retry = true) {
    const token = await getAccessToken(authHubUrl, callbackUrl);
    const headers = {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    };
    if (sessionId) headers["Mcp-Session-Id"] = sessionId;

    const { response, text } = await fetchText(
      url,
      {
        method: "POST",
        headers,
        body: JSON.stringify(message),
      },
      positiveIntegerSetting("FIREPLAY_MCP_REQUEST_TIMEOUT_MS", DEFAULT_MCP_TIMEOUT_MS),
      `${label} MCP request`,
    );
    if (response.status === 401 && retry) {
      await getAccessToken(authHubUrl, callbackUrl, true);
      return forward(message, false);
    }
    if (!response.ok && response.status !== 202) {
      const detail = text.slice(0, 500);
      throw new Error(`${label} MCP returned HTTP ${response.status}${detail ? `: ${detail}` : ""}`);
    }

    sessionId = response.headers.get("mcp-session-id") || sessionId;
    if (response.status === 202 || response.status === 204) return;
    if (!text) return;
    const contentType = response.headers.get("content-type") || "";
    const replies = contentType.includes("text/event-stream") ? parseSse(text) : [JSON.parse(text)];
    for (const reply of replies) emit(reply);
  }

  process.stderr.write(`[fireplay-mcp-suite] ${label} -> ${url}\n`);
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of input) {
    if (!line.trim()) continue;
    let message;
    try {
      message = JSON.parse(line);
      await forward(message);
    } catch (error) {
      emitError(message, error);
    }
  }
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  runProxy(parseArgs(process.argv.slice(2))).catch((error) => {
    process.stderr.write(`[fireplay-mcp-suite] fatal: ${error.message}\n`);
    process.exitCode = 1;
  });
}

export {
  CredentialCorruptError,
  HttpResponseError,
  fetchText,
  isRefreshCredentialRejected,
  loadCredential,
  normalizeAuthHubUrl,
  parseArgs,
  parseSse,
  saveCredential,
  tokenExpiresAt,
};
