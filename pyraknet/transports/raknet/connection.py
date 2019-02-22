"""
Reliability layer. UDP doesn't guarantee delivery or ordering, so this is where RakNet provides optional support for these features.
For retransmission algorithm see http://www.saminiir.com/lets-code-tcp-ip-stack-5-tcp-retransmission
Congestion control is based on TCP Reno, see http://ee.lbl.gov/papers/congavoid.pdf
"""
# Todo: Congestion avoidance instead of congestion control (prevent congestion control beforehand instead of coping with it afterwards)
import asyncio
import logging
import math
import time
from collections import OrderedDict
from typing import Container, Dict, Iterator, MutableSequence, Optional, SupportsBytes, Tuple

from event_dispatcher import EventDispatcher

from bitstream import c_bit, c_uint, c_ushort, ReadStream, WriteStream

from . import _rangelist
from ...messages import Address, Message
from ..abc import Connection, ConnectionEvent, ConnectionType, Reliability
from .calcs import CWNDCalc, RTOCalc

log = logging.getLogger(__name__)

#MTU_SIZE = 1492  # Default used by RakNet, Ethernet
MTU_SIZE = 1228  # Hardcoded by LU for some reason
UDP_HEADER_SIZE = 28

_Packet = Tuple[bytes, Reliability, Optional[int], Optional[Tuple[int, int, int]]]

