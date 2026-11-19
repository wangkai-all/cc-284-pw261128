"""测试入口：在 -I（隔离模式）下显式注入仓库根目录后运行发现。

用法：python3 -I tests/run_tests.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

loader = unittest.TestLoader()
suite = loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
