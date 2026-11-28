import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { GripVertical, Maximize, Minus, Plus } from 'lucide-react';
import { MAX_ZOOM, MIN_ZOOM } from './useOrganizationZoom';

type Position = { x: number; y: number };
type Drag = { id: number; startX: number; startY: number; offsetX: number; offsetY: number };
const STORAGE_KEY = 'loqman.team.zoom-toolbar-position.v1';
const DEFAULT_POSITION: Position = { x: 1, y: 1 };
const MARGIN = 8;
const clamp = (value: number) => Math.max(0, Math.min(1, value));
const isFraction = (value: unknown): value is number => typeof value === 'number' && value >= 0 && value <= 1;

function readPosition(): Position {
  try {
    const position = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
    if (isFraction(position?.x) && isFraction(position?.y)) return { x: position.x, y: position.y };
  } catch { /* The saved position may be unavailable or invalid. */ }
  return DEFAULT_POSITION;
}

export function OrganizationZoomToolbar({ viewport, scale, zoomBy, fit }: {
  viewport: HTMLElement;
  scale: number;
  zoomBy: (factor: number) => void;
  fit: () => void;
}) {
  const toolbar = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const dragged = useRef(false);
  const transient = useRef<Position | null>(null);
  const [position, setPosition] = useState(readPosition);
  const [preview, setPreview] = useState<Position | null>(null);
  const [bounds, setBounds] = useState({ x: 0, y: 0, marginX: 0, marginY: 0 });
  const help = useId();

  useLayoutEffect(() => {
    const element = toolbar.current!;
    const measure = () => {
      const horizontal = Math.max(0, viewport.clientWidth - element.offsetWidth);
      const vertical = Math.max(0, viewport.clientHeight - element.offsetHeight);
      const marginX = Math.min(MARGIN, horizontal / 2);
      const marginY = Math.min(MARGIN, vertical / 2);
      const x = horizontal - 2 * marginX;
      const y = vertical - 2 * marginY;
      setBounds(previous => previous.x === x && previous.y === y && previous.marginX === marginX && previous.marginY === marginY
        ? previous : { x, y, marginX, marginY });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    observer.observe(element);
    return () => observer.disconnect();
  }, [viewport]);

  function save(next: Position) {
    setPosition(next);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* Keep the position for this session. */ }
  }

  function startDrag(event: PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0 || !event.isPrimary || drag.current) return;
    const rect = toolbar.current!.getBoundingClientRect();
    dragged.current = false;
    drag.current = {
      id: event.pointerId, startX: event.clientX, startY: event.clientY,
      offsetX: event.clientX - rect.left, offsetY: event.clientY - rect.top,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    const pointer = drag.current;
    if (pointer?.id !== event.pointerId) return;
    if (!dragged.current && Math.hypot(event.clientX - pointer.startX, event.clientY - pointer.startY) < 3) return;
    dragged.current = true;
    const rect = viewport.getBoundingClientRect();
    const next = {
      x: bounds.x ? clamp((event.clientX - rect.left - pointer.offsetX - bounds.marginX) / bounds.x) : position.x,
      y: bounds.y ? clamp((event.clientY - rect.top - pointer.offsetY - bounds.marginY) / bounds.y) : position.y,
    };
    transient.current = next;
    setPreview(next);
  }

  function finishDrag(event: PointerEvent<HTMLButtonElement>, commit: boolean) {
    if (drag.current?.id !== event.pointerId) return;
    if (commit && transient.current) save(transient.current);
    drag.current = null;
    transient.current = null;
    setPreview(null);
  }

  function moveWithKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.altKey || event.ctrlKey || event.metaKey || drag.current) return;
    const step = event.shiftKey ? 64 : 16;
    const next = { ...position };
    switch (event.key) {
      case 'ArrowLeft': if (bounds.x) next.x = clamp(position.x - step / bounds.x); break;
      case 'ArrowRight': if (bounds.x) next.x = clamp(position.x + step / bounds.x); break;
      case 'ArrowUp': if (bounds.y) next.y = clamp(position.y - step / bounds.y); break;
      case 'ArrowDown': if (bounds.y) next.y = clamp(position.y + step / bounds.y); break;
      case 'Home': save(DEFAULT_POSITION); break;
      default: return;
    }
    event.preventDefault();
    event.stopPropagation();
    if (event.key !== 'Home') save(next);
  }

  const shown = preview ?? position;
  return (
    <div
      ref={toolbar}
      className="org-zoom-toolbar"
      role="group"
      aria-label="Organization zoom"
      data-dragging={preview ? '' : undefined}
      style={{ left: bounds.marginX + bounds.x * shown.x, top: bounds.marginY + bounds.y * shown.y }}
    >
      <button
        type="button"
        className="org-zoom-grip"
        aria-label="Reset zoom toolbar position"
        aria-describedby={help}
        title="Drag to move · Click to reset"
        onPointerDown={startDrag}
        onPointerMove={moveDrag}
        onPointerUp={event => finishDrag(event, true)}
        onPointerCancel={event => finishDrag(event, false)}
        onLostPointerCapture={event => finishDrag(event, false)}
        onKeyDown={moveWithKey}
        onClick={event => {
          if (event.detail === 0 || !dragged.current) save(DEFAULT_POSITION);
          dragged.current = false;
        }}
      ><GripVertical size={15} aria-hidden="true" /></button>
      <span id={help} className="sr-only">Drag or use the arrow keys to move the toolbar. Press Home to reset its position.</span>
      <button type="button" aria-label="Zoom out" title="Zoom out" disabled={scale <= MIN_ZOOM} onClick={() => zoomBy(1 / 1.2)}><Minus size={16} aria-hidden="true" /></button>
      <output aria-label="Zoom level">{Math.round(scale * 100)}%</output>
      <button type="button" aria-label="Zoom in" title="Zoom in" disabled={scale >= MAX_ZOOM} onClick={() => zoomBy(1.2)}><Plus size={16} aria-hidden="true" /></button>
      <span className="org-zoom-divider" aria-hidden="true" />
      <button type="button" aria-label="Fit to view" title="Fit to view" onClick={fit}><Maximize size={15} aria-hidden="true" /></button>
    </div>
  );
}
