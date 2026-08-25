"""In-subprocess wrapper: loads the dataset, execs user code, captures figures.

Runs as an untrusted child process (see server.py for the sandbox limits). It
provides the user code with `pd`, `np`, `plt`, and a preloaded `df` DataFrame,
then serializes any matplotlib figures the code produced as PNG images.
"""
import base64
import io
import json
import os
import sys
import traceback

import matplotlib
matplotlib.use("Agg")  # headless; render to PNG buffers
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def main():
    data_path = os.environ.get("DATA_PATH", "")
    df = None
    if data_path:
        try:
            df = pd.read_csv(data_path)
        except Exception as exc:
            print(f"[could not load dataset: {exc}]", file=sys.stderr)

    with open(os.environ["USER_CODE"]) as fh:
        code = fh.read()

    ns = {
        "__name__": "__main__",
        "pd": pd, "np": np, "plt": plt,
        "df": df, "DATA_PATH": data_path,
    }
    error = ""
    try:
        exec(compile(code, "<playground>", "exec"), ns)  # noqa: S102
    except SystemExit:
        pass
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc().strip()
        error = tb.rsplit("\n", 1)[-1] if "\n" in tb else tb

    # Capture every open matplotlib figure as a base64 PNG.
    images = []
    for num in plt.get_fignums():
        buf = io.BytesIO()
        try:
            plt.figure(num).savefig(buf, format="png", bbox_inches="tight", dpi=110)
            images.append(base64.b64encode(buf.getvalue()).decode())
        except Exception:  # noqa: BLE001
            pass

    result = {"error": error, "images": images}
    with open(os.environ["RESULTS_PATH"], "w") as fh:
        json.dump(result, fh)


if __name__ == "__main__":
    main()
