const destinations = [
  { path: "/cleansing", label: "정제 검토" },
  { path: "/grouping", label: "품목 그룹핑" },
  { path: "/standard-prices", label: "표준단가" },
  { path: "/analysis", label: "견적 비교" },
] as const;

export function AppNavigation({
  currentPath,
  onNavigate,
}: {
  currentPath: string;
  onNavigate: (path: string) => void;
}) {
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
        <span aria-hidden="true">P</span>
        <strong>Price Analyzer</strong>
      </a>
      <div className="navigation-links">
        {destinations.map(({ path, label }) => {
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
              {label}
            </a>
          );
        })}
      </div>
    </nav>
  );
}
