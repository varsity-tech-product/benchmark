"""Component ABC and shared rendering primitives.

Every chart and table in the stability report is a Component. Components own
their own ``render_html`` / ``render_csv`` logic and dump both representations
to disk via ``dump_to`` so they are independently inspectable and (in Phase 4)
re-exportable as paper assets.
"""

from __future__ import annotations

import base64
import functools
import html
import io
import textwrap
from abc import ABC, abstractmethod
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from experiments.student_sim_stability.core.config import STUDENT_MODELS  # noqa: E402

# ---------------------------------------------------------------------------
# Shared constants (also re-exported by analysis/report.py)
# ---------------------------------------------------------------------------

_PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]
_MODEL_ORDER = [model.split("/")[-1] for model in STUDENT_MODELS]
_COLORS = {
    model: _PALETTE[idx % len(_PALETTE)] for idx, model in enumerate(_MODEL_ORDER)
}

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _fmt_score(score: object) -> str:
    return f"{score:.2f}" if isinstance(score, (int, float)) else "n/a"


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _embed_img(b64: str, alt: str = "") -> str:
    return f'<img src="data:image/png;base64,{b64}" alt="{alt}" style="max-width:100%;margin:10px 0;">'


def _html(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _color_for(model: str) -> str:
    short = model.split("/")[-1] if "/" in model else model
    return _COLORS.get(short, "#999999")


def _count_votes(names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for n in names:
        counts[n] += 1
    return dict(counts)


def _wrap_label(label: str, width: int = 14) -> str:
    cleaned = str(label).replace("_", " ")
    return "\n".join(textwrap.wrap(cleaned, width=width)) or cleaned


def _score_class(score: float | None) -> str:
    if not isinstance(score, (int, float)):
        return ""
    if score >= 4.0:
        return "high"
    if score >= 3.0:
        return "mid"
    return "low"


def csv_bytes(rows: list[list[object]]) -> bytes:
    """Render a list of rows as a UTF-8 CSV byte string.

    Used by tables that want to dump structured data alongside their HTML.
    Stdlib only; no external deps.
    """
    import csv as _csv

    buf = io.StringIO()
    writer = _csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buf.getvalue().encode("utf-8")


_TEX_REPLACEMENTS = (
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
)


def tex_escape(value: object) -> str:
    """Escape a string for inclusion in a LaTeX document body.

    Order of replacements matters — backslash must be replaced first so the
    sequences we introduce for the other specials aren't double-escaped.
    """
    if value is None:
        return ""
    s = str(value)
    for needle, replacement in _TEX_REPLACEMENTS:
        s = s.replace(needle, replacement)
    return s


def booktabs_table(
    headers: list[str],
    col_align: str,
    rows: list[list[object]],
    *,
    escape: bool = True,
) -> str:
    """Render a booktabs ``tabular`` snippet.

    ``headers`` and ``rows`` cells are passed through ``tex_escape`` by
    default. Pass ``escape=False`` to emit pre-formatted cells (e.g., math
    expressions or already-escaped strings).
    """
    fmt = tex_escape if escape else (lambda v: "" if v is None else str(v))
    out = ["\\begin{tabular}{" + col_align + "}", "\\toprule"]
    out.append(" & ".join(fmt(h) for h in headers) + r" \\")
    out.append("\\midrule")
    for row in rows:
        out.append(" & ".join(fmt(c) for c in row) + r" \\")
    out.append("\\bottomrule")
    out.append("\\end{tabular}")
    return "\n".join(out)


# Deterministic PDF metadata so re-running paper-export with unchanged
# inputs produces byte-identical PDFs (and hence stable sha256 hashes).
_PDF_METADATA = {
    "Title": "",
    "Author": "",
    "Subject": "",
    "Keywords": "",
    "Producer": "matplotlib",
    "Creator": "student_sim_stability",
    "CreationDate": None,
    "ModDate": None,
}


def save_pdf(fig, path: Path) -> Path:
    """Save a matplotlib Figure to ``path`` as PDF with deterministic metadata.

    Closes the figure after writing. Returns ``path`` for fluent use.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        path,
        format="pdf",
        bbox_inches="tight",
        facecolor="white",
        metadata=_PDF_METADATA,
    )
    plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Component ABC
# ---------------------------------------------------------------------------


class Component(ABC):
    """Base class for every chart and table in the report.

    Subclasses populate ``name`` (used as the artifact filename stem) and
    implement ``render_html``. Table subclasses also override ``render_csv``
    / ``render_tex``; chart subclasses override ``figure`` so the same
    matplotlib Figure can be embedded as a base64 PNG (HTML) or saved as a
    deterministic PDF (paper export).
    """

    name: str = "component"

    def __init_subclass__(cls, **kwargs) -> None:
        # Wrap each concrete subclass's render_html with a per-instance cache.
        # render_html is called once during section composition and again from
        # dump_to; for chart components each call invokes matplotlib via
        # figure(), so memoizing the HTML string avoids a redundant render.
        super().__init_subclass__(**kwargs)
        if "render_html" in cls.__dict__:
            raw = cls.__dict__["render_html"]

            @functools.wraps(raw)
            def cached(self, _raw=raw):
                cached_value = self.__dict__.get("_render_html_cache")
                if cached_value is not None:
                    return cached_value
                result = _raw(self)
                self.__dict__["_render_html_cache"] = result
                return result

            cls.render_html = cached  # type: ignore[method-assign]

    @abstractmethod
    def render_html(self) -> str:
        """Return the HTML fragment for this component (may be empty)."""

    def render_csv(self) -> bytes | None:
        """Return CSV bytes for this component, or None if not applicable."""
        return None

    def render_tex(self) -> str | None:
        """Return a standalone-includable LaTeX snippet, or None if not
        applicable. Tables override this to emit ``booktabs`` markup; charts
        leave it ``None`` and ship a PDF figure instead."""
        return None

    def _compute(self) -> dict:
        """Aggregate the data this component needs into a dict.

        Chart subclasses override this to do all per-eval iteration / numeric
        crunching once. ``_data`` caches the result so HTML alt-text, CSV/TeX
        export, base64 PNG figure, and PDF figure all read from the same
        dict instead of re-aggregating from ``self.raw``-style inputs.
        Non-chart Components leave it returning ``{}``.
        """
        return {}

    @functools.cached_property
    def _data(self) -> dict:
        return self._compute()

    def _draw(self, data: dict):
        """Build a matplotlib ``Figure`` from precomputed ``data``.

        Chart subclasses override this. Default returns ``None`` so non-chart
        Components naturally produce no figure.
        """
        return None

    def figure(self):
        """Return a freshly-built matplotlib ``Figure`` for chart components,
        or ``None`` for non-chart components. Called once per render path
        (HTML and PDF each get their own Figure so the caller can close it)."""
        return self._draw(self._data)

    def render_pdf(self, path: Path) -> Path | None:
        """Save a PDF rendering of this component to ``path`` and return the
        path on success. Default implementation produces a PDF when
        ``figure()`` returns a Figure; otherwise returns ``None``."""
        fig = self.figure()
        if fig is None:
            return None
        return save_pdf(fig, path)

    def dump_to(self, directory: Path) -> None:
        """Write all available representations under ``directory``.

        Always writes ``<name>.html``. Writes ``<name>.csv`` when
        ``render_csv`` is non-None, ``<name>.tex`` when ``render_tex`` is
        non-None, and ``<name>.pdf`` when ``render_pdf`` returns a path.
        """
        directory.mkdir(parents=True, exist_ok=True)
        html_text = self.render_html()
        (directory / f"{self.name}.html").write_text(html_text, encoding="utf-8")
        csv_payload = self.render_csv()
        if csv_payload is not None:
            (directory / f"{self.name}.csv").write_bytes(csv_payload)
        tex_text = self.render_tex()
        if tex_text is not None:
            (directory / f"{self.name}.tex").write_text(tex_text, encoding="utf-8")
        pdf_target = directory / f"{self.name}.pdf"
        self.render_pdf(pdf_target)
