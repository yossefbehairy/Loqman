/*
 * Copied from RuseBot 0.1.127 src/components/GlassScrollFrame.tsx (Apache-2.0, Copyright 2026 Milind Soni and OpenMausBot contributors).
 * Only the import path of cn changed.
 */
import { useLayoutEffect, useRef, type HTMLAttributes, type ReactNode } from "react";

import { cn } from "../lib/cn";
import { frameBars, publishGlassInsets, type GlassEdge as GlassEdgeName } from "../lib/glassInsets";

type GlassEdge = GlassEdgeName;

export function GlassScrollFrame({ className, children, ...rest }: HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const frame = ref.current;
    if (!frame) return;
    const apply = () => publishGlassInsets(frame);
    const resize = new ResizeObserver(apply);
    const watch = () => {
      resize.disconnect();
      for (const bar of Object.values(frameBars(frame))) if (bar) resize.observe(bar);
      apply();
    };
    watch();
    const children = new MutationObserver(watch);
    children.observe(frame, { childList: true });
    return () => {
      resize.disconnect();
      children.disconnect();
    };
  }, []);
  return (
    <div ref={ref} data-glass-frame="" className={cn("relative min-h-0", className)} {...rest}>
      {children}
    </div>
  );
}

export function GlassBar({ edge, className, children, ...rest }: HTMLAttributes<HTMLDivElement> & { edge: GlassEdge; children: ReactNode }) {
  return (
    <div
      data-glass-bar={edge}
      className={cn("glass-bar absolute inset-x-0 z-10", edge === "top" ? "top-0" : "bottom-0", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function GlassScroller({ className, children, ...rest }: HTMLAttributes<HTMLDivElement> & { children: ReactNode }) {
  return (
    <div className={cn("glass-scroller h-full overflow-y-auto", className)} {...rest}>
      <div className="glass-scroller-content">{children}</div>
    </div>
  );
}
