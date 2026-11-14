import { useCallback, useState } from 'react';
import { engineCheck, engineOpenSetup, engineTest, type EngineTestResult } from '../../data/worker';
import { explainCheck, explainError } from '../../state/providerText';
import { messageOf } from '../../state/useChat';
import { applyEngineCheck } from '../../state/useProviders';

export type SetupStep = 'install' | 'login';

export interface LaunchResult {
  step: SetupStep;
  launched: boolean;
  reason: string | null;
  failure: string | null;
}

/** Opens the official install / sign-in command in a terminal (engines.open_setup). */
export function useSetupLauncher(engineId: string) {
  const [busy, setBusy] = useState<SetupStep | null>(null);
  const [result, setResult] = useState<LaunchResult | null>(null);
  const run = useCallback(
    async (step: SetupStep) => {
      setBusy(step);
      setResult(null);
      try {
        const out = await engineOpenSetup(engineId, step);
        setResult({ step, launched: Boolean(out.launched), reason: out.reason ?? null, failure: null });
      } catch (e) {
        setResult({ step, launched: false, reason: null, failure: messageOf(e) });
      } finally {
        setBusy(null);
      }
    },
    [engineId]
  );
  return { busy, result, run };
}

export type TestState =
  | { phase: 'idle' }
  | { phase: 'running' }
  | { phase: 'done'; result: EngineTestResult }
  | { phase: 'failed'; message: string };

/** One real generation (engines.test). Only ever started from a click. */
export function useEngineTest(engineId: string) {
  const [state, setState] = useState<TestState>({ phase: 'idle' });
  const run = useCallback(
    async (modelId?: string) => {
      setState({ phase: 'running' });
      try {
        setState({ phase: 'done', result: await engineTest(engineId, modelId) });
      } catch (e) {
        setState({ phase: 'failed', message: messageOf(e) });
      }
    },
    [engineId]
  );
  return { state, run };
}

export interface CheckState {
  busy: boolean;
  text: string | null;
}

/** engines.check: free, no generation. The result also updates the shared provider status. */
export function useEngineCheck(engineId: string, title: string) {
  const [state, setState] = useState<CheckState>({ busy: false, text: null });
  const run = useCallback(async () => {
    setState({ busy: true, text: null });
    try {
      const out = await engineCheck(engineId);
      applyEngineCheck(engineId, out.discovery);
      setState({ busy: false, text: explainCheck(out, title) });
    } catch (e) {
      setState({ busy: false, text: explainError(null, messageOf(e)) });
    }
  }, [engineId, title]);
  return { state, run };
}
