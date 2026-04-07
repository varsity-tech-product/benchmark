# QuantTutorBench UI 优化指令

## 背景说明

这是一个 AI benchmark 评测平台（QuantTutorBench），用于评测不同 LLM（Anthropic、OpenAI 等）在量化金融教学任务上的表现。当前页面风格为深色/浅色自适应主题，主色调为橙色。

**目标**：在保留现有深色/浅色双主题和橙色品牌色的基础上，系统性提升排版质量、视觉层级和组件一致性，使整体风格更接近 Claude.ai 的克制、精致感。

---

## 第一步：字体系统（全局）

引入以下 Google Fonts，替换当前系统默认字体：

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Plus+Jakarta+Sans:wght@400;500&family=JetBrains+Mono:wght@400;500&family=Playfair+Display:ital,wght@0,400;1,400&display=swap" rel="stylesheet">
```

在全局 CSS / Tailwind 配置中定义字体变量：

```css
:root {
  --font-sans: 'Inter', system-ui, sans-serif;
  --font-display: 'Plus Jakarta Sans', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
  --font-serif: 'Playfair Display', Georgia, serif;
}
```

### 字体使用规则

| 用途 | 字体 | 字号 | 字重 |
|------|------|------|------|
| 导航栏链接 | Inter | 14px | 400 |
| 正文、表单、说明文字 | Inter | 14–15px | 400 |
| 列表行任务名 | Plus Jakarta Sans | 15px | 500 |
| 页面主标题（如 S01_ma_crossover） | Plus Jakarta Sans | 20px | 500 |
| 面包屑导航 | Inter | 12px | 400 |
| 数字统计（turns、tools） | Inter | 13px | 400 |
| 代码块、Tool Calls、shell 命令 | JetBrains Mono | 13px | 400 |
| Dashboard 空状态引言 | Playfair Display italic | 22px | 400 |

**全局规则**：只使用 400（regular）和 500（medium）两个字重，不使用 600 或 700，避免页面过重。

---

## 第二步：颜色系统优化

### 橙色使用克制化

当前问题：橙色同时出现在导航激活态、复选框、group 标签、Evaluate 按钮——四处同时竞争视觉焦点。

**修改规则**：

```css
/* 橙色只保留给主操作按钮 */
.btn-primary {
  background-color: var(--color-brand);  /* 保持橙色 */
  color: white;
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 500;
  border-radius: 8px;
  padding: 6px 14px;
}

/* 导航激活态：改为字重+下划线，不用橙色背景 */
nav a.active {
  font-weight: 500;
  color: var(--color-text-primary);
  border-bottom: 2px solid var(--color-brand);
  /* 移除橙色背景 pill */
}

/* group 标签：改为中性色 */
.badge-group {
  background-color: var(--color-bg-muted);   /* 浅灰/深灰，跟随主题 */
  color: var(--color-text-secondary);
  font-family: var(--font-sans);
  font-size: 11px;
  font-weight: 400;
  padding: 2px 8px;
  border-radius: 20px;
  border: 0.5px solid var(--color-border);
  /* 移除橙色 */
}

/* persona 标签（advanced_quant / beginner_no_finance / intermediate_developer）*/
.badge-persona {
  background-color: transparent;
  color: var(--color-text-secondary);
  font-family: var(--font-mono);   /* 用等宽字体，技术感 */
  font-size: 11px;
  font-weight: 400;
  padding: 2px 10px;
  border-radius: 6px;
  border: 0.5px solid var(--color-border);
}
```

### 深色/浅色双主题变量

```css
:root {
  /* 浅色（默认） */
  --color-bg: #FAF9F6;              /* 米白色底 */
  --color-bg-surface: #FFFFFF;
  --color-bg-muted: #F2F1EE;
  --color-text-primary: #1A1A18;
  --color-text-secondary: #6B6A65;
  --color-text-tertiary: #9E9D98;
  --color-border: rgba(0,0,0,0.1);
  --color-border-strong: rgba(0,0,0,0.18);
  --color-brand: #E8820C;           /* 橙色主色 */
}

