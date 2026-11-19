import unittest

from app import qi
from app.qi import QIError


class TestQI(unittest.TestCase):
    def test_age_levels(self):
        self.assertEqual(qi.generalize_value("age", 34, 0), "34")
        self.assertEqual(qi.generalize_value("age", 34, 1), "30-34")
        self.assertEqual(qi.generalize_value("age", 34, 2), "30-39")
        self.assertEqual(qi.generalize_value("age", 34, 3), "30-44")
        self.assertEqual(qi.generalize_value("age", 34, 4), "*")

    def test_zip_levels(self):
        self.assertEqual(qi.generalize_value("zip", "10009", 0), "10009")
        self.assertEqual(qi.generalize_value("zip", "10009", 1), "100**")
        self.assertEqual(qi.generalize_value("zip", "10009", 2), "10***")
        self.assertEqual(qi.generalize_value("zip", "10009", 3), "*")

    def test_occupation_levels(self):
        self.assertEqual(qi.generalize_value("occupation", "软件工程师", 0), "软件工程师")
        self.assertEqual(qi.generalize_value("occupation", "软件工程师", 1), "工程技术类")
        self.assertEqual(qi.generalize_value("occupation", "软件工程师", 2), "*")

    def test_gender(self):
        self.assertEqual(qi.generalize_value("gender", "男", 0), "男")
        self.assertEqual(qi.generalize_value("gender", "男", 1), "*")

    def test_covered_raw_and_compat(self):
        # 细粒度值落在粗桶内
        self.assertIn("10009", qi.covered_raw("zip", "10***"))
        self.assertTrue(qi.compatible("zip", "10009", "10***"))
        self.assertFalse(qi.compatible("zip", "20005", "10***"))
        # 年龄跨层级相容
        self.assertTrue(qi.compatible("age", "34", "30-39"))
        self.assertFalse(qi.compatible("age", "41", "30-39"))
        # 职业大类
        self.assertTrue(qi.compatible("occupation", "软件工程师", "工程技术类"))
        self.assertFalse(qi.compatible("occupation", "医生", "工程技术类"))
        # * 与一切相容
        self.assertTrue(qi.compatible("age", "34", "*"))

    def test_invalid(self):
        with self.assertRaises(QIError):
            qi.generalize_value("age", 34, 9)
        with self.assertRaises(QIError):
            qi.generalize_value("zip", "99999", 0)
        with self.assertRaises(QIError):
            qi.generalize_value("occupation", "宇航员", 0)


if __name__ == "__main__":
    unittest.main()
