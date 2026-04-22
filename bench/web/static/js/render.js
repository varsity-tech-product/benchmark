/**
 * render.js — Markdown + LaTeX rendering pipeline.
 *
 * Uses marked.js for Markdown and KaTeX for LaTeX math.
 * Exports: renderMarkdown(text) → HTML string
 */

/* global marked, katex */

(function (window) {
  'use strict';

  // ── KaTeX helper ──────────────────────────────────────────

  function renderLatex(text) {
    if (!window.katex) return text;

    // Display math: $$ ... $$
    text = text.replace(/\$\$([\s\S]+?)\$\$/g, function (_, math) {
      try {
        return katex.renderToString(math.trim(), { displayMode: true, throwOnError: false });
      } catch (e) {
        return '<span class="katex-error">' + escapeHtml(math) + '</span>';
      }
    });

    // Inline math: $ ... $ (but not $10, $0.00 currency, or $$)
    text = text.replace(/(?<!\$)\$(?!\$)(?!\d)(.+?)(?<!\$)\$(?!\$)/g, function (_, math) {
      try {
        return katex.renderToString(math.trim(), { displayMode: false, throwOnError: false });
      } catch (e) {
        return '<code>' + escapeHtml(math) + '</code>';
      }
    });

    return text;
  }

  // ── Marked configuration ──────────────────────────────────

  if (window.marked) {
    marked.setOptions({
      breaks: true,
      gfm: true,
    });
  }

  // ── Main render function ──────────────────────────────────

  function renderMarkdown(text) {
    if (!text) return '';

    // Pre-process: protect code blocks from LaTeX processing
    var codeBlocks = [];
    var processed = text.replace(/```[\s\S]*?```/g, function (match) {
      codeBlocks.push(match);
      return '%%CODEBLOCK_' + (codeBlocks.length - 1) + '%%';
    });

    // Inline code
    var inlineCode = [];
    processed = processed.replace(/`[^`]+`/g, function (match) {
      inlineCode.push(match);
      return '%%INLINE_' + (inlineCode.length - 1) + '%%';
    });

    // Render LaTeX on non-code text
    processed = renderLatex(processed);

    // Restore code blocks
    processed = processed.replace(/%%CODEBLOCK_(\d+)%%/g, function (_, i) {
      return codeBlocks[parseInt(i)];
    });
    processed = processed.replace(/%%INLINE_(\d+)%%/g, function (_, i) {
      return inlineCode[parseInt(i)];
    });

    // Render Markdown
    if (window.marked) {
      return marked.parse(processed);
    }

    // Fallback: basic HTML escaping + newlines
    return escapeHtml(processed).replace(/\n/g, '<br>');
  }

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ── Exports ───────────────────────────────────────────────

  window.QTB = window.QTB || {};
  window.QTB.renderMarkdown = renderMarkdown;
  window.QTB.escapeHtml = escapeHtml;

})(window);
