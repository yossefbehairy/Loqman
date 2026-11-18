import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './fonts.css';
import './styles.css';
import { applySkin, readSkin } from './lib/skins';
import { applyFont, readFont } from './lib/fonts';
import { applyChatWidth, readChatWidth } from './lib/chat-width';
import { Shell } from './Shell';

applySkin(readSkin());
applyFont(readFont());
applyChatWidth(readChatWidth());

export function mountRuseBotUi(): void {
  document.documentElement.removeAttribute('data-theme');
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Shell />
    </StrictMode>
  );
}
