"""Independent processing branches for side-by-side review; never edits the saved line."""
import base64
import io

import numpy as np

from gpr_analyzer.processing.views import grey_view
from gpr_analyzer.processing.workflows import run_workflow


def compare_workflows(raw, left: dict, right: dict) -> dict:
    a, b = run_workflow(raw, left), run_workflow(raw, right)
    clip = max(float(np.percentile(np.abs(r.analysis.data), 99)) for r in (a, b)) or 1.
    out = []
    for label, result in zip(("A", "B"), (a, b)):
        fig = grey_view(result.analysis, title=f"Workflow {label} | analysis | shared amplitude scale")
        fig.axes[0].images[0].set_clim(-clip, clip)
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        out.append({"png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
                    "parameters": result.parameter_table(), "n_samples": result.analysis.n_samples,
                    "velocity_m_per_ns": result.velocity_m_per_ns})
    return {"left": out[0], "right": out[1], "shared_amplitude_clip": clip,
            "note": "Independent analysis branches on the same raw line; saved results/reviews unchanged"}
