import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createServer, type Server, type Socket } from "node:net";
import { randomUUID } from "node:crypto";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

type Status = "IDLE" | "WORKING" | "WAITING_APPROVAL" | "COMPLETED" | "ERROR";
type Platform = "wsl" | "windows" | "linux" | "unsupported";
type ClientMessage = {
  type?: string;
  protocolVersion?: number;
  instanceId?: string;
  asset?: string;
  status?: string;
  clip?: string;
  row?: number;
  frame?: number;
  frames?: number;
  intervalMs?: number;
  fps?: number;
  bubble?: boolean;
  height?: number;
  scale?: number;
  reason?: string;
};

const PROTOCOL_VERSION = 1;
const PORT = Number(process.env.PI_HUD_PORT || 38741);
const COMPLETED_MS = Number(process.env.PI_HUD_COMPLETED_MS || 4_000);
const SHUTDOWN_GRACE_MS = 750;
const RESTART_DELAY_MS = 400;
const LAUNCH_THROTTLE_MS = 1_000;

function detectPlatform(): Platform {
  if (process.platform === "win32") return "windows";
  if (process.platform !== "linux") return "unsupported";
  try {
    const version = readFileSync("/proc/version", "utf8").toLowerCase();
    if (process.env.WSL_INTEROP || version.includes("microsoft") || version.includes("wsl")) return "wsl";
  } catch { /* non-Linux test/runtime */ }
  return "linux";
}

const delay = (milliseconds: number) => new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

