import logging

log = logging.getLogger(__file__)

class RTOCalc:
	def __init__(self):
		self._srtt: float = -1 # smoothed round trip time
		self._rtt_var: float = -1 # round trip time variation
		self._rto: float = 1  # retransmission timeout = 1 second

	def rto(self) -> float:
		return self._rto

	def update(self, rtt: float):
		if self._srtt == -1:
			self._srtt = rtt
			self._rtt_var = rtt/2
		else:
			alpha = 0.125
			beta = 0.25
			self._rtt_var = (1 - beta) * self._rtt_var + beta * abs(self._srtt - rtt)
			self._srtt = (1 - alpha) * self._srtt + alpha * rtt
		self._rto = max(1, self._srtt + 4*self._rtt_var)  # originally specified be at least clock resolution but since the client loop is set at 10 milliseconds there's no way it can be smaller anyways

class CWNDCalc:
	def __init__(self):
		self._cwnd: float = 1  # congestion window, limits how many packets we can send at once
		self._ssthresh = float("inf")  # slow start threshold, the level at which we switch from slow start to congestion control

	def cwnd(self) -> float:
		return self._cwnd

	def update(self, packets_sent: int, num_acks: int, num_holes: int) -> None:
		if num_holes > 0:
			log.info("Missing Acks/Holes: %i", num_holes)
			self._ssthresh = self._cwnd/2
			self._cwnd = self._ssthresh
		else:
			if packets_sent >= self._cwnd: # we're actually hitting the limit and not idling
				if num_acks > self._ssthresh:
					self._cwnd += num_acks/self._cwnd
				else:
					self._cwnd += num_acks
