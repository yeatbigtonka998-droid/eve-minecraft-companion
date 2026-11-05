"""Run the mind service: `python -m eve_mind` (needs EVE_LINK_TOKEN; EVE_DB, EVE_PORT, EVE_ROOT optional)."""
import asyncio
import faulthandler
import logging

from . import config
from .hub import Hub
from .mind import Mind, open_db
from .registry import Registry

log = logging.getLogger("eve")


async def start(cfg):
    reg = Registry(cfg.registry_path)
    mind = Mind(cfg, reg, open_db(cfg))
    hub = await Hub(cfg, mind).start()
    mind.supervisor = asyncio.get_running_loop().create_task(mind.supervise())
    log.info("lane %s; root %s; envelope %s (v%s, hash %s); registry %s hash %s; db %s; %s, mode %s",
             config.lane(), cfg.root, cfg.envelope_path, cfg.envelope["envelope_version"], cfg.envelope_hash[:12], reg.version,
             reg.hash[:12], cfg.db_path, "STOPPED" if mind.stopped else "not stopped", mind.mode)
    return mind, hub


async def watchdog():
    """I23: a loop blocked 3 s dumps every thread's stack to stderr (mind.log), so a stall names its own cause."""
    while True:
        faulthandler.dump_traceback_later(3)    # re-armed each second; fires only if this coroutine stops running
        await asyncio.sleep(1)


async def serve_forever(cfg):
    mind, hub = await start(cfg)
    mind.watchdog = asyncio.get_running_loop().create_task(watchdog())
    print(f"READY port={hub.port}", flush=True)
    await asyncio.Future()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(serve_forever(config.load()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
