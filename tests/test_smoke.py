"""Smoke tests — verify package imports and basic configuration."""
from __future__ import annotations


def test_package_imports():
    """Top-level package and core modules import without error."""
    import activist_scout
    from activist_scout import config, domain, utils
    assert activist_scout.__version__


def test_stages_import():
    """All pipeline stages import without error (no API calls)."""
    from activist_scout.stages import (  # noqa: F401
        backtest,
        classify,
        enrich,
        fetch,
        related_party,
        report,
        score,
        screen,
        validate,
    )


def test_main_modules_import():
    """Pipeline / monitor / deep_dive import without error."""
    import activist_scout.deep_dive  # noqa: F401
    import activist_scout.monitor    # noqa: F401
    import activist_scout.pipeline   # noqa: F401


def test_config_paths_resolve():
    from activist_scout import config

    # Project structure is correct
    assert config.PROJECT_ROOT.exists()
    assert config.DATA_DIR.exists()
    # Paths use Path objects
    assert config.CORP_MAP_FILE.parent == config.DATA_DIR


def test_config_constants_present():
    from activist_scout import config

    # Critical scoring constants
    assert "deep_cash" in config.TARGET_WEIGHTS
    assert "buy_days_65pct" in config.ACCUM_WEIGHTS
    assert "treasury_dispose" in config.LEGAL_WEIGHTS
    assert config.TIER_THRESHOLDS["axis_strong"] == 60
    assert config.AVOID_OWNER_PCT == 65.0
