"""Identity rules (ARCHITECTURE §7.1): stable across renames of line-noise,
strict enough that fuzzy tiers — not the key — handle true renames."""
import unittest

from hh import keys


class TestNormalize(unittest.TestCase):
    def test_unittest_id(self):
        self.assertEqual(
            keys.normalize("unittest", "test_s001.TestS001.test_t000"),
            "unittest::test_s001/test_t000")

    def test_pytest_nodeid_drops_class_target(self):
        # class rename must not break identity: the class segment is dropped
        a = keys.normalize("pytest", "tests/a/test_b.py::TestC::test_x")
        b = keys.normalize("pytest", "tests/a/test_b.py::Renamed::test_x")
        self.assertEqual(a, b)

    def test_param_hash_is_stable_and_distinct(self):
        a = keys.normalize("pytest", "tests/x.py::test_p[p-1]")
        b = keys.normalize("pytest", "tests/x.py::test_p[p-1]")
        c = keys.normalize("pytest", "tests/x.py::test_p[p-2]")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertIn(" :: ", a)          # param hash rides after the marker

    def test_go_and_node_shapes(self):
        self.assertTrue(keys.normalize("go", "example.com/calc::TestAdd").startswith("go::"))
        self.assertTrue(keys.normalize("node", "tests/x.mjs/some test").startswith("node::"))

    def test_file_and_name_of(self):
        """Pin *current* identity behaviour, quirks included (stores are keyed on
        it): the runner prefix is stripped and the file extension becomes a path
        segment. Changing this would orphan every existing store, so it is
        documented here rather than 'fixed'."""
        k = keys.normalize("pytest", "tests/a/test_b.py::TestC::test_x")
        self.assertEqual(k, "pytest::tests/a/test_b/py/test_x")
        self.assertEqual(keys.file_of(k), "tests/a/test_b/py")
        self.assertEqual(keys.name_of(k), "test_x")

    def test_suite_signature_order_insensitive(self):
        self.assertEqual(keys.suite_signature(["a", "b"]), keys.suite_signature(["b", "a"]))


if __name__ == "__main__":
    unittest.main()
