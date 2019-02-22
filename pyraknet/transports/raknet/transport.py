import asyncio
import logging
from typing import cast

from event_dispatcher import EventDispatcher

from ...messages import Address, Message
from ..abc import ConnectionEvent, ConnectionType, TransportEvent
from .connection import RaknetConnection

log = logging.getLogger(__name__)

class RaknetTransport(asyncio.DatagramProtocol):
	def __init__(self, listen_addr: Address, max_connections: int, dispatcher: EventDispatcher):
		self._dispatcher = dispatcher
		self._connections: Dict[Address, RaknetConnection] = {}
		self._max_connections = max_connections
		self._dispatcher.add_listener(ConnectionEvent.Close, self._on_close_conn)
		asyncio.ensure_future(self._init_network(listen_addr))

	async def _init_network(self, listen_addr) -> None:
		loop = asyncio.get_event_loop()
		await loop.create_datagram_endpoint(lambda: self, local_addr=listen_addr)
		self._dispatcher.dispatch(TransportEvent.NetworkInit, ConnectionType.RakNet, self._transport.get_extra_info("sockname"))

	def connection_lost(self, exc: Exception) -> None:
		log.exception(str(exc))

	def error_received(self, exc: Exception) -> None:
		log.exception(str(exc))

	def connection_made(self, transport: asyncio.BaseTransport) -> None:
		self._transport = cast(asyncio.DatagramTransport, transport)

	def pause_writing(self) -> None:
		log.warning("Sending too much, getting throttled")

	def resume_writing(self) -> None:
		log.info("Sending is within limits again")

	def datagram_received(self, data: bytes, address: Address) -> None:
		if len(data) <= 2:  # If the length is leq 2 then this is a raw datagram
			if data[0] == Message.OpenConnectionRequest.value:
				self._on_open_connection_request(address)
		else:
			if address in self._connections:
				conn = self._connections[address]
				conn.handle_datagram(data)

	def _on_open_connection_request(self, address: Address) -> None:
		if len(self._connections) < self._max_connections:
			if address not in self._connections:
				self._connections[address] = RaknetConnection(self._transport, self._dispatcher, address)
			self._transport.sendto(bytes((Message.OpenConnectionReply.value, 0)), address)
		else:
			self._transport.sendto(bytes((Message.NoFreeIncomingConnections.value, 0)), address)

	def _on_close_conn(self, conn):
		if isinstance(conn, RaknetConnection):
			del self._connections[conn.get_address()]
