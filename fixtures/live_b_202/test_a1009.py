import json, os, time, unittest
DIR = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(DIR, "durations.json")))

class TestS009(unittest.TestCase):
    def test_t000(self):
        time.sleep(DUR["test_a1009.t000"])
    def test_t001(self):
        time.sleep(DUR["test_a1009.t001"])
    def test_t002(self):
        time.sleep(DUR["test_a1009.t002"])
    def test_t003(self):
        time.sleep(DUR["test_a1009.t003"])
    def test_t004(self):
        time.sleep(DUR["test_a1009.t004"])
    def test_t005(self):
        time.sleep(DUR["test_a1009.t005"])
    def test_t006(self):
        time.sleep(DUR["test_a1009.t006"])
    def test_t007(self):
        time.sleep(DUR["test_a1009.t007"])
    def test_t008(self):
        time.sleep(DUR["test_a1009.t008"])

