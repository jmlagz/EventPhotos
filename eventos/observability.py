import json
import logging


operations_logger = logging.getLogger("eventos.operations")


def log_operation(event, **fields):
    """Emit one structured event from explicitly selected, safe fields."""
    payload = {"event": event}
    payload.update(
        (name, value)
        for name, value in fields.items()
        if value is not None
    )
    operations_logger.info(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    )
