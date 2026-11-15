import asyncio
import pytest


def test_smoke():
    """Basic smoke test to ensure test runner works."""
    assert True


@pytest.mark.asyncio
async def test_async_smoke():
    """Verify asyncio test mode works cleanly."""
    await asyncio.sleep(0.01)
    assert True
