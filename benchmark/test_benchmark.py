# flake8: noqa: E501

import tempfile
import unittest
from pathlib import Path

from benchmark.benchmark import (cleanup_test_output, filter_by_keywords,
                                 read_run_info, resolve_seed,
                                 shuffle_with_seed, write_run_info)


class TestCleanupTestOutput(unittest.TestCase):
    def test_removes_timing_info(self):
        testdir = Path("/tmp/exercises/python/bob")
        output = "Ran 5 tests in 0.003s\nOK"
        self.assertEqual(cleanup_test_output(output, testdir), "Ran 5 tests \nOK")

    def test_output_without_timing_is_unchanged(self):
        testdir = Path("/tmp/exercises/python/bob")
        self.assertEqual(cleanup_test_output("OK", testdir), "OK")

    def test_replaces_testdir_path_with_name(self):
        testdir = Path("/Users/someone/aider/tmp.benchmarks/bob")
        output = 'File "/Users/someone/aider/tmp.benchmarks/bob/bob_test.py", line 14'
        self.assertEqual(cleanup_test_output(output, testdir), 'File "bob/bob_test.py", line 14')


class TestExerciseSelection(unittest.TestCase):
    dnames = ["2024-01-01-python-bob", "2024-01-01-python-alphametics", "2024-01-01-go-bob"]

    def test_keyword_filter_keeps_each_exercise_once(self):
        # "bob" and "python" both match python-bob; it must still appear once.
        result = filter_by_keywords(self.dnames, ["python", "bob"])
        self.assertEqual(sorted(result), sorted(self.dnames))

    def test_keyword_filter_drops_non_matches(self):
        self.assertEqual(filter_by_keywords(self.dnames, ["go"]), ["2024-01-01-go-bob"])

    def test_same_seed_gives_same_order(self):
        a = shuffle_with_seed(self.dnames, 42)
        b = shuffle_with_seed(self.dnames, 42)
        self.assertEqual(a, b)
        self.assertEqual(sorted(a), sorted(self.dnames))

    def test_shuffle_does_not_mutate_input(self):
        original = list(self.dnames)
        shuffle_with_seed(self.dnames, 7)
        self.assertEqual(self.dnames, original)

    def test_run_info_roundtrip_and_seed_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            self.assertIsNone(read_run_info(run_dir))
            write_run_info(run_dir, 123, "abc1234")
            self.assertEqual(read_run_info(run_dir), {"seed": 123, "exercises_commit": "abc1234"})
            # An earlier run's seed is reused when no --seed is given...
            self.assertEqual(resolve_seed(run_dir, None), 123)
            # ...but an explicit --seed always wins.
            self.assertEqual(resolve_seed(run_dir, 9), 9)

    def test_fresh_dir_gets_a_random_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(resolve_seed(Path(tmp) / "new", None), int)
