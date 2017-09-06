"""
Reliability layer. UDP doesn't guarantee delivery or ordering, so this is where RakNet provides optional support for these features.
For retransmission algorithm see http://www.saminiir.com/lets-code-tcp-ip-stack-5-tcp-retransmission
Congestion control is based on TCP Reno, see http://ee.lbl.gov/papers/congavoid.pdf
"""
# Todo: combine packets in datagrams if possible
# Todo: Congestion avoidance instead of congestion control (prevent congestion control beforehand instead of coping with it afterwards)
import asyncio
import logging
import math
import time
from collections import OrderedDict

from . import rangelist
from .bitstream import BitStream, c_bit, c_uint, c_ushort

log = logging.getLogger(__name__)

MTU_SIZE = 1492  # Default used by RakNet, Ethernet
MTU_SIZE = 1228  # Hardcoded by LU for some reason
UDP_HEADER_SIZE = 28

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
		self._cwnd = 1  # congestion window, limits how many packets we can send at once
		self._ssthresh = float("inf")  # slow start threshold, the level at which we switch from slow start to congestion control
		self._packets_sent = 0
		self._send_message_number_index = 0
		self._sequenced_write_index = 0
		self._sequenced_read_index = 0
		self._ordered_write_index = 0
		self._ordered_read_index = 0
		self._out_of_order_packets = {}  # for ReliableOrdered
		self._split_packet_queue = {}
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
			else:
				alpha = 0.125
				beta = 0.25
				self._rtt_var = (1 - beta) * self._rtt_var + beta * abs(self._srtt - rtt)
				self._srtt = (1 - alpha) * self._srtt + alpha * rtt
			self._rto = max(1, self._srtt + 4*self._rtt_var)  # originally specified be at least clock resolution but since the client loop is set at 10 milliseconds there's no way it can be smaller anyways

			acks = rangelist.RangeList(data)
			for message_number in acks:
				if message_number in self._resends:
					del self._resends[message_number]

			num_acks = len(acks)
			act_num_holes = 0 # number of holes that actually correspond to resends
			for hole in acks.holes():
				if hole in self._resends:
					act_num_holes += 1

			if act_num_holes > 0:
				log.info("Missing Acks/Holes: %i", act_num_holes)
				self._ssthresh = self._cwnd/2
				self._cwnd = self._ssthresh
			else:
				if self._packets_sent >= self._cwnd: # we're actually hitting the limit and not idling
					if num_acks > self._ssthresh:
						self._cwnd += num_acks/self._cwnd
					else:
						self._cwnd += num_acks

			self._packets_sent = 0
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
				split_packet_id = data.read(c_ushort)
				split_packet_index = data.read(c_uint, compressed=True)
				split_packet_count = data.read(c_uint, compressed=True)

			length = data.read(c_ushort, compressed=True)
			data.align_read()
			packet_data = data.read(bytes, length=math.ceil(length / 8))

			if reliability in (PacketReliability.Reliable, PacketReliability.ReliableOrdered):
				self._acks.insert(message_number)

			if is_split_packet:
				if not split_packet_id in self._split_packet_queue:
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

		if ReliabilityLayer.packet_header_length(reliability, False) + len(data) >= MTU_SIZE - UDP_HEADER_SIZE:
			data_offset = 0
			chunks = []
			while data_offset < len(data):
				data_length = MTU_SIZE - UDP_HEADER_SIZE - ReliabilityLayer.packet_header_length(reliability, True)
				chunks.append(data[data_offset:data_offset+data_length])
				data_offset += data_length

			split_packet_id = self._split_packet_id
			self._split_packet_id += 1
			for split_packet_index, chunk in enumerate(chunks):
				self._sends.append((chunk, reliability, ordering_index, split_packet_id, split_packet_index, len(chunks)))
		else:
			self._sends.append((data, reliability, ordering_index, None, None, None))

	def _send_loop(self):
		for message_number, resend_data in self._resends.items():
			resend_time, packet = resend_data
			if resend_time > time.time():
				continue

			if self._packets_sent >= self._cwnd:
				break
			self._packets_sent += 1
			log.info("actually resending %i", message_number)

			data, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count = packet
			self._send_packet(data, message_number, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count)
			if reliability == PacketReliability.Reliable or reliability == PacketReliability.ReliableOrdered:
				self._resends[message_number] = time.time()+self._rto, packet

		while self._sends:
			if self._packets_sent >= self._cwnd:
				break
			packet = self._sends.pop(0)
			self._packets_sent += 1

			data, reliability, ordering_index, split_packet_id, split_packet_index, split_packet_count = packet
			message_number = self._send_message_number_index
			self._send_message_number_index += 1

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
		out.write(c_bit(bool(self._acks)))
		if self._acks:
			out.write(c_uint(self._remote_system_time))
			out.write(self._acks.serialize())
			self._acks.clear()

		assert ReliabilityLayer.packet_header_length(reliability, split_packet_id is not None) + len(data) <= MTU_SIZE - UDP_HEADER_SIZE

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
