import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: runs the planner to its full budget (10+ min each); EVE_SLOW=1 runs them")


def pytest_collection_modifyitems(config, items):
    try:
        import litemapy  # noqa: F401
    except ImportError:                  # the optional GPL-3.0 extra is absent: .litematic tests cannot run
        for it in items:
            if "litematic" in open(it.fspath, encoding="utf-8").read():
                it.add_marker(pytest.mark.skip(reason="needs the optional litemapy extra (GPL-3.0)"))
    if os.environ.get("EVE_SLOW") != "1":
        for it in items:
            if "slow" in it.keywords:
                it.add_marker(pytest.mark.skip(reason="slow: EVE_SLOW=1 runs it"))


@pytest.fixture
def fast_finish(monkeypatch):
    """Generation-loop tests: skip the scaffold planner + placement simulation for the written design (minutes for a
    two-storey house). tests/test_placecheck.py runs one generation end to end with them."""
    from eve_design import generate
    from eve_design.validate import validate

    def finish(rec, gd, brief):
        g, _, b, _ = rec["_built"]
        rec["_built"] = (g, None, b, validate(g, gd, brief=brief, bom=b, skip_buildable=True))
        return True
    monkeypatch.setattr(generate, "_finish", finish)
