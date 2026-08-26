import { animate, motion, useMotionValue, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useState } from "react";

interface AnimatedNumberProps {
  value: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
  duration?: number;
  useGrouping?: boolean;
}

export function AnimatedNumber({
  value,
  decimals = 0,
  prefix = "",
  suffix = "",
  className,
  duration = 1.15,
  useGrouping = true,
}: AnimatedNumberProps) {
  const reduceMotion = useReducedMotion();
  const motionValue = useMotionValue(reduceMotion ? value : 0);
  const [displayValue, setDisplayValue] = useState(reduceMotion ? value : 0);

  const formatter = useMemo(
    () => new Intl.NumberFormat("ko-KR", {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
      useGrouping,
    }),
    [decimals, useGrouping],
  );

  useEffect(() => {
    const controls = animate(motionValue, value, {
      duration: reduceMotion ? 0.001 : duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: setDisplayValue,
    });
    return () => controls.stop();
  }, [duration, motionValue, reduceMotion, value]);

  const finalText = `${prefix}${formatter.format(value)}${suffix}`;
  const displayText = `${prefix}${formatter.format(displayValue)}${suffix}`;

  return (
    <motion.span
      className={className}
      aria-label={finalText}
      initial={reduceMotion ? false : { opacity: 0, scale: 0.82, y: 5 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={{ duration, ease: [0.16, 1, 0.3, 1] }}
    >
      <span className="sr-only">{finalText}</span>
      <span key={finalText} className="t-digit-group is-animating" aria-hidden="true">
        {Array.from(displayText).map((character, index) => (
          <span
            className="t-digit"
            key={index}
            style={{ animationDelay: `calc(var(--digit-stagger) * ${Math.min(index, 10)})` }}
          >
            {character === " " ? "\u00a0" : character}
          </span>
        ))}
      </span>
    </motion.span>
  );
}
