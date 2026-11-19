"""The UTF-8 net for the pys processes the tests start (servers, brains, hooks): they inherit
this, so a text open, read_text or subprocess in pys that leaves out encoding= raises there
too, and the test that needed it fails. The test process itself gets the net from
PYTHONWARNDEFAULTENCODING=1 (CI) and pyproject's filterwarnings."""
import os

os.environ.setdefault("PYTHONWARNDEFAULTENCODING", "1")
os.environ.setdefault("PYTHONWARNINGS", "error::EncodingWarning")
