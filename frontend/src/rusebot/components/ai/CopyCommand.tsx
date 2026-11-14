import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

export function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-lg bg-inset px-3 py-1.5">
      <code dir="ltr" className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap py-0.5 text-[12.5px] text-ink">
        {command}
      </code>
      <button
        type="button"
        aria-label="Copy command"
        title="Copy command"
        onClick={() => {
          navigator.clipboard
            .writeText(command)
            .then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1400);
            })
            .catch(() => undefined);
        }}
        className="flex size-7 shrink-0 items-center justify-center rounded-md text-ink-secondary hover:bg-raised hover:text-ink"
      >
        {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
      </button>
    </div>
  );
}
