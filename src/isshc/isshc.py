import logging
import re
import select
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Self, Tuple

from paramiko import Channel as _SSHChannel
from paramiko import SSHClient
from paramiko.config import SSH_PORT as _SSH_DEFAULT_PORT

_logger = logging.getLogger(__name__)


def _wait_recv_ready(
    channel: _SSHChannel,
    timeout: Optional[float] = None,
) -> bool:
    (rlist, _, _) = select.select([channel], [], [], timeout)
    # False on timeout
    return bool(rlist)


def _try_decode(data: bytearray, encoding: str) -> Optional[str]:
    try:
        return data.decode(encoding, "strict")
    except UnicodeDecodeError:
        return None


def _find_pattern(patterns: Iterable[str], text: str) -> Optional[str]:
    for pattern in patterns:
        if re.search(pattern, text):
            return pattern
    return None


class InteractiveSSHClient:
    """
    An SSH client specialized for interactive shell.

    Attributes:
        auto_replies (Optional[Dict[str, str]]):
            Dictionary of auto-reply patterns and texts

        encoding (str):
            Text encoding (default is "utf-8")

        on_recv_partial_text (Optional[Callable[[str], None]]):
            Event handler called when partial text is received

        prompts (Optional[List[str]]):
            List of prompt patterns

        recv_nbytes (int):
            Number of bytes of data to receive at one time
            (default is 1024)

        recv_timeout (float):
            Maximum number of seconds to wait for a pattern to appear
            (default is 30)

        sshc (paramiko.SSHClient):
            SSHClient in paramiko
    """

    def __init__(self) -> None:
        """
        Create a new InteractiveSSHClient.
        """
        super().__init__()
        self.auto_replies: Optional[Dict[str, str]] = None
        self.encoding: str = "utf-8"
        self.on_recv_partial_text: Optional[Callable[[str], None]] = None
        self.prompts: Optional[List[str]] = None
        self.recv_nbytes: int = 1024
        self.recv_timeout: float = 30.0
        self._session: Optional[_SSHChannel] = None
        self._sshc: SSHClient = SSHClient()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close_shell()

    @property
    def sshc(self) -> SSHClient:
        return self._sshc

    def _close_connection(self) -> None:
        self._sshc.close()
        _logger.info("Connection closed.")

    def _close_session(self) -> None:
        if self._session:
            if not self._session.closed:
                self._session.close()
                _logger.info("Interactive shell terminated.")
            self._session = None

    def _open_connection(self, hostname: str, **kwargs) -> None:
        try:
            port = kwargs.get("port", _SSH_DEFAULT_PORT)
            _logger.debug(f"Connecting to {hostname}:{port}.")
            self._sshc.connect(hostname, **kwargs)
            _logger.info(f"Successfully connected to {hostname}:{port}.")
        except Exception:
            _logger.exception(f"Failed to connect to {hostname}:{port}.")
            raise

    def _open_session(self) -> None:
        try:
            _logger.debug("Opening an interactive shell.")
            transport = self._sshc.get_transport()
            assert transport is not None
            self._session = transport.open_session()
            self._session.get_pty()
            self._session.invoke_shell()
            _logger.info("Successfully opened an interactive shell.")
        except Exception:
            _logger.exception("Failed to open an interactive shell.")
            self._close_session()
            self._close_connection()
            raise

    def close_shell(self) -> None:
        """
        Terminate the interactive shell and close the connection.
        """
        self._close_session()
        self._close_connection()

    def open_shell(self, hostname: str, **kwargs) -> None:
        """
        Connect to a host and open an interactive shell.

        See connect() for arguments and exceptions.
        """
        self._open_connection(hostname, **kwargs)
        self._open_session()

    def recv_text(
        self,
        auto_replies: Optional[Dict[str, str]] = None,
        prompts: Optional[List[str]] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Receive text from the interactive shell.

        Args:
            auto_replies (Dict[str, str]):
                Dictionary of auto-reply patterns and texts

            prompts (List[str]):
                List of prompt patterns

        Returns:
            Tuple[str, Optional[str]]:
                Received text and matched prompt pattern

        Raises:
            ValueError:
                Timeout must be greater than 0.
        """
        if self.recv_nbytes <= 0:
            raise ValueError("recv_nbytes must be greater than 0.")
        if self.recv_timeout <= 0:
            raise ValueError("recv_timeout must be greater than 0.")
        if auto_replies is None:
            auto_replies = self.auto_replies or {}
        if prompts is None:
            prompts = self.prompts or []

        wait_start_time = datetime.now()
        recv_buffer = bytearray()
        text_buffer = ""
        text_archived = ""
        while True:
            if self._session is None or self._session.closed:
                _logger.warning("Connection closed while receiving text.")
                text_archived += text_buffer
                break

            elapsed = (datetime.now() - wait_start_time).total_seconds()
            timeout_remaining = max(0, self.recv_timeout - elapsed)
            if not _wait_recv_ready(self._session, timeout_remaining):
                _logger.warning("Timeout reached while waiting for prompt.")
                text_archived += text_buffer
                break

            while self._session.recv_ready():
                recv_buffer.extend(self._session.recv(self.recv_nbytes))
            decoded = _try_decode(recv_buffer, self.encoding)
            if decoded is None:
                continue
            if self.on_recv_partial_text:
                self.on_recv_partial_text(decoded)
            text_buffer += decoded
            recv_buffer.clear()

            if auto_reply := _find_pattern(auto_replies.keys(), text_buffer):
                reply_text = auto_replies[auto_reply]
                _logger.debug(
                    f"Found auto-reply pattern: {auto_reply.strip()}"
                    + f" -> Sending: {reply_text.strip()}"
                )
                self.send_text(reply_text)
                wait_start_time = datetime.now()
                text_archived += text_buffer
                text_buffer = ""
                continue

            if prompt := _find_pattern(prompts, text_buffer):
                _logger.debug(f"Found prompt pattern: {prompt.strip()}")
                return text_archived + text_buffer, prompt

        text_broken = ""
        if recv_buffer:
            text_broken = recv_buffer.decode(self.encoding, "replace")
            if self.on_recv_partial_text:
                self.on_recv_partial_text(text_broken)
        return text_archived + text_buffer + text_broken, None

    def send_text(self, text: str) -> int:
        """
        Send text to the interactive shell.

        Args:
            text (str): Text to send

        Returns:
            int: Number of bytes of encoded text actually sent

        Raises:
            socket.timeout:
                No data could be sent before the timeout.

            RuntimeError:
                No data could be sent due to no interactive shell.
        """
        if self._session is None:
            raise RuntimeError("Not connected.")
        return self._session.send(text.encode(self.encoding))
