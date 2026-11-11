import sys
from unittest.mock import Mock

from contract_kb.interfaces.web import app


def test_request_logging_does_not_write_to_console(monkeypatch):
    console = Mock()
    monkeypatch.setattr(sys, "stderr", console)
    logger = Mock()
    monkeypatch.setattr(app, "_ACCESS_LOGGER", logger, raising=False)
    handler = object.__new__(app.DiffVisualizerHandler)
    handler.client_address = ("127.0.0.1", 12345)
    handler.log_message('"%s" %s', "POST /api/ref_templates/load HTTP/1.1", "200")
    console.write.assert_not_called()
    logger.info.assert_called_once()
