import { useState, type CSSProperties } from 'react';
import * as Select from '@radix-ui/react-select';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import './settings-select.css';

/** Presentation only: the caller owns the existing provider/model value and callbacks. */
export function SettingsSelect({ id, value, disabled = false, options, onValueChange, className, size = 'default', onPreviewValue, onOpenChange, menuClassName, menuSide = 'bottom', selectedLabel, ...aria }: {
  id: string;
  value: string;
  disabled?: boolean;
  className?: string;
  onPreviewValue?: (value: string) => void;
  onOpenChange?: (open: boolean) => void;
  menuClassName?: string;
  menuSide?: 'top' | 'bottom';
  selectedLabel?: string;
  size?: 'default' | 'compact';
  'aria-label'?: string;
  'aria-labelledby'?: string;
  options: Array<{ value: string; label: string; disabled?: boolean; style?: CSSProperties }>;
  onValueChange: (value: string) => void;
}) {
  const [trigger, setTrigger] = useState<HTMLButtonElement | null>(null);
  const label = options.find(option => option.value === value)?.label ?? selectedLabel;
  return (
    <Select.Root value={value} disabled={disabled} onValueChange={onValueChange} onOpenChange={onOpenChange}>
      <Select.Trigger ref={setTrigger} id={id} data-value={value} {...aria} className={`settings-select-trigger${size === 'compact' ? ' settings-select-trigger-compact' : ''}${className ? ` ${className}` : ''}`}>
        <span className="settings-select-value"><Select.Value placeholder={label}>{label}</Select.Value></span>
        <Select.Icon className="settings-select-chevron"><ChevronDown size={15} strokeWidth={1.75} aria-hidden="true" /></Select.Icon>
      </Select.Trigger>
      <Select.Portal>
        <Select.Content
          className={`settings-select-menu${menuClassName ? ` ${menuClassName}` : ''}`} position="popper" side={menuSide} sideOffset={6} align="start"
          collisionBoundary={trigger?.closest('[role="dialog"]') ?? undefined} collisionPadding={8}
          onEscapeKeyDown={event => event.stopPropagation()}
        >
          <Select.ScrollUpButton className="settings-select-scroll"><ChevronUp size={14} /></Select.ScrollUpButton>
          <Select.Viewport className="settings-select-viewport">
            {options.filter(option => option.value !== '').map(option => (
              <Select.Item key={option.value} data-value={option.value} value={option.value} disabled={option.disabled} textValue={option.label} className="settings-select-option" style={option.style}
                  onFocus={() => onPreviewValue?.(option.value)}
                  onPointerMove={event => { if (event.pointerType === 'mouse' && !option.disabled) onPreviewValue?.(option.value); }}>
                <Select.ItemText>{option.label}</Select.ItemText>
                <Select.ItemIndicator className="settings-select-check"><Check size={14} aria-hidden="true" /></Select.ItemIndicator>
              </Select.Item>
            ))}
          </Select.Viewport>
          <Select.ScrollDownButton className="settings-select-scroll"><ChevronDown size={14} /></Select.ScrollDownButton>
        </Select.Content>
      </Select.Portal>
    </Select.Root>
  );
}
