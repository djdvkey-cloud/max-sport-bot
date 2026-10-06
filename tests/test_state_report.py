import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import state_report as R  # noqa: E402


class StateReport(unittest.TestCase):
    def test_empty_dir_does_not_crash_and_writes_nothing(self):
        d = tempfile.mkdtemp()
        out = R.lines(d)
        self.assertTrue(out)
        self.assertEqual(os.listdir(d), [])

    def test_profile_is_hashed_and_zenit_holder_found(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "tribun_members.json"), "w", encoding="utf-8") as f:
            json.dump({"members": {"12345": {"display_name": "Секретное Имя", "clubs": ["zenit"], "active_in_group": True}}}, f)
        text = "\n".join(R.lines(d, owner_id=12345))
        self.assertNotIn("Секретное", text)
        self.assertNotIn("12345", text)
        self.assertIn("(владелец)", text)
        self.assertIn("zenit", text)


if __name__ == "__main__":
    unittest.main()