export default function (pi: ExtensionAPI) {
  const instanceId = randomUUID();
  const currentPlatform = detectPlatform();
  const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
  const clientPath = join(packageRoot, "clients", "hud_client.py");
  const peers = new Set<Socket>();
  const verifiedPeers = new Set<Socket>();
  let server: Server | undefined;
  let client: ChildProcess | undefined;
  let status: Status = "IDLE";
  let visible = true;
  let lastVerifiedHeartbeat = 0;
  let lastLaunchAt = 0;
  let lastLaunchError = "none";
  let healthTimer: NodeJS.Timeout | undefined;
  let completedTimer: NodeJS.Timeout | undefined;
  let stopPromise: Promise<void> | undefined;
  let isStopping = false;
  let sessionClosing = false;
  let phase = "not started";
  let diagnostic = "not started";
  let petDiagnostic = "client has not reported pet diagnostics";

  const serialize = (message: object) => `${JSON.stringify({ protocolVersion: PROTOCOL_VERSION, instanceId, ...message })}\n`;
  const send = (message: object) => {
    const line = serialize(message);
    for (const peer of verifiedPeers) if (!peer.destroyed) peer.write(line);
  };
  const sendState = (socket: Socket) => socket.write(serialize({ type: "state", status, visible, timestamp: Date.now() }));
  const setStatus = (next: Status, detail?: string) => {
    if (completedTimer) clearTimeout(completedTimer);
    completedTimer = undefined;
    status = next;
    send({ type: "status", status, detail, visible, timestamp: Date.now() });
    if (next === "COMPLETED") {
      completedTimer = setTimeout(() => setStatus("IDLE"), COMPLETED_MS);
      completedTimer.unref();
    }
  };

  const getLauncher = (): { command?: string; args: string[]; reason?: string } => {
    if (!existsSync(clientPath)) return { args: [], reason: `HUD client is missing: ${clientPath}` };
    if (currentPlatform === "wsl") {
      const command = process.env.PI_HUD_PYW || "/mnt/c/Windows/pyw.exe";
      return existsSync(command)
        ? { command, args: ["-3", clientPath, "--port", String(PORT), "--platform", "windows"] }
        : { args: [], reason: `Windows pyw.exe was not found: ${command}` };
    }
    if (currentPlatform === "windows") {
      return { command: process.env.PI_HUD_PYW || "pyw.exe", args: ["-3", clientPath, "--port", String(PORT), "--platform", "windows"] };
    }
    if (currentPlatform === "linux") {
      const command = process.env.HUD_PYTHON || "python3";
      return spawnSync(command, ["-c", "import PySide6"], { stdio: "ignore" }).status === 0
        ? { command, args: [clientPath, "--port", String(PORT), "--platform", "linux"] }
        : { args: [], reason: `PySide6 is unavailable. Run: ${command} -m pip install --user PySide6` };
    }
    return { args: [], reason: `Unsupported platform: ${process.platform}` };
  };

  const launchClient = () => {
    if (isStopping || verifiedPeers.size > 0 || process.env.PI_HUD_NO_AUTOSTART === "1") return;
    const now = Date.now();
    if (now - lastLaunchAt < LAUNCH_THROTTLE_MS) return;
    lastLaunchAt = now;
    const launcher = getLauncher();
    if (!launcher.command) {
      lastLaunchError = launcher.reason ?? "no GUI launcher";
      diagnostic = lastLaunchError;
      phase = "launch failed";
      return;
    }
    try {
      phase = "launching client";
      const launched = spawn(launcher.command, launcher.args, { detached: true, stdio: "ignore", windowsHide: true });
      client = launched;
      launched.once("error", (error) => {
        lastLaunchError = error.message;
        diagnostic = `client launch failed: ${error.message}`;
        phase = "launch failed";
        if (client === launched) client = undefined;
      });
      launched.once("exit", (code, signal) => {
        if (client === launched) client = undefined;
        if (verifiedPeers.size === 0 && !isStopping) {
          lastLaunchError = `client exited code=${code ?? "null"}, signal=${signal ?? "none"}`;
          diagnostic = lastLaunchError;
          phase = "client exited; retry pending";
        }
      });
      launched.unref();
      lastLaunchError = "none";
      diagnostic = `launcher=${launcher.command}`;
    } catch (error) {
      lastLaunchError = String(error);
      diagnostic = `client launch failed: ${lastLaunchError}`;
      phase = "launch failed";
    }
  };

  const ensureHealthTimer = () => {
    if (healthTimer) return;
    healthTimer = setInterval(() => {
      if (server && !isStopping && verifiedPeers.size === 0) launchClient();
    }, 2_000);
    healthTimer.unref();
  };

  const start = () => {
    if (server || isStopping) return;
    phase = "binding server";
    const next = createServer((socket) => {
      peers.add(socket);
      socket.setEncoding("utf8");
      let buffer = "";
      socket.on("data", (chunk: string) => {
        buffer += chunk;
        for (;;) {
          const boundary = buffer.indexOf("\n");
          if (boundary < 0) break;
          const line = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 1);
          try {
            const message = JSON.parse(line) as ClientMessage;
            if (message.protocolVersion !== PROTOCOL_VERSION) continue;
            if (message.type === "hello") {
              verifiedPeers.add(socket);
              lastVerifiedHeartbeat = Date.now();
              phase = "running";
              lastLaunchError = "none";
              sendState(socket);
            } else if (message.type === "heartbeat" && verifiedPeers.has(socket)) {
              lastVerifiedHeartbeat = Date.now();
              socket.write(serialize({ type: "heartbeat_ack", timestamp: lastVerifiedHeartbeat }));
            } else if (message.type === "diagnostic" && verifiedPeers.has(socket)) {
              const animation = Number.isInteger(message.row) && Number.isInteger(message.frame) && Number.isInteger(message.frames)
                ? `clip=${message.clip ?? "unknown"}, row=${message.row}, frame=${message.frame}/${message.frames}, ${message.fps ?? "?"}fps (${message.intervalMs ?? "?"}ms)`
                : "animation unknown";
              const size = Number.isFinite(message.height) ? `height=${message.height}px, scale=${message.scale ?? "?"}` : "size unknown";
              petDiagnostic = `asset=${message.asset ?? "unknown"}; status=${message.status ?? "unknown"}; ${animation}; ${size}; bubble=${message.bubble ? "shown" : "hidden"}${message.reason ? ` (${message.reason})` : ""}`;
            }
          } catch { /* Ignore malformed local messages. */ }
        }
      });
      const removePeer = () => {
        peers.delete(socket);
        verifiedPeers.delete(socket);
        if (!isStopping && verifiedPeers.size === 0 && server) phase = "client disconnected; retry pending";
      };
      socket.on("close", removePeer);
      socket.on("error", removePeer);
    });
    server = next;
    next.once("listening", () => {
      phase = "server listening";
      diagnostic = `listening on 127.0.0.1:${PORT}`;
      launchClient();
    });
    next.once("error", (error) => {
      diagnostic = error.code === "EADDRINUSE"
        ? `port ${PORT} is already used by another Pi/HUD instance`
        : `server error: ${error.message}`;
      lastLaunchError = diagnostic;
      phase = "server failed";
      if (server === next) server = undefined;
    });
    next.listen(PORT, "127.0.0.1");
    ensureHealthTimer();
  };

  const stop = (): Promise<void> => {
    if (stopPromise) return stopPromise;
    stopPromise = (async () => {
      isStopping = true;
      phase = "stopping";
      if (completedTimer) clearTimeout(completedTimer);
      completedTimer = undefined;
      if (healthTimer) clearInterval(healthTimer);
      healthTimer = undefined;

      const sockets = [...peers];
      const shutdownLine = serialize({ type: "shutdown", timestamp: Date.now() });
      const closed = sockets.map((peer) => new Promise<void>((resolve) => {
        if (peer.destroyed) {
          resolve();
          return;
        }
        peer.once("close", resolve);
        peer.end(shutdownLine);
      }));
      if (closed.length) await Promise.race([Promise.all(closed).then(() => undefined), delay(SHUTDOWN_GRACE_MS)]);
      for (const peer of sockets) if (!peer.destroyed) peer.destroy();
      peers.clear();
      verifiedPeers.clear();

      const closingServer = server;
      server = undefined;
      if (closingServer) {
        await new Promise<void>((resolve) => {
          let finished = false;
          const finish = () => {
            if (finished) return;
            finished = true;
            resolve();
          };
          const fallback = setTimeout(finish, SHUTDOWN_GRACE_MS);
          fallback.unref();
          try {
            closingServer.close(() => {
              clearTimeout(fallback);
              finish();
            });
          } catch {
            clearTimeout(fallback);
            finish();
          }
        });
      }
      client = undefined;
      phase = "stopped";
      diagnostic = "stopped cleanly";
    })().finally(() => { stopPromise = undefined; });
    return stopPromise;
  };

  const restart = async () => {
    phase = "restart requested";
    await stop();
    phase = "waiting for client mutex release";
    await delay(RESTART_DELAY_MS);
    if (sessionClosing) return;
    isStopping = false;
    phase = "restarting";
    start();
  };

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui" && ctx.mode !== "rpc") return;
    sessionClosing = false;
    isStopping = false;
    start();
  });
  pi.on("turn_start", () => setStatus("WORKING"));
  pi.on("tool_execution_start", (event) => setStatus("WORKING", event.toolName));
  pi.on("tool_execution_end", (event) => { if (event.isError) setStatus("ERROR", event.toolName); });
  pi.on("turn_end", (event) => setStatus(event.toolResults.some((result) => result.isError) ? "ERROR" : "COMPLETED"));
  pi.on("agent_settled", () => { if (status !== "ERROR") setStatus("COMPLETED"); });
  pi.on("after_provider_response", (event) => { if (event.status >= 400) setStatus("ERROR", `HTTP ${event.status}`); });
  pi.on("session_shutdown", async () => {
    sessionClosing = true;
    await stop();
  });

  pi.registerCommand("hud", {
    description: "Control HUD: /hud [toggle|show|hide|restart|status|waiting|idle]",
    handler: async (args, ctx) => {
      const action = args.trim().toLowerCase() || "toggle";
      if (action === "restart") {
        await restart();
        ctx.ui.notify("HUD restart started; the desktop pet may take a moment to reconnect.", "info");
        return;
      }
      if (action === "waiting") {
        if (!server && !isStopping) start();
        setStatus("WAITING_APPROVAL");
        return;
      }
      if (action === "idle") {
        if (!server && !isStopping) start();
        setStatus("IDLE");
        return;
      }
      if (!server && !isStopping) start();
      if (action === "status") {
        const heartbeat = lastVerifiedHeartbeat ? `${Math.round((Date.now() - lastVerifiedHeartbeat) / 1000)}s ago` : "not connected";
        const launched = lastLaunchAt ? `${Math.round((Date.now() - lastLaunchAt) / 1000)}s ago` : "never";
        ctx.ui.notify(
          `HUD ${instanceId.slice(0, 8)}: ${currentPlatform}, phase=${phase}, port=${PORT}, server=${server ? "bound" : "closed"}, verified=${verifiedPeers.size}, heartbeat=${heartbeat}, lastLaunch=${launched}, launchError=${lastLaunchError}; ${diagnostic}; pet: ${petDiagnostic}`,
          "info",
        );
        return;
      }
      if (action === "toggle") visible = !visible;
      else if (action === "show") visible = true;
      else if (action === "hide") visible = false;
      else {
        ctx.ui.notify("Usage: /hud [toggle|show|hide|restart|status|waiting|idle]", "warning");
        return;
      }
      send({ type: "visibility", visible });
      if (visible && verifiedPeers.size === 0) launchClient();
    },
  });
}
