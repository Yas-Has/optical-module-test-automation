"""Whole program: the simulator and the analysis agree with the math in the design (section 2.7).

With the default fault rates, the predicted device pass rate is about 66%:
  0.999^400 (no impossible values in 400 readings) x ~0.99 (communication retries that also fail)
"""

from __future__ import annotations

import pytest

from tests.helpers import build_config, run

RUNS = 200


@pytest.mark.slow
def test_pass_rate_matches_prediction(tmp_path):
    passes = sum(run(build_config(tmp_path, seed=seed)).overall_result == "PASS" for seed in range(RUNS))
    rate = passes / RUNS
    print(f"\npass rate over {RUNS} seeded runs: {rate:.1%} (predicted about 66%)")
    assert 0.55 <= rate <= 0.77  # +/- about 3 standard errors
