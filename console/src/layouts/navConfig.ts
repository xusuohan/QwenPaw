/** Menu keys toggled by the sidebar “advanced menus” control. */
export const ADVANCED_NAV_KEYS = [
  "heartbeat",
  "workspace",
  "tools",
  "agent-config",
  "backups",
  "voice-transcription",
  "debug",
] as const;

export type AdvancedNavKey = (typeof ADVANCED_NAV_KEYS)[number];

export const ADVANCED_NAV_STORAGE_KEY = "qwenpaw_show_advanced_nav";

export function readShowAdvancedNav(): boolean {
  try {
    return localStorage.getItem(ADVANCED_NAV_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

export function writeShowAdvancedNav(show: boolean): void {
  try {
    localStorage.setItem(ADVANCED_NAV_STORAGE_KEY, String(show));
  } catch {
    // ignore quota / private mode
  }
}