class RaknetConnection(Connection):
	def __init__(self, transport: asyncio.DatagramTransport, dispatcher: EventDispatcher, address: Address):
		super().__init__(dispatcher)
		self._transport = transport
		self._address = address
		self._check_close_handle = None
		self._last_ack_time: float = 0
		self._start_time = int(time.perf_counter() * 1000)
		self._split_packet_id = 0
		self._remote_system_time = 0
		self._acks = _rangelist.RangeList()
		self._send_acks_handle = None
		self._rto_calc = RTOCalc()
		self._cwnd_calc = CWNDCalc()
		self._packets_sent = 0
		self._send_message_number_index = 0
		self._sequenced_write_index = 0
		self._sequenced_read_index = 0
		self._ordered_write_index = 0
		self._ordered_read_index = 0
		self._last_rel_received = [-1] * 20
		self._out_of_order_packets: Dict[int, bytes] = {}  # for ReliableOrdered
		self._split_packet_queue: Dict[int, MutableSequence[bytes]] = {}
		self._sends: MutableSequence[_Packet] = []
		self._resends: Dict[int, asnycio.Handle] = OrderedDict()

		asyncio.get_event_loop().call_later(10, self._check_close)

	def get_address(self) -> Address:
		return self._address

	def get_type(self) -> ConnectionType:
		return ConnectionType.RakNet

	def _send(self, data: bytes, reliability: Reliability) -> None:
		ordering_index: Optional[int]
		if reliability == Reliability.UnreliableSequenced:
			ordering_index = self._sequenced_write_index
			self._sequenced_write_index += 1
		elif reliability == Reliability.ReliableOrdered:
			ordering_index = self._ordered_write_index
			self._ordered_write_index += 1
		else:
			ordering_index = None

		if RaknetConnection._packet_header_length(reliability, False) + len(data) >= MTU_SIZE - UDP_HEADER_SIZE:
			data_offset = 0
			chunks = []
			while data_offset < len(data):
				data_length = MTU_SIZE - UDP_HEADER_SIZE - RaknetConnection._packet_header_length(reliability, True)
				chunks.append(data[data_offset:data_offset+data_length])
				data_offset += data_length

			split_packet_id = self._split_packet_id
			self._split_packet_id += 1
			for split_packet_index, chunk in enumerate(chunks):
				message_number = self._send_message_number_index
				self._send_message_number_index += 1
				self._schedule_send(chunk, message_number, reliability, ordering_index, (split_packet_id, split_packet_index, len(chunks)))
		else:
			message_number = self._send_message_number_index
			self._send_message_number_index += 1
			self._schedule_send(data, message_number, reliability, ordering_index, None)

	def _schedule_send(self, data: bytes, message_number: int, reliability: Reliability, ordering_index: Optional[int], split_packet_info: Optional[Tuple[int, int, int]]) -> None:
		if reliability == Reliability.Reliable or reliability == Reliability.ReliableOrdered:
			self._resends[message_number] = asyncio.get_event_loop().call_later(self._rto_calc.rto(), self._schedule_send, data, message_number, reliability, ordering_index, split_packet_info)
		if self._packets_sent >= self._cwnd_calc.cwnd():
			return
		self._packets_sent += 1
		self._send_packet(data, message_number, reliability, ordering_index, split_packet_info)

	def close(self) -> None:
		log.info("Closing connection %s", self._address)
		self._dispatcher.dispatch(ConnectionEvent.Close, self)
		if self._check_close_handle is not None:
			self._check_close_handle.cancel()

	def handle_datagram(self, datagram: bytes) -> None:
		stream = ReadStream(datagram)
		if self._handle_datagram_header(stream):
			return  # Acks only packet
		# There can be multiple packets in one datagram
		for packet in self._parse_packets(stream):
			if packet[0] in (Message.DisconnectionNotification.value, Message.ConnectionLost.value):
				self.close()
			else:
				self._dispatcher.dispatch(ConnectionEvent.Receive, packet, self)

	def _handle_datagram_header(self, data: ReadStream) -> bool:
		has_acks = data.read(c_bit)
		if has_acks:
			old_time = data.read(c_uint)
			rtt = time.perf_counter() - self._start_time/1000 - old_time/1000
			self._rto_calc.update(rtt)

			acks = data.read(_rangelist.RangeList)
			for message_number in acks:
				if message_number in self._resends:
					self._resends[message_number].cancel()
					del self._resends[message_number]

			num_acks = len(acks)
			act_num_holes = 0 # number of holes that actually correspond to resends
			for hole in acks.holes():
				if hole in self._resends:
					act_num_holes += 1

			self._cwnd_calc.update(self._packets_sent, num_acks, act_num_holes)
			self._packets_sent = 0
			self._last_ack_time = time.perf_counter()
		if data.all_read():
			return True
		has_remote_system_time = data.read(c_bit)
		if has_remote_system_time:
			self._remote_system_time = data.read(c_uint)
		return False

	def _parse_packets(self, data: ReadStream) -> Iterator[bytes]:
		while not data.all_read():
			message_number = data.read(c_uint)
			reliability = Reliability(data.read_bits(3))
			assert reliability != Reliability.ReliableSequenced  # This is never used

			if reliability in (Reliability.UnreliableSequenced, Reliability.ReliableOrdered):
				ordering_channel = data.read_bits(5)
				assert ordering_channel == 0  # No one actually uses a custom ordering channel
				ordering_index = data.read(c_uint)

			is_split_packet = data.read(c_bit)
			if is_split_packet:
				split_packet_id = data.read(c_ushort)
				split_packet_index = data.read_compressed(c_uint)
				split_packet_count = data.read_compressed(c_uint)

			length = data.read_compressed(c_ushort)
			data.align_read()
			packet_data = data.read(bytes, length=int(math.ceil(length / 8)))
			if reliability in (Reliability.Reliable, Reliability.ReliableOrdered):
				self._acks.insert(message_number)
				if self._send_acks_handle is None:
					self._send_acks_handle = asyncio.get_event_loop().call_later(0.03, self._send_acks_only)

			if is_split_packet:
				if split_packet_id not in self._split_packet_queue:
					self._split_packet_queue[split_packet_id] = [None]*split_packet_count
				self._split_packet_queue[split_packet_id][split_packet_index] = packet_data
				# check if packet is ready yet
				ready = True
				for packet_part in self._split_packet_queue[split_packet_id]:
					if packet_part is None:
						ready = False
						break
				if ready:
					packet_data = b"".join(self._split_packet_queue[split_packet_id])
					del self._split_packet_queue[split_packet_id]
				else:
					continue

			# Duplicate packet checks and ordering
			# Depending on reliability type:
			# Unreliable & Unreliable Sequenced:
			# Since unreliable packets are not resent, there can't be any duplicates. No checks needed.
			# Reliable:
			# Reliable.* packets are resent, therefore we need to check for duplicates. Reliable (no ordering or sequencing) packets also don't have an easy way of detecting duplicates, since they may arrive out of order.
			# Simplest solution is to just keep a list of the last received reliable message numbers, and check packets against the list. If they're included, they will be detected as duplicate.
			# Note that the failure rate of this method depends on list size vs packet frequency.
			# Keeping a list of "missing" message numbers doesn't work too well because raknet is stupid and assigns message numbers to unreliable packets which don't even need them.
			# Reliable Ordered:
			# Reliable Ordered packets need to be checked for order, which as a side effect can detect duplicates. No extra duplicate detection needed.
			# Reliable Sequenced:
			# Is not used, but if it were, it would be handled similarly to Reliable Ordered.

			if reliability == Reliability.Reliable:
				if message_number not in self._last_rel_received:
					del self._last_rel_received[0]
					self._last_rel_received.append(message_number)
				else:
					log.info("detected reliable duplicate")
					continue

			if reliability == Reliability.UnreliableSequenced:
				if ordering_index >= self._sequenced_read_index:
					self._sequenced_read_index = ordering_index + 1
				else:
					# sequenced means ignore older packets
					continue
			elif reliability == Reliability.ReliableOrdered:
				if ordering_index == self._ordered_read_index:
					self._ordered_read_index += 1
					ord = ordering_index+1
					while ord in self._out_of_order_packets:
						self._ordered_read_index += 1
						log.info("Releasing ord-index %i", ord)
						yield self._out_of_order_packets.pop(ord)
						ord += 1
				elif ordering_index < self._ordered_read_index:
					log.info("detected reliable ordered duplicate")
					continue
				else:
					# Packet arrived too early, we're still waiting for a previous packet
					# Add this one to a queue so we can process it later
					self._out_of_order_packets[ordering_index] = packet_data
					log.info("Packet too early m# %i ord-index %i>%i", message_number, ordering_index, self._ordered_read_index)
			yield packet_data

	def _send_acks_only(self) -> None:
		self._send_acks_handle = None
		if self._acks:
			out = WriteStream()
			out.write(c_bit(True))
			out.write(c_uint(self._remote_system_time))
			out.write(self._acks)
			self._acks.clear()
			self._transport.sendto(bytes(out), self._address)

	def _send_packet(self, data: bytes, message_number: int, reliability: Reliability, ordering_index: Optional[int], split_packet_info: Optional[Tuple[int, int, int]]) -> None:
		out = WriteStream()
		out.write(c_bit(bool(self._acks)))
		if self._acks:
			out.write(c_uint(self._remote_system_time))
			out.write(self._acks)
			self._acks.clear()

		assert RaknetConnection._packet_header_length(reliability, split_packet_info is not None) + len(data) <= MTU_SIZE - UDP_HEADER_SIZE

		has_remote_system_time = True
		out.write(c_bit(has_remote_system_time))
		out.write(c_uint(int(time.perf_counter() * 1000) - self._start_time))

		out.write(c_uint(message_number))

		out.write_bits(reliability.value, 3)

		if reliability in (Reliability.UnreliableSequenced, Reliability.ReliableOrdered):
			out.write_bits(0, 5)  # ordering_channel, no one ever uses anything else than 0
			out.write(c_uint(ordering_index))

		out.write(c_bit(split_packet_info is not None))
		if split_packet_info is not None:
			split_packet_id, split_packet_index, split_packet_count = split_packet_info
			out.write(c_ushort(split_packet_id))
			out.write_compressed(c_uint(split_packet_index))
			out.write_compressed(c_uint(split_packet_count))
		out.write_compressed(c_ushort(len(data) * 8))
		out.align_write()
		out.write(data)

		self._transport.sendto(bytes(out), self._address)

	@staticmethod
	def _packet_header_length(reliability: Reliability, is_split_packet: bool) -> int:
		length = 32  # message number
		length += 3  # reliability
		if reliability in (Reliability.UnreliableSequenced, Reliability.ReliableOrdered):
			length += 5  # ordering channel
			length += 32
		length += 1  # is split packet
		if is_split_packet:
			length += 16  # split packet id
			length += 32  # split packet index (actually a compressed write so assume the maximum)
			length += 32  # split packet count (actually a compressed write so assume the maximum)
		length += 16  # data length (actually a compressed write so assume the maximum)
		return int(math.ceil(length / 8))

	def _check_close(self) -> None:
		# close connection if we haven't received acks in the last 10 seconds
		if self._resends and self._last_ack_time < time.perf_counter() - 10:
			log.info("Connection to %s probably dead - closing connection" % str(self._address))
			self.close()
		else:
			self._check_close_handle = asyncio.get_event_loop().call_later(10, self._check_close)
