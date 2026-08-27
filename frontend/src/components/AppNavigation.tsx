import {
  ChevronDown,
  Database,
  FileClock,
  LayoutDashboard,
  Moon,
  ScanSearch,
  Settings,
  ShieldCheck,
  Sun,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { AppTheme } from "../theme";

const destinations = [
  { path: "/dashboard", label: "종합현황", icon: LayoutDashboard },
  { path: "/standard-prices", label: "표준 DB", icon: Database },
] as const;

const trailingDestinations = [
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
  const [analysisMenuOpen, setAnalysisMenuOpen] = useState(false);
  const analysisMenuCloseTimer = useRef<number | null>(null);
  const lightThemeActive = theme === "light";
  const nextTheme = lightThemeActive ? "dark" : "light";
  const themeToggleLabel = lightThemeActive
    ? "다크 모드로 전환"
    : "라이트 모드로 전환";

  const cancelAnalysisMenuClose = () => {
    if (analysisMenuCloseTimer.current === null) return;
    window.clearTimeout(analysisMenuCloseTimer.current);
    analysisMenuCloseTimer.current = null;
  };

  const openAnalysisMenu = () => {
    cancelAnalysisMenuClose();
    setAnalysisMenuOpen(true);
  };

  const scheduleAnalysisMenuClose = () => {
    cancelAnalysisMenuClose();
    analysisMenuCloseTimer.current = window.setTimeout(() => {
      setAnalysisMenuOpen(false);
      analysisMenuCloseTimer.current = null;
    }, 220);
  };

  useEffect(() => () => cancelAnalysisMenuClose(), []);

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
        <div
          className="navigation-menu"
          onMouseEnter={openAnalysisMenu}
          onMouseLeave={scheduleAnalysisMenuClose}
          onFocusCapture={openAnalysisMenu}
          onBlurCapture={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              scheduleAnalysisMenuClose();
            }
          }}
        >
          <button
            type="button"
            className="navigation-menu-trigger"
            data-current={currentPath.startsWith("/analysis") ? "page" : undefined}
            aria-label="신규 견적 분석 메뉴"
            aria-haspopup="menu"
            aria-expanded={analysisMenuOpen}
            onClick={() => {
              cancelAnalysisMenuClose();
              setAnalysisMenuOpen((open) => !open);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                openAnalysisMenu();
              }
              if (event.key === "Escape") {
                event.preventDefault();
                setAnalysisMenuOpen(false);
              }
            }}
          >
            <ScanSearch aria-hidden="true" size={15} strokeWidth={1.7} />
            <span>신규 견적 분석</span>
            <ChevronDown className="navigation-menu-chevron" aria-hidden="true" size={13} />
          </button>
          {analysisMenuOpen ? (
            <div className="navigation-submenu" role="menu" aria-label="신규 견적 분석 메뉴">
              <a
                className="navigation-submenu-item"
                role="menuitem"
                href="/analysis"
                onClick={(event) => {
                  event.preventDefault();
                  onNavigate("/analysis");
                  setAnalysisMenuOpen(false);
                }}
              >
                <ScanSearch aria-hidden="true" size={17} strokeWidth={1.7} />
                <span><strong>새 견적 분석</strong><small>견적서를 올리고 결과 확인</small></span>
              </a>
              <a
                className="navigation-submenu-item"
                role="menuitem"
                href="/analysis/history"
                data-current={currentPath === "/analysis/history" ? "page" : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  onNavigate("/analysis/history");
                  setAnalysisMenuOpen(false);
                }}
              >
                <FileClock aria-hidden="true" size={17} strokeWidth={1.7} />
                <span><strong>분석 이력 관리</strong><small>표준 DB 반영·제외 선택</small></span>
              </a>
            </div>
          ) : null}
        </div>
        {trailingDestinations.map(({ path, label, icon: Icon }) => {
          const active = currentPath === path;
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
