import json, os, time, unittest
DIR = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(DIR, "durations.json")))

class TestS009(unittest.TestCase):
    def test_t000(self):
        time.sleep(DUR["test_e8009.t000"])
    def test_t001(self):
        time.sleep(DUR["test_e8009.t001"])
    def test_t002(self):
        time.sleep(DUR["test_e8009.t002"])
    def test_t003(self):
        time.sleep(DUR["test_e8009.t003"])
    def test_t004(self):
        time.sleep(DUR["test_e8009.t004"])
    def test_t005(self):
        time.sleep(DUR["test_e8009.t005"])
    def test_t006(self):
        time.sleep(DUR["test_e8009.t006"])
    def test_t007(self):
        time.sleep(DUR["test_e8009.t007"])
    def test_t008(self):
        time.sleep(DUR["test_e8009.t008"])
    def test_t009(self):
        time.sleep(DUR["test_e8009.t009"])
    def test_t010(self):
        time.sleep(DUR["test_e8009.t010"])

