import { motion, useReducedMotion } from "motion/react";
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

const playedAnimationKeys = new Set<string>();

interface TextBlockAnimationProps {
  children: ReactNode;
  blockColor?: string;
  animateOnScroll?: boolean;
  delay?: number;
  duration?: number;
  className?: string;
  onceKey?: string;
}

export function TextBlockAnimation({
  children,
  blockColor = "#00287a",
  animateOnScroll = false,
  delay = 0,
  duration = 0.8,
  className = "",
  onceKey,
}: TextBlockAnimationProps) {
  const reduceMotion = useReducedMotion();
  const [shouldAnimate] = useState(
    () => !reduceMotion && (!onceKey || !playedAnimationKeys.has(onceKey)),
  );

  useEffect(() => {
    if (onceKey) playedAnimationKeys.add(onceKey);
  }, [onceKey]);

  const reveal = shouldAnimate ? { opacity: 1, clipPath: "inset(0 0% 0 0)" } : undefined;
  const block = shouldAnimate ? { x: ["-105%", "0%", "105%"] } : undefined;
  const revealTransition = {
    delay: delay + duration * 0.42,
    duration: duration * 0.58,
    ease: [0.22, 1, 0.36, 1] as const,
  };

  return (
    <div
      className={`text-block-animation ${className}`.trim()}
      style={{ "--text-block-color": blockColor } as CSSProperties}
    >
      {shouldAnimate ? (
        <motion.span
          aria-hidden="true"
          className="text-block-animation-sweep"
          initial={{ x: "-105%" }}
          animate={animateOnScroll ? undefined : block}
          whileInView={animateOnScroll ? block : undefined}
          viewport={animateOnScroll ? { once: true, amount: 0.55 } : undefined}
          transition={{ delay, duration, times: [0, 0.48, 1], ease: [0.22, 1, 0.36, 1] }}
        />
      ) : null}
      <motion.div
        className="text-block-animation-content"
        initial={shouldAnimate ? { opacity: 0, clipPath: "inset(0 100% 0 0)" } : false}
        animate={animateOnScroll ? undefined : reveal}
        whileInView={animateOnScroll ? reveal : undefined}
        viewport={animateOnScroll ? { once: true, amount: 0.55 } : undefined}
        transition={revealTransition}
      >
        {children}
      </motion.div>
    </div>
  );
}
