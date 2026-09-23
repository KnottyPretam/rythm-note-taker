"""Grammar and vocabulary. No filesystem, no vault -- pure parsing.

Every case here is either a real line from the user's vault or a trap that was
found by hand-probing the regexes before they were written.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "rhythm"))
os.environ.setdefault("RHYTHM_VAULT", "/nonexistent-vault-for-tests")

import board          # noqa: E402
import notes          # noqa: E402
import vocab          # noqa: E402


class Resolve(unittest.TestCase):
    def test_exact_and_case(self):
        self.assertEqual(vocab.resolve("ACTIVE", vocab.ACTIVITIES), "Active")
        self.assertEqual(vocab.resolve("gnc", vocab.PROJECTS), "GNC")

    def test_misspelled(self):
        # the user's own example
        self.assertEqual(vocab.resolve("montiR", vocab.ACTIVITIES), "Monitor")
        self.assertEqual(vocab.resolve("scEptEr", vocab.PROJECTS), "Sceptre")
        self.assertEqual(vocab.resolve("invictis", vocab.PROJECTS), "Invictus")

    def test_unrelated_word_is_not_forced(self):
        self.assertIsNone(vocab.resolve("meeting", vocab.ACTIVITIES))
        self.assertIsNone(vocab.resolve("", vocab.ACTIVITIES))

    def test_a_project_never_looks_like_an_activity(self):
        # what makes the back-compat rule in classify() unambiguous
        for p in vocab.PROJECTS:
            self.assertIsNone(vocab.resolve(p, vocab.ACTIVITIES), p)


class Classify(unittest.TestCase):
    def test_both_slots(self):
        self.assertEqual(vocab.classify("montiR", "scEptEr"), ("Monitor", "Sceptre"))

    def test_untriaged_goes_to_backlog(self):
        self.assertEqual(vocab.classify(None, None), ("Backlog", None))
        self.assertEqual(vocab.classify(None, "GNC"), ("Backlog", "GNC"))

    def test_old_one_slot_project_form(self):
        # every existing note in the vault is this shape
        self.assertEqual(vocab.classify("Sceptre", None), ("Backlog", "Sceptre"))

    def test_unresolvable_is_not_invented(self):
        self.assertEqual(vocab.classify("meeting", "acme"), ("Backlog", None))


class Marker(unittest.TestCase):
    def groups(self, line):
        m = notes.MARKER.match(line)
        return m.group(2, 3, 4) if m else None

    def test_shapes(self):
        self.assertEqual(self.groups("*Active/Sceptre> fix it"),
                         ("Active", "Sceptre", "fix it"))
        self.assertEqual(self.groups("*Active> fix it"), ("Active", None, "fix it"))
        self.assertEqual(self.groups("*> fix it"), (None, None, "fix it"))
        self.assertEqual(self.groups("*/Sceptre> fix it"), (None, "Sceptre", "fix it"))
        self.assertEqual(self.groups("*Active/> fix it"), ("Active", None, "fix it"))
        self.assertEqual(self.groups("- *Route/GNC> in a bullet"),
                         ("Route", "GNC", "in a bullet"))

    def test_traps_do_not_match(self):
        for line in ["*ptr->field = 3;",            # pasted C, documented trap
                     "*see https://x.com> foo",     # a URL
                     "*Route>   ",                  # no invisible cards
                     "a * b > c"]:
            self.assertIsNone(notes.MARKER.match(line), line)

    def test_canonical_write_back_fixes_spelling_not_shape(self):
        m = notes.MARKER.match("*montiR/scEptEr> dummy task")
        line = notes._canonical(m.group(1), m.group(2), m.group(3), m.group(4),
                                "rk-0123456789ab")
        self.assertEqual(line, "*Monitor/Sceptre> dummy task ^rk-0123456789ab")

    def test_canonical_keeps_one_slot_form(self):
        m = notes.MARKER.match("- *scEptEr> keep the bullet")
        line = notes._canonical(m.group(1), m.group(2), m.group(3), m.group(4),
                                "rk-0123456789ab")
        self.assertEqual(line, "- *Sceptre> keep the bullet ^rk-0123456789ab")

    def test_canonical_leaves_unknown_words_alone(self):
        m = notes.MARKER.match("*meeting> talk to someone")
        line = notes._canonical(m.group(1), m.group(2), m.group(3), m.group(4),
                                "rk-0123456789ab")
        self.assertEqual(line, "*meeting> talk to someone ^rk-0123456789ab")


class Card(unittest.TestCase):
    def one(self, line):
        lines = ["## Active", "", line]
        got = board._cards(lines, 0)
        return got[0] if got else None

    def test_project_token(self):
        self.assertEqual(self.one("- [ ] fix it @Sceptre ^rk-0123456789ab"),
                         ("fix it", False, "rk-0123456789ab", "Sceptre"))

    def test_no_project(self):
        self.assertEqual(self.one("- [x] fix it ^rk-0123456789ab"),
                         ("fix it", True, "rk-0123456789ab", None))

    def test_misspelled_project_on_a_card_is_corrected(self):
        self.assertEqual(self.one("- [ ] fix it @scEptEr ^rk-0123456789ab")[3],
                         "Sceptre")

    def test_at_word_in_prose_stays_prose(self):
        # the guard: without it this files itself under a project 'alice'
        self.assertEqual(self.one("- [ ] ping @alice ^rk-0123456789ab"),
                         ("ping @alice", False, "rk-0123456789ab", None))

    def test_prose_at_word_and_a_real_project(self):
        self.assertEqual(self.one("- [ ] mail @bob @GNC ^rk-0123456789ab"),
                         ("mail @bob", False, "rk-0123456789ab", "GNC"))

    def test_hand_written_card_without_an_id(self):
        self.assertEqual(self.one("- [ ] typed straight in @GNC"),
                         ("typed straight in", False, None, "GNC"))

    def test_line_round_trips(self):
        line = board._line("fix it", "Sceptre", "rk-0123456789ab")
        self.assertEqual(self.one(line), ("fix it", False, "rk-0123456789ab", "Sceptre"))


if __name__ == "__main__":
    unittest.main()
