"""Linux peer credentials for the backend's private adapter listener."""

import asyncio
import grp
import os
import pwd
import socket
import struct
from collections.abc import Mapping
from pathlib import Path

from uvicorn.protocols.http.h11_impl import H11Protocol


def peer_protocol(adapters: Mapping[str, str]) -> type[H11Protocol]:
    """Bind interface identity to kernel credentials, never request headers."""
    identities = {pwd.getpwnam(user).pw_uid: kind for user, kind in adapters.items()}

    class LocalProtocol(H11Protocol):
        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            if not isinstance(transport, asyncio.Transport):
                transport.close()
                return
            super().connection_made(transport)
            connection = transport.get_extra_info("socket")
            if connection is None or connection.family != socket.AF_UNIX:
                transport.close()
                return
            _, uid, _ = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            kind = identities.get(uid)
            if kind is None:
                transport.close()
                return
            self.client = ("unix-adapter:" + kind, 0)

    return LocalProtocol


def bind_socket(path: str, group: str) -> socket.socket:
    """Bind only in a service-owned runtime directory; permissions restrict access."""
    location = Path(path)
    location.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if location.exists():
        location.unlink()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.bind(path)
        os.chown(path, -1, grp.getgrnam(group).gr_gid)
        os.chmod(path, 0o660)
        connection.listen(128)
        return connection
    except BaseException:
        connection.close()
        raise
