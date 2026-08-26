import { lazy, Suspense, useEffect, useState } from "react";

import { AppNavigation } from "./components/AppNavigation";
import { CleansingReviewPage } from "./pages/CleansingReviewPage";
import { GroupingReviewPage } from "./pages/GroupingReviewPage";
import { QuoteAnalysisPage } from "./pages/QuoteAnalysisPage";
import { SettingsPage } from "./pages/SettingsPage";
import { useQuoteAnalysisWorkflowState } from "./state/quoteAnalysisState";
import { applyTheme, getStoredTheme, type AppTheme } from "./theme";

function currentPathname() {
  return window.location.pathname.replace(/\/+$/, "") || "/";
}

let dashboardIntroPlayed = false;

const dashboardPagePromise = import("./pages/DashboardPage");
const DashboardPage = lazy(() => dashboardPagePromise.then(
  (module) => ({ default: module.DashboardPage }),
));
const StandardPricesPage = lazy(() => import("./pages/StandardPricesPage").then(
  (module) => ({ default: module.StandardPricesPage }),
));
const WiaLogoIntro = lazy(() => import("./components/WiaLogoIntro").then(
  (module) => ({ default: module.WiaLogoIntro }),
));

function DashboardIntroFallback() {
  return (
    <div className="wia-intro" role="status" aria-label="Price Analyzer 시작 화면을 준비하는 중">
      <div className="wia-intro-mark">
        <img src="/brand/hyundai-wia.png" alt="HYUNDAI WIA" />
      </div>
      <p>PROCUREMENT INTELLIGENCE</p>
    </div>
  );
}

export function App() {
  const [path, setPath] = useState(currentPathname);
  const [theme, setTheme] = useState<AppTheme>(getStoredTheme);
  const quoteAnalysisWorkflow = useQuoteAnalysisWorkflowState();
  const [showDashboardIntro, setShowDashboardIntro] = useState(
    () => (path === "/" || path === "/dashboard") && !dashboardIntroPlayed,
  );

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const handlePopState = () => setPath(currentPathname());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    const focusHeading = () => {
      const heading = document.querySelector<HTMLElement>("main h1");
      if (!heading) return false;
      heading.tabIndex = -1;
      heading.focus({ preventScroll: true });
      return true;
    };
    if (focusHeading()) return;
    const observer = new MutationObserver(() => {
      if (focusHeading()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [path]);

  const navigate = (nextPath: string) => {
    if (nextPath === path) return;
    window.history.pushState({}, "", nextPath);
    setPath(currentPathname());
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
  };

  let page: React.ReactNode;
  switch (path) {
    case "/":
    case "/dashboard":
      page = <DashboardPage onNavigate={navigate} />;
      break;
    case "/cleansing":
      page = <CleansingReviewPage />;
      break;
    case "/grouping":
      page = <GroupingReviewPage />;
      break;
    case "/standard-prices":
      page = <StandardPricesPage />;
      break;
    case "/analysis":
      page = <QuoteAnalysisPage workflow={quoteAnalysisWorkflow} />;
      break;
    case "/settings":
      page = <SettingsPage />;
      break;
    default:
      page = (
        <main className="workspace-state" role="status">
          <p>요청한 작업 화면을 찾을 수 없습니다.</p>
          <button type="button" onClick={() => navigate("/cleansing")}>
            정제 검토로 이동
          </button>
        </main>
      );
  }

  return (
    <div className="application-frame">
      <AppNavigation
        currentPath={path}
        onNavigate={navigate}
        theme={theme}
        onThemeChange={setTheme}
      />
      <Suspense fallback={<main className="workspace-state" role="status">화면을 준비하고 있습니다.</main>}>
        <div className="route-stage" key={path}>
          {page}
        </div>
      </Suspense>
      {showDashboardIntro ? (
        <Suspense fallback={<DashboardIntroFallback />}>
          <WiaLogoIntro
            onComplete={() => {
              dashboardIntroPlayed = true;
              setShowDashboardIntro(false);
            }}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
