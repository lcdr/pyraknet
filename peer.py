import asyncio
import socket
import time

from .bitstream import BitStream, c_ubyte, c_uint, c_ushort
from .reliability import PacketReliability, ReliabilityLayer
from .messages import Message

class Peer:
	def __init__(self, address, max_incoming_connections, incoming_password):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		self._address = host, port

		self.incoming_password = incoming_password
		self.max_incoming_connections = max_incoming_connections
		self._connected = {}
		self._outgoing_passwords = {}
		self.handlers = {}
		self.not_console_logged_packets = set(("InternalPing", "ConnectedPong"))
		self.file_logged_packets = set()

		asyncio.ensure_future(self.init_network())
		asyncio.ensure_future(self._check_connections_loop())

		self.register_handler(Message.ConnectionRequest, self.on_connection_request)
		self.register_handler(Message.ConnectionRequestAccepted, self.on_connection_request_accepted)
		self.register_handler(Message.NewIncomingConnection, self.on_new_connection)
		self.register_handler(Message.InternalPing, self.on_internal_ping)
		self.register_handler(Message.DisconnectionNotification, self.on_disconnect_or_connection_lost)
		self.register_handler(Message.ConnectionLost, self.on_disconnect_or_connection_lost)
		self.register_handler(Message.ConnectionAttemptFailed, self.raise_exception_on_failed_connection_attempt)
		print("Started up")

	async def init_network(self, remote_address=None):
		loop = asyncio.get_event_loop()
		self._transport, protocol = await loop.create_datagram_endpoint(lambda: self, local_addr=self._address, remote_addr=remote_address)
		self._address = self._transport.get_extra_info("sockname")

	# Protocol methods

	@staticmethod
	def connection_lost(exc):
		print(exc)

	@staticmethod
	def error_received(exc):
		print(exc, vars(exc))

	def connection_made(self, transport):
		self._transport = transport

	@staticmethod
	def pause_writing():
		print("Sending too much, getting throttled")

	@staticmethod
	def resume_writing():
		print("Sending is within limits again")

	def datagram_received(self, data, address):
		if len(data) <= 2: # If the length is leq 2 then this is a raw datagram
			packet_id = data[0]
			if packet_id == Message.OpenConnectionRequest:
				self.on_open_connection_request(address)
			elif packet_id == Message.OpenConnectionReply:
				self.on_open_connection_reply(address)
		else:
			if address in self._connected:
				for packet in self._connected[address].handle_datagram(data):
					self.on_packet(packet, address)

	async def _check_connections_loop(self):
		while True:
			for address, layer in self._connected.copy().items():
				if layer._resends and layer.last_ack_time < time.time() - 10:
					self.close_connection(address)
			await asyncio.sleep(10)

	# Sort of API methods

	async def connect(self, address, server_password):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		address = host, port

		self._outgoing_passwords[address] = server_password
		await self.init_network(address)
		self.send(bytes((Message.OpenConnectionRequest, 0)), address, raw=True)

	def close_connection(self, address):
		if address in self._connected:
			self.send(bytes((Message.DisconnectionNotification,)), address)
			self.on_packet(bytes((Message.DisconnectionNotification,)), address)
		else:
			print("Tried closing connection to someone we are not connected to! (Todo: Implement the router)")

	def send(self, data, address=None, broadcast=False, reliability=PacketReliability.ReliableOrdered, raw=False):
		assert reliability != PacketReliability.ReliableSequenced # If you need this one, tell me
		if broadcast:
			recipients = self._connected.copy()
			if address is not None:
				del recipients[address]
			for recipient in recipients:
				self.send(data, recipient, False, reliability, raw)
			return
		if address is None:
			raise ValueError
		if address not in self._connected:
			print("Sending to someone we are not connected to!")
			return
		self.log_packet(data, address, received=False)
		if raw:
			self._transport.sendto(data, address)
			return
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

	def register_handler(self, packet_id, handler=None, origin=None):
		handlers = self.handlers.setdefault(packet_id, [])
		if handler is None:
			handler = asyncio.Future()
			handlers.append((handler, origin))
			return handler
		handlers.append((handler, origin))

	def log_packet(self, data, address, received):
		try:
			packetname = self.packetname(data)
			file_log = packetname in self.file_logged_packets
			console_log = packetname not in self.not_console_logged_packets
		except ValueError:
			packetname = self.unknown_packetname(data)
			file_log = True
			console_log = True

		if file_log:
			with open("logs/"+packetname+".bin", "wb") as file:
				file.write(data)

		if console_log:
			if received:
				print("got", packetname)
			else:
				print("snd", packetname)

	def on_packet(self, data, address):
		self.log_packet(data, address, received=True)

		handlers = self.handlers.get(self.packet_id(data), ())
		origin_handlers = [i for i in handlers if i[1] is None or i[1] == address]
		if not origin_handlers:
			print("No handlers for the previously received message")

		data = self.handler_data(data)
		for handler_tuple in origin_handlers:
			handler, origin_filter = handler_tuple
			stream = BitStream(data)
			if isinstance(handler, asyncio.Future):
				handler.set_result((stream, address))
				# Futures are one-time-use only
				handlers.remove(handler_tuple)
			elif asyncio.iscoroutinefunction(handler):
				asyncio.ensure_future(handler(stream, address))
			else:
				handler(stream, address)

	# Packet callbacks

	def on_open_connection_request(self, address):
		if len(self._connected) < self.max_incoming_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			self.send(bytes((Message.OpenConnectionReply, 0)), address, raw=True)
		else:
			raise NotImplementedError

	def on_open_connection_reply(self, address):
		if len(self._connected) < self.max_incoming_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			response = BitStream()
			response.write(c_ubyte(Message.ConnectionRequest))
			response.write(self._outgoing_passwords[address])
			self.send(response, address, reliability=PacketReliability.Reliable)
		else:
			raise NotImplementedError

	def on_connection_request(self, data, address):
		packet_password = data
		if self.incoming_password == packet_password:
			response = BitStream()
			response.write(c_ubyte(Message.ConnectionRequestAccepted))
			response.write(socket.inet_aton(address[0]))
			response.write(c_ushort(address[1]))
			response.write(bytes(2)) # Connection index, seems like this was right out ignored in RakNet
			response.write(socket.inet_aton(self._address[0]))
			response.write(c_ushort(self._address[1]))
			self.send(response, address, reliability=PacketReliability.Reliable)
		else:
			raise NotImplementedError

	def on_connection_request_accepted(self, data, address):
		response = BitStream()
		response.write(c_ubyte(Message.NewIncomingConnection))
		response.write(socket.inet_aton(address[0]))
		response.write(c_ushort(address[1]))
		response.write(socket.inet_aton(self._address[0]))
		response.write(c_ushort(self._address[1]))
		self.send(response, address, reliability=PacketReliability.Reliable)

	def on_new_connection(self, data, address):
		print("New Connection from", address)

	def on_internal_ping(self, data, address):
		ping_send_time = data[:4]

		pong = BitStream()
		pong.write(c_ubyte(Message.ConnectedPong))
		pong.write(ping_send_time)
		pong.write(c_uint(int(time.perf_counter() * 1000)))
		self.send(pong, address, PacketReliability.Unreliable)

	def on_disconnect_or_connection_lost(self, data, address):
		print("Disconnect/Connection lost to %s" % str(address))
		self._connected[address].stop = True
		del self._connected[address]
		# Remove any registered handlers associated with the disconnected address
		for packet_type in self.handlers:
			handlers_to_remove = [handler for handler in self.handlers[packet_type] if handler[1] == address]
			for handler in handlers_to_remove:
				self.handlers[packet_type].remove(handler)

	@staticmethod
	def raise_exception_on_failed_connection_attempt(data, address):
		raise RuntimeError("Connection attempt to %s failed! :(" % str(address))
