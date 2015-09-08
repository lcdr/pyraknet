"""
Sample implementation of a pyraknet server, with no automation other than the part handled by pyraknet. Useful for manually sending test packets.
"""
import asyncio
import os
import threading
import traceback

import pyraknet.peer

class Server(pyraknet.peer.Peer):
	@asyncio.coroutine
	def __init__(self, *args, **kwargs):
		yield from super().__init__(*args, **kwargs)
		print("Enter packet directory path to send packets in directory")
		command_line = threading.Thread(target=self.input_loop, daemon=True) # I'd like to do this with asyncio but I can't figure out how
		command_line.start()

	def input_loop(self):
		while True:
			try:
				path = "./packets/"+input()
				for file in os.listdir(path):
					with open(path+"/"+file, "rb") as content:
						print("sending", file)
						self.send(content.read(), broadcast=True)
			except OSError:
				traceback.print_exc()

@asyncio.coroutine
def main():
	server = Server.__new__(Server)
	print("Enter server port")
	port = int(input())
	yield from server.__init__(("localhost", port), max_incoming_connections=10, incoming_password=b"3.25 ND1")

if __name__ == "__main__":
	asyncio.async(main())

	loop = asyncio.get_event_loop()
	loop.run_forever()
	loop.close()
