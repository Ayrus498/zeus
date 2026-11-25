import asyncio
from app.toolchain.runner import Runner


async def test_cancel():
    r = Runner()
    t = asyncio.create_task(r.run("x", "sleep 30", "/tmp", 60))
    await asyncio.sleep(0.5)
    assert r.cancel("x")
    res = await asyncio.wait_for(t, 5)
    assert res["status"] == "cancelled"


async def test_streams_both():
    got = []

    async def cb(s, t): got.append((s, t))
    res = await Runner().run("y", "echo a; echo b >&2", "/tmp", 10, cb)
    assert res["exit_code"] == 0 and {s for s, _ in got} == {"stdout", "stderr"}
