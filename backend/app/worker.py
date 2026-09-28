"""LogForge worker runner for background execution."""
import logging
import signal
import time

from app.config import get_settings
from app.services import scheduler

stopping = False


def stop(*_):
    global stopping
    stopping = True


def main():
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    logging.info("Starting LogForge background worker...")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    scheduler.start()
    try:
        while not stopping:
            time.sleep(1)
    finally:
        scheduler.stop()
        logging.info("LogForge worker stopped cleanly.")


if __name__ == "__main__":
    main()
