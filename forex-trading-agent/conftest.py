import os
import sys

# Make `fxagent` importable however pytest is started.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
