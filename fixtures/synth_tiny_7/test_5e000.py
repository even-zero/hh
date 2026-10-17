import json, os, time, unittest
DIR = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(DIR, "durations.json")))

class TestS000(unittest.TestCase):
    def test_t000(self):
        time.sleep(DUR["test_5e000.t000"])
    def test_t001(self):
        time.sleep(DUR["test_5e000.t001"])
    def test_t002(self):
        time.sleep(DUR["test_5e000.t002"])
    def test_t003(self):
        time.sleep(DUR["test_5e000.t003"])
    def test_t004(self):
        time.sleep(DUR["test_5e000.t004"])
    def test_t005(self):
        time.sleep(DUR["test_5e000.t005"])

