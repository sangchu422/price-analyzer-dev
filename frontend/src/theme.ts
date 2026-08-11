export const THEME_STORAGE_KEY = "price-analyzer-theme";

export type AppTheme = "dark" | "light";

export function getStoredTheme(): AppTheme {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY) === "dark"
      ? "dark"
      : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(theme: AppTheme) {
  document.documentElement.dataset.theme = theme;

  const themeColor = document.querySelector('meta[name="theme-color"]');
  themeColor?.setAttribute("content", theme === "light" ? "#f4f7f6" : "#080c0d");

  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage can be unavailable in a restricted browser context. The active theme still applies.
  }
}
