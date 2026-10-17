"""Deterministic signal/state tests; synthetic vowels do not establish real-world accuracy."""
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from pathlib import Path

import numpy as np
from scipy import signal

from browserkit.voice import VowelDetector, Microphone


def vowel(formants, pitch=140, rate=48000):
    t = np.arange(int(rate*.15))/rate
    source = signal.sawtooth(2*np.pi*pitch*t)
    for frequency, bandwidth in zip(formants, [70, 100, 150]):
        radius = np.exp(-np.pi*bandwidth/rate)
        source = signal.lfilter([1], [1, -2*radius*np.cos(2*np.pi*frequency/rate), radius**2], source)
    source = source[-int(rate*.06):]
    return .08 * source / np.max(np.abs(source))


class VoiceTests(unittest.TestCase):
    def test_higher_pitched_aaa(self):
        for pitch in [320, 400, 500, 600]:
            with self.subTest(pitch=pitch):
                detector = VowelDetector()
                block = vowel([800, 1300, 2600], pitch)
                for i in range(8):
                    detector.update(block, 48000, i*.03)
                self.assertEqual(detector.direction, -1)

    def test_voiced_hold_survives_missing_formants_but_cannot_latch_forever(self):
        detector = VowelDetector()
        block = vowel([800, 1300, 2600])
        for i in range(10):
            detector.update(block, 48000, i*.03)
        with patch.object(detector, 'features', return_value=(None, .8, 320)):
            for i in range(10, 40):
                self.assertEqual(detector.update(block, 48000, i*.03), -1)
            # A different spectrum must not keep an unidentified hold alive forever.
            unrelated = vowel([350, 850, 2500])
            for i in range(40, 75):
                detector.update(unrelated, 48000, i*.03)
            self.assertEqual(detector.direction, 0)

    def test_ooo_with_varied_second_resonance(self):
        for f2 in [650, 1000, 1500, 1800]:
            detector = VowelDetector()
            block = vowel([300, f2, 2600])
            for i in range(8):
                detector.update(block, 48000, i*.03)
            self.assertEqual(detector.direction, 1, f2)

    def test_same_quieter_sound_holds_without_formants_then_silence_stops(self):
        detector = VowelDetector()
        block = vowel([350, 850, 2500])
        for i in range(10):
            detector.update(block, 48000, i*.03)
        with patch.object(detector, 'features', return_value=(None, .8, 140)):
            for i in range(10, 100):
                self.assertEqual(detector.update(block*.2, 48000, i*.03), 1)
        detector.update(np.zeros_like(block), 48000, 3.6)
        self.assertEqual(detector.direction, 0)

    def test_gap_coasts_fades_recovers_without_rearming_and_expires(self):
        detector = VowelDetector()
        block = vowel([350, 850, 2500])
        for i in range(10):
            detector.update(block, 48000, i*.03)
        self.assertEqual(detector.strength(.5), 1.)
        self.assertGreater(detector.strength(.7), 0.)
        self.assertLess(detector.strength(.7), 1.)
        self.assertEqual(detector.update(block, 48000, .72), 1)
        self.assertEqual(detector.strength(.72), 1.)
        self.assertEqual(detector.strength(1.3), 0.)
        self.assertEqual(detector.direction, 0)

    def test_microphone_checks_default_and_honors_explicit_selection(self):
        mic = Microphone()
        stream = Mock()
        stream.start.side_effect = lambda: mic.blocks.put((0., np.zeros(100), False))
        sd = SimpleNamespace(default=SimpleNamespace(device=[3, 4]),
                             query_devices=Mock(return_value={'name': 'Test microphone', 'default_samplerate': 48000}),
                             check_input_settings=Mock(), InputStream=Mock(return_value=stream))
        with patch.dict('sys.modules', {'sounddevice': sd}):
            mic.start()
            self.assertEqual(sd.InputStream.call_args.kwargs['device'], 3)
            self.assertEqual(mic.name, 'Test microphone')
            sd.check_input_settings.assert_called_once()
            mic.close()
            sd.default.device = [5, 4]
            mic.start()
            self.assertEqual(sd.InputStream.call_args.kwargs['device'], 5)
            mic.close()
            mic.start(7)
            self.assertEqual(sd.InputStream.call_args.kwargs['device'], 7)
            mic.close()

    def test_vowels_at_different_pitches(self):
        for pitch in [100, 140, 200, 260]:
            for direction, formants in [(1, [350, 850, 2500]), (-1, [800, 1300, 2600])]:
                with self.subTest(pitch=pitch, direction=direction):
                    detector = VowelDetector()
                    block = vowel(formants, pitch)
                    for i in range(20):
                        detector.update(block, 48000, i*.03)
                    self.assertEqual(detector.direction, direction)

    def test_silence_noise_and_brief_sounds_do_not_start(self):
        detector = VowelDetector()
        for block in [np.zeros(2880), np.random.default_rng(4).normal(0, .03, 2880)]:
            for i in range(30):
                self.assertEqual(detector.update(block, 48000, i*.03), 0)
        block = vowel([350, 850, 2500])
        for i in range(2):
            self.assertEqual(detector.update(block, 48000, 2+i*.03), 0)

    def test_quiet_vowels_start_within_120ms_of_analysis(self):
        for rate in [44100, 48000]:
            for direction, formants in [(1, [350, 850, 2500]), (-1, [800, 1300, 2600])]:
                with self.subTest(rate=rate, direction=direction):
                    detector = VowelDetector()
                    quiet = vowel(formants, rate=rate)*.025
                    self.assertLess(np.sqrt(np.mean(quiet**2)), .002)
                    outputs = [detector.update(quiet, rate, i*.03) for i in range(5)]
                    self.assertIn(direction, outputs)

    def test_single_miss_and_pitch_octave_jump_do_not_restart_activation(self):
        detector = VowelDetector()
        feature = np.log([350., 850.])
        frames = [(feature, .9, 140), (None, .4, None),
                  (feature, .9, 280), (feature, .9, 140), (feature, .9, 140)]
        with patch.object(detector, 'features', side_effect=frames):
            outputs = [detector.update(np.ones(2880)*.02, 48000, i*.03) for i in range(5)]
        self.assertEqual(outputs[-1], 1)

    def test_ambient_tracks_noise_without_absorbing_held_vowel(self):
        detector = VowelDetector()
        rng = np.random.default_rng(8)
        for i in range(120):
            self.assertEqual(detector.update(rng.normal(0, .001, 2880), 48000, i*.03), 0)
        self.assertGreater(detector.noise, .0008)
        self.assertLess(detector.noise, .0012)
        baseline = detector.noise
        block = vowel([350, 850, 2500])
        for i in range(30):
            detector.update(block, 48000, 4+i*.03)
        self.assertEqual(detector.direction, 1)
        self.assertEqual(detector.noise, baseline)

    def test_hold_survives_gap_then_stops_and_never_flips_immediately(self):
        detector = VowelDetector()
        down = vowel([350, 850, 2500])
        for i in range(20):
            detector.update(down, 48000, i*.03)
        self.assertEqual(detector.update(np.zeros(2880), 48000, .8), 1)
        self.assertEqual(detector.update(down, 48000, .81), 1)
        self.assertEqual(detector.update(vowel([800, 1300, 2600]), 48000, .84), 1)
        self.assertEqual(detector.update(np.zeros(2880), 48000, 1.5), 0)

    def test_learning_is_bounded_saved_and_corrupt_profiles_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"profile.json"
            detector = VowelDetector(path)
            block = vowel([380, 900, 2500])
            for i in range(500):
                detector.update(block, 48000, i*.03)
            self.assertGreater(np.linalg.norm(detector.centers[1]-detector.DEFAULTS[1]), 0)
            self.assertTrue(np.all(np.abs(detector.centers[1]-detector.DEFAULTS[1]) <= .14))
            detector.save()
            np.testing.assert_allclose(VowelDetector(path).centers[1], detector.centers[1])
            path.write_text("broken")
            np.testing.assert_allclose(VowelDetector(path).centers[1], detector.DEFAULTS[1])


if __name__ == '__main__':
    unittest.main()
