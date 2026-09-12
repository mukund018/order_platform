import json
import logging

import pytest
import structlog
from fastapi.testclient import TestClient

from common.logging import configure_logging, get_logger
from common.request_id import REQUEST_ID_HEADER, bind_request_id, clear_context


@pytest.fixture
def log_file(tmp_path):
    configure_logging("testsvc", level="INFO", log_dir=tmp_path)
    yield tmp_path / "testsvc.log"
    clear_context()
    logging.getLogger().handlers = []


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_every_line_is_json_with_the_required_fields(log_file):
    get_logger("unit").info("something_happened", order_id="abc")

    line = read_lines(log_file)[0]
    assert line["service"] == "testsvc"
    assert line["level"] == "info"
    assert line["event"] == "something_happened"
    assert line["order_id"] == "abc"
    assert line["timestamp"].endswith("Z")


def test_the_request_id_is_attached_without_being_passed(log_file):
    bind_request_id("bound-id")
    get_logger("unit").info("inside_a_request")

    assert read_lines(log_file)[0]["request_id"] == "bound-id"


def test_no_request_id_outside_a_request(log_file):
    clear_context()
    get_logger("unit").info("outside_a_request")

    assert "request_id" not in read_lines(log_file)[0]


def test_the_level_is_respected(tmp_path):
    configure_logging("testsvc", level="WARNING", log_dir=tmp_path)
    log = get_logger("unit")
    log.info("not_written")
    log.warning("written")

    events = [line["event"] for line in read_lines(tmp_path / "testsvc.log")]
    assert events == ["written"]
    logging.getLogger().handlers = []


def test_exceptions_are_rendered_into_the_line(log_file):
    try:
        raise ValueError("kaboom")
    except ValueError:
        get_logger("unit").exception("it_broke")

    line = read_lines(log_file)[0]
    assert line["level"] == "error"
    assert "ValueError: kaboom" in line["exception"]


def test_logs_from_the_standard_library_come_out_as_json_too(log_file):
    # sqlalchemy, celery and uvicorn all log through stdlib, not structlog.
    logging.getLogger("some.library").warning("legacy style %s", "message")

    line = read_lines(log_file)[0]
    assert line["service"] == "testsvc"
    assert line["event"] == "legacy style message"
    assert line["logger"] == "some.library"


def test_access_log_line_per_request(log_file, make_app):
    with TestClient(make_app()) as client:
        client.get("/things/abc", headers={REQUEST_ID_HEADER: "access-log-id"})

    access = [line for line in read_lines(log_file) if line["event"] == "http_request"]
    assert len(access) == 1
    assert access[0]["method"] == "GET"
    assert access[0]["path"] == "/things/{thing_id}"
    assert access[0]["status_code"] == 200
    assert access[0]["request_id"] == "access-log-id"
    assert access[0]["duration_ms"] >= 0


def test_access_log_records_the_status_of_a_failed_request(log_file, make_app):
    with TestClient(make_app(), raise_server_exceptions=False) as client:
        client.get("/things/boom")

    access = [line for line in read_lines(log_file) if line["event"] == "http_request"]
    assert access[0]["status_code"] == 500


def test_metrics_and_health_are_not_access_logged(log_file, make_app):
    with TestClient(make_app()) as client:
        client.get("/metrics")
        client.get("/health")

    assert not [line for line in read_lines(log_file) if line["event"] == "http_request"]


def test_configure_logging_twice_does_not_duplicate_lines(tmp_path):
    configure_logging("testsvc", log_dir=tmp_path)
    configure_logging("testsvc", log_dir=tmp_path)
    get_logger("unit").info("only_once")

    assert len(read_lines(tmp_path / "testsvc.log")) == 1
    logging.getLogger().handlers = []


def test_stdout_only_when_no_log_dir_is_given(capsys):
    configure_logging("testsvc", log_dir=None)
    get_logger("unit").info("to_stdout")

    line = json.loads(capsys.readouterr().out.strip())
    assert line["event"] == "to_stdout"
    logging.getLogger().handlers = []
    structlog.reset_defaults()
