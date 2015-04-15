import asyncio
import socket
import time
from ctypes import c_ubyte, c_uint, c_ushort

from .bitstream import BitStream
from .reliability import PacketReliability, ReliabilityLayer
from .msgIDs import MsgID

class Peer:
	@asyncio.coroutine
	def __init__(self, address, max_incoming_connections, incoming_password):
		if address is None:
			self._address = ("", 0)
		else:
			host, port = address
			if host == "localhost":
				host = "127.0.0.1"
			self._address = host, port
			loop = asyncio.get_event_loop()
			self._transport, protocol = yield from loop.create_datagram_endpoint(lambda: self, local_addr=self._address)

		self.incoming_password = incoming_password
		self.max_incoming_connections = max_incoming_connections
		self._connected = {}
		self._outgoing_passwords = {}
		self._raknet_packet_handlers = {}
		self.not_console_logged_packets = set(("InternalPing", "ConnectedPong"))
		self.file_logged_packets = set()

		self.register_for_raknet_packet(MsgID.ConnectionRequest, self.on_connection_request)
		self.register_for_raknet_packet(MsgID.NewIncomingConnection, self.on_new_connection)
		self.register_for_raknet_packet(MsgID.InternalPing, self.on_internal_ping)
		self.register_for_raknet_packet(MsgID.DisconnectionNotification, self.on_disconnect_or_connection_lost)
		self.register_for_raknet_packet(MsgID.ConnectionLost, self.on_disconnect_or_connection_lost)
		self.register_for_raknet_packet(MsgID.ConnectionAttemptFailed, self.raise_exception_on_failed_connection_attempt)
		print("Started up")

	# Protocol methods

	@staticmethod
	def connection_lost(exc):
		print(exc)

	@staticmethod
	def error_received(exc):
		print(exc, vars(exc))

	def connection_made(self, transport):
		self._transport = transport

	def datagram_received(self, data, address):
		if len(data) <= 2: # If the length is leq 2 then this is a raw datagram
			packet_id = data[0]
			if packet_id == MsgID.OpenConnectionRequest:
				self.on_open_connection_request(data, address)
			elif packet_id == MsgID.OpenConnectionReply:
				self.on_open_connection_reply(data, address)
		else:
			if address in self._connected:
				for packet in self._connected[address].handle_datagram(data):
					self.on_packet(packet, address)

	# Sort of API methods

	@asyncio.coroutine
	def connect(self, address, server_password):
		host, port = address
		if host == "localhost":
			host = "127.0.0.1"
		address = host, port

		self._outgoing_passwords[address] = server_password

		loop = asyncio.get_event_loop()
		self._transport, protocol = yield from loop.create_datagram_endpoint(lambda: self, remote_addr=address)
		self.send(bytes((MsgID.OpenConnectionRequest, 0)), address, raw=True)

	def close_connection(self, address):
		if address in self._connected:
			self.send(bytes((MsgID.DisconnectionNotification,)), address)
			self.on_packet(bytes((MsgID.DisconnectionNotification,)), address)
		else:
			print("Tried closing connection to someone we are not connected to! (Todo: Implement the router)")

	def send(self, data, address=None, broadcast=False, reliability=PacketReliability.ReliableOrdered, raw=False):
		assert reliability not in (PacketReliability.UnreliableSequenced, PacketReliability.ReliableSequenced) # If you need these, tell me
		if broadcast:
			assert address is None
			for recipient in self._connected:
				self.send(data, recipient, False, reliability, raw)
			return
		if address is None:
			raise ValueError
		self._log_packet(data, "snd")
		if raw:
			self._transport.sendto(data, address)
			return
		self._connected[address]._sends.append((data, reliability))

	# Overridable hooks

	def packetname(self, data):
		"""String name of the packet for logging. If the name is not known, ValueError should be returned, in which case unknown_packetname will be called"""
		return MsgID(data[0]).name

	def unknown_packetname(self, data):
		"""Called when a packet name is unknown (see above). This should not throw an exception."""
		return "%.2x" % data[0]

	def handlers(self, data):
		return self._raknet_packet_handlers.get(data[0], [])

	def handler_data(self, data):
		"""For cutting off headers that the handler already knows and are therefore redundant."""
		return data[1:]

	# Handler stuff

	def _log_packet(self, data, prefix):
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
			print(prefix, packetname)

	def on_packet(self, data, address):
		self._log_packet(data, "got")

		handlers = self.handlers(data)
		data = self.handler_data(data)

		origin_handlers = [i for i in handlers if i[1] is None or i[1] == address]
		if not origin_handlers:
			pass#print("No handlers for the previously received message")

		for handler_tuple in origin_handlers:
			handler, origin_filter = handler_tuple
			if isinstance(handler, asyncio.Future):
				handler.set_result((data, address))
				# Futures are one-time-use only
				handlers.remove(handler_tuple)
			else:
				handler(data, address)

	def register_for_raknet_packet(self, *args, **kwargs):
		return self._register(self._raknet_packet_handlers, *args, **kwargs)

	@staticmethod
	def _remove_handlers(address, handler_dict):
		for packet_type in handler_dict:
			handlers_to_remove = [handler for handler in handler_dict[packet_type] if handler[1] == address]
			for handler in handlers_to_remove:
				handler_dict[packet_type].remove(handler)

	@staticmethod
	def _register(handler_dict, packet_id, callback=None, origin=None):
		handlers = handler_dict.setdefault(packet_id, [])

		if callback is None:
			callback = asyncio.Future()
			handlers.append((callback, origin))
			return callback
		handlers.append((callback, origin))

	# Packet callbacks

	def on_open_connection_request(self, data, address):
		if len(self._connected) < self.max_incoming_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			self.send(bytes((MsgID.OpenConnectionReply, 0)), address, raw=True)
		else:
			raise NotImplementedError

	def on_open_connection_reply(self, data, address):
		if len(self._connected) < self.max_incoming_connections:
			if address not in self._connected:
				self._connected[address] = ReliabilityLayer(self._transport, address)
			response = BitStream()
			response.write(c_ubyte(MsgID.ConnectionRequest))
			response.write(self._outgoing_passwords[address])
			self.send(response, address)
		else:
			raise NotImplementedError

	def on_connection_request(self, data, address):
		packet_password = data
		if self.incoming_password == packet_password:
			response = BitStream()
			response.write(c_ubyte(MsgID.ConnectionRequestAccepted))
			response.write(socket.inet_aton(address[0]))
			response.write(c_ushort(address[1]))
			response.write(bytes(2)) # Connection index, seems like this was right out ignored in RakNet
			response.write(socket.inet_aton(self._address[0]))
			response.write(c_ushort(self._address[1]))
			self.send(response, address, reliability=PacketReliability.Reliable)
		else:
			raise NotImplementedError

	def on_new_connection(self, data, address):
		print("New Connection from", address)

	def on_internal_ping(self, data, address):
		ping_send_time = data[:4]

		pong = BitStream()
		pong.write(c_ubyte(MsgID.ConnectedPong))
		pong.write(ping_send_time)
		pong.write(c_uint(int(time.perf_counter() * 1000)))
		self.send(pong, address, PacketReliability.Unreliable)

	def on_disconnect_or_connection_lost(self, data, address):
		print("Disconnect/Connection lost to %s" % str(address))
		del self._connected[address]
		# Remove any registered handlers associated with the disconnected address
		self._remove_handlers(address, self._raknet_packet_handlers)

	@staticmethod
	def raise_exception_on_failed_connection_attempt(data, address):
		raise RuntimeError("Connection attempt to %s failed! :(" % str(address))