@media (prefers-color-scheme: dark) {
  :root {
    --color-bg: #141614;
    --color-bg-surface: #1C1E1C;
    --color-bg-muted: #252725;
    --color-text-primary: #EDECEA;
    --color-text-secondary: #9A9994;
    --color-text-tertiary: #636158;
    --color-border: rgba(255,255,255,0.08);
    --color-border-strong: rgba(255,255,255,0.15);
    --color-brand: #F59330;         /* 深色模式橙色稍亮 */
  }
}
```

---

## 第三步：截图一 — Evaluate 列表页

### 问题清单

1. 任务名与 model 名视觉权重相同
2. turns / tools 数字没有单位区分
3. 列宽不固定，扫视困难
4. 复选框使用橙色填充

### 修改方案

```css
/* 列表行整体 */
.eval-row {
  display: grid;
  grid-template-columns: 32px 1fr 220px 60px 60px 180px 100px;
  align-items: center;
  gap: 0 12px;
  padding: 12px 16px;
  border-bottom: 0.5px solid var(--color-border);
  font-family: var(--font-sans);
}

/* 任务名 — 主信息 */
.eval-row .task-name {
  font-family: var(--font-display);
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text-primary);
}

/* model 名 — 次要信息 */
.eval-row .model-name {
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-tertiary);
  font-family: var(--font-mono);   /* 模型名用等宽，技术感强 */
}

/* turns / tools — 统计数字 */
.eval-row .stat {
  font-size: 13px;
  font-weight: 400;
  color: var(--color-text-secondary);
  font-family: var(--font-sans);
  font-variant-numeric: tabular-nums;  /* 数字等宽对齐 */
}

/* 复选框 — 去掉橙色，改为边框式 */
.eval-row input[type="checkbox"] {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--color-border-strong);
  border-radius: 4px;
  background: transparent;
  accent-color: var(--color-brand);  /* 仅选中时出现橙色 */
}

/* 面包屑 */
.breadcrumb {
  font-family: var(--font-sans);
  font-size: 12px;
  font-weight: 400;
  color: var(--color-text-tertiary);
}
.breadcrumb .current {
  color: var(--color-text-primary);
  font-weight: 500;
}
.breadcrumb .separator {
  margin: 0 6px;
  opacity: 0.4;
}
```

---

## 第四步：截图二 — Group 卡片页

### 问题清单

1. 图标色块（深橙方块）与卡片底色对比过于强烈
2. unscored 数字用橙色标注尚可，但字体和间距需统一

### 修改方案

```css
/* Group 卡片 */
.group-card {
  background: var(--color-bg-surface);
  border: 0.5px solid var(--color-border);
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.group-card:hover {
  border-color: var(--color-border-strong);
}

/* 图标区域 — 柔和橙色填充，不再用深色实底 */
.group-card .icon-wrap {
  width: 44px;
  height: 44px;
  border-radius: 10px;
  background: rgba(232, 130, 12, 0.12);  /* 橙色低透明度 */
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: var(--color-brand);
}

/* 卡片标题 */
.group-card .card-title {
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 0 0 4px;
}

/* 卡片副标题 */
.group-card .card-meta {
  font-family: var(--font-sans);
  font-size: 13px;
  font-weight: 400;
  color: var(--color-text-secondary);
}

/* unscored 高亮数字 */
.group-card .unscored {
  color: var(--color-brand);
  font-weight: 500;
}
```

---

## 第五步：截图三 — 对话详情页

### 问题清单

1. INFO 栏文字全部相同字号，层级混乱
2. 消息气泡圆角过小（约 6px）
3. Tool Calls 面板：工具名橙色 + ok 绿色 + 时间戳 + 代码内容，颜色噪音过多
4. 三栏宽度比例不合理
5. 代码/shell 内容未使用等宽字体

### 修改方案

```css
/* 三栏布局 */
.detail-layout {
  display: grid;
  grid-template-columns: 160px 1fr 320px;
  height: 100vh;
  overflow: hidden;
}

/* INFO 栏 */
.info-panel {
  font-family: var(--font-sans);
  padding: 20px 16px;
  border-right: 0.5px solid var(--color-border);
  overflow-y: auto;
}
.info-panel .info-label {
  font-size: 10px;
  font-weight: 400;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-text-tertiary);
  margin: 16px 0 4px;
}
.info-panel .info-label:first-child { margin-top: 0; }
.info-panel .info-value {
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-primary);
}
/* 模型名用等宽字体 */
.info-panel .info-value.mono {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 400;
}

/* 消息气泡 */
.bubble-student {
  background: var(--color-brand);
  color: white;
  border-radius: 16px 16px 4px 16px;  /* 右下角收尖，指示方向 */
  padding: 12px 16px;
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.65;
  max-width: 75%;
  margin-left: auto;
}

