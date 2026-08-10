import { Database, Moon, ScanSearch, ShieldCheck, Sun } from "lucide-react";

import type { AppTheme } from "../theme";

const destinations = [
  { path: "/cleansing", label: "정제 검토", icon: ShieldCheck },
  { path: "/standard-prices", label: "표준 DB", icon: Database },
  { path: "/analysis", label: "신규 견적 분석", icon: ScanSearch },
] as const;

export function AppNavigation({
  currentPath,
  onNavigate,
  theme,
  onThemeChange,
}: {
  currentPath: string;
  onNavigate: (path: string) => void;
  theme: AppTheme;
  onThemeChange: (theme: AppTheme) => void;
}) {
  const lightThemeActive = theme === "light";
  const nextTheme = lightThemeActive ? "dark" : "light";
  const themeToggleLabel = lightThemeActive
    ? "다크 모드로 전환"
    : "라이트 모드로 전환";

  return (
    <nav className="app-navigation" aria-label="주요 작업">
      <a
        className="app-wordmark"
        href="/cleansing"
        onClick={(event) => {
          event.preventDefault();
          onNavigate("/cleansing");
        }}
      >
        <span className="wordmark-copy" aria-hidden="true">
          <strong>PRICE</strong>
          <i>/</i>
          <strong>ANALYZER</strong>
        </span>
        <span className="sr-only">Price Analyzer 견적 적정성 분석</span>
      </a>
      <div className="navigation-cluster">
        <span className="runtime-status"><i aria-hidden="true" /> LOCAL MODE</span>
        <div className="navigation-links">
        {destinations.map(({ path, label, icon: Icon }) => {
          const active =
            currentPath === path ||
            (currentPath === "/" && path === "/cleansing");
          return (
            <a
              key={path}
              href={path}
              aria-current={active ? "page" : undefined}
              onClick={(event) => {
                event.preventDefault();
                onNavigate(path);
              }}
            >
              <Icon aria-hidden="true" size={15} strokeWidth={1.7} />
              {label}
            </a>
          );
        })}
        </div>
        <button
          className="theme-toggle"
          type="button"
          aria-label={themeToggleLabel}
          aria-pressed={lightThemeActive}
          title={themeToggleLabel}
          onClick={() => onThemeChange(nextTheme)}
        >
          {lightThemeActive ? (
            <Moon aria-hidden="true" size={16} strokeWidth={1.8} />
          ) : (
            <Sun aria-hidden="true" size={16} strokeWidth={1.8} />
          )}
        </button>
      </div>
    </nav>
  );
}
