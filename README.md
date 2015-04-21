Python implementation of RakNet 3.25. This is not aimed to be a complete port of RakNet, just everything that's needed to run a server or client.

#### Not implemented:

* Encryption

* Sequenced sending

* ReliableSequenced receiving

* Split packets / Packets longer than 1500 bytes

* Grouped sending

* (BitStream) Compressed floats/doubles

#### Requirements:
* Python 3.4

### Sample server setup
Run python -m pyraknet or execute \_\_main\_\_.py to start a sample server that will send packets manually, from ./packets/

To send something, place your packets in ./packets/<your subfolder> and enter the subfolder name in the command line.

The server logs incoming and outgoing packets in ./logs/, if you get errors you might have to create the directory (and possibly subdirectories).

## License: GPL v3
