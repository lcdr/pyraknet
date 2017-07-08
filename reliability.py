"""
Reliability layer. UDP doesn't guarantee delivery or ordering, so this is where RakNet provides optional support for these features.
For retransmission algorithm see http://www.saminiir.com/lets-code-tcp-ip-stack-5-tcp-retransmission
"""
# Todo: combine packets in datagrams if possible
import asyncio
import logging
import math
import time
from collections import OrderedDict

from . import rangelist
from .bitstream import BitStream, c_bit, c_uint, c_ushort

log = logging.getLogger(__name__)

class PacketReliability:
	Unreliable = 0
	UnreliableSequenced = 1
	Reliable = 2
	ReliableOrdered = 3
	ReliableSequenced = 4

class ReliabilityLayer:
	def __init__(self, transport, address):
		self.stop = False
		self.last_ack_time = 0
		self._split_packet_id = 0
		self._remote_system_time = 0
		self._transport = transport
		self._address = address
		self._acks = rangelist.RangeList()
		self._rto = 1  # retransmission timeout = 1 second
		self._srtt = None  # smoothed round trip time
		self._rtt_var = None  # round trip time variation
		self._send_message_number_index = 0
		self._sequenced_write_index = 0
		self._sequenced_read_index = 0
		self._ordered_write_index = 0
		self._ordered_read_index = 0
		self._out_of_order_packets = {}  # for ReliableOrdered
		self._sends = []
		self._resends = OrderedDict()

		self._send_loop()

	def handle_datagram(self, datagram):
		stream = BitStream(datagram)
		if self.handle_datagram_header(stream):
			return  # Acks only packet
		# There can be multiple packets in one datagram
		yield from self.parse_packets(stream)

	def handle_datagram_header(self, data):
		has_acks = data.read(c_bit)
		if has_acks:
			old_time = data.read(c_uint)
			rtt = time.perf_counter() - old_time/1000
			if self._srtt is None:
				self._srtt = rtt
				self._rtt_var = rtt/2
				self._rto = self._srtt + 4*self._rtt_var  # originally specified be at least clock resolution but since the client loop is set at 10 milliseconds there's no way it can be smaller anyways
			else:
				alpha = 0.125
				beta = 0.25
				self._rtt_var = (1 - beta) * self._rtt_var + beta * abs(self._srtt - rtt)
				self._srtt = (1 - alpha) * self._srtt + alpha * rtt
				self._rto = self._srtt + 4*self._rtt_var  # resolution same as above
			self._rto = max(1, self._rto)

			acks = rangelist.RangeList(data)
			for message_number in acks.ranges():
				if message_number in self._resends:
					del self._resends[message_number]

			self.last_ack_time = time.time()
		if data.all_read():
			return True
		has_remote_system_time = data.read(c_bit)
		if has_remote_system_time:
			self._remote_system_time = data.read(c_uint)

	def parse_packets(self, data):
		while not data.all_read():
			message_number = data.read(c_uint)
			reliability = data.read_bits(3)
			assert reliability != PacketReliability.ReliableSequenced  # This is never used

			if reliability == PacketReliability.UnreliableSequenced or reliability == PacketReliability.ReliableOrdered:
				ordering_channel = data.read_bits(5)
				assert ordering_channel == 0  # No one actually uses a custom ordering channel
				ordering_index = data.read(c_uint)

			is_split_packet = data.read(c_bit)
			if is_split_packet:
				raise NotImplementedError

			length = data.read(c_ushort, compressed=True)
			data.align_read()
			packet_data = data.read(bytes, length=math.ceil(length / 8))

			if reliability in (PacketReliability.Reliable, PacketReliability.ReliableOrdered):
				self._acks.append(message_number)

			if reliability == PacketReliability.UnreliableSequenced:
				if ordering_index >= self._sequenced_read_index:
					self._sequenced_read_index = ordering_index + 1
				else:
					log.warn("got duplicate")
					continue
			elif reliability == PacketReliability.ReliableOrdered:
				if ordering_index == self._ordered_read_index:
					self._ordered_read_index += 1
					ord = ordering_index+1
					while ord in self._out_of_order_packets:
						self._ordered_read_index += 1
						log.info("Releasing ord-index %i", ord)
						yield self._out_of_order_packets.pop(ord)
						ord += 1
				elif ordering_index < self._ordered_read_index:
					log.warn("got duplicate")
					continue
				else:
					# Packet arrived too early, we're still waiting for a previous packet
					# Add this one to a queue so we can process it later
					self._out_of_order_packets[ordering_index] = packet_data
					log.info("Packet too early m# %i ord-index %i>%i", message_number, ordering_index, self._ordered_read_index)
			yield packet_data

	def send(self, data, reliability):
		if reliability == PacketReliability.UnreliableSequenced:
			ordering_index = self._sequenced_write_index
			self._sequenced_write_index += 1
		elif reliability == PacketReliability.ReliableOrdered:
			ordering_index = self._ordered_write_index
			self._ordered_write_index += 1
		else:
			ordering_index = None

		if ReliabilityLayer.packet_header_length(reliability, False) + len(data) >= 1492 - 28:  # mtu - udp header
			data_offset = 0
			chunks = []
			while data_offset < len(data):
				data_length = 1492 - 28 - ReliabilityLayer.packet_header_length(reliability, True)
				chunks.append(data[data_offset:data_offset+data_length])
				data_offset += data_length

			split_packet_id = self._split_packet_id
			self._split_packet_id += 1
			for split_packet_index, chunk in enumerate(chunks):
				self._sends.append((chunk, reliability, ordering_index, split_packet_id, split_packet_index, len(chunks)))
		else:
			self._sends.append((data, reliability, ordering_index, None, None, None))

	def _send_loop(self):
		for packet in self._sends:
			data, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count = packet
			message_number = self._send_message_number_index
			self._send_message_number_index += 1

			self._send_packet(data, message_number, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count)

			if reliability == PacketReliability.Reliable or reliability == PacketReliability.ReliableOrdered:
				self._resends[message_number] = time.time()+self._rto, packet
		self._sends.clear()

		for message_number, resend_data in self._resends.items():
			resend_time, packet = resend_data
			if resend_time > time.time():
				continue
			log.info("actually resending %i", message_number)
			data, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count = packet
			self._send_packet(data, message_number, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count)
			if reliability == PacketReliability.Reliable or reliability == PacketReliability.ReliableOrdered:
				self._resends[message_number] = time.time()+self._rto, packet

		if self._acks:
			out = BitStream()
			out.write(c_bit(True))
			out.write(c_uint(self._remote_system_time))
			out.write(self._acks.serialize())
			self._acks.clear()
			self._transport.sendto(out, self._address)

		if not self.stop:
			asyncio.get_event_loop().call_later(0.03, self._send_loop)

	def _send_packet(self, data, message_number, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count):
		out = BitStream()
		out.write(c_bit(len(self._acks) != 0))
		if self._acks:
			out.write(c_uint(self._remote_system_time))
			out.write(self._acks.serialize())
			self._acks.clear()

		assert len(out) + ReliabilityLayer.packet_header_length(reliability, split_packet_id is not None) + len(data) < 1492

		has_remote_system_time = True
		out.write(c_bit(has_remote_system_time))
		out.write(c_uint(int(time.perf_counter() * 1000)))

		out.write(c_uint(message_number))

		out.write_bits(reliability, 3)

		if reliability in (PacketReliability.UnreliableSequenced, PacketReliability.ReliableOrdered):
			out.write_bits(0, 5)  # ordering_channel, no one ever uses anything else than 0
			out.write(c_uint(ordering_index))

		is_split_packet = split_packet_id is not None
		out.write(c_bit(is_split_packet))
		if is_split_packet:
			out.write(c_ushort(split_packet_id))
			out.write(c_uint(split_packet_index), compressed=True)
			out.write(c_uint(split_packet_count), compressed=True)
		out.write(c_ushort(len(data) * 8), compressed=True)
		out.align_write()
		out.write(data)

		assert len(out) < 1492  # maximum packet size handled by raknet
		self._transport.sendto(out, self._address)

	@staticmethod
	def packet_header_length(reliability, is_split_packet):
		length = 32  # message number
		length += 3  # reliability
		if reliability in (PacketReliability.UnreliableSequenced, PacketReliability.ReliableOrdered):
			length += 5  # ordering channel
			length += 32
		length += 1  # is split packet
		if is_split_packet:
			length += 16  # split packet id
			length += 32  # split packet index (actually a compressed write so assume the maximum)
			length += 32  # split packet count (actually a compressed write so assume the maximum)
		length += 16  # data length (actually a compressed write so assume the maximum)
		return math.ceil(length / 8)
