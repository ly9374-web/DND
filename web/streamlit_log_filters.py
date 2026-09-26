"""Filter a harmless Streamlit message from expired auto-rerun requests."""

import logging


_APP_SESSION_LOGGER = "streamlit.runtime.app_session"
_STALE_FRAGMENT_MESSAGE = (
    "The fragment with id %s does not exist anymore - "
    "it might have been removed during a preceding full-app rerun."
)


class _StaleFragmentFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.levelno == logging.INFO
            and record.name == _APP_SESSION_LOGGER
            and record.msg == _STALE_FRAGMENT_MESSAGE
        )


def install_stale_fragment_filter() -> None:
    """Install once, even though Streamlit executes its entrypoint repeatedly."""
    logger = logging.getLogger(_APP_SESSION_LOGGER)
    if not any(isinstance(item, _StaleFragmentFilter) for item in logger.filters):
        logger.addFilter(_StaleFragmentFilter())
