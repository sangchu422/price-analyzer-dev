import {
  Database,
  LayoutDashboard,
  Moon,
  ScanSearch,
  Settings,
  ShieldCheck,
  Sun,
} from "lucide-react";

import type { AppTheme } from "../theme";

const destinations = [
  { path: "/dashboard", label: "종합현황", icon: LayoutDashboard },
  { path: "/standard-prices", label: "표준 DB", icon: Database },
  { path: "/analysis", label: "신규 견적 분석", icon: ScanSearch },
  { path: "/cleansing", label: "정제 검토", icon: ShieldCheck },
  { path: "/settings", label: "설정", icon: Settings },
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
        href="/dashboard"
        onClick={(event) => {
          event.preventDefault();
          onNavigate("/dashboard");
        }}
      >
        <span className="wordmark-copy" aria-hidden="true">
          <strong>통합 견적 분석 시스템</strong>
        </span>
        <span className="sr-only">통합 견적 분석 시스템</span>
      </a>
      <div className="navigation-cluster">
        <div className="navigation-links">
        {destinations.map(({ path, label, icon: Icon }) => {
          const active =
            currentPath === path ||
            (currentPath === "/" && path === "/dashboard");
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
