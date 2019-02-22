import asyncio
from ssl import SSLContext
from typing import Optional, SupportsBytes

from bitstream import c_uint, c_ushort
from event_dispatcher import EventDispatcher

from ...messages import Address
from ..abc import Connection, ConnectionEvent, ConnectionType, Reliability, TransportEvent

class TCPUDPConnection(Connection, asyncio.Protocol):
	def __init__(self, transport):
		super().__init__(transport._dispatcher)
		self._transport = transport
		self._tcp = None
		self._remote_addr = None
		self._seq_num = 0
		self._packet_len = 0
		self._packet = bytearray()

	# TCP

	def connection_made(self, transport):
		local_addr = transport.get_extra_info("sockname")
		self._remote_addr = transport.get_extra_info("peername")
		print("connection made", local_addr, self._remote_addr)
		self._transport._conns[self._remote_addr] = self
		self._tcp = transport

	def connection_lost(self, exc):
		print("connection lost", exc)
		self._dispatcher.dispatch(ConnectionEvent.Close, self)

	def data_received(self, data: bytes):
		offset = 0
		whole_len = len(data)
		while offset < whole_len:
			if self._packet_len == 0:
				self._packet_len = c_uint._struct.unpack(data[offset:offset+4])[0]
				offset += 4
			else:
				packet = data[offset:offset+self._packet_len-len(self._packet)]
				offset += len(packet)
				self._packet.extend(packet)
				if len(self._packet) == self._packet_len:
					self._dispatcher.dispatch(ConnectionEvent.Receive, self._packet, self)
					self._packet_len = 0
					self._packet = bytearray()

	# UDP

	def datagram_received(self, data: bytes):
		if data[0] == 0: # unreliable
			self._dispatcher.dispatch(ConnectionEvent.Receive, data[1:], self)
		elif data[0] == 1: # unreliable sequenced
			seq_num = c_uint._struct.unpack(data[1:5])[0]
			if seq_num >= self._seq_num:
				self._seq_num = seq_num
				self._dispatcher.dispatch(ConnectionEvent.Receive, data[5:], self)

	def get_address(self) -> Address:
		return self._remote_addr

	def get_type(self) -> ConnectionType:
		return ConnectionType.TcpUdp

	def close(self) -> None:
		if self._remote_addr in self._transport._conns:
			del self._transport._conns[self._remote_addr]
		self._tcp.close()

	def _send(self, data: bytes, reliability: Reliability) -> None:
		self._tcp.write(c_uint._struct.pack(len(data)))
		self._tcp.write(data)

class TCPUDPTransport(asyncio.DatagramProtocol):
	def __init__(self, listen_addr: Address, max_connections: int, dispatcher: EventDispatcher, ssl: Optional[SSLContext]):
		self._dispatcher = dispatcher
		self._conns = {}
		asyncio.ensure_future(self._init_network(listen_addr, ssl))

	async def _init_network(self, listen_addr, ssl):
		host, port = listen_addr
		loop = asyncio.get_event_loop()
		server = await loop.create_server(lambda: TCPUDPConnection(self), host, port, ssl=ssl)
		listen_addr = server.sockets[0].getsockname()
		await loop.create_datagram_endpoint(lambda: self, local_addr=listen_addr)
		self._dispatcher.dispatch(TransportEvent.NetworkInit, ConnectionType.TcpUdp, listen_addr)

	def datagram_received(self, data: bytes, address: Address) -> None:
		if address in self._conns:
			self._conns[address].datagram_received(data)
