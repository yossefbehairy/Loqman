import { MANAGER_OPTIONS } from '../../data/defaultCompany';


interface ManagerSelectorPillProps {
  selectedManagerId: string;
  onSelectManager: (id: string) => void;
}

export const ManagerSelectorPill: React.FC<ManagerSelectorPillProps> = ({
  selectedManagerId,
  onSelectManager,
}) => {
  return (
    <div className="flex flex-wrap items-center justify-center gap-2 p-1.5 bg-gray-900/90 backdrop-blur-md rounded-2xl border border-gray-800 shadow-xl max-w-4xl mx-auto">
      {MANAGER_OPTIONS.map((manager) => {
        const isSelected = selectedManagerId === manager.id;
        const Icon = manager.icon;

        return (
          <button
            key={manager.id}
            type="button"
            data-persona={manager.dataPersona}
            onClick={() => onSelectManager(manager.id)}
            className={`flex items-center gap-2.5 px-3.5 py-2 rounded-xl text-left transition-all duration-200 relative group ${
              isSelected
                ? 'bg-gradient-to-r from-gray-800 to-gray-850 text-white shadow-lg border border-amber-500/50 ring-1 ring-amber-500/30'
                : 'bg-gray-950/40 text-gray-300 hover:text-white hover:bg-gray-800/60 border border-transparent'
            }`}
          >
            {/* Crown & Avatar Icon */}
            <div
              className={`w-8 h-8 rounded-lg bg-gradient-to-tr ${manager.avatarColor} flex items-center justify-center text-white shadow-sm flex-shrink-0 relative`}
            >
              <Icon className="w-4 h-4" />
              <span className="absolute -top-1 -right-1 text-[11px] leading-none" title="Manager">
                👑
              </span>
            </div>

            {/* Persona Names and Department */}
            <div className="flex flex-col min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-sm font-semibold truncate text-white">{manager.nameArabic}</span>
                <span className="text-xs text-gray-400 truncate">({manager.nameEnglish})</span>
                {isSelected && (
                  <span className="text-[10px] px-1 py-0.2 bg-amber-500/20 text-amber-300 rounded font-bold border border-amber-500/40 ml-0.5">
                    👑 Manager
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1 text-[11px] text-gray-400 group-hover:text-gray-300">
                <span className="truncate">{manager.departmentEnglish}</span>
                <span className="text-gray-600">•</span>
                <span className="truncate font-arabic text-gray-400">{manager.departmentArabic}</span>
              </div>
            </div>
          </button>
        );
      })}
    </div>
  );
};
