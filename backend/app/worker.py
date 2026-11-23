import logging

from .db import init_db
from .services.scheduler import run_scheduler_forever


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from . import config
    config.validate_runtime_config()
    # the worker runs COLLECTOR_MAX_PARALLEL collectors and CERT_CHECK_PARALLEL TLS checks at once, each needing a connection
    config.DB_POOL_SIZE = max(config.DB_POOL_SIZE, config.COLLECTOR_MAX_PARALLEL + config.CERT_CHECK_PARALLEL + 4)
    init_db()
    run_scheduler_forever()


if __name__ == "__main__":
    main()
