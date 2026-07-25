import { useEffect, useState } from "react";

import { AppNavigation } from "./components/AppNavigation";
import { CleansingReviewPage } from "./pages/CleansingReviewPage";
import { GroupingReviewPage } from "./pages/GroupingReviewPage";
import { QuoteAnalysisPage } from "./pages/QuoteAnalysisPage";
import { StandardPricesPage } from "./pages/StandardPricesPage";

function currentPathname() {
  return window.location.pathname.replace(/\/+$/, "") || "/";
}

export function App() {
  const [path, setPath] = useState(currentPathname);

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
      heading.focus();
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
    setPath(nextPath);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  let page: React.ReactNode;
  switch (path) {
    case "/":
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
      page = <QuoteAnalysisPage />;
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
      <AppNavigation currentPath={path} onNavigate={navigate} />
      {page}
    </div>
  );
}
