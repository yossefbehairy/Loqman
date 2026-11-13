/*
 * Layout, classes and keyboard behaviour follow RuseBot 0.1.127
 * src/components/Composer.tsx and ComposerPlusMenu.tsx (Apache-2.0, Copyright
 * 2026 Milind Soni and OpenMausBot contributors): rounded composer surface,
 * "+" button, auto-growing textarea, Enter to send, Shift+Enter for a newline.
 * The voice/call control is replaced by send/stop wired to the AgentOS worker;
 * the "+" menu holds the engine and model choice.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp, Plus, Square, UserPlus } from 'lucide-react';
import { cn } from '../lib/cn';
import type { EngineState } from '../state/useChat';

export interface ComposerProps {
  placeholder: string;
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string) => Promise<void>;
  onStop: () => Promise<void>;
  running: boolean;
  disabled?: boolean;
  engines: EngineState;
  onNewAgent?: () => void;
}

export function Composer({ placeholder, value, onChange, onSend, onStop, running, disabled, engines, onNewAgent }: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [menu, setMenu] = useState(false);
  const [sending, setSending] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 144)}px`;
  }, [value]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setMenu(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [menu]);

  const canSend = value.trim().length > 0 && !running && !sending && !disabled;
  const submit = async () => {
    if (!canSend) return;
    const text = value;
    setSending(true);
    try {
      await onSend(text);
    } catch {
      onChange(text);
    } finally {
      setSending(false);
      ref.current?.focus();
    }
  };

  const choice = engines.choice;

  return (
    <div
      data-tour="composer"
      className="@container/composer relative z-[1] rounded-3xl bg-composer px-2 py-1.5 ring-1 ring-composer-ring"
    >
      <div data-composer-row="true" className="flex items-end gap-1 @max-[30rem]/composer:flex-wrap">
        <div className="flex flex-wrap items-center gap-1">
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={menu}
              aria-label="Add"
              title="New agent, engine and model"
              onClick={() => setMenu((m) => !m)}
              className="flex shrink-0 rounded-full"
            >
              <span
                data-composer-plus="true"
                className="flex size-8 items-center justify-center rounded-full bg-ink/[0.07] text-ink transition-colors hover:bg-ink/[0.12]"
              >
                <Plus size={18} aria-hidden="true" />
              </span>
            </button>
            {menu && (
              <div
                role="menu"
                aria-label="Engine and model"
                className="absolute bottom-full left-0 z-50 mb-2 w-[min(320px,calc(100vw-48px))] rounded-xl border border-hairline/50 bg-menu p-2 shadow-2xl shadow-black/30"
              >
                {onNewAgent && (
                  <button
                    type="button"
                    role="menuitem"
                    data-composer-new-agent
                    onClick={() => {
                      setMenu(false);
                      onNewAgent();
                    }}
                    className="mb-2 flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[13.5px] text-ink hover:bg-raised/70"
                  >
                    <UserPlus size={15} aria-hidden="true" /> New agent
                  </button>
                )}
                <label className="block px-2 pb-1 pt-1 text-[12px] font-medium text-ink-secondary" htmlFor="rb-engine">
                  Engine
                </label>
                <select
                  id="rb-engine"
                  value={choice?.engine ?? ''}
                  onChange={async (e) => {
                    const list = await engines.loadModels(e.target.value);
                    engines.setChoice({ engine: e.target.value, model: list[0]?.id ?? '' });
                  }}
                  className="mb-2 w-full rounded-lg border border-hairline/40 bg-control px-2 py-1.5 text-[13px] text-ink focus:outline-none focus:ring-1 focus:ring-focus"
                >
                  {engines.engines.map((e) => (
                    <option key={e.id} value={e.id} disabled={e.id !== 'mock' && e.status?.install_state !== 'installed'}>
                      {e.id}
                      {e.status && e.status.install_state !== 'installed' ? ' (not installed)' : ''}
                    </option>
                  ))}
                </select>
                <label className="block px-2 pb-1 text-[12px] font-medium text-ink-secondary" htmlFor="rb-model">
                  Model
                </label>
                <select
                  id="rb-model"
                  value={choice?.model ?? ''}
                  onChange={(e) => choice && engines.setChoice({ engine: choice.engine, model: e.target.value })}
                  className="w-full rounded-lg border border-hairline/40 bg-control px-2 py-1.5 text-[13px] text-ink focus:outline-none focus:ring-1 focus:ring-focus"
                >
                  {engines.models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        </div>
        <div className="mention-editor relative min-w-0 flex-1 self-center @max-[30rem]/composer:order-first @max-[30rem]/composer:basis-full">
          <textarea
            ref={ref}
            dir="auto"
            rows={1}
            value={value}
            placeholder={placeholder}
            aria-label={placeholder}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void submit();
              }
            }}
            className="block max-h-[9rem] min-h-6 w-full resize-none overflow-y-auto bg-transparent px-1 py-1 text-[15px] leading-6 placeholder:text-ink-secondary focus:outline-none"
          />
        </div>
        <div data-composer-actions="true" className="flex items-center gap-1 @max-[30rem]/composer:ml-auto">
          {running ? (
            <button
              type="button"
              onClick={() => void onStop()}
              aria-label="Stop"
              title="Stop"
              className="flex size-8 items-center justify-center rounded-full bg-ink text-app hover:opacity-90"
            >
              <Square size={13} fill="currentColor" aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              disabled={!canSend}
              onClick={() => void submit()}
              aria-label="Send"
              title="Send"
              className={cn('flex size-8 items-center justify-center rounded-full bg-accent text-accent-ink hover:brightness-110')}
            >
              <ArrowUp size={16} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
