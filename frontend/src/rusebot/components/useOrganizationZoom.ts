import { useEffectEvent, useLayoutEffect, useRef, useState, type RefObject } from 'react';

export const MIN_ZOOM = 0.5;
export const MAX_ZOOM = 2;
const EDITABLE = 'dialog, [role="dialog"], [data-agent-panel], input, textarea, select, [contenteditable]:not([contenteditable="false"])';

interface ViewportAnchor { x: number; y: number; stackTop: number }
const centered: ViewportAnchor = { x: 0.5, y: 0.5, stackTop: 0 };

interface AxisSpace { leading: number; trailing: number }
const noSpace: AxisSpace = { leading: 0, trailing: 0 };
const initialZoom = { scale: 1, horizontal: noSpace, vertical: noSpace };

function clampScale(scale: number) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));
}

function sceneAxis(sceneSize: number, viewportSize: number, scale: number, space: AxisSpace) {
  const scaled = sceneSize * scale;
  const start = Math.max((viewportSize - scaled) / 2, space.leading);
  return { start, extent: Math.max(viewportSize, Math.ceil(start + scaled + space.trailing)) };
}

function anchorAxis(sceneSize: number, viewportSize: number, scale: number, scenePoint: number, viewportPoint: number) {
  const scaledPoint = scenePoint * scale;
  const leading = Math.max(0, viewportPoint - scaledPoint);
  const { start } = sceneAxis(sceneSize, viewportSize, scale, { leading, trailing: 0 });
  const scroll = start + scaledPoint - viewportPoint;
  // Leave just enough blank space to preserve the anchor at either scroll boundary.
  const trailing = Math.max(0, scroll + viewportSize - start - sceneSize * scale);
  return { scroll, space: { leading, trailing } };
}

