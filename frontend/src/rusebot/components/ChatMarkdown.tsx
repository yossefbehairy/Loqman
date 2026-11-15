/*
 * Adapted from RuseBot 0.1.127 src/components/ChatMarkdown.tsx (Apache-2.0,
 * Copyright 2026 Milind Soni and OpenMausBot contributors). The element
 * mapping, class names, code-block chrome and Shiki themes follow the source.
 * Left out: mentions, thread references, GitHub references, attachments and
 * Mermaid, which depend on the RuseBot store.
 */
import { memo, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { HighlighterCore } from 'shiki/core';
import Markdown, { defaultUrlTransform, type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import { Check, Copy, Download, WrapText, X } from 'lucide-react';
import {
  countLines,
  downloadSnippetFile,
  formatLineCount,
  getLanguageDisplayName,
  getSnippetFileName,
  parseFenceInfo,
} from '../lib/code-block';
import 'katex/dist/katex.min.css';
import { textDirection } from '../lib/textDirection';

interface HastNode {
  type?: string;
  tagName?: string;
  value?: string;
  children?: HastNode[];
}

function blockText(node: HastNode | undefined): string {
  if (!node) return '';
  if (node.type === 'text') return node.value ?? '';
  if (node.tagName === 'code' || node.tagName === 'pre') return '';
  return (node.children ?? []).map(blockText).join('');
}

function blockDirection(node: unknown): 'rtl' | 'ltr' {
  return textDirection(blockText(node as HastNode));
}

interface BlockProps {
  node?: unknown;
  children?: ReactNode;
}

const LIGHT_THEME = 'github-light-default';
const DARK_THEME = 'github-dark-default';
const HIGHLIGHT_MAX_CHARS = 100_000;

const grammarLoaders = {
  javascript: () => import('shiki/langs/javascript.mjs'),
  typescript: () => import('shiki/langs/typescript.mjs'),
  tsx: () => import('shiki/langs/tsx.mjs'),
  jsx: () => import('shiki/langs/jsx.mjs'),
  json: () => import('shiki/langs/json.mjs'),
  bash: () => import('shiki/langs/bash.mjs'),
  powershell: () => import('shiki/langs/powershell.mjs'),
  python: () => import('shiki/langs/python.mjs'),
  html: () => import('shiki/langs/html.mjs'),
  css: () => import('shiki/langs/css.mjs'),
  markdown: () => import('shiki/langs/markdown.mjs'),
  yaml: () => import('shiki/langs/yaml.mjs'),
  sql: () => import('shiki/langs/sql.mjs'),
  diff: () => import('shiki/langs/diff.mjs'),
} as const;
type GrammarFile = keyof typeof grammarLoaders;
const grammarAlias: Record<string, GrammarFile> = {
  js: 'javascript',
  node: 'javascript',
  ts: 'typescript',
  sh: 'bash',
  zsh: 'bash',
  shell: 'bash',
  ps1: 'powershell',
  py: 'python',
  htm: 'html',
  yml: 'yaml',
  md: 'markdown',
};
const PLAIN_LANGS = new Set(['', 'text', 'txt', 'plaintext', 'plain']);

let highlighterPromise: Promise<HighlighterCore> | undefined;
const grammarLoading = new Map<string, Promise<void>>();
const highlightCache = new Map<string, string>();

function grammarFile(lang: string): GrammarFile | undefined {
  const alias = grammarAlias[lang];
  if (alias) return alias;
  return Object.prototype.hasOwnProperty.call(grammarLoaders, lang) ? (lang as GrammarFile) : undefined;
}

function getHighlighter(): Promise<HighlighterCore> {
  if (highlighterPromise) return highlighterPromise;
  highlighterPromise = (async () => {
    const { createHighlighterCore } = await import('shiki/core');
    const { createJavaScriptRegexEngine } = await import('shiki/engine/javascript');
    return createHighlighterCore({
      themes: [
        import('shiki/themes/github-light-default.mjs').then((mod) => mod.default),
        import('shiki/themes/github-dark-default.mjs').then((mod) => mod.default),
      ],
      langs: [],
      engine: createJavaScriptRegexEngine(),
    });
  })().catch((error: unknown) => {
    highlighterPromise = undefined;
    throw error;
  });
  return highlighterPromise;
}

function loadGrammar(highlighter: HighlighterCore, file: GrammarFile): Promise<void> {
  let pending = grammarLoading.get(file);
  if (!pending) {
    pending = grammarLoaders[file]().then((mod) => highlighter.loadLanguage(mod.default));
    grammarLoading.set(file, pending);
  }
  return pending;
}

async function highlightFence(code: string, lang: string): Promise<string> {
  const highlighter = await getHighlighter();
  const requested = lang.trim().toLowerCase();
  const plain = PLAIN_LANGS.has(requested);
  const file = plain ? undefined : grammarFile(requested);
  if (!plain && !file) throw new Error(`No bundled grammar for ${requested}`);
  if (file) await loadGrammar(highlighter, file);
  return highlighter.codeToHtml(code, {
    lang: file ?? 'text',
    themes: { light: LIGHT_THEME, dark: DARK_THEME },
    defaultColor: 'light-dark()',
  });
}

export function CodeBlock({ code, lang, initialWrap = false }: { code: string; lang: string; initialWrap?: boolean }) {
  const highlightable = code.length <= HIGHLIGHT_MAX_CHARS;
  const key = `${lang}:${code}`;
  const [highlighted, setHtml] = useState<string | null>(() => (highlightable ? (highlightCache.get(key) ?? null) : null));
  const html = highlightable ? (highlightCache.get(key) ?? highlighted) : highlighted;
  const [wrapLines, setWrapLines] = useState(initialWrap);
  const [copied, setCopied] = useState<'idle' | 'copied' | 'failed'>('idle');

  useEffect(() => {
    if (!highlightable) return;
    if (highlightCache.get(key)) return;
    let alive = true;
    highlightFence(code, lang || 'text')
      .then((out) => {
        if (!alive) return;
        highlightCache.set(key, out);
        setHtml(out);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [code, lang, key, highlightable]);

  const copy = () => {
    navigator.clipboard
      .writeText(code)
      .then(() => setCopied('copied'))
      .catch(() => setCopied('failed'))
      .finally(() => window.setTimeout(() => setCopied('idle'), 1600));
  };

  const displayLanguage = getLanguageDisplayName(lang);
  const lineCount = countLines(code);

  return (
    <div dir="ltr" className="my-2 overflow-hidden rounded-lg border border-hairline/40 bg-inset">
      <div className="flex items-center justify-between gap-2 border-b border-hairline/30 bg-raised/30 px-3 py-1.5 text-xs">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span
            title={displayLanguage}
            className="min-w-0 truncate rounded border border-hairline/40 bg-raised px-1.5 py-0.5 text-[11px] font-medium tracking-wide text-ink select-none"
          >
            {displayLanguage}
          </span>
          {lineCount > 0 && (
            <span className="shrink-0 whitespace-nowrap text-[11px] text-ink-secondary select-none">
              {formatLineCount(lineCount)}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1 whitespace-nowrap">
          <button
            type="button"
            onClick={() => setWrapLines((w) => !w)}
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-colors ${
              wrapLines ? 'bg-accent/15 text-accent font-medium' : 'text-ink-secondary hover:bg-raised hover:text-ink'
            }`}
            title={wrapLines ? 'Disable line wrapping' : 'Wrap long lines'}
            aria-label={wrapLines ? 'Disable line wrapping' : 'Wrap long lines'}
            aria-pressed={wrapLines}
          >
            <WrapText size={12} aria-hidden="true" />
            <span className="hidden sm:inline">{wrapLines ? 'Unwrap' : 'Wrap'}</span>
          </button>
          <button
            type="button"
            onClick={() => downloadSnippetFile(getSnippetFileName(lang), code)}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-ink-secondary hover:bg-raised hover:text-ink transition-colors"
            title="Download snippet as file"
            aria-label="Download snippet as file"
          >
            <Download size={12} aria-hidden="true" />
            <span className="hidden sm:inline">Save</span>
          </button>
          <button
            type="button"
            onClick={copy}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-ink-secondary hover:bg-raised hover:text-ink transition-colors"
            title={copied === 'copied' ? 'Copied to clipboard' : copied === 'failed' ? 'Copy failed' : 'Copy code'}
            aria-label={copied === 'copied' ? 'Code copied to clipboard' : 'Copy code to clipboard'}
          >
            {copied === 'failed' ? (
              <>
                <X size={12} className="text-danger" aria-hidden="true" />
                <span className="text-danger font-medium hidden sm:inline">Copy failed</span>
              </>
            ) : copied === 'copied' ? (
              <>
                <Check size={12} className="text-success" aria-hidden="true" />
                <span className="text-success font-medium hidden sm:inline">Copied!</span>
              </>
            ) : (
              <>
                <Copy size={12} aria-hidden="true" />
                <span className="hidden sm:inline">Copy</span>
              </>
            )}
          </button>
        </div>
      </div>
      {html ? (
        <div
          className={`text-[13px] leading-relaxed [&_pre]:!bg-transparent [&_pre]:m-0 [&_pre]:p-3 ${
            wrapLines
              ? 'whitespace-pre-wrap break-words overflow-x-hidden [&_pre]:!whitespace-pre-wrap [&_pre]:!break-words [&_code]:!whitespace-pre-wrap [&_code]:!break-words'
              : 'overflow-x-auto'
          }`}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      ) : (
        <pre
          className={`p-3 text-[13px] leading-relaxed text-ink ${
            wrapLines ? 'whitespace-pre-wrap break-words overflow-x-hidden' : 'overflow-x-auto'
          }`}
        >
          {code}
        </pre>
      )}
    </div>
  );
}

function MarkdownCode({ node, children }: BlockProps) {
  const element = (node as { children?: Array<{ properties?: { className?: string[] }; data?: { meta?: string }; children?: HastNode[] }> })
    ?.children?.[0];
  const className = element?.properties?.className?.find((c) => c.startsWith('language-'));
  const parsed = parseFenceInfo(className?.slice('language-'.length) ?? '', element?.data?.meta);
  const code = blockText2(element?.children).replace(/\n$/, '');
  void children;
  return <CodeBlock code={code} lang={parsed.lang} initialWrap={parsed.wrap} />;
}

function blockText2(children: HastNode[] | undefined): string {
  return (children ?? []).map((c) => (c.type === 'text' ? (c.value ?? '') : blockText2(c.children))).join('');
}

const MARKDOWN_COMPONENTS: Components = {
  img({ src, alt }) {
    if (typeof src === 'string' && /^(?:data:image\/|blob:)/i.test(src)) {
      return <img src={src} alt={alt ?? ''} />;
    }
    return <a href={src} target="_blank" rel="noreferrer" className="text-accent underline">{alt || src || 'Image'}</a>;
  },
  pre: MarkdownCode,
  code({ children }: { children?: ReactNode }) {
    return (
      <code dir="ltr" className="ui-code-chip break-words [unicode-bidi:isolate]">
        {children}
      </code>
    );
  },
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noreferrer" className="text-accent underline decoration-accent/40 hover:decoration-accent">
        {children}
      </a>
    );
  },
  table({ children }: BlockProps) {
    return (
      <div className="max-w-full overflow-x-auto rounded-lg border border-hairline/40">
        <table className="w-full border-collapse text-[13.5px]">{children}</table>
      </div>
    );
  },
  th({ children }: { children?: ReactNode }) {
    return <th className="border-b border-hairline/40 px-2 py-1.5 text-start font-semibold">{children}</th>;
  },
  td({ children }: { children?: ReactNode }) {
    return <td className="border-b border-hairline/20 px-2 py-1.5 align-top">{children}</td>;
  },
  p({ node, children }: BlockProps) {
    return <p dir={blockDirection(node)}>{children}</p>;
  },
  ul({ node, children }: BlockProps) {
    return (
      <ul dir={blockDirection(node)} className="list-disc space-y-1 ps-5">
        {children}
      </ul>
    );
  },
  ol({ node, children }: BlockProps) {
    return (
      <ol dir={blockDirection(node)} className="list-decimal space-y-1 ps-5">
        {children}
      </ol>
    );
  },
  h1({ node, children }: BlockProps) {
    return (
      <div dir={blockDirection(node)} className="mt-2 text-[16px] font-semibold">
        {children}
      </div>
    );
  },
  h2({ node, children }: BlockProps) {
    return (
      <div dir={blockDirection(node)} className="mt-2 text-[15.5px] font-semibold">
        {children}
      </div>
    );
  },
  h3({ node, children }: BlockProps) {
    return (
      <div dir={blockDirection(node)} className="mt-1.5 font-semibold">
        {children}
      </div>
    );
  },
  blockquote({ node, children }: BlockProps) {
    return (
      <blockquote dir={blockDirection(node)} className="border-s-2 border-hairline ps-3 text-ink-secondary">
        {children}
      </blockquote>
    );
  },
  hr() {
    return <hr className="border-hairline/40" />;
  },
};

const MATH = /\$\$[\s\S]+?\$\$|\$[^$\n]+\$|\\\(|\\\[/;

function ChatMarkdownComponent({ text }: { text: string }) {
  const rehype = useMemo(() => (MATH.test(text) ? [rehypeKatex] : []), [text]);
  return (
    <div className="chat-md min-w-0 [&>*+*]:mt-2">
      <Markdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={rehype} components={MARKDOWN_COMPONENTS}
        urlTransform={(url, key, node) => node.tagName === 'img' && key === 'src' && /^(?:data:image\/|blob:)/i.test(url) ? url : defaultUrlTransform(url)}>
        {text}
      </Markdown>
    </div>
  );
}

export const ChatMarkdown = memo(ChatMarkdownComponent, (a, b) => a.text === b.text);
