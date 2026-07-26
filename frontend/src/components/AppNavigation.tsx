const destinations = [
  { path: "/cleansing", label: "정제 검토" },
  { path: "/standard-prices", label: "표준 DB" },
  { path: "/analysis", label: "신규 견적 분석" },
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
        <span aria-hidden="true">PA</span>
        <span className="wordmark-copy">
          <strong>Price Analyzer</strong>
          <small>견적 적정성 분석</small>
        </span>
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
