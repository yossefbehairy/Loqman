import React, { useState, useEffect } from 'react';
import {
  Cpu,
  RefreshCw,
  Server,
  CheckCircle2,
  Sparkles,
  Layers,
  Coins,
  Loader2,
} from 'lucide-react';
import { getRuntimeHealth, getFreeModels, refreshModels } from '../../api/client';
import type { ModelCatalogEntry, RuntimeHealth } from '../../types';

export const SettingsModelsView: React.FC = () => {
  const [models, setModels] = useState<ModelCatalogEntry[]>([]);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const loadData = async (triggerLoading: boolean = false) => {
    if (triggerLoading) {
      setIsLoading(true);
      setError(null);
    }
    try {
      const [h, m] = await Promise.all([
        getRuntimeHealth().catch(() => null),
        getFreeModels().catch(() => []),
      ]);
      setHealth(h);
      if (!h || h.status !== 'ok') {
        setError('OpenCode runtime is unreachable or unhealthy. Model list below (if any) is the last known catalog, not live verification.');
      }
      if (m && m.length > 0) {
        setModels(m);
      } else {
        // Last-known fallback: explicitly NOT verified live.
        setModels([
          {
            id: 'deepseek-v4.1-flash-free',
            name: 'DeepSeek V4.1 Flash Free (last known — unverified)',
            provider_id: 'opencode',
            cost: { input: 0, output: 0 },
            variants: ['low', 'high', 'max'],
            is_free: true,
            status: 'unknown',
          },
          {
            id: 'muse-spark-1.3-contributor-free',
            name: 'Muse Spark 1.3 Free (last known — unverified)',
            provider_id: 'opencode',
            cost: { input: 0, output: 0 },
            variants: ['minimal', 'low', 'medium', 'high', 'xhigh'],
            is_free: true,
            status: 'unknown',
          },
        ]);
      }
    } catch (err: any) {
      setError(err.message || 'Failed to load settings data');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    let ignore = false;
    async function initSettings() {
      try {
        const [h, m] = await Promise.all([
          getRuntimeHealth().catch(() => null),
          getFreeModels().catch(() => []),
        ]);
        if (!ignore) {
          if (h) setHealth(h);
          if (!h || h.status !== 'ok') {
            setError('OpenCode runtime is unreachable or unhealthy. Model list below (if any) is the last known catalog, not live verification.');
          }
          if (m && m.length > 0) {
            setModels(m);
          } else {
            setModels([
              {
                id: 'deepseek-v4.1-flash-free',
                name: 'DeepSeek V4.1 Flash Free (last known — unverified)',
                provider_id: 'opencode',
                cost: { input: 0, output: 0 },
                variants: ['low', 'high', 'max'],
                is_free: true,
                status: 'unknown',
              },
              {
                id: 'muse-spark-1.3-contributor-free',
                name: 'Muse Spark 1.3 Free (last known — unverified)',
                provider_id: 'opencode',
                cost: { input: 0, output: 0 },
                variants: ['minimal', 'low', 'medium', 'high', 'xhigh'],
                is_free: true,
                status: 'unknown',
              },
            ]);
          }
        }
      } catch (err: any) {
        if (!ignore) {
          setError(err.message || 'Failed to load settings data');
        }
      } finally {
        if (!ignore) {
          setIsLoading(false);
        }
      }
    }
    initSettings();
    return () => {
      ignore = true;
    };
  }, []);

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      const updated = await refreshModels();
      setModels(updated);
      const h = await getRuntimeHealth();
      setHealth(h);
    } catch (err: any) {
      console.warn('Refresh models error:', err);
      await loadData();
    } finally {
      setIsRefreshing(false);
    }
  };

  const isOnline = health?.status === 'ok';

  return (
    <div className="flex-1 flex flex-col h-full bg-gray-950 text-gray-100 overflow-y-auto p-6 md:p-8 font-sans">
      <div className="max-w-5xl w-full mx-auto space-y-8">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-gray-800">
          <div>
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-indigo-500" />
              <h1 className="text-2xl font-bold tracking-tight text-white flex items-center gap-2">
                <span>Settings & Models Catalog</span>
                <span className="text-gray-500 font-normal">|</span>
                <span className="text-gray-300 font-arabic text-xl">الإعدادات والموديلات</span>
              </h1>
            </div>
            <p className="text-sm text-gray-400 mt-1 font-arabic">
              إدارة نماذج الذكاء الاصطناعي المجانية ومتابعة صحة المحرك التشغيلي وإعدادات التفكير المنطقي.
            </p>
          </div>

          <button
            type="button"
            onClick={handleRefresh}
            disabled={isRefreshing || isLoading}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-800 text-white rounded-xl text-xs font-semibold flex items-center gap-2 transition-all shadow-md shadow-indigo-600/20 self-start sm:self-auto"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing || isLoading ? 'animate-spin' : ''}`} />
            <span>{isRefreshing ? 'تحديث الفهرس...' : isLoading ? 'تحميل...' : 'Refresh Models Catalog'}</span>
          </button>
        </div>

        {error && (
          <div className="p-3.5 rounded-xl bg-red-950/50 border border-red-500/30 text-xs text-red-300">
            {error}
          </div>
        )}

        {isLoading && (
          <div className="flex items-center gap-2 text-xs text-indigo-400 p-2">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Loading verified catalog...</span>
          </div>
        )}


        {/* Runtime Health Dashboard Card */}
        <div className="bg-gray-900/90 rounded-2xl border border-gray-800 p-6 shadow-xl relative overflow-hidden">
          <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/5 rounded-full blur-3xl pointer-events-none" />
          
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-indigo-600/20 border border-indigo-500/30 flex items-center justify-center text-indigo-400">
                <Server className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-semibold text-white flex items-center gap-2">
                  <span>OpenCode AI Runtime Engine</span>
                  <span className={`px-2 py-0.5 text-[11px] font-medium rounded-full border ${isOnline ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-red-500/10 text-red-300 border-red-500/30'}`}>
                    {isOnline ? 'Active & Healthy' : 'Offline / Degraded'}
                  </span>
                </h3>
                <p className="text-xs text-gray-400">
                  حالة المحرك التشغيلي المحلي ومسارات التكامل مع الموديلات الصفرية
                </p>
              </div>
            </div>

            <span className={`flex items-center gap-1.5 text-xs px-3 py-1 rounded-full border font-medium ${isOnline ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30' : 'text-red-300 bg-red-500/10 border-red-500/30'}`}>
              <span className={`w-2 h-2 rounded-full ${isOnline ? 'bg-emerald-500 animate-pulse' : 'bg-red-400'}`} />
              {isOnline ? 'Online' : 'Offline'}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-6">
            <div className="p-3.5 rounded-xl bg-gray-950/60 border border-gray-850">
              <span className="text-[11px] text-gray-500 block mb-1">Service Status</span>
              <span className={`text-sm font-semibold capitalize ${isOnline ? 'text-emerald-400' : 'text-red-300'}`}>
                {health?.status || 'unknown (unreachable)'}
              </span>
            </div>
            <div className="p-3.5 rounded-xl bg-gray-950/60 border border-gray-850">
              <span className="text-[11px] text-gray-500 block mb-1">Verified Free Models</span>
              <span className="text-sm font-semibold text-white">
                {models.filter((m) => m.is_free && m.status === 'active').length} Models
              </span>
            </div>
            <div className="p-3.5 rounded-xl bg-gray-950/60 border border-gray-850">
              <span className="text-[11px] text-gray-500 block mb-1">Cost Confirmation</span>
              <span className="text-sm font-semibold text-indigo-300">
                $0.00 / 0 Tokens
              </span>
            </div>
            <div className="p-3.5 rounded-xl bg-gray-950/60 border border-gray-850">
              <span className="text-[11px] text-gray-500 block mb-1">Reasoning Variants</span>
              <span className="text-sm font-semibold text-amber-300">
                {(models.find((m) => m.id === 'muse-spark-1.3-contributor-free')?.variants || ['minimal', 'low', 'medium', 'high', 'xhigh']).join(' / ')}
              </span>
            </div>
          </div>
        </div>

        {/* Verified Zero-Cost Models Catalog */}
        <div>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <Sparkles className="w-5 h-5 text-amber-400" />
                <span>Verified Zero-Cost Models Catalog</span>
              </h2>
              <p className="text-xs text-gray-400 font-arabic">
                نماذج تم التحقق من مجانيتها 100% ودعمها لخاصية التفكير المنطقي المتدرج.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {models.map((model) => (
              <div
                key={model.id}
                className="bg-gray-900/80 rounded-2xl border border-gray-800 hover:border-gray-700 p-5 shadow-lg transition-all flex flex-col justify-between group"
              >
                <div>
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex items-center gap-2.5">
                      <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-indigo-600/30 to-purple-600/30 border border-indigo-500/30 flex items-center justify-center text-indigo-400 flex-shrink-0">
                        <Cpu className="w-4 h-4" />
                      </div>
                      <div>
                        <h4 className="text-sm font-bold text-white group-hover:text-indigo-300 transition-colors">
                          {model.name}
                        </h4>
                        <span className="text-[11px] text-gray-500 font-mono">{model.id}</span>
                      </div>
                    </div>

                    <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border flex items-center gap-1 ${model.status === 'active' && model.is_free ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' : 'bg-amber-500/10 text-amber-300 border-amber-500/30'}`}>
                      <CheckCircle2 className="w-3 h-3" />
                      {model.status === 'active' && model.is_free ? 'Verified free' : 'Last known · unverified'}
                    </span>
                  </div>

                  <p className="text-xs text-gray-400 mb-4 leading-relaxed font-arabic">
                    {model.id.includes('deepseek')
                      ? 'نموذج ديب سيك فلاش عالي السرعة، مثالي للمهام الإستراتيجية والتحليل البرمجي مع توفير كامل للتكلفة.'
                      : model.id.includes('muse')
                      ? 'نموذج ميوز سبارك التشاركي، يدعم التفكير المعمق والتحليل المعماري وصياغة العقود التنفيذية.'
                      : 'نموذج مجاني مصرح للاستخدام ضمن بيئة العمل الافتراضية.'}
                  </p>
                </div>

                <div className="pt-3 border-t border-gray-800/80 space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500 flex items-center gap-1">
                      <Coins className="w-3.5 h-3.5 text-amber-400" />
                      Cost Confirmation:
                    </span>
                    <span className={`font-semibold font-mono ${model.status === 'active' && model.is_free ? 'text-emerald-400' : 'text-amber-300'}`}>
                      {model.status === 'active' && model.is_free
                        ? `${model.cost.input} Input / ${model.cost.output} Output (Free)`
                        : 'Catalog entry is not currently verified'}
                    </span>
                  </div>

                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500 flex items-center gap-1">
                      <Layers className="w-3.5 h-3.5 text-indigo-400" />
                      Reasoning Variants:
                    </span>
                    <div className="flex items-center gap-1">
                      {(model.variants || ['low', 'high', 'max']).map((v) => (
                        <span
                          key={v}
                          className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-300 text-[10px] font-mono border border-gray-700"
                        >
                          {v}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
