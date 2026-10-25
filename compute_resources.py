"""One CPU budget shared by preview and export encoders."""
import os
import threading
from pathlib import Path

available = os.cpu_count() or 1
if hasattr(os, "sched_getaffinity"):
    available = min(available, len(os.sched_getaffinity(0)))
try:
    quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
    if quota != 'max':
        available = min(available, max(1, int(quota) // int(period)))
except (OSError, ValueError):
    pass
try:
    requested = int(os.environ.get('YUXU_CPU_BUDGET', '14' if os.name == 'nt' else '20'))
except ValueError:
    requested = 14 if os.name == 'nt' else 20
CPU_THREADS = max(1, min(requested, available))
ENCODING_LOCK = threading.Lock()
