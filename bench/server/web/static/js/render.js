/**
 * render.js — isolated Markdown + LaTeX rendering helpers for the new UI.
 *
 * This is a local copy of the legacy renderer so the new frontend under
 * `bench/server/web` stays fully decoupled from `bench/web`.
 */

/* global marked, katex */

(function (window) {
  'use strict';

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function renderLatex(text) {
    if (!window.katex) return text;

    text = text.replace(/\$\$([\s\S]+?)\$\$/g, function (_, math) {
      try {
        return katex.renderToString(math.trim(), { displayMode: true, throwOnError: false });
      } catch (error) {
        return '<span class="katex-error">' + escapeHtml(math) + '</span>';
      }
    });

    text = text.replace(/(?<!\$)\$(?!\$)(?!\d)(.+?)(?<!\$)\$(?!\$)/g, function (_, math) {
      try {
        return katex.renderToString(math.trim(), { displayMode: false, throwOnError: false });
      } catch (error) {
        return '<code>' + escapeHtml(math) + '</code>';
      }
    });

    return text;
  }

  if (window.marked) {
    marked.setOptions({
      breaks: true,
      gfm: true
    });
  }

  function renderMarkdown(text) {
    if (!text) return '';

    var codeBlocks = [];
    var processed = text.replace(/```[\s\S]*?```/g, function (match) {
      codeBlocks.push(match);
      return '%%CODEBLOCK_' + (codeBlocks.length - 1) + '%%';
    });

    var inlineCode = [];
    processed = processed.replace(/`[^`]+`/g, function (match) {
      inlineCode.push(match);
      return '%%INLINE_' + (inlineCode.length - 1) + '%%';
    });

    processed = renderLatex(processed);

    processed = processed.replace(/%%CODEBLOCK_(\d+)%%/g, function (_, index) {
      return codeBlocks[parseInt(index, 10)];
    });
    processed = processed.replace(/%%INLINE_(\d+)%%/g, function (_, index) {
      return inlineCode[parseInt(index, 10)];
    });

    if (window.marked) {
      return marked.parse(processed);
    }

    return escapeHtml(processed).replace(/\n/g, '<br>');
  }

  window.QTB = window.QTB || {};
  window.QTB.renderMarkdown = renderMarkdown;
  window.QTB.escapeHtml = escapeHtml;
})(window);
