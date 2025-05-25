import logging
from unittest.mock import MagicMock, patch

import pytest
from isshc import InteractiveSSHClient
from isshc.isshc import _find_pattern, _try_decode, _wait_recv_ready


# --- Test for _wait_recv_ready() --- #
@patch("isshc.select.select", return_value=(["dummy"], [], []))
def test_wait_recv_ready_true(mock_select):
    channel = MagicMock()
    channel.closed = False
    channel.fileno.return_value = 1
    assert _wait_recv_ready(channel) is True


@patch("isshc.select.select", return_value=([], [], []))
def test_wait_recv_ready_false(mock_select):
    channel = MagicMock()
    channel.closed = False
    channel.fileno.return_value = 1
    assert _wait_recv_ready(channel) is False


# --- Test for _try_decode() --- #
def test_try_decode_success():
    assert _try_decode(bytearray(b"hello"), "utf-8") == "hello"


def test_try_decode_failure():
    assert _try_decode(bytearray(b"\xff\xff"), "utf-8") is None


# --- Test for _find_pattern() --- #
def test_find_pattern_match():
    assert _find_pattern(["world", "test"], "hello world") == "world"


def test_find_pattern_no_match():
    assert _find_pattern(["abc", "def"], "hello world") is None


# --- Test for InteractiveSSHClient._close_session() --- #
def test_close_session_when_not_closed(caplog):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        client._session = mock_session

        with caplog.at_level(logging.INFO):
            client._close_session()

        assert client._session is None
        mock_session.close.assert_called_once()
        assert "Interactive shell terminated." in caplog.text


def test_close_session_when_closed(caplog):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = True
        client._session = mock_session

        with caplog.at_level(logging.INFO):
            client._close_session()

        assert client._session is None
        assert "Interactive shell terminated." not in caplog.text


# --- Test for InteractiveSSHClient._open_connection() --- #
@patch("isshc.SSHClient.connect")
def test_open_connection_success(mock_connect):
    with InteractiveSSHClient() as client:
        client._open_connection("example.com", port=2222)
        mock_connect.assert_called_once_with("example.com", port=2222)


@patch("isshc.SSHClient.connect", side_effect=Exception("connection error"))
def test_open_connection_failure(mock_connect):
    with InteractiveSSHClient() as client:
        with pytest.raises(Exception, match="connection error"):
            client._open_connection("example.com")


# --- Test for InteractiveSSHClient._open_session() --- #
@patch("isshc.InteractiveSSHClient.get_transport")
def test_open_session_success(mock_transport):
    mock_session = MagicMock()
    mock_transport.return_value.open_session.return_value = mock_session

    with InteractiveSSHClient() as client:
        client.get_transport = mock_transport
        client._session = None
        client._open_session()

        assert client._session == mock_session
        mock_session.get_pty.assert_called_once()
        mock_session.invoke_shell.assert_called_once()


@patch("isshc.InteractiveSSHClient.get_transport", return_value=None)
def test_open_session_no_transport(mock_transport):
    with InteractiveSSHClient() as client:
        with pytest.raises(AssertionError):
            client._open_session()


@patch("isshc.InteractiveSSHClient.get_transport")
def test_open_session_failure(mock_transport):
    mock_transport.side_effect = Exception("session failed")
    with InteractiveSSHClient() as client:
        with (
            patch.object(client, "_close_session") as close_sess,
            patch.object(client, "_close_connection") as close_conn,
        ):
            with pytest.raises(Exception, match="session failed"):
                client._open_session()
            close_sess.assert_called_once()
            close_conn.assert_called_once()


# --- Test for InteractiveSSHClient.close_shell() --- #
@patch("isshc.InteractiveSSHClient._close_session")
@patch("isshc.InteractiveSSHClient._close_connection")
def test_close_shell(mock_close_conn, mock_close_session):
    with InteractiveSSHClient() as client:
        client.close_shell()
        mock_close_session.assert_called_once()
        mock_close_conn.assert_called_once()


# --- Test for InteractiveSSHClient.open_shell() --- #
@patch("isshc.InteractiveSSHClient._open_connection")
@patch("isshc.InteractiveSSHClient._open_session")
def test_open_shell_success(mock_open_session, mock_open_conn):
    with InteractiveSSHClient() as client:
        client.open_shell("example.com")
        mock_open_conn.assert_called_once_with("example.com")
        mock_open_session.assert_called_once()


# --- Test for InteractiveSSHClient.recv_text() --- #
@patch("isshc.select.select")
def test_recv_text_success(mock_select):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.fileno.return_value = 1
        client._session = mock_session
        client.prompts = ["prompt>"]

        state = {"step": "0_WAIT_FOR_PROMPT_PATTERN"}

        def fake_select(rlist, _, __, timeout=None):
            return ([mock_session], [], []) if fake_recv_ready() else ([], [], [])

        def fake_recv_ready():
            match state["step"]:
                case "0_WAIT_FOR_PROMPT_PATTERN":
                    return True
                case _:
                    return False

        def fake_recv(_):
            match state["step"]:
                case "0_WAIT_FOR_PROMPT_PATTERN":
                    state["step"] = "1_NO_MORE_DATA"
                    return b"prompt>"
                case _:
                    return b""

        mock_session.recv_ready.side_effect = fake_recv_ready
        mock_session.recv.side_effect = fake_recv
        mock_select.side_effect = fake_select

        text, pattern = client.recv_text()

        assert "prompt>" in text
        assert pattern == "prompt>"
        assert state["step"] == "1_NO_MORE_DATA"


