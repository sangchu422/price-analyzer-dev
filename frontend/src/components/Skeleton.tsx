import type { CSSProperties, ElementType } from "react";

export function Skeleton({
  as: Component = "span",
  className = "",
  width,
  height,
}: {
  as?: ElementType;
  className?: string;
  width?: CSSProperties["width"];
  height?: CSSProperties["height"];
}) {
  return (
    <Component
      className={`ui-skeleton ${className}`.trim()}
      style={{ width, height }}
      aria-hidden="true"
    />
  );
}
