import asyncio
import logging
import socket
import time

from .bitstream import c_ubyte, c_uint, c_ushort, ReadStream, WriteStream
from .reliability import PacketReliability, ReliabilityLayer
from .messages import Message

log = logging.getLogger(__name__)

class Server:
	def __init__(self, address, max_connections, incoming_password):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		self._address = host, port

		self.max_connections = max_connections
		self.incoming_password = incoming_password
		self._transport = None
		self._start_time = int(time.perf_counter() * 1000)
		self._connected = {}
		self.handlers = {}
		self.not_console_logged_packets = set(("InternalPing", "ConnectedPong"))
		self.file_logged_packets = set()

		asyncio.ensure_future(self.init_network())
		asyncio.get_event_loop().call_later(10, self._check_connections)

		self.register_handler(Message.ConnectionRequest, self.on_connection_request)
		self.register_handler(Message.NewIncomingConnection, self.on_new_connection)
		self.register_handler(Message.InternalPing, self.on_internal_ping)
		self.register_handler(Message.DisconnectionNotification, self.on_disconnect_or_connection_lost)
		self.register_handler(Message.ConnectionLost, self.on_disconnect_or_connection_lost)
		log.info("Started up")

	async def init_network(self):
		loop = asyncio.get_event_loop()
		await loop.create_datagram_endpoint(lambda: self, local_addr=self._address)
		self._address = self._transport.get_extra_info("sockname")

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
				self.on_open_connection_request(address)
		else:
			if address in self._connected:
				for packet in self._connected[address].handle_datagram(data):
					self.on_packet(packet, address)

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
			self.on_packet(bytes((Message.DisconnectionNotification,)), address)
		else:
			log.error("Tried closing connection to someone we are not connected to! (Todo: Implement the router)")

	def send(self, data, address=None, broadcast=False, reliability=PacketReliability.ReliableOrdered):
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
		self.log_packet(data, address, received=False)
		self._connected[address].send(data, reliability)

	# Overridable hooks

	@staticmethod
	def packetname(data):
		"""String name of the packet for logging. If the name is not known, ValueError should be returned, in which case unknown_packetname will be called"""
		return Message(data[0]).name

	@staticmethod
	def unknown_packetname(data):
		"""Called when a packet name is unknown (see above). This should not throw an exception."""
		return "%.2x" % data[0]

	@staticmethod
	def packet_id(data):
		return data[0]

	@staticmethod
	def handler_data(data):
		"""For cutting off headers that the handler already knows and are therefore redundant."""
		return data[1:]

	# Handler stuff

	def register_handler(self, packet_id, handler):
		handlers = self.handlers.setdefault(packet_id, [])
		handlers.insert(0, handler)

	def log_packet(self, data, address, received):
		try:
			packetname = self.packetname(data)
			file_log = packetname in self.file_logged_packets
			console_log = packetname not in self.not_console_logged_packets
		except ValueError:
			packetname = self.unknown_packetname(data)
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

	def on_packet(self, data, address):
		self.log_packet(data, address, received=True)

		handlers = self.handlers.get(self.packet_id(data), ())
		if not handlers:
			try:
				packetname = self.packetname(data)
			except ValueError:
				packetname = self.unknown_packetname(data)
			log.info("No handlers for %s", packetname)

		data = self.handler_data(data)
		stream = ReadStream(data)
		for handler in handlers:
			stream.read_offset = 0
			if asyncio.iscoroutinefunction(handler):
				asyncio.ensure_future(handler(stream, address))
			else:
				handler(stream, address)

	# Packet callbacks

	def on_open_connection_request(self, address):
		if len(self._connected) < self.max_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			self._transport.sendto(bytes((Message.OpenConnectionReply, 0)), address)
		else:
			raise NotImplementedError

	def on_connection_request(self, data, address):
		packet_password = bytes(data)
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

	def on_new_connection(self, data, address):
		log.info("New Connection from %s", address)

	def on_internal_ping(self, data, address):
		ping_send_time = data[:4]

		pong = WriteStream()
		pong.write(c_ubyte(Message.ConnectedPong))
		pong.write(ping_send_time)
		pong.write(c_uint(int(time.perf_counter() * 1000) - self._start_time))
		self.send(pong, address, PacketReliability.Unreliable)

	def on_disconnect_or_connection_lost(self, data, address):
		log.info("Disconnect/Connection lost to %s", address)
		self._connected[address].stop = True
		del self._connected[address]
