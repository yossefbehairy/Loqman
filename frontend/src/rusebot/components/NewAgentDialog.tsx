/*
 * Modal frame follows the Settings dialog (glass popup). Creates a worker profile:
 * the agent is real (persisted, usable in missions and in its own chat).
 */
import { useEffect, useRef, useState } from 'react';
import { FocusScope } from '@radix-ui/react-focus-scope';
import { UserPlus, X } from 'lucide-react';
import { createProfile, type Profile } from '../data/worker';
import { messageOf } from '../state/useChat';
import { isExecutableChoice, useProviders, type EngineChoice } from '../state/useProviders';
import { ProviderPicker } from './ai/ProviderPicker';

const FIELD =
  'w-full rounded-lg border border-hairline/40 bg-inset px-3 py-2 text-[14px] text-ink placeholder:text-ink-secondary focus:outline-none focus:ring-1 focus:ring-focus';

function canSaveAgentChoice(choice: EngineChoice | null, chosenInDialog: boolean): boolean {
  // With neither field to send, the worker supplies its defaults.
  return !choice || (!chosenInDialog && choice.engine === 'mock' && !choice.model) || isExecutableChoice(choice);
}

export function NewAgentDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (profile: Profile) => void;
}) {
  const [name, setName] = useState('');
  const [role, setRole] = useState('');
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  // Starts from the current default (Settings / Composer); changing it here only affects this agent.
  const providers = useProviders();
  const [picked, setPicked] = useState<EngineChoice | null>(null);
  const pickedRef = useRef<EngineChoice | null>(null);
  const choice = picked ?? providers.choice;
  const choose = (next: EngineChoice) => {
    pickedRef.current = next;
    setPicked(next);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && !e.defaultPrevented && onClose();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const fieldsReady = name.trim().length > 0 && instructions.trim().length > 0 && !busy;
  const choiceReady = canSaveAgentChoice(choice, picked !== null);
  const catalog = choice ? providers.models[choice.engine] : undefined;
  const choiceNotice = choiceReady ? null
    : !catalog || catalog.status === 'loading' ? 'Wait for this provider’s models to load.'
    : catalog.status === 'error' ? 'Could not load models. Change the provider to continue.'
    : 'Choose an available provider and model.';
  const submit = async () => {
    // The picker can change intent before React commits the next render.
    const intended = pickedRef.current ?? choice;
    if (!fieldsReady || !canSaveAgentChoice(intended, pickedRef.current !== null)) return;
    setBusy(true);
    setError(null);
    try {
      const profile = await createProfile({
        name: name.trim(),
        role: role.trim() || 'Agent',
        instructions: instructions.trim(),
        modelId: intended?.model || undefined,
        engine: intended?.engine && intended.engine !== 'mock' ? intended.engine : undefined,
      });
      onCreated(profile);
    } catch (e) {
      setError(messageOf(e));
      setBusy(false);
    }
  };

  return (
    <div className="glass-popup-frame" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div aria-hidden="true" className="glass-scrim pointer-events-none absolute inset-0" />
      <FocusScope
        trapped
        loop
        onMountAutoFocus={(event) => {
          if (!nameRef.current) return;
          event.preventDefault();
          nameRef.current.focus();
        }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-agent-title"
        className="glass-surface glass-popup animate-pop-in relative flex !h-auto max-h-full w-[min(520px,calc(100vw-32px))] flex-col overflow-hidden rounded-[24px]"
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-hairline/30 px-5 py-3.5">
          <div className="flex items-center gap-2 text-[15px] font-semibold text-ink" id="new-agent-title">
            <UserPlus size={16} aria-hidden="true" /> New agent
          </div>
          <button type="button" aria-label="Close" title="Close" onClick={onClose} className="ui-icon-button">
            <X size={16} />
          </button>
        </div>
        <form
          className="flex min-h-0 flex-col gap-3 overflow-y-auto px-5 py-4 [&>*]:shrink-0"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <label className="text-[12px] font-medium text-ink-secondary" htmlFor="na-name">
            Name
          </label>
          <input id="na-name" ref={nameRef} dir="auto" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Sara" className={FIELD} />
          <label className="text-[12px] font-medium text-ink-secondary" htmlFor="na-role">
            Role
          </label>
          <input id="na-role" dir="auto" value={role} onChange={(e) => setRole(e.target.value)} placeholder="e.g. Research analyst" className={FIELD} />
          <label className="text-[12px] font-medium text-ink-secondary" htmlFor="na-instructions">
            What should this agent do?
          </label>
          <textarea
            id="na-instructions"
            dir="auto"
            rows={5}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="Describe how this agent works, what it owns and how it reports back."
            className={`${FIELD} resize-y`}
          />
          <div className="mt-1 flex flex-col gap-2">
            <div className="text-[12px] font-medium text-ink-secondary">Runs on</div>
            <ProviderPicker idPrefix="na" value={choice} onChange={choose} onEngineIntent={(engine) => choose({ engine, model: '' })} />
            {choiceNotice && <p id="new-agent-model-status" role="status" className="text-[12px] text-ink-secondary">{choiceNotice}</p>}
            <p className="text-[12px] text-ink-secondary">You can also ask Loqman to create agents for you.</p>
          </div>
          {error && (
            <p role="alert" className="text-[12.5px] text-danger">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose} className="ui-button ui-button-sm">
              Cancel
            </button>
            <button type="submit" disabled={!fieldsReady || !choiceReady} aria-describedby={choiceNotice ? 'new-agent-model-status' : undefined} className="ui-button ui-button-sm bg-accent text-accent-ink">
              Create agent
            </button>
          </div>
        </form>
      </FocusScope>
    </div>
  );
}
