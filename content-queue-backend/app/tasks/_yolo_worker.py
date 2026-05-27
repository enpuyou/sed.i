"""
Subprocess entry point for isolated YOLO PDF extraction.

Invoked by extract_with_yolo() as:
    python /abs/path/to/_yolo_worker.py

Reads pickled (pdf_bytes, url) from stdin, runs _extract_yolo_sync,
writes pickled HTML string to stdout. stderr is inherited from the
parent so all logging appears in the worker's Railway log stream.

torch + ultralytics load here and are freed by the OS when this
process exits — they never enter the main Celery worker's address space.
"""

import pathlib
import pickle
import sys

# Belt-and-suspenders: ensure app.* is importable even if PYTHONPATH
# wasn't inherited. __file__ is .../app/tasks/_yolo_worker.py so
# going up two levels reaches the project root where app/ lives.
_project_root = str(pathlib.Path(__file__).parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def main() -> None:
    pdf_bytes, url = pickle.loads(sys.stdin.buffer.read())

    import torch

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)

    from app.tasks.extraction_implementations import _extract_yolo_sync

    html = _extract_yolo_sync(pdf_bytes, url)

    sys.stdout.buffer.write(pickle.dumps(html))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
