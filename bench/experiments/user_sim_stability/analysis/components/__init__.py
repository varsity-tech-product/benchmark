"""Component package for stability report.

Each chart / table is a Component (subclass of base.Component) that owns its
own HTML rendering, optional CSV export, and dump_to(directory) logic. Sections
in analysis/report.py compose Component HTML rather than concatenating raw
table markup.
"""

from experiments.user_sim_stability.analysis.components.base import Component

__all__ = ["Component"]
