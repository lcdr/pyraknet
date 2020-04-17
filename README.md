# pyraknet
## A Python implementation of RakNet 3.25.
### Created by lcdr
### Source repository at https://github.com/lcdr/pyraknet/
### License: [AGPL v3](https://www.gnu.org/licenses/agpl-3.0.html)


This is not aimed to be a complete port of RakNet, just everything that's needed to run a server.

#### Not implemented:

* Client side

* Encryption

* ReliableSequenced sending / receiving

* Grouped sending

* (BitStream) Compressed floats/doubles

#### Requirements:
* Python 3.6

### Installation

`pip install git+https://github.com/lcdr/pyraknet` should handle the installation automatically. If you run into problems you might have to execute pip as admin, or if you have multiple Python versions installed explicitly use the pip of the compatible Python version.

### Sample server setup
Run python -m pyraknet or execute \_\_main\_\_.py to start a sample server that will send packets manually, from ./packets/

To send something, place your packets in ./packets/<your subfolder> and enter the subfolder name in the command line.

The server logs incoming and outgoing packets in ./logs/, if you get errors you might have to create the directory (and possibly subdirectories).
