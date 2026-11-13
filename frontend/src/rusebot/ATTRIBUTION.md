# RuseBot-derived interface: sources, licence and differences

Reference: RuseBot 0.1.127 (`D:/rusebot`, package `rusebot`), Apache License 2.0.
RuseBot is a modified version of OpenMausBot, Copyright 2026 Milind Soni and OpenMausBot
contributors. The licence text is in `LICENSE-Apache-2.0.txt` and the project notice in
`NOTICE.txt` (both copied unchanged). Per NOTICE, the OpenMausBot name and mascot are not
licensed for this product: no mascot, avatar art, logo or brand name from RuseBot is used.

Every ported file carries a header naming its source. Third-party notices that NOTICE lists
for features this port does not use (T3 Code, Cache Tax, hpke-js, WebRTC) are not bundled.

## Copied (verbatim, header added)

| File here | Source in `src/` |
|---|---|
| `styles.css` | `styles.css` (header comment only; compiled by Tailwind 4, see below) |
| `lib/cn.ts` | `lib/cn.ts` |
| `lib/fonts.ts` | `lib/fonts.ts` |
| `lib/chat-width.ts` | `lib/chat-width.ts` |
| `lib/code-block.ts` | `lib/code-block.ts` |
| `lib/composer-dock.ts` | `lib/composer-dock.ts` |
| `lib/color-contrast.ts` | `lib/color-contrast.ts` |
| `lib/use-media-query.ts` | `lib/use-media-query.ts` |
| `components/GlassScrollFrame.tsx` | `components/GlassScrollFrame.tsx` (import path only) |
| `lib/user-bubble-tone.ts` | `lib/user-bubble-tone.ts` (mascot import path only) |

## Adapted (markup, class names and states from the source; data via props)

| File here | Source in `src/components/` |
|---|---|
| `lib/skins.ts` | `lib/skins.ts` (Electron call and prior-install fallback removed) |
| `lib/mascot-colors.ts` | colour table from `lib/mascot.ts` |
| `Shell.tsx` | `App.tsx` |
| `components/Sidebar.tsx` | `Sidebar.tsx`, `SidebarSimpleTop.tsx`, `SidebarFooter.tsx`, `SidebarFooterNav.tsx`, `SidebarThreadRow.tsx` |
| `components/ChatView.tsx` | `ChatView.tsx`, `ChatEmptyState.tsx`, `ChatErrorBanner.tsx`, `WorkingIndicator.tsx` |
| `components/Composer.tsx` | `Composer.tsx`, `ComposerPlusMenu.tsx` |
| `components/ChatMarkdown.tsx` | `ChatMarkdown.tsx`, `MarkdownTable.tsx` (code-block chrome, element mapping, Shiki themes) |
| `components/ApprovalCard.tsx` | `ApprovalCard.tsx`, `PendingApproval.tsx` |
| `components/DeliverableCard.tsx` | `AttachmentGallery.tsx`, `AttachmentPreview.tsx` |
| `components/TeamView.tsx` | `TeamMapPage.tsx`, `TeamCanvas.tsx` |
| `components/ResultsView.tsx` | `TeamMapPage.tsx` header |
| `components/SettingsModal.tsx` | `SettingsModal.tsx`, `SettingsPrimitives.tsx` |

Written for AgentOS (no RuseBot source): `data/*`, `state/*`, `entry.tsx`.

## Build

`postcss.config.js` sends stylesheets under `src/rusebot/` through `@tailwindcss/postcss`
(Tailwind 4) and every other stylesheet through Tailwind 3, so the classic interface is
unchanged. `vite.config.ts` aliases the bare `tailwindcss` import to the Tailwind 4 copy for
`styles.css`. The new interface is loaded with a dynamic import, so its global styles never
reach the classic screens.

## Wiring

`conversations.*`, `runs.start_turn / get / events / cancel` and the `run` stream,
`approvals.list / decide`, `ceo.actions / approve / deny`, `deliverables.*`,
`engines.status / capabilities / check / models`, `models.free`, `profiles.list / update`,
`missions.list / create`. Old CEO history: `conversations.import_ceo` (copies `ceo_messages`
only), plus browser-stored threads (`agentos_ceo_threads_v1`) shown read-only under "Earlier chats".

## Known differences from the reference

Intended (content or capability AgentOS does not have):

- No mascot/avatar art; initials are used. No voice, call, phone pairing, apps/plugins,
  routines, channels, browser/computer panels, usage page, updates, onboarding tour,
  command palette, keyboard-shortcut sheet, message reactions/replies, attachments,
  `@` mentions, thread references, Mermaid diagrams.
- Header badge shows the selected engine (reference: "Chief of Staff"). Sidebar bottom row
  navigates to Team, Contracts, Results (reference: Automations, Team map, Apps).
- The worker's engines do not stream text yet (`streaming_text: false`), so a reply arrives
  whole, after a "Working" indicator.
- Team view is a static layout of department cards: no pan, zoom or drag-to-arrange, no
  department memory or shared-instruction dialogs.
- Settings has General, Appearance, Engines and About (reference: General, Appearance, AI,
  Devices, Account).
- Sidebar has no resize handle, pinned threads, sections or per-conversation menu.

Unverified: pixel-level equality of the Team, Settings and Results pages (their structure and
classes follow the source, but there is no identical-content reference render for them), and
rendering inside Electron (verified in headless Chromium through the dev bridge).
