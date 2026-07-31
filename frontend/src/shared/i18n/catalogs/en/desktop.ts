import type { DesktopMessageKey } from "../../keys/desktop";

export const desktopEn = {
  "desktop.localTerminal.open": "Open local terminal",
  "desktop.localTerminal.title": "Local terminal",
  "desktop.localTerminal.description": "Runs only on this desktop device. It never passes through the server.",
  "desktop.localTerminal.connecting": "Starting local shell…",
  "desktop.localTerminal.connected": "Connected",
  "desktop.localTerminal.ended": "Shell exited",
  "desktop.localTerminal.failed": "Local terminal failed",
  "desktop.localTerminal.reconnect": "Start a new shell",
  "desktop.localTerminal.close": "Close local terminal",
  "desktop.localTerminal.closeShortcut": "Alt+Esc closes",
  "desktop.localTerminal.shell": "Shell: {shell}",
  "desktop.localTerminal.failure.start": "Could not start the local shell. Start a new shell to try again.",
  "desktop.localTerminal.failure.operation": "The local terminal connection was lost. Start a new shell to continue.",
  "desktop.localTerminal.failure.native": "The local terminal reported an error. Start a new shell to continue.",
  "desktop.localTerminal.failure.exit": "The shell ended with code {exitCode}.",
  "desktop.localTerminal.unavailable": "Local terminal is not available in this environment.",
} satisfies Record<DesktopMessageKey, string>;
