# hh/benchmark asset: applied as sitecustomize.py for tornado runs (both arms).
# Mirrors tornado/test/runtests.py:146 — tornado's own test runner silences the
# access logger; without it, plain-unittest runs of the suite report ~540 false
# "unexpected logs found" failures. Any repo may declare "sitecustomize":
# "asset-name.py" in repos.json; it is copied to _work/env/sitecustomize.py,
# which every benchmark subprocess gets on PYTHONPATH (see config.env_for).
import logging
logging.getLogger("tornado.access").setLevel(logging.CRITICAL)
