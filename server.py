import asyncio
import logging
import socket
import time
from typing import ByteString, Callable, Tuple

from .bitstream import c_ubyte, c_uint, c_ushort, ReadStream, WriteStream
from .reliability import PacketReliability, ReliabilityLayer
from .messages import Message

log = logging.getLogger(__name__)

Address = Tuple[str, int]

class Server:
	def __init__(self, address: Address, max_connections: int, incoming_password: bytes):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		self._address = host, port

		self.max_connections = max_connections
		self.incoming_password = incoming_password
		self._transport = None
		self._start_time = int(time.perf_counter() * 1000)
		self._connected = {}
		self._handlers = {}
		self.not_console_logged_packets = {"InternalPing", "ConnectedPong"}
		self.file_logged_packets = set()

		asyncio.ensure_future(self.init_network())
		asyncio.get_event_loop().call_later(10, self._check_connections)

		log.info("Started up")

	async def init_network(self):
		loop = asyncio.get_event_loop()
		await loop.create_datagram_endpoint(lambda: self, local_addr=self._address)
		self._address = self._transport.get_extra_info("sockname")
		self.handle("network_init", self._address)

	# Protocol methods

	@staticmethod
	def connection_lost(exc):
		log.exception(exc)

	@staticmethod
	def error_received(exc):
		log.exception(exc)

	def connection_made(self, transport):
		self._transport = transport

	@staticmethod
	def pause_writing():
		log.warning("Sending too much, getting throttled")

	@staticmethod
	def resume_writing():
		log.info("Sending is within limits again")

	def datagram_received(self, data, address):
		if len(data) <= 2:  # If the length is leq 2 then this is a raw datagram
			if data[0] == Message.OpenConnectionRequest:
				self._on_open_connection_request(address)
		else:
			if address in self._connected:
				for packet in self._connected[address].handle_datagram(data):
					self._on_packet(packet, address)

	def _check_connections(self):
		# close connections that haven't sent acks in the last 10 seconds
		for address, layer in self._connected.copy().items():
			if layer._resends and layer.last_ack_time < time.perf_counter() - 10:
				log.info("Connection to %s probably dead - closing connection" % str(address))
				self.close_connection(address)
		asyncio.get_event_loop().call_later(10, self._check_connections)

	# Sort of API methods

	def close_connection(self, address):
		if address in self._connected:
			self.send(bytes((Message.DisconnectionNotification,)), address)
			self._on_disconnect_or_connection_lost(address)
		else:
			log.error("Tried closing connection to someone we are not connected to! (Todo: Implement the router)")

	def send(self, data: ByteString, address: Address=None, broadcast=False, reliability=PacketReliability.ReliableOrdered):
		assert reliability != PacketReliability.ReliableSequenced  # If you need this one, tell me
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

	EVENT_NAMES = "disconnect_or_connection_lost", "network_init", "user_packet"

	# General handler system
	def add_handler(self, event_name: str, handler: Callable):
		if event_name not in Server.EVENT_NAMES:
			raise ValueError("Invalid event name %s", event_name)
		self._handlers.setdefault(event_name, []).append(handler)

	def handle(self, event_name: str, *args):
		if event_name not in Server.EVENT_NAMES:
			raise ValueError("Invalid event name %s", event_name)
		if event_name not in self._handlers:
			return
		for handler in self._handlers[event_name]:
			handler(*args)

	# Packet handler stuff

	def _log_packet(self, data, received):
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
				file.write(data)

		if console_log:
			if received:
				log.debug("got %s", packetname)
			else:
				log.debug("snd %s", packetname)

	def _on_packet(self, data, address):
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
			self.handle("user_packet", stream, address)

	# Packet callbacks

	def _on_open_connection_request(self, address):
		if len(self._connected) < self.max_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			self._transport.sendto(bytes((Message.OpenConnectionReply, 0)), address)
		else:
			raise NotImplementedError

	def _on_connection_request(self, data, address):
		packet_password = data.read(bytes, length=len(data)-1)
		if self.incoming_password == packet_password:
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

	def _on_new_connection(self, address):
		log.info("New Connection from %s", address)

	def _on_internal_ping(self, data, address):
		ping_send_time = data.read(c_uint)

		pong = WriteStream()
		pong.write(c_ubyte(Message.ConnectedPong))
		pong.write(c_uint(ping_send_time))
		pong.write(c_uint(int(time.perf_counter() * 1000) - self._start_time))
		self.send(pong, address, PacketReliability.Unreliable)

	def _on_disconnect_or_connection_lost(self, address):
		log.info("Disconnect/Connection lost to %s", address)
		self._connected[address].stop = True
		del self._connected[address]
		self.handle("disconnect_or_connection_lost", address)
