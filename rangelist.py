from typing import Collection, Iterator, List

from .bitstream import BitStream, c_bit, c_uint, c_ushort

class _Range:
	__slots__ = "min", "max"

	def __init__(self, min: int, max: int):
		self.min = min
		self.max = max

class RangeList(Collection[int]):
	"""
	List that stores integers and compresses them to ranges if possible.
	To add an item, use insert.
	To get the uncompressed ranges, use ranges.

	Internal:
		The internal list of ranges is auto-sorted.
		Ranges in the internal representation are inclusive from both ends (that is, (20, 25) contains both 20 and 25 and everything in between)
	"""

	def __init__(self, input_stream: BitStream=None):
		"""Init the rangelist, optionally by deserializing from a bitstream."""
		self._ranges: List[_Range] = []
		if input_stream is not None:
			count = input_stream.read(c_ushort, compressed=True)
			for _ in range(count):
				max_equal_to_min = input_stream.read(c_bit)
				min = input_stream.read(c_uint)
				if max_equal_to_min:
					max = min
				else:
					max = input_stream.read(c_uint)
				self._ranges.append(_Range(min, max))

	def __bool__(self):
		return bool(self._ranges)

	def __len__(self):
		len = 0
		for range_ in self._ranges:
			len += range_.max - range_.min + 1
		return len

	def __iter__(self):
		"""Yield the numbers in the ranges, basically uncompressing the ranges."""
		for range_ in self._ranges:
			yield from range(range_.min, range_.max + 1)

	def __contains__(self, item):
		if not isinstance(item, int):
			return False
		for range in self._ranges:
			if range.min <= item <= range.max:
				return True

		return False

	def clear(self) -> None:
		self._ranges.clear()

	def holes(self) -> Iterator[int]:
		"""Yield the items 'between' the ranges."""
		last_max = None
		for range_ in self._ranges:
			if last_max is not None:
				yield from range(last_max + 1, range_.min)
			last_max = range_.max

	def num_holes(self) -> int:
		"""Return the number of items 'between' the ranges."""
		num_holes = 0
		last_max = None
		for range in self._ranges:
			if last_max is not None:
				num_holes += range.min - last_max - 1
			last_max = range.max
		return num_holes

	def insert(self, item: int) -> None:
		iter_ = iter(self._ranges)
		for range in iter_:
			if range.min == item + 1:  # The item can extend the range
				range.min -= 1
				return
			if range.min <= item:
				if range.max == item - 1:  # The item can extend the range
					range.max += 1
					try:
						nextrange = next(iter_)
						if nextrange.min == item + 1:  # The newly updated list has a max of one less than the next list, in which case we can merge these
							# Merge the ranges
							range.max = nextrange.max
							self._ranges.remove(nextrange)
					except StopIteration:
						pass
					return
				if range.max >= item:  # The item is within the range, we don't even need to update it
					return
			else:
				# If we got here, the range starts at a higher position than the item, so we should insert it now (the list is auto-sorted so there can't be any other position)
				self._ranges.insert(self._ranges.index(range), _Range(item, item))
				return

		# We ran through the whole list and couldn't find a good existing range
		self._ranges.append(_Range(item, item))

	def serialize(self) -> BitStream:
		"""
		Serialize the RangeList. This is meant to be compatible with RakNet's serialization.
		(This currently serializes items as uints, since currently the only occurrence where I need to serialize a rangelist is with an uint)
		"""
		out = BitStream()
		out.write(c_ushort(len(self._ranges)), compressed=True)
		for range in self._ranges:
			out.write(c_bit(range.min == range.max))
			out.write(c_uint(range.min))
			if range.min != range.max:
				out.write(c_uint(range.max))
		return out