@patch("isshc.select.select", return_value=([], [], []))
def test_recv_text_session_closed(mock_select):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = True
        mock_session.fileno.return_value = 1
        client._session = mock_session
        client.prompts = ["prompt>"]
        text, pattern = client.recv_text()
        assert text == ""
        assert pattern is None


@patch("isshc.select.select")
def test_recv_text_prompt_and_reply(mock_select):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.fileno.return_value = 1
        client._session = mock_session
        client.prompts = ["prompt>"]
        client.auto_replies = {"Password:": "yes\n"}

        state = {
            "step": "0_WAIT_FOR_AUTO_REPLY_PATTERN",
            "recv_count": 0,
        }

        def mock_send_text(text):
            assert text == "yes\n"
            assert state["step"] == "1_SENDING_AUTO_REPLY_TEXT"
            state["step"] = "2_WAIT_FOR_DUMMY_TEXT"

        client.send_text = MagicMock(side_effect=mock_send_text)

        def fake_select(rlist, _, __, timeout=None):
            return ([mock_session], [], []) if fake_recv_ready() else ([], [], [])

        def fake_recv_ready():
            match state["step"]:
                case "0_WAIT_FOR_AUTO_REPLY_PATTERN":
                    return True
                case "2_WAIT_FOR_DUMMY_TEXT":
                    return True
                case "3_WAIT_FOR_DATA":
                    state["step"] = "4_WAIT_FOR_PROMPT_PATTERN"
                    return False
                case "4_WAIT_FOR_PROMPT_PATTERN":
                    return True
                case _:
                    return False

        def fake_recv(_):
            match state["step"]:
                case "0_WAIT_FOR_AUTO_REPLY_PATTERN":
                    state["step"] = "1_SENDING_AUTO_REPLY_TEXT"
                    return b"Password:"
                case "2_WAIT_FOR_DUMMY_TEXT":
                    state["step"] = "3_WAIT_FOR_DATA"
                    return b"dummy text not including any patterns"
                case "4_WAIT_FOR_PROMPT_PATTERN":
                    state["step"] = "5_NO_MORE_DATA"
                    return b"prompt>"
                case _:
                    return b""

        def handler(_):
            state["recv_count"] += 1

        client.on_recv_partial_text = handler

        mock_session.recv_ready.side_effect = fake_recv_ready
        mock_session.recv.side_effect = fake_recv
        mock_select.side_effect = fake_select

        text, pattern = client.recv_text()

        assert "prompt>" in text
        assert pattern == "prompt>"
        assert state["step"] == "5_NO_MORE_DATA"
        assert state["recv_count"] > 0
        client.send_text.assert_called_once_with("yes\n")


@patch("isshc.select.select", return_value=([], [], []))
def test_recv_text_wait_recv_ready_timeout(mock_select, caplog):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.fileno.return_value = 1
        client._session = mock_session

        with caplog.at_level(logging.WARNING):
            text, pattern = client.recv_text()

        assert text == ""
        assert pattern is None
        assert "Timeout reached while waiting for prompt." in caplog.text


@patch("isshc.select.select")
def test_recv_text_decode_fails(mock_select):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.fileno.return_value = 1
        client._session = mock_session
        client.prompts = ["prompt>"]

        state = {
            "retry": 0,
            "recv_count": 0,
        }

        def fake_select(rlist, _, __, timeout=None):
            if state["retry"] < 2:
                return ([mock_session], [], [])
            return ([], [], [])

        def fake_recv_ready():
            return state["retry"] < 2

        def fake_recv(_):
            state["retry"] += 1
            return b"\xff\xfe"

        def handler(_):
            state["recv_count"] += 1

        client.on_recv_partial_text = handler

        mock_session.recv_ready.side_effect = fake_recv_ready
        mock_session.recv.side_effect = fake_recv
        mock_select.side_effect = fake_select

        text, pattern = client.recv_text()

        assert text == "\ufffd\ufffd\ufffd\ufffd"
        assert pattern is None
        assert state["retry"] == 2
        assert state["recv_count"] == 1


@patch("isshc.select.select")
def test_recv_text_decode_fails_without_handler(mock_select):
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.fileno.return_value = 1
        client._session = mock_session

        state = {"retry": 0}

        def fake_select(rlist, _, __, timeout=None):
            if state["retry"] < 2:
                return ([mock_session], [], [])
            return ([], [], [])

        def fake_recv_ready():
            return state["retry"] < 2

        def fake_recv(_):
            state["retry"] += 1
            return b"\xff\xfe"

        mock_session.recv_ready.side_effect = fake_recv_ready
        mock_session.recv.side_effect = fake_recv
        mock_select.side_effect = fake_select

        text, pattern = client.recv_text(prompts=["prompt>"], auto_replies={})

        assert text == "\ufffd\ufffd\ufffd\ufffd"
        assert pattern is None
        assert state["retry"] == 2


def test_recv_text_invalid_nbytes():
    with InteractiveSSHClient() as client:
        client.prompts = ["a"]
        client.recv_nbytes = 0
        with pytest.raises(ValueError):
            client.recv_text()


def test_recv_text_invalid_timeout():
    with InteractiveSSHClient() as client:
        client.prompts = ["a"]
        client.recv_timeout = 0
        with pytest.raises(ValueError):
            client.recv_text()


# --- Test for InteractiveSSHClient.send_text() --- #
def test_send_text_success():
    with InteractiveSSHClient() as client:
        mock_session = MagicMock()
        mock_session.closed = False
        client._session = mock_session

        mock_session.send.return_value = 10
        sent = client.send_text("hello")
        assert sent == 10
        mock_session.send.assert_called_once()


def test_send_text_without_session():
    with InteractiveSSHClient() as client:
        client._session = None
        with pytest.raises(RuntimeError, match="Not connected."):
            client.send_text("hello")
