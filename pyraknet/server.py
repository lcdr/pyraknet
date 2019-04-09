import asyncio
import logging
import socket
import time
from ssl import SSLContext
from typing import Optional

from bitstream import c_ubyte, c_uint, c_ushort, ReadStream, WriteStream

from .logger import PacketLogger
from .messages import Address, Message
from .transports.abc import Connection, ConnectionEvent, Reliability
from .transports.raknet.transport import RaknetTransport
from .transports.tcpudp.transport import TCPUDPTransport

from event_dispatcher import EventDispatcher

log = logging.getLogger(__name__)

class Server:
	def __init__(self, address: Address, max_connections: int, incoming_password: bytes, ssl: Optional[SSLContext], dispatcher=None, excluded_packets=None):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		self._address = host, port

		self._incoming_password = incoming_password
		if dispatcher is not None:
			self._dispatcher = dispatcher
		else:
			self._dispatcher = EventDispatcher()
		self._logger = PacketLogger(self._dispatcher, excluded_packets)
		self._dispatcher.add_listener(ConnectionEvent.Receive, self._on_packet)
		self._dispatcher.add_listener(Message.ConnectionRequest, self._on_connection_request)
		self._dispatcher.add_listener(Message.NewIncomingConnection, self._on_new_connection)
		self._dispatcher.add_listener(Message.InternalPing, self._on_internal_ping)

		self._start_time = int(time.perf_counter() * 1000)

		if port == 1001:
			tcp_udp_port = 21836
		elif port != 0:
			tcp_udp_port = port + 1
		else:
			tcp_udp_port = 0
		TCPUDPTransport((host, tcp_udp_port), max_connections, self._dispatcher, ssl)
		RaknetTransport(self._address, max_connections, self._dispatcher)

		log.info("Started up")

	def _on_packet(self, data: bytes, conn: Connection) -> None:
		message = Message(data[0])
		if message != Message.UserPacket:
			args = lambda: ((ReadStream(data[1:]), conn), {})
			self._dispatcher.dispatch_callable(message, args)
		else:
			self._dispatcher.dispatch(Message.UserPacket, data[1:], conn)

	def _on_connection_request(self, data: ReadStream, conn: Connection) -> None:
		packet_password = data.read_remaining()
		if packet_password == self._incoming_password:
			address = conn.get_address()
			response = WriteStream()
			response.write(c_ubyte(Message.ConnectionRequestAccepted.value))
			response.write(socket.inet_aton(address[0]))
			response.write(c_ushort(address[1]))
			response.write(bytes(2))  # Connection index, seems like this was right out ignored in RakNet
			response.write(socket.inet_aton(self._address[0]))
			response.write(c_ushort(self._address[1]))
			conn.send(response, reliability=Reliability.Reliable)
		else:
			conn.close()
			raise NotImplementedError

	def _on_new_connection(self, data: ReadStream, conn: Connection) -> None:
		log.info("New Connection from %s", conn.get_address())

	def _on_internal_ping(self, data: ReadStream, conn: Connection) -> None:
		ping_send_time = data.read(c_uint)

		pong = WriteStream()
		pong.write(c_ubyte(Message.ConnectedPong.value))
		pong.write(c_uint(ping_send_time))
		pong.write(c_uint(int(time.perf_counter() * 1000) - self._start_time))
		# not sure why this isn't working, todo: find out
		#conn.send(pong, reliability=Reliability.Unreliable)
		conn.send(pong)
