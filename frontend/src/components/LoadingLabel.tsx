import type { ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";

export function LoadingLabel({
  as = "span",
  className,
  role,
  ariaLive,
  children,
}: {
  as?: "span" | "p";
  className?: string;
  role?: string;
  ariaLive?: "polite" | "assertive";
  children: ReactNode;
}) {
  const reduceMotion = useReducedMotion();
  const pulse = reduceMotion
    ? {}
    : {
        animate: { opacity: [0.55, 1, 0.55] },
        transition: {
          duration: 1.3,
          repeat: Infinity,
          ease: "easeInOut" as const,
        },
      };
  const combinedClassName = className
    ? `loading-pulse ${className}`
    : "loading-pulse";
  if (as === "p") {
    return (
      <motion.p
        className={combinedClassName}
        role={role}
        aria-live={ariaLive}
        {...pulse}
      >
        {children}
      </motion.p>
    );
  }
  return (
    <motion.span
      className={combinedClassName}
      role={role}
      aria-live={ariaLive}
      {...pulse}
    >
      {children}
    </motion.span>
  );
}
