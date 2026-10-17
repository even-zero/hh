import json, os, time, unittest
DIR = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(DIR, "durations.json")))

class TestS005(unittest.TestCase):
    def test_t000(self):
        time.sleep(DUR["test_a1005.t000"])
    def test_t001(self):
        time.sleep(DUR["test_a1005.t001"])
    def test_t002(self):
        time.sleep(DUR["test_a1005.t002"])
    def test_t003(self):
        time.sleep(DUR["test_a1005.t003"])
    def test_t004(self):
        time.sleep(DUR["test_a1005.t004"])
    def test_t005(self):
        time.sleep(DUR["test_a1005.t005"])
    def test_t006(self):
        time.sleep(DUR["test_a1005.t006"])
    def test_t007(self):
        time.sleep(DUR["test_a1005.t007"])
    def test_t008(self):
        time.sleep(DUR["test_a1005.t008"])
    def test_t009(self):
        time.sleep(DUR["test_a1005.t009"])
    def test_t010(self):
        time.sleep(DUR["test_a1005.t010"])

