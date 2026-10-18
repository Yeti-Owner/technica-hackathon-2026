"""One-shot click gating: generated audio is not a real-speech accuracy benchmark."""
import unittest
from unittest.mock import patch
import numpy as np
from browserkit.voice import ClickDetector
from test_voice import vowel


class ClickTests(unittest.TestCase):
    def setUp(self):
        self.gate = ClickDetector()
        self.now = 0.
        self.silence = np.zeros(2880)

    def feed(self, block, seconds):
        events = []
        for _ in range(round(seconds/.03)):
            self.now += .03
            event = self.gate.update(block, 48000, self.now)
            if event:
                events.append(event)
        return events

    def test_long_holds_click_once_and_need_real_quiet_to_rearm(self):
        self.feed(self.silence, .4)
        self.assertEqual(self.feed(vowel([350, 850, 2500]), .4), [])
        self.assertEqual(self.feed(vowel([350, 850, 2500]), 2.), [1])
        self.assertEqual(self.feed(vowel([800, 1300, 2600]), 1.), [])
        self.feed(self.silence, .5)
        self.assertEqual(self.feed(vowel([800, 1300, 2600]), 1.), [-1])

    def test_startup_sound_and_brief_syllables_do_not_click(self):
        self.assertEqual(self.feed(vowel([350, 850, 2500]), 1.), [])
        for formants in [[350, 850, 2500], [800, 1300, 2600]]:
            self.feed(self.silence, .5)
            self.assertEqual(self.feed(vowel(formants), .3), [])
            self.assertEqual(self.feed(self.silence, .5), [])

    def test_noise_and_changing_speech_like_syllables_do_not_click(self):
        rng = np.random.default_rng(42)
        self.feed(self.silence, .4)
        self.assertEqual(self.feed(rng.normal(0, .02, 2880), 1.), [])
        self.feed(self.silence, 1.)
        for _ in range(8):
            self.assertEqual(self.feed(vowel([350, 850, 2500]), .15), [])
            self.assertEqual(self.feed(vowel([800, 1300, 2600]), .15), [])
        self.assertEqual(self.feed(vowel([350, 850, 2500]), 1.), [])

    def test_single_tone_background_does_not_click(self):
        for hz in [80, 120, 200, 350, 800, 1300]:
            self.gate = ClickDetector()
            self.feed(self.silence, .5)
            tone = .03*np.sin(2*np.pi*hz*np.arange(2880)/48000)
            self.assertEqual(self.feed(tone, 1.5), [], hz)

    def test_lost_audio_and_cancellation_cannot_complete_a_click(self):
        self.feed(self.silence, .4)
        self.feed(vowel([350, 850, 2500]), .45)
        self.now += .3
        self.assertEqual(self.feed(vowel([350, 850, 2500]), 1.), [])
        self.feed(self.silence, .5)
        self.feed(vowel([350, 850, 2500]), .45)
        self.gate.cancel()
        self.assertEqual(self.feed(vowel([350, 850, 2500]), 1.), [])

    def test_short_detection_gap_is_tolerated_but_not_counted(self):
        self.feed(self.silence, .4)
        block = vowel([350, 850, 2500])
        self.assertEqual(self.feed(block, .4), [])
        self.assertEqual(self.feed(self.silence, .06), [])
        self.assertEqual(self.feed(block, .3), [1])

    def test_soft_vowels_and_ooo_with_weak_upper_harmonics(self):
        for formants, action in [([250, 650, 2500], 1), ([300, 850, 2500], 1),
                                 ([300, 1500, 2600], 1), ([800, 1300, 2600], -1)]:
            for pitch in [100, 140, 200, 260, 320]:
                with self.subTest(formants=formants, pitch=pitch):
                    self.gate = ClickDetector()
                    self.feed(self.silence, .5)
                    self.assertEqual(self.feed(vowel(formants, pitch)*.025, .75), [action])

    def test_120ms_dropout_preserves_progress_but_cannot_fire_in_silence(self):
        for formants, action in [([350, 850, 2500], 1), ([800, 1300, 2600], -1)]:
            self.gate = ClickDetector()
            self.feed(self.silence, .5)
            block = vowel(formants)
            self.assertEqual(self.feed(block, .45), [])
            self.assertEqual(self.feed(self.silence, .12), [])
            self.assertEqual(self.feed(block, .15), [action])

    def test_single_wrong_estimate_survives_but_repeated_uncertainty_cannot_click(self):
        self.feed(self.silence, .5)
        block = vowel([350, 850, 2500])
        self.assertEqual(self.feed(block, .3), [])
        with patch.object(self.gate.detector, 'features', return_value=(np.log([800, 1300]), .9, 140)):
            self.assertEqual(self.feed(block, .03), [])
        self.assertEqual(self.feed(block, .3), [1])
        self.feed(self.silence, .5)
        self.feed(block, .3)
        with patch.object(self.gate.detector, 'features', return_value=(None, .8, 140)):
            self.assertEqual(self.feed(block, 1.), [])
        self.assertEqual(self.feed(block, 1.), [])

    def test_completed_click_learns_but_brief_attempt_does_not(self):
        block = vowel([380, 900, 2500])
        initial = self.gate.detector.centers[1].copy()
        self.feed(self.silence, .5)
        self.feed(block, .3)
        np.testing.assert_array_equal(self.gate.detector.centers[1], initial)
        self.assertEqual(self.feed(block, .3), [1])
        self.assertGreater(np.linalg.norm(self.gate.detector.centers[1]-initial), 0)
        self.assertTrue(np.all(np.abs(self.gate.detector.centers[1]-initial) <= .14))

    def test_separate_short_pulses_do_not_accumulate_into_a_click(self):
        self.feed(self.silence, .5)
        block = vowel([350, 850, 2500])
        for _ in range(20):
            self.assertEqual(self.feed(block, .06), [])
            self.assertEqual(self.feed(self.silence, .12), [])


if __name__ == '__main__':
    unittest.main()
