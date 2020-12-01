# Switch NFC Proxy

This repository provides a simple script to proxy Joy-Con <-> Switch communication with ability to spoof data read from NFC reader.

# Installation

* Clone this repository
* Install dependencies
```
sudo pip3 install nxbt crc8
```
* Run proxy.py script. Check comment in proxy.py for full usage procedure.
```
sudo python3 proxy.py --mac XX:XX:XX:XX:XX:XX --nfc-data /path/to/data.bin
```

# Getting MAC address

To obtain MAC address of right Joy-Con use following command:
```
bluetoothctl scan on
```
Now press pairing button (tiny button below lights) and wait for Joy-Con to show up on the list. Press Ctrl-C to terminate scanning process.

# Credits

* [dekuNukem/Nintendo_Switch_Reverse_Engineering](https://github.com/dekuNukem/Nintendo_Switch_Reverse_Engineering)
* [Brikwerk/nxbt](https://github.com/Brikwerk/nxbt) - this repository is based on this amazing controller emulation effort, original proxy.py was taken from there
* [mart1nro/joycontrol](https://github.com/mart1nro/joycontrol) - NFC emulation logic has been taken from this project

# License

This project is licensed under GPLv3 because of dependency on joycontrol.

# Limitation/known issues
* Please keep in mind this whole project is just a Proof of Concept based on a simple idea of gluing nxbt with pieces of joycontrol. Don't expect high coding standards from it ;)
* IrNfcMcu - contains a lot of magic constants and logic. I used it as a blackbox because it "just works" but ideally this should be removed from the project and replaced with a component written from a scratch. Unfortunately I would need some real NFC communication dumps for research and I don't have any right now. If you would like to contribute to this project this is a great way to start.