.bubble-tutor {
  background: var(--color-bg-muted);
  color: var(--color-text-primary);
  border-radius: 16px 16px 16px 4px;  /* 左下角收尖 */
  padding: 12px 16px;
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.65;
  max-width: 85%;
}

/* 对话中的标题（如 The Conceptual Framework） */
.bubble-tutor h3 {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 14px 0 6px;
}

/* 对话中的正文 */
.bubble-tutor p {
  font-family: var(--font-sans);
  font-size: 14px;
  color: var(--color-text-secondary);
  line-height: 1.7;
}

/* Tool Calls 面板 */
.tool-panel {
  border-left: 0.5px solid var(--color-border);
  overflow-y: auto;
  padding: 16px 12px;
}

/* 单个 tool call 卡片 */
.tool-call-item {
  border: 0.5px solid var(--color-border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
  background: var(--color-bg-surface);
}

/* 工具名 — 等宽字体，主色文字，不用橙色 */
.tool-call-item .tool-name {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-primary);
  margin: 0 0 4px;
}

/* ok 徽章 — 极浅绿，降低存在感 */
.badge-ok {
  display: inline-block;
  font-family: var(--font-sans);
  font-size: 10px;
  font-weight: 400;
  padding: 1px 6px;
  border-radius: 20px;
  background: rgba(99, 153, 34, 0.1);
  color: #3B6D11;
  margin-left: 6px;
  vertical-align: middle;
}

/* 时间戳 — 极度降权 */
.tool-call-item .timestamp {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-tertiary);
  opacity: 0.5;
  float: right;
}

/* 工具调用内容（path、command 等） */
.tool-call-item .tool-content {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 400;
  color: var(--color-text-secondary);
  line-height: 1.6;
  margin-top: 6px;
  white-space: pre-wrap;
  word-break: break-all;
}
```

---

## 第六步：导航栏

```css
/* 导航栏整体 */
.navbar {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 0 24px;
  height: 52px;
  border-bottom: 0.5px solid var(--color-border);
  background: var(--color-bg);
  font-family: var(--font-sans);
}

/* Logo 文字 */
.navbar .logo-text {
  font-family: var(--font-display);
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text-primary);
  margin-right: 32px;
}

/* 导航链接 */
.navbar a {
  font-size: 14px;
  font-weight: 400;
  color: var(--color-text-secondary);
  text-decoration: none;
  padding: 0 14px;
  height: 52px;
  display: flex;
  align-items: center;
  border-bottom: 2px solid transparent;
  transition: color 0.15s, border-color 0.15s;
}

/* 激活态 — 用下划线而非背景色 */
.navbar a.active {
  color: var(--color-text-primary);
  font-weight: 500;
  border-bottom-color: var(--color-brand);
  /* 不使用橙色背景 pill */
}

.navbar a:hover:not(.active) {
  color: var(--color-text-primary);
}
```

---

## 执行优先级

| 优先级 | 改动 | 预期收益 |
|--------|------|----------|
| P0 | 引入 Inter + Plus Jakarta Sans + JetBrains Mono | 整体质感提升最大 |
| P0 | 代码/工具名统一使用 JetBrains Mono | 专业感立竿见影 |
| P1 | 橙色仅保留主 CTA 按钮，其余改中性 | 消除视觉噪音 |
| P1 | 列表行字重层级（任务名 500，其余降权） | 扫视效率大幅提升 |
| P2 | 消息气泡圆角改为 16px | 对话界面更现代 |
| P2 | Tool Calls ok 徽章改为浅绿色 | 降低颜色噪音 |
| P3 | 三栏宽度比例调整 | 信息密度更平衡 |
| P3 | Playfair Display 用于 Dashboard 空状态 | 品牌感提升 |

---

## 注意事项

- 所有字体引入需在 `<head>` 最顶部，避免 FOUT（无样式文字闪烁）
- `font-display: swap` 已包含在 Google Fonts URL 中，无需额外配置
- 深色模式通过 `@media (prefers-color-scheme: dark)` 自动切换，不需要 JS
- 如果使用 Tailwind CSS，在 `tailwind.config.js` 中扩展 `fontFamily`：

```js
theme: {
  extend: {
    fontFamily: {
      sans: ['Inter', 'system-ui', 'sans-serif'],
      display: ['Plus Jakarta Sans', 'system-ui', 'sans-serif'],
      mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      serif: ['Playfair Display', 'Georgia', 'serif'],
    }
  }
}
```
