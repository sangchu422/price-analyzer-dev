import { Canvas, useFrame } from "@react-three/fiber";
import { motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";

const LOGO_URL = "/brand/hyundai-wia.png";

function seededUnit(index: number, salt: number) {
  const value = Math.sin(index * 12.9898 + salt * 78.233) * 43758.5453;
  return value - Math.floor(value);
}

function LogoParticles({ positions, colors }: { positions: Float32Array; colors: Float32Array }) {
  const points = useRef<THREE.Points>(null);
  const startedAt = useRef<number | null>(null);
  const targets = useMemo(() => positions.slice(), [positions]);
  const start = useMemo(() => {
    const values = positions.slice();
    for (let index = 0; index < values.length; index += 3) {
      const pointIndex = index / 3;
      const radius = 3 + seededUnit(pointIndex, 1) * 6;
      const angle = seededUnit(pointIndex, 2) * Math.PI * 2;
      values[index] += Math.cos(angle) * radius;
      values[index + 1] += Math.sin(angle) * radius;
      values[index + 2] = (seededUnit(pointIndex, 3) - 0.5) * 6;
    }
    return values;
  }, [positions]);

  useFrame((state) => {
    const node = points.current;
    if (!node) return;
    startedAt.current ??= state.clock.elapsedTime;
    const elapsed = state.clock.elapsedTime - startedAt.current;
    const progress = Math.min(1, elapsed / 1.15);
    const eased = 1 - Math.pow(1 - progress, 3);
    const attribute = node.geometry.getAttribute("position") as THREE.BufferAttribute;
    for (let index = 0; index < targets.length; index += 1) {
      attribute.array[index] = start[index] + (targets[index] - start[index]) * eased;
    }
    attribute.needsUpdate = true;
    node.rotation.y += (state.pointer.x * 0.16 - node.rotation.y) * 0.055;
    node.rotation.x += (-state.pointer.y * 0.08 - node.rotation.x) * 0.055;
  });

  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[start, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <pointsMaterial
        vertexColors
        size={0.065}
        sizeAttenuation
        transparent
        opacity={0.95}
      />
    </points>
  );
}

export function WiaLogoIntro({ onComplete }: { onComplete: () => void }) {
  const reduceMotion = useReducedMotion();
  const [particles, setParticles] = useState<{
    positions: Float32Array;
    colors: Float32Array;
  } | null>(null);
  const [webglAvailable] = useState(() => {
    try {
      const canvas = document.createElement("canvas");
      return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
    } catch {
      return false;
    }
  });

  useEffect(() => {
    if (reduceMotion || !webglAvailable) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (!active) return;
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return;
      context.drawImage(image, 0, 0);
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      const lowPower = (navigator as Navigator & { deviceMemory?: number }).deviceMemory !== undefined
        && ((navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8) <= 4;
      const repeats = window.innerWidth < 700 ? 2 : lowPower ? 1 : 4;
      const positionValues: number[] = [];
      const colorValues: number[] = [];
      for (let y = 0; y < canvas.height; y += 1) {
        for (let x = 0; x < canvas.width; x += 1) {
          const offset = (y * canvas.width + x) * 4;
          if (pixels[offset + 3] < 70) continue;
          for (let repeat = 0; repeat < repeats; repeat += 1) {
            positionValues.push(
              (x - canvas.width / 2 + (Math.random() - 0.5) * 0.9) / 8,
              (canvas.height / 2 - y + (Math.random() - 0.5) * 0.9) / 8,
              (Math.random() - 0.5) * 0.12,
            );
            colorValues.push(
              pixels[offset] / 255,
              pixels[offset + 1] / 255,
              pixels[offset + 2] / 255,
            );
          }
        }
      }
      setParticles({
        positions: new Float32Array(positionValues),
        colors: new Float32Array(colorValues),
      });
    };
    image.src = LOGO_URL;
    return () => {
      active = false;
    };
  }, [reduceMotion, webglAvailable]);

  useEffect(() => {
    const timeout = window.setTimeout(onComplete, reduceMotion ? 550 : 2450);
    return () => window.clearTimeout(timeout);
  }, [onComplete, reduceMotion]);

  return (
    <motion.div
      className="wia-intro"
      role="dialog"
      aria-label="Price Analyzer 시작 화면"
      initial={{ opacity: 1 }}
      animate={{ opacity: [1, 1, 0] }}
      transition={{ duration: reduceMotion ? 0.55 : 2.45, times: [0, 0.86, 1] }}
    >
      <motion.div
        className="wia-intro-mark"
        initial={{ scale: 0.92, x: 0, y: 0 }}
        animate={reduceMotion ? { scale: 1 } : {
          scale: [0.92, 1, 0.2],
          x: [0, 0, "-42vw"],
          y: [0, 0, "39vh"],
        }}
        transition={{ duration: 2.15, times: [0, 0.58, 1], ease: [0.22, 1, 0.36, 1] }}
      >
        {particles && webglAvailable && !reduceMotion ? (
          <Canvas
            camera={{ position: [0, 0, 12], fov: 42 }}
            dpr={[1, 1.5]}
            gl={{ antialias: false, alpha: true, powerPreference: "high-performance" }}
          >
            <LogoParticles positions={particles.positions} colors={particles.colors} />
          </Canvas>
        ) : (
          <img src={LOGO_URL} alt="HYUNDAI WIA" />
        )}
      </motion.div>
      <motion.p
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: [0, 1, 1, 0], y: [8, 0, 0, -5] }}
        transition={{ duration: 2.15, times: [0, 0.24, 0.72, 1] }}
      >
        PROCUREMENT INTELLIGENCE
      </motion.p>
      <button type="button" onClick={onComplete}>건너뛰기</button>
    </motion.div>
  );
}
