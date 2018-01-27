import asyncio
import logging
import socket
import time
from enum import auto, Enum
from typing import Any, Callable, cast, Dict, MutableSequence, Set, SupportsBytes, Union

from .bitstream import c_ubyte, c_uint, c_ushort, ReadStream, WriteStream
from ._reliability import PacketReliability, ReliabilityLayer
from .messages import Address, Message

log = logging.getLogger(__name__)

class Event(Enum):
	NetworkInit = auto()
	Disconnect = auto()
	UserPacket = auto()

class Server(asyncio.DatagramProtocol):
	def __init__(self, address: Address, max_connections: int, incoming_password: bytes):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		self._address = host, port

		self._max_connections = max_connections
		self._incoming_password = incoming_password
		self._transport: asyncio.DatagramTransport = cast(asyncio.DatagramTransport, None)
		self._start_time = int(time.perf_counter() * 1000)
		self._connected: Dict[Address, ReliabilityLayer] = {}
		self._handlers: Dict[Event, MutableSequence[Callable[..., None]]] = {}
		self.not_console_logged_packets = {"InternalPing", "ConnectedPong"}
		self.file_logged_packets: Set[str] = set()

		asyncio.ensure_future(self._init_network())
		asyncio.get_event_loop().call_later(10, self._check_connections)

		log.info("Started up")

	async def _init_network(self) -> None:
		loop = asyncio.get_event_loop()
		await loop.create_datagram_endpoint(lambda: self, local_addr=self._address)
		self._address = self._transport.get_extra_info("sockname")
		self._handle(Event.NetworkInit, self._address)

	# Protocol methods

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
			if data[0] == Message.OpenConnectionRequest:
				self._on_open_connection_request(address)
		else:
			if address in self._connected:
				for packet in self._connected[address].handle_datagram(data):
					self._on_packet(packet, address)

	def _check_connections(self) -> None:
		# close connections that haven't sent acks in the last 10 seconds
		for address, layer in self._connected.copy().items():
			if layer.are_there_resends() and layer.last_ack_time < time.perf_counter() - 10:
				log.info("Connection to %s probably dead - closing connection" % str(address))
				self.close_connection(address)
		asyncio.get_event_loop().call_later(10, self._check_connections)

	# Sort of API methods

	def close_connection(self, address: Address) -> None:
		if address in self._connected:
			self.send(bytes((Message.DisconnectionNotification,)), address)
			self._on_disconnect_or_connection_lost(address)
		else:
			log.error("Tried closing connection to someone we are not connected to! (Todo: Implement the router)")

	def send(self, data: Union[bytes, SupportsBytes], address: Address=None, broadcast: bool=False, reliability: int=PacketReliability.ReliableOrdered) -> None:
		assert reliability != PacketReliability.ReliableSequenced  # If you need this one, tell me
		data = bytes(data)
		if broadcast:
			recipients = self._connected.copy()
			if address is not None:
				del recipients[address]
			for recipient in recipients:
				self.send(data, recipient, False, reliability)
			return
		if address is None:
			raise ValueError
		if address not in self._connected:
			log.error("Tried sending %s to %s but we are not connected!" % (data, address))
			return
		if data[0] != Message.UserPacket:
			self._log_packet(data, received=False)
		self._connected[address].send(data, reliability)

	# General handler system
	def add_handler(self, event: Event, handler: Callable[..., None]) -> None:
		self._handlers.setdefault(event, []).append(handler)

	def _handle(self, event: Event, *args: Any) -> None:
		if event not in self._handlers:
			return
		for handler in self._handlers[event]:
			handler(*args)

	# Packet handler stuff

	def _log_packet(self, data: bytes, received: bool) -> None:
		try:
			packetname = Message(data[0]).name
			file_log = packetname in self.file_logged_packets
			console_log = packetname not in self.not_console_logged_packets
		except ValueError:
			packetname = "Nonexisting packet %i" % data[0]
			file_log = False
			console_log = True

		if file_log:
			with open("logs/"+packetname+".bin", "wb") as file:
				file.write(bytes(data))

		if console_log:
			if received:
				log.debug("got %s", packetname)
			else:
				log.debug("snd %s", packetname)

	def _on_packet(self, data: bytes, address: Address) -> None:
		stream = ReadStream(data)
		message_id = stream.read(c_ubyte)
		if message_id != Message.UserPacket:
			self._log_packet(data, received=True)
		if message_id == Message.ConnectionRequest:
			self._on_connection_request(stream, address)
		elif message_id == Message.NewIncomingConnection:
			self._on_new_connection(address)
		elif message_id == Message.InternalPing:
			self._on_internal_ping(stream, address)
		elif message_id in (Message.DisconnectionNotification, Message.ConnectionLost):
			self._on_disconnect_or_connection_lost(address)
		elif message_id == Message.UserPacket:
			self._handle(Event.UserPacket, data, address)

	# Packet callbacks

	def _on_open_connection_request(self, address: Address) -> None:
		if len(self._connected) < self._max_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			self._transport.sendto(bytes((Message.OpenConnectionReply, 0)), address)
		else:
			raise NotImplementedError

	def _on_connection_request(self, data: ReadStream, address: Address) -> None:
		packet_password = data.read_remaining()
		if self._incoming_password == packet_password:
			response = WriteStream()
			response.write(c_ubyte(Message.ConnectionRequestAccepted))
			response.write(socket.inet_aton(address[0]))
			response.write(c_ushort(address[1]))
			response.write(bytes(2))  # Connection index, seems like this was right out ignored in RakNet
			response.write(socket.inet_aton(self._address[0]))
			response.write(c_ushort(self._address[1]))
			self.send(response, address, reliability=PacketReliability.Reliable)
		else:
			raise NotImplementedError

	def _on_new_connection(self, address: Address) -> None:
		log.info("New Connection from %s", address)

	def _on_internal_ping(self, data: ReadStream, address: Address) -> None:
		ping_send_time = data.read(c_uint)

		pong = WriteStream()
		pong.write(c_ubyte(Message.ConnectedPong))
		pong.write(c_uint(ping_send_time))
		pong.write(c_uint(int(time.perf_counter() * 1000) - self._start_time))
		# not sure why this isn't working, todo: find out
		#self.send(pong, address, reliability=PacketReliability.Unreliable)
		self.send(pong, address)

	def _on_disconnect_or_connection_lost(self, address: Address) -> None:
		log.info("Disconnect/Connection lost to %s", address)
		self._connected[address].stop = True
		del self._connected[address]
		self._handle(Event.Disconnect, address)
