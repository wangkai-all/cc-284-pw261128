#!/usr/bin/env python3
"""服务启动器：支持隔离模式运行。

    python3 -I run.py --data data/state.json --port 8000

用 -I 启动时解释器忽略 PYTHONPATH 与当前目录，本文件显式把仓库根加入 sys.path，
保证只加载仓库内代码（不受外部同名模块污染）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.server import main  # noqa: E402

if __name__ == "__main__":
    main()
