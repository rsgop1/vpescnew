import sys, os

# Automatically include local persistent python_libs in sys.path
_libs = os.path.join(os.path.dirname(__file__), "python_libs")
if os.path.isdir(_libs) and _libs not in sys.path:
    sys.path.insert(0, _libs)
