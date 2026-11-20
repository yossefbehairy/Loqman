/*
 * Layout, classes and keyboard behaviour follow RuseBot 0.1.127
 * src/components/Composer.tsx and ComposerPlusMenu.tsx (Apache-2.0, Copyright
 * 2026 Milind Soni and OpenMausBot contributors): rounded composer surface,
 * "+" button, auto-growing textarea, Enter to send, Shift+Enter for a newline.
 * The voice/call control is replaced by send/stop wired to the Loqman worker;
 * the "+" menu holds the engine and model choice.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ArrowUp, Plus, Server, Square, UserPlus } from 'lucide-react';
import { cn } from '../lib/cn';
import { SettingsSelect } from './ai/SettingsSelect';
import type { EngineState } from '../state/useChat';
import { STATE_SHORT, selectable } from '../state/providerText';

export interface ComposerProps {
  placeholder: string;
  value: string;
  onChange: (text: string) => void;
  onSend: (text: string) => Promise<void>;
  onStop: () => Promise<void>;
  /** False while the running answer cannot be cancelled (a Loqman answer once it has started). */
  canStop?: boolean;
  /** Stop was pressed and the worker has not confirmed yet. */
  stopping?: boolean;
  running: boolean;
  sending: boolean;
  disabled?: boolean;
  engines: EngineState;
  onNewAgent?: () => void;
  /** Opens Settings on the AI page. */
  onManageProviders?: () => void;
}

export function Composer({ placeholder, value, onChange, onSend, onStop, canStop = true, stopping = false, running, sending, disabled, engines, onNewAgent, onManageProviders }: ComposerProps) {
  const choice = engines.choice;
  const ref = useRef<HTMLTextAreaElement>(null);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || CSS.supports('field-sizing', 'content')) return;
    const resize = () => {
      el.style.height = 'auto';
      el.style.height = `${el.scrollHeight}px`;
    };
    resize();
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (width === el.clientWidth) return;
      resize();
      width = el.clientWidth;
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [value]);

  useEffect(() => {
    if (!menu) return;
    const close = (e: PointerEvent) => {
      // Radix portals disable outside pointer targets; dismiss the select before its parent menu.
      if (menuRef.current?.querySelector('[role="combobox"][data-state="open"]')) return;
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setMenu(false);
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', esc);
    };
  }, [menu]);

  // No usable provider with a model: sending would go nowhere, so say so and point to Settings > AI.
  const noProvider = !disabled && !engines.executionReady;
  const providerNotice = engines.catalogStatus === 'loading' ? 'Loading models for the selected provider.'
    : engines.catalogStatus === 'error' ? 'Could not load models for the selected provider.'
    : choice ? 'Choose an available model for the selected provider.' : 'No AI provider is ready to answer.';
  const canSend = value.trim().length > 0 && !running && !sending && !disabled && !noProvider;
  const submit = async () => {
    if (!canSend) return;
    const text = value;
    try {
      await onSend(text);
    } catch {
      onChange(text);
    } finally {
      ref.current?.focus();
    }
  };

  return (
    <div
      data-tour="composer"
      className="@container/composer relative z-[1] rounded-3xl bg-composer px-2 py-1.5 ring-1 ring-composer-ring"
    >
      {noProvider && (
        <p role="status" data-composer-no-provider className="px-3 pb-1 pt-1.5 text-[12.5px] leading-relaxed text-ink-secondary">
          {providerNotice}{' '}
          {onManageProviders ? (
            <button type="button" onClick={onManageProviders} className="font-medium text-accent-text hover:underline">
              Open Settings &gt; AI
            </button>
          ) : (
            'Open Settings > AI'
          )}{' '}
          to manage providers.
        </p>
      )}
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
                  Provider
                </label>
                <SettingsSelect
                  id="rb-engine"
                  size="compact"
                  className="mb-2"
                  value={choice?.engine ?? ''}
                  onValueChange={(id) => void engines.selectEngine(id)}
                  options={[
                    ...(!choice ? [{ value: '', label: 'Choose a provider', disabled: true }] : []),
                    ...engines.engines.map(e => ({
                      value: e.id,
                      label: e.title + (e.id !== 'mock' && e.state !== 'ready' ? ` (${STATE_SHORT[e.state]})` : ''),
                      disabled: e.id !== 'mock' && !selectable(e.state),
                    })),
                  ]}
                />
                <label className="block px-2 pb-1 text-[12px] font-medium text-ink-secondary" htmlFor="rb-model">
                  Model
                </label>
                <SettingsSelect
                  id="rb-model"
                  size="compact"
                  value={choice?.model ?? ''}
                  onValueChange={(model) => choice && engines.setChoice({ engine: choice.engine, model })}
                  options={engines.models.map(m => ({ value: m.id, label: m.name }))}
                />
                {onManageProviders && (
                  <button
                    type="button"
                    role="menuitem"
                    data-composer-manage-providers
                    onClick={() => {
                      setMenu(false);
                      onManageProviders();
                    }}
                    className="mt-2 flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-[13.5px] text-ink hover:bg-raised/70"
                  >
                    <Server size={15} aria-hidden="true" /> Manage providers…
                  </button>
                )}
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
            className="block max-h-[min(9rem,max(2rem,40dvh))] min-h-6 w-full resize-none overflow-y-auto bg-transparent px-1 py-1 text-[15px] leading-6 [field-sizing:content] placeholder:text-ink-secondary focus:outline-none"
          />
        </div>
        <div data-composer-actions="true" className="flex items-center gap-1 @max-[30rem]/composer:ml-auto">
          {running ? (
            <button
              type="button"
              onClick={() => void onStop()}
              disabled={!canStop || stopping}
              aria-label="Stop"
              title={!canStop ? 'This answer cannot be stopped once it has started' : stopping ? 'Stopping…' : 'Stop'}
              className="flex size-8 items-center justify-center rounded-full bg-ink text-app hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
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
