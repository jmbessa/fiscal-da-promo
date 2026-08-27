import os
import runpy
import sys

root = os.getcwd()
sys.path.insert(0, os.path.join(root, "src"))
sys.path.insert(0, root)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
runpy.run_path(sys.argv[1], run_name="__main__")
