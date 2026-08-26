import { motion, useMotionValue, useReducedMotion, useSpring, useTransform } from "motion/react";
import { useEffect, useId, useState, type PointerEvent } from "react";

let hasPlayedLogoOutro = false;

export function WiaInteractiveMark() {
  const reduceMotion = useReducedMotion();
  const titleId = useId();
  const [playOutro] = useState(() => !hasPlayedLogoOutro);

  useEffect(() => {
    hasPlayedLogoOutro = true;
  }, []);
  const pointerX = useMotionValue(0);
  const pointerY = useMotionValue(0);
  const spring = { stiffness: 190, damping: 22, mass: 0.75 };
  const rotateX = useSpring(useTransform(pointerY, [-1, 1], [5, -5]), spring);
  const rotateY = useSpring(useTransform(pointerX, [-1, 1], [-7, 7]), spring);
  const translateX = useSpring(useTransform(pointerX, [-1, 1], [-5, 5]), spring);
  const translateY = useSpring(useTransform(pointerY, [-1, 1], [-3, 3]), spring);

  const updatePointer = (event: PointerEvent<HTMLDivElement>) => {
    if (reduceMotion || event.pointerType === "touch") return;
    const bounds = event.currentTarget.getBoundingClientRect();
    pointerX.set((event.clientX - bounds.left) / bounds.width * 2 - 1);
    pointerY.set((event.clientY - bounds.top) / bounds.height * 2 - 1);
  };

  const reset = () => {
    pointerX.set(0);
    pointerY.set(0);
  };

  return (
    <div
      className="wia-hero-stage"
      onPointerMove={updatePointer}
      onPointerLeave={reset}
      onPointerCancel={reset}
    >
      <motion.div
        className={`wia-hero-mark${playOutro && !reduceMotion ? " is-logo-outro" : ""}`}
        style={reduceMotion ? undefined : { rotateX, rotateY, x: translateX, y: translateY }}
      >
        <span className="wia-hero-bloom" aria-hidden="true" />
        <svg
          className="wia-vector-logo"
          viewBox="0 0 720 259.552"
          role="img"
          aria-labelledby={titleId}
        >
          <title id={titleId}>HYUNDAI WIA</title>
          <g className="wia-logo-piece is-hyundai">
            <path d="M387.246 13.301h27.928V33.08h-31.771V17.201c.078-.786-.082-2.179.596-3.036.708-.886 1.813-.834 3.247-.864zm-15.407-9.406c-3.127 3.196-3.287 7.87-3.336 10.589v53.643h14.9V46.384h31.771v21.743h14.978V.003H383.53c-4.411.042-8.593.655-11.691 3.892zM265.946.003h-46.709v68.124h14.938V13.301h28.086c1.385.029 2.529-.04 3.246.871.668.849.529 2.242.558 3.024v50.93h14.97V14.483c-.1-2.719-.229-7.392-3.366-10.604C274.59.658 270.437.045 265.946.003zM191.06 50.908c-.068.781.061 2.18-.597 3.047-.708.897-1.842.806-3.257.826H159.16V.003h-14.969v68.124h46.72c4.492-.034 8.595-.676 11.712-3.883 3.186-3.227 3.276-7.883 3.377-10.625V.003h-14.94v50.905zM341.183 50.908c-.09.784.05 2.18-.587 3.047-.697.894-1.828.806-3.241.826h-28.086v-41.48h28.086c1.413.029 2.544-.023 3.241.871.636.849.497 2.242.587 3.024v33.712zm14.861-36.425c-.07-2.719-.158-7.39-3.297-10.619C349.589.658 345.466.045 341.054 0H294.29v68.127h46.764c4.413-.034 8.535-.676 11.693-3.883 3.139-3.227 3.226-7.883 3.297-10.625V14.483zM46.83 26.609H14.978V0H0v68.127h14.978V39.898H46.83v28.229h14.938V0H46.83v26.609zM103.01 29.794L84.844 0H67.048l27.964 42.947v25.18h16.005v-25.18L138.914 0h-17.768L103.01 29.794zM445.191.003h15.904v68.124h-15.904z" />
          </g>
          <path className="wia-logo-piece is-w" d="M347.956 205.607l-22.095-103.439h-47.864l-5.508.114-22.09 103.325-24.112-103.439h-48.841l36.032 157.384h67.745l17.937-73.904 17.936 73.904h67.74l36.071-157.384h-48.85l-24.101 103.439z" />
          <path className="wia-logo-piece is-i" d="M444.313 259.552h57.207V102.054h-57.207v157.498z" />
          <path className="wia-logo-piece is-a" d="M646.617 102.054h-57.652l-73.333 157.498h52.507l49.677-108.433 49.61 108.433H720l-73.383-157.498z" />
          <path className="wia-logo-piece is-accent" d="M501.52 62.196h39.898v39.858H501.52z" />
        </svg>
      </motion.div>
    </div>
  );
}
