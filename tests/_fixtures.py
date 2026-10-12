"""共享测试夹具。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CONFIG_PATH = os.path.join(ROOT, "configs", "test.json")
DEFAULT_CONFIG_PATH = os.path.join(ROOT, "configs", "default.json")
