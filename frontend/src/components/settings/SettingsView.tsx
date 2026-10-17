import React from 'react';
import type { AppSettings, ModelReasoning } from '../../types/agentos';
import { Download, RotateCcw, Activity, ShieldCheck, Bell, Sliders, Sun } from 'lucide-react';
import type { ModelCatalogEntry } from '../../types';

interface Props {
  settings: AppSettings;
  models: ModelCatalogEntry[];
  onUpdateSettings: (newSettings: Partial<AppSettings>) => void;
  onCheckRuntime: () => void;
  onExportJSON: () => void;
  onResetDemo: () => void;
}

export const SettingsView: React.FC<Props> = ({
  settings,
  models,
  onUpdateSettings,
  onCheckRuntime,
  onExportJSON,
  onResetDemo,
}) => {
  return (
    <div className="flex flex-col gap-4 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text)] m-0">Settings</h1>
        <p className="text-xs text-[var(--muted)] m-0 mt-1">
          Defaults for new employees, runtime, safety and notifications. Changes save immediately.
        </p>
      </div>

      <div className="os-card divide-y divide-[var(--line)] p-0 overflow-hidden">
        {/* Appearance */}
        <div className="p-4 sm:p-5 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">
            <Sun size={14} className="text-[var(--acc)]" />
            Appearance
          </div>
          <div className="flex items-center justify-between gap-4 py-1">
            <div>
              <b className="text-sm text-[var(--text)] block">Theme</b>
              <small className="text-xs text-[var(--muted)]">
                Dark matches the cosmic terminal aesthetic. Light is optimal for bright ambient rooms.
              </small>
            </div>
            <select
              value={settings.theme}
              onChange={(e) => onUpdateSettings({ theme: e.target.value as any })}
              className="w-40 text-xs py-1.5 rounded-xl"
            >
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
          </div>
        </div>

        {/* Runtime */}
        <div className="p-4 sm:p-5 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">
            <Activity size={14} className="text-[var(--green)]" />
            Runtime
          </div>
          <div className="flex items-center justify-between gap-4 py-1 flex-wrap">
            <div>
              <b className="text-sm text-[var(--text)] block">OpenCode Engine</b>
              <small className="text-xs text-[var(--muted)]">
                Local runtime that coordinates agents and executes verified free models.
              </small>
            </div>
            <div className="flex items-center gap-2.5">
              <span className="os-tag os-tag-green text-xs">● Online</span>
              <button onClick={onCheckRuntime} className="os-btn os-btn-ghost os-btn-sm">
                Check connection
              </button>
            </div>
          </div>
        </div>

        {/* Model Defaults */}
        <div className="p-4 sm:p-5 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">
            <Sliders size={14} className="text-[var(--acc2)]" />
            Model Defaults for New Employees
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Default model</b>
              <small className="text-xs text-[var(--muted)]">
                Default LLM applied when creating an employee or department.
              </small>
            </div>
            <select
              value={settings.defModel}
              onChange={(e) => {
                const model = models.find((entry) => entry.id === e.target.value);
                const reason = model?.variants.includes(settings.reason)
                  ? settings.reason
                  : (model?.variants[0] || settings.reason);
                onUpdateSettings({ defModel: e.target.value, reason: reason as ModelReasoning });
              }}
              className="w-56 text-xs py-1.5 rounded-xl mono"
            >
              {!models.some((model) => model.id === settings.defModel) && (
                <option value={settings.defModel}>{settings.defModel} (not verified)</option>
              )}
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name} · {model.provider_id}
                </option>
              ))}
            </select>
            {models.length === 0 && <small className="text-[var(--muted)]">Connect OpenCode to load verified free models.</small>}
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Default reasoning</b>
              <small className="text-xs text-[var(--muted)]">
                Depth of chain-of-thought before finalizing an answer.
              </small>
            </div>
            <select
              value={settings.reason}
              onChange={(e) => onUpdateSettings({ reason: e.target.value as ModelReasoning })}
              className="w-40 text-xs py-1.5 rounded-xl capitalize"
            >
              {(models.find((model) => model.id === settings.defModel)?.variants.length
                ? models.find((model) => model.id === settings.defModel)!.variants
                : ['none', 'low', 'high', 'max']
              ).map((variant) => <option key={variant} value={variant}>{variant}</option>)}
            </select>
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Timeout (seconds)</b>
              <small className="text-xs text-[var(--muted)]">Maximum wait for a single LLM response.</small>
            </div>
            <input
              type="number"
              value={settings.timeout}
              min={10}
              max={600}
              onChange={(e) => onUpdateSettings({ timeout: Number(e.target.value) || 180 })}
              className="w-28 text-xs py-1.5 rounded-xl mono"
            />
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Retries on failure</b>
              <small className="text-xs text-[var(--muted)]">
                Attempts before an agent task is flagged as failed.
              </small>
            </div>
            <input
              type="number"
              value={settings.retries}
              min={0}
              max={5}
              onChange={(e) => onUpdateSettings({ retries: Number(e.target.value) || 2 })}
              className="w-28 text-xs py-1.5 rounded-xl mono"
            />
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Strict JSON output</b>
              <small className="text-xs text-[var(--muted)]">
                Force valid JSON output from employees.
              </small>
            </div>
            <label className="os-switch">
              <input
                type="checkbox"
                checked={settings.json}
                onChange={(e) => onUpdateSettings({ json: e.target.checked })}
              />
              <span className="slider" />
            </label>
          </div>
        </div>

        {/* Safety */}
        <div className="p-4 sm:p-5 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">
            <ShieldCheck size={14} className="text-[var(--amber)]" />
            Safety & Protection
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Allow paid fallback</b>
              <small className="text-xs text-[var(--muted)]">
                Off means runs stop gracefully rather than incurring unexpected credit charges.
              </small>
            </div>
            <label className="os-switch">
              <input
                type="checkbox"
                checked={settings.fallback}
                onChange={(e) => onUpdateSettings({ fallback: e.target.checked })}
              />
              <span className="slider" />
            </label>
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Require review before results are final</b>
              <small className="text-xs text-[var(--muted)]">
                Every pipeline deliverable lands in the Inbox for human verification.
              </small>
            </div>
            <label className="os-switch">
              <input
                type="checkbox"
                checked={settings.reqReview}
                onChange={(e) => onUpdateSettings({ reqReview: e.target.checked })}
              />
              <span className="slider" />
            </label>
          </div>
        </div>

        {/* Notifications */}
        <div className="p-4 sm:p-5 flex flex-col gap-3">
          <div className="flex items-center gap-2 text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">
            <Bell size={14} className="text-[var(--acc)]" />
            Notifications
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Review requests</b>
              <small className="text-xs text-[var(--muted)]">
                Notify when a pipeline stage deliverable is waiting for review.
              </small>
            </div>
            <label className="os-switch">
              <input
                type="checkbox"
                checked={settings.notifyReview}
                onChange={(e) => onUpdateSettings({ notifyReview: e.target.checked })}
              />
              <span className="slider" />
            </label>
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5">
            <div>
              <b className="text-sm text-[var(--text)] block">Failures</b>
              <small className="text-xs text-[var(--muted)]">
                Notify immediately when an employee requires supervisor attention.
              </small>
            </div>
            <label className="os-switch">
              <input
                type="checkbox"
                checked={settings.notifyFail}
                onChange={(e) => onUpdateSettings({ notifyFail: e.target.checked })}
              />
              <span className="slider" />
            </label>
          </div>
        </div>

        {/* Data */}
        <div className="p-4 sm:p-5 flex flex-col gap-3">
          <div className="text-xs font-semibold text-[var(--muted)] uppercase tracking-wider">
            Data & Backup
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5 flex-wrap">
            <div>
              <b className="text-sm text-[var(--text)] block">Export everything</b>
              <small className="text-xs text-[var(--muted)]">
                Download all teams, employees, inbox items, run receipts and settings as JSON.
              </small>
            </div>
            <button onClick={onExportJSON} className="os-btn os-btn-ghost os-btn-sm">
              <Download size={13} />
              Export JSON
            </button>
          </div>

          <div className="flex items-center justify-between gap-4 py-1.5 flex-wrap">
            <div>
              <b className="text-sm text-[var(--text)] block">Reset workspace</b>
              <small className="text-xs text-[var(--muted)]">
                Clears all teams, employees, inbox, runs and settings back to an empty workspace.
              </small>
            </div>
            <button onClick={onResetDemo} className="os-btn os-btn-danger os-btn-sm">
              <RotateCcw size={13} />
              Reset workspace
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
