import json, os, time, unittest
DIR = os.path.dirname(os.path.abspath(__file__))
DUR = json.load(open(os.path.join(DIR, "durations.json")))

class TestS014(unittest.TestCase):
    def test_t000(self):
        time.sleep(DUR["test_80014.t000"])

