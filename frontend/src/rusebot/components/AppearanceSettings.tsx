import { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronUp } from 'lucide-react';
import { SKINS, applySkin, readSkin, type SkinId } from '../lib/skins';
import { FONT_IDS, applyFont, readFont, type FontId } from '../lib/fonts';
import { CHAT_WIDTH_IDS, applyChatWidth, readChatWidth, type ChatWidthId } from '../lib/chat-width';
import { TEXT_SIZE_IDS, applyTextSize, readTextSize } from '../lib/text-size';
import { SettingsSelect } from './ai/SettingsSelect';
import './appearance-settings.css';

const label = (id: string) => id === 'skin' ? 'Theme default' : id.charAt(0).toUpperCase() + id.slice(1);

function ConversationPreview({ font, skin, name }: { font: FontId; skin: SkinId; name: string }) {
  return <div data-skin={skin} data-skin-preview className="type-preview-theme">
    <div className="font-sample type-preview" data-font={font} data-font-preview={font}>
      <div className="type-preview-caption"><span>Live preview</span><span>{name}</span></div>
      <div className="type-message type-message-user" lang="en">Let’s make room for your next idea.</div>
      <div className="type-message" lang="ar" dir="rtl">مرحباً، لنحوّل أفكارك إلى خطوات واضحة.</div>
    </div>
  </div>;
}

export function AppearanceSettings() {
  const [skin, setSkin] = useState(readSkin);
  const [font, setFont] = useState(readFont);
  const [previewFont, setPreviewFont] = useState<FontId | null>(null);
  const [width, setWidth] = useState(readChatWidth);
  const [size, setSize] = useState(readTextSize);
  const [expanded, setExpanded] = useState(false);

  // Keep an already-open gallery in step with preferences changed elsewhere.
  useEffect(() => {
    const sync = () => {
      // The live document is authoritative even when local storage is blocked.
      const data = document.documentElement.dataset;
      setSkin(SKINS.find(theme => theme.id === data.skin)?.id ?? readSkin());
      setFont(FONT_IDS.find(id => id === data.font) ?? 'skin');
      setWidth(CHAT_WIDTH_IDS.find(id => id === data.chatWidth) ?? 'standard');
      setSize(TEXT_SIZE_IDS.find(id => id === data.textSize) ?? 'default');
    };
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-skin', 'data-font', 'data-chat-width', 'data-text-size'] });
    return () => observer.disconnect();
  }, []);

  const selectedTheme = SKINS.find(theme => theme.id === skin)!;
  const themes = expanded ? SKINS : [selectedTheme, ...SKINS.filter(theme => theme.id !== skin).slice(0, 2)];
  const shownFont = previewFont ?? font;
  return <section data-settings-block="appearance" className="appearance-settings">
    <section className="appearance-section" aria-labelledby="appearance-theme">
      <div className="appearance-heading"><h2 id="appearance-theme">Theme</h2><p>Make Loqman feel like your workspace.</p></div>
      <div id="appearance-themes" className="theme-gallery">
        {themes.map(theme => <button key={theme.id} type="button" className="theme-card" data-theme-card={theme.id}
          aria-label={`Use ${theme.name} theme`} aria-pressed={skin === theme.id}
          onClick={() => { applySkin(theme.id); setSkin(theme.id); }}>
          <span className="theme-workspace" data-skin={theme.id} data-skin-preview aria-hidden="true">
            <span className="theme-mini-rail"><b>L</b><i /><i /><i /><span /></span>
            <span className="theme-mini-main"><span className="theme-mini-header">Loqman <i /></span><span className="theme-mini-user" /><span className="theme-mini-reply"><i /><i /><i /></span><span className="theme-mini-composer"><i /><b>↑</b></span></span>
          </span>
          <span className="theme-card-label"><span>{theme.name}</span>{skin === theme.id && <Check size={15} aria-hidden="true" />}</span>
        </button>)}
      </div>
      <button type="button" className="theme-expand" aria-expanded={expanded} aria-controls="appearance-themes" onClick={() => setExpanded(!expanded)}>
        {expanded ? 'Show fewer themes' : `View all ${SKINS.length} themes`}{expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
    </section>
    <section className="appearance-section" aria-labelledby="appearance-typography">
      <div className="appearance-setting-row"><div className="appearance-heading"><h2 id="appearance-typography">Typography</h2><p>Choose a typeface. Hover or use arrow keys to preview.</p></div>
        <SettingsSelect id="settings-font" aria-label="Font" value={font} className="appearance-font-select" menuClassName="appearance-font-menu" menuSide="top"
          onPreviewValue={value => setPreviewFont(value as FontId)} onOpenChange={() => setPreviewFont(null)}
          onValueChange={value => { applyFont(value as FontId); setFont(value as FontId); setPreviewFont(null); }}
          options={FONT_IDS.map(id => ({ value: id, label: label(id), style: id === 'skin' ? undefined : { fontFamily: `var(--font-choice-${id})` } }))} />
      </div>
      <ConversationPreview font={shownFont} skin={skin} name={shownFont === 'skin' ? `${selectedTheme.name} · Theme default` : label(shownFont)} />
    </section>
    <section className="appearance-section" aria-labelledby="appearance-size">
      <div className="appearance-setting-row"><div className="appearance-heading"><h2 id="appearance-size">Text Size</h2><p>A comfortable reading size throughout Loqman.</p></div>
        <div className="text-size-options" role="group" aria-label="Text size">{TEXT_SIZE_IDS.map(id => <button key={id} type="button" aria-pressed={size === id} onClick={() => { applyTextSize(id); setSize(id); }}>{label(id)}</button>)}</div>
      </div>
      <div className="text-size-sample"><p lang="en">Good ideas start with a clear conversation.</p><p lang="ar" dir="rtl">كل فكرة رائعة تبدأ بمحادثة واضحة.</p></div>
    </section>
    <section className="appearance-section" aria-labelledby="appearance-width">
      <div className="appearance-heading"><h2 id="appearance-width">Chat Width</h2><p>Give your conversations the space they need.</p></div>
      <div className="chat-width-options" role="group" aria-label="Chat width">{CHAT_WIDTH_IDS.map(id => <button key={id} type="button" data-width-card={id} aria-label={`${label(id)} chat width`} aria-pressed={width === id} onClick={() => { applyChatWidth(id as ChatWidthId); setWidth(id); }}>
        <span className="width-preview" aria-hidden="true"><span className={`width-column width-${id}`}><i /><i /><i /><b /></span></span>
        <span>{label(id)}</span>{width === id && <Check size={13} aria-hidden="true" />}
      </button>)}</div>
    </section>
  </section>;
}
