"""The backtest runs on small data, never touches the AI, and reports both policies."""

import subprocess
import sys

from planner.backtest import run_backtest
from planner.config import ROOT


def test_backtest_reports_both_policies(clean_data):
    clean_data.backtest["weeks"] = 20
    r = run_backtest(clean_data)
    assert set(r["monthly_cycle"]) == set(r["rules_twice_weekly"])
    assert r["stockouts"]["warned_1_to_6_weeks_ahead"] <= r["stockouts"]["episodes"]
    assert len(r["weekly"]) == 20
    assert len(r["skus"]) == 40


def test_backtest_does_not_import_the_ai_client():
    # checked in a fresh Python process, so other tests loading the AI module don't matter
    code = ("import sys, planner.backtest; "
            "print(any(m.startswith(('google.genai', 'planner.ai')) for m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