export function useOrganizationZoom(
  host: RefObject<HTMLDivElement | null>,
  scene: { width: number; height: number },
  viewportSize: { width: number; height: number },
  { enabled, narrow, panelOpen }: { enabled: boolean; narrow: boolean; panelOpen: boolean },
) {
  const [zoom, setZoom] = useState(initialZoom);
  const pending = useRef<{ zoom: typeof initialZoom; left: number; top: number } | null>(null);
  const horizontal = sceneAxis(scene.width, viewportSize.width, zoom.scale, zoom.horizontal);
  const vertical = sceneAxis(scene.height, viewportSize.height, zoom.scale, zoom.vertical);

  const anchor = useRef(centered);
  const bookmark = useRef<ViewportAnchor | null>(null);
  const appliedScroll = useRef<{ left: number; top: number } | null>(null);

  function readAnchor(viewport: HTMLElement): ViewportAnchor {
    if (narrow) return { ...anchor.current, stackTop: viewport.scrollTop };
    return {
      ...anchor.current,
      x: (viewport.scrollLeft + viewport.clientWidth / 2 - horizontal.start) / (scene.width * zoom.scale),
      y: (viewport.scrollTop + viewport.clientHeight / 2 - vertical.start) / (scene.height * zoom.scale),
    };
  }

  function rememberViewport() {
    const viewport = host.current!.parentElement!;
    anchor.current = readAnchor(viewport);
    bookmark.current ??= anchor.current;
  }

  const alignViewport = useEffectEvent(() => {
    const viewport = host.current?.parentElement;
    if (!viewport || !viewportSize.width) return;

    const next = pending.current;
    if (next?.zoom === zoom) {
      viewport.scrollTo(next.left, next.top);
      pending.current = null;
      anchor.current = readAnchor(viewport);
    } else {
      if (!panelOpen && bookmark.current) {
        anchor.current = bookmark.current;
        bookmark.current = null;
      }
      const { x, y, stackTop } = anchor.current;
      viewport.scrollTo(
        narrow ? 0 : horizontal.start + x * scene.width * zoom.scale - viewport.clientWidth / 2,
        narrow ? stackTop : vertical.start + y * scene.height * zoom.scale - viewport.clientHeight / 2,
      );
    }
    appliedScroll.current = { left: viewport.scrollLeft, top: viewport.scrollTop };
  });

  // Agent additions at the same viewport keep the current scroll pixels.
  useLayoutEffect(() => {
    alignViewport();
  }, [zoom, viewportSize.width, viewportSize.height, narrow, panelOpen]);

  const onScroll = useEffectEvent(() => {
    const frame = host.current!;
    const viewport = frame.parentElement!;
    // Scroll clamping during resize can fire before ResizeObserver delivers
    // the new dimensions. Keep the last user position through that reflow.
    if (frame.clientWidth !== viewportSize.width || viewport.offsetHeight !== viewportSize.height) return;
    const applied = appliedScroll.current;
    if (applied?.left === viewport.scrollLeft && applied.top === viewport.scrollTop) return;
    appliedScroll.current = null;
    anchor.current = readAnchor(viewport);
  });

  useLayoutEffect(() => {
    const viewport = host.current!.parentElement!;
    const listener = () => onScroll();
    viewport.addEventListener('scroll', listener, { passive: true });
    return () => viewport.removeEventListener('scroll', listener);
  }, [host]);

  function zoomBy(factor: number, clientX?: number, clientY?: number) {
    const viewport = host.current!.parentElement!;
    const previous = pending.current;
    const current = previous?.zoom ?? zoom;
    const scale = clampScale(current.scale * factor);
    if (scale === current.scale) return;
    const bounds = viewport.getBoundingClientRect();
    const x = clientX === undefined ? viewport.clientWidth / 2 : clientX - bounds.left;
    const y = clientY === undefined ? viewport.clientHeight / 2 : clientY - bounds.top;
    const oldX = sceneAxis(scene.width, viewportSize.width, current.scale, current.horizontal);
    const oldY = sceneAxis(scene.height, viewportSize.height, current.scale, current.vertical);
    const sceneX = ((previous?.left ?? viewport.scrollLeft) + x - oldX.start) / current.scale;
    const sceneY = ((previous?.top ?? viewport.scrollTop) + y - oldY.start) / current.scale;
    const nextX = anchorAxis(scene.width, viewportSize.width, scale, sceneX, x);
    const nextY = anchorAxis(scene.height, viewportSize.height, scale, sceneY, y);
    const next = { scale, horizontal: nextX.space, vertical: nextY.space };
    pending.current = { zoom: next, left: nextX.scroll, top: nextY.scroll };
    setZoom(next);
  }

  function fit() {
    const viewport = host.current!.parentElement!;
    const scale = clampScale(Math.min(viewport.clientWidth / scene.width, viewport.clientHeight / scene.height));
    const next = { ...initialZoom, scale };
    pending.current = {
      zoom: next,
      left: Math.max(0, (scene.width * scale - viewport.clientWidth) / 2),
      top: Math.max(0, (scene.height * scale - viewport.clientHeight) / 2),
    };
    setZoom(next);
  }

  const onWheel = useEffectEvent((event: WheelEvent) => {
    if (!event.ctrlKey || !event.cancelable || event.defaultPrevented) return;
    if (event.target instanceof Element && event.target.closest(EDITABLE)) return;
    const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? viewportSize.height : 1;
    const delta = event.deltaY * unit;
    if (!Number.isFinite(delta) || !delta) return;

    // Chromium delivers touchpad pinch updates as cancelable Ctrl+wheel events.
    event.preventDefault();
    const step = Math.max(-40, Math.min(40, delta));
    zoomBy(Math.exp(-step / 100), event.clientX, event.clientY);
  });

  useLayoutEffect(() => {
    if (!enabled || !host.current) return;
    const frame = host.current;
    const listener = (event: WheelEvent) => onWheel(event);
    frame.addEventListener('wheel', listener, { passive: false });
    return () => frame.removeEventListener('wheel', listener);
  }, [enabled, host]);

  return { scale: zoom.scale, horizontal, vertical, zoomBy, fit, rememberViewport };
}
