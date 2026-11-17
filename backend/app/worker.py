import logging

from .db import init_db
from .services.scheduler import run_scheduler_forever


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_db()
    run_scheduler_forever()


if __name__ == "__main__":
    main()
