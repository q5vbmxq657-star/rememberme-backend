"use client";

import { useEffect, useRef } from "react";

export function AudioWave({ level, active }: { level: number; active: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const levelRef = useRef(level);
  levelRef.current = level;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    let frame = 0;
    let animation = 0;
    const draw = () => {
      const ratio = window.devicePixelRatio || 1;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
        canvas.width = width * ratio;
        canvas.height = height * ratio;
      }
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);
      context.lineCap = "round";
      context.strokeStyle = "#f35f56";
      context.lineWidth = 5;
      const amplitude = active ? Math.max(0.12, levelRef.current) : 0.05;
      for (let index = 0; index < 9; index += 1) {
        const x = (width / 10) * (index + 1);
        const phase = Math.sin(frame * 0.08 + index * 0.9) * 0.5 + 0.5;
        const barHeight = 12 + amplitude * (24 + phase * 36);
        context.beginPath();
        context.moveTo(x, height / 2 - barHeight / 2);
        context.lineTo(x, height / 2 + barHeight / 2);
        context.stroke();
      }
      frame += 1;
      animation = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(animation);
  }, [active]);

  return <canvas ref={canvasRef} className="h-24 w-full" aria-hidden="true" />;
}
