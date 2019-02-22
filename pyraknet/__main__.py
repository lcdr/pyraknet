"""
Sample implementation of a pyraknet server, with no automation other than the part handled by pyraknet. Useful for manually sending test packets.
"""
import asyncio
import logging
import os
import threading
import traceback

import pyraknet.server
from pyraknet.messages import Address

logging.basicConfig(format="%(levelname).1s:%(message)s", level=logging.DEBUG)

class Server(pyraknet.server.Server):
	def __init__(self, address: Address, max_connections: int, incoming_password: bytes):
		super().__init__(address, max_connections, incoming_password, None)
		print("Enter packet directory path to send packets in directory")
		command_line = threading.Thread(target=self.input_loop, daemon=True) # I'd like to do this with asyncio but I can't figure out how
		command_line.start()

	def input_loop(self) -> None:
		while True:
			try:
				path = "./packets/"+input()
				for file in os.listdir(path):
					with open(path+"/"+file, "rb") as content:
						print("sending", file)
						self.send(content.read(), broadcast=True)
			except OSError:
				traceback.print_exc()

if __name__ == "__main__":
	print("Enter server port")
	port = int(input())
	Server(("localhost", port), max_connections=10, incoming_password=b"3.25 ND1")

	loop = asyncio.get_event_loop()
	loop.run_forever()
	loop.close()
