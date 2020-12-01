"""
This script acts as a transparent proxy except NFC communication which can be
actively spoofed.

Note: If you get an Invalid Exchange error when running this script, this means
that the Switch has paired to the controller, invalidating the original pairing
key we created. You'll need to remove the controller before continuing.

This was tested on a Raspberry Pi 4B (4GB) with Python 3.7

-------------------------------------------------------------------------------
Usage:
-------------------------------------------------------------------------------

1) Turn on Switch
2) Go to Controllers -> "Change Grip/Order" menu
3) Detach both Joycons from console
4) Pair left Joycon as the only controller (press any key to pair it, then
SL + SR)
5) Exit the menu using left Joycon
6) Start the proxy.py script
7) Immediately after starting the script, press and hold the small, circular
button on the back of the right Joycon until the player lights begin flashing.
8) Once the script prints "Got Connection" and then "Waiting for Switch to
connect...", navigate to the "Change Grip/Order" menu using left Joycon.
9) The Switch should connect the right Joycon Proxy and the script should
enter the mainloop.
10) Press any key on the left Joycon to reconnect it
11) Hold L + R until Switch recognizes both Joycons (sometimes you need to
press those keys a few times and hold them a bit longer than usual)
12) Your Joycons are now usable but every time Switch requests NFC data,
proxy script for right Joycon will spoof responses to present data read from
file. If you didn't specify --nfc-data argument script acts as fully
transparent proxy.
13) Press Ctrl-C to end the script and dump the commands in the current
working directory (messages.txt file).
"""

import socket
import sys
import os
import time
import fcntl
import argparse
from time import perf_counter

from nxbt import toggle_input_plugin
from nxbt import BlueZ
from nxbt import Controller
from nxbt import JOYCON_R
from ir_nfc_mcu import IrNfcMcu, McuState, Action
from crc8 import crc8


def format_message(data, split, name):
    """Formats a given byte message in hex format split
    into payload and subcommand sections.

    :param data: A series of bytes
    :type data: bytes
    :param split: The location of the payload/subcommand split
    :type split: integer
    :param name: The name featured in the start/end messages
    :type name: string
    :return: The formatted data
    :rtype: string
    """

    payload = ""
    subcommand = ""
    for i in range(0, len(data)):
        data_byte = str(hex(data[i]))[2:].upper()
        if len(data_byte) < 2:
            data_byte = "0" + data_byte
        if i <= split:
            payload += data_byte + " "
        else:
            subcommand += data_byte + " "
            if i == 49 and len(data) > 50:
                subcommand += '\n'

    formatted = (
        f"--- {name} Msg ---\n" +
        f"Payload:    {payload}\n" +
        f"Subcommand: {subcommand}")

    return formatted


def write_to_buffer(buffer, message, message_type):
    if message_type == "switch":
        formatted_message = format_message(message, 10, "Switch")
    elif message_type == "controller":
        formatted_message = format_message(message, 13, "Controller")
    elif message_type == "comment":
        formatted_message = "### " + message + " ###"
    else:
        raise ValueError("Unspecified or wrong message type")

    buffer.append(formatted_message)


def command_set_nfc_ir_mcu_config(mcu, report, output_report):
    report[1] = 0x21
    report[3] = 0x8E
    report[14] = 0xA0
    report[15] = 0x21

    mcu.update_status()
    data = list(bytes(mcu)[0:34])
    crc = crc8()
    crc.update(bytes(data[:-1]))
    checksum = crc.digest()
    data[-1] = ord(checksum)

    for i in range(len(data)):
        report[16 + i] = data[i]

    sub_command_data = output_report[12:]
    if sub_command_data[1] == 0:
        if sub_command_data[2] == 0:
            write_to_buffer(message_buffer, "Changed MCU state to stand by", "comment")
            mcu.set_state(McuState.STAND_BY)
        elif sub_command_data[2] == 4:
            write_to_buffer(message_buffer, "Changed MCU state to NFC", "comment")
            mcu.set_state(McuState.NFC)
        else:
            print(f"unknown mcu state {sub_command_data[2]}")
    else:
        print(f"unknown mcu config command {sub_command_data}")

    return bytes(report)


def command_set_nfc_ir_mcu_state(mcu, report, output_report):
    report[1] = 0x21
    report[3] = 0x8E
    report[14] = 0x80
    report[15] = 0x22
    sub_command_id = output_report[12]
    if sub_command_id == 0x01:      # Resume
        mcu.set_action(Action.NON)
        mcu.set_state(McuState.STAND_BY)
    elif sub_command_id == 0x00:    # Suspend
        mcu.set_state(McuState.STAND_BY)
    else:
        raise NotImplementedError(f"Argument {sub_command_id} of SET_NFC_IR_MCU_STATE isn't implemented")

    return bytes(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Proxy Joycon (R) traffic except NFC communication')
    parser.add_argument('--mac', required=True)
    parser.add_argument('--nfc-data')
    args = parser.parse_args()

    nfc_data = None
    if args.nfc_data is not None:
        with open(args.nfc_data, 'rb') as datafile:
            nfc_data = datafile.read()

    port_ctrl = 17
    port_itr = 19
    message_buffer = []

    toggle_input_plugin(False)
    bt = BlueZ(adapter_path="/org/bluez/hci0")

    controller = Controller(bt, JOYCON_R)

    # Joy-Con Sockets
    jc_ctrl = socket.socket(family=socket.AF_BLUETOOTH,
                            type=socket.SOCK_SEQPACKET,
                            proto=socket.BTPROTO_L2CAP)
    jc_itr = socket.socket(family=socket.AF_BLUETOOTH,
                           type=socket.SOCK_SEQPACKET,
                           proto=socket.BTPROTO_L2CAP)

    # Switch sockets
    switch_itr = socket.socket(family=socket.AF_BLUETOOTH,
                               type=socket.SOCK_SEQPACKET,
                               proto=socket.BTPROTO_L2CAP)
    switch_ctrl = socket.socket(family=socket.AF_BLUETOOTH,
                                type=socket.SOCK_SEQPACKET,
                                proto=socket.BTPROTO_L2CAP)

    time_old = 0
    timer_old = 0
    timer_counter = 0
    try:
        # Remove the device before we try to re-pair
        device_path = bt.find_device_by_address(args.mac)
        if not device_path:
            print("Device not paired. Pairing...")

            # Ensure we are paired/connected to the JC
            print("Attempting to re-pair with device")
            devices = bt.discover_devices(alias="Joy-Con (R)", timeout=8)
            jc_device_path = None
            for key in devices.keys():
                print(devices[key]["Address"])
                if devices[key]["Address"] == args.mac:
                    jc_device_path = key
                    break

            if not jc_device_path:
                print("The specified Joy-Con could not be found")
            else:
                bt.pair_device(jc_device_path)
            print("Paired Joy-Con")

        bt.set_alias("Nintendo Switch")
        print("Connecting to Joy-Con: ", args.mac)
        jc_ctrl.connect((args.mac, port_ctrl))
        jc_itr.connect((args.mac, port_itr))
        print("Got connection.")

        switch_ctrl.bind((bt.address, port_ctrl))
        switch_itr.bind((bt.address, port_itr))

        bt.set_alias("Joy-Con (R)")
        bt.set_discoverable(True)

        print("Waiting for Switch to connect...")
        switch_itr.listen(1)
        switch_ctrl.listen(1)

        client_control, control_address = switch_ctrl.accept()
        print("Got Switch Control Client Connection")
        client_interrupt, interrupt_address = switch_itr.accept()
        print("Got Switch Interrupt Client Connection")

        # Creating a non-blocking client interrupt connection
        fcntl.fcntl(client_interrupt, fcntl.F_SETFL, os.O_NONBLOCK)

        # Initial Input report from Joy-Con
        jc_data = jc_itr.recv(350)
        print("Got initial Joy-Con Empty Report")
        write_to_buffer(message_buffer, "Joy-Con Empty Report", "comment")
        write_to_buffer(message_buffer, jc_data, "controller")
        print(message_buffer)

        # Send the input report to the Switch a couple times
        for i in range(3):
            print("Sending input report", i)
            client_interrupt.sendall(jc_data)
            time.sleep(1)

        # Get the Switch's reply and send it to the Joy-Con
        reply = client_interrupt.recv(350)
        write_to_buffer(message_buffer, "Switch Input Report Reply", "comment")
        write_to_buffer(message_buffer, reply, "switch")
        jc_itr.sendall(reply)

        # Waste some cycles here until we get the controllers info.
        print("Waiting on Joy-Con Device Info")
        while True:
            jc_data = jc_itr.recv(350)
            if jc_data[1] == 0x21:
                print("Got Device Info")
                print("Joy-Con Device Info Reply Length", len(jc_data))
                write_to_buffer(message_buffer, "Joy-Con Device Info", "comment")
                write_to_buffer(message_buffer, jc_data, "controller")
                client_interrupt.sendall(jc_data)
                break

        # Main loop
        print("Entering main proxy loop")
        write_to_buffer(message_buffer, "Entering Main Loop", "comment")
        time_old = perf_counter()
        mcu = IrNfcMcu()
        mcu.set_nfc(nfc_data)
        last_output_report = None
        while True:
            try:
                reply = client_interrupt.recv(350)
                write_to_buffer(message_buffer, reply, "switch")
            except BlockingIOError:
                reply = None

            if reply:
                if reply[1] == 0x01 and (reply[11] == 0x21 or reply[11] == 0x22):
                    last_output_report = reply
                jc_itr.sendall(reply)
            jc_data = jc_itr.recv(350)

            timer_new = int(jc_data[2])
            if timer_new < timer_old:
                timer_counter += timer_new - (timer_old - 255)
            else:
                timer_counter += timer_new - timer_old
            timer_old = timer_new

            if nfc_data is not None:
                # Modify NFC-related input reports from Joycon
                if jc_data is not None:
                    if jc_data[14] == 0xA0 and jc_data[15] == 0x21:
                        write_to_buffer(message_buffer, "SET_NFC_IR_CONFIG(original)", "comment")
                        write_to_buffer(message_buffer, jc_data, "controller")
                        jc_data = command_set_nfc_ir_mcu_config(mcu, list(jc_data), last_output_report)
                        write_to_buffer(message_buffer, "SET_NFC_IR_CONFIG(spoofed)", "comment")
                        write_to_buffer(message_buffer, jc_data, "controller")
                    elif jc_data[14] == 0x80 and jc_data[15] == 0x22:
                        write_to_buffer(message_buffer, "SET_NFC_IR_STATE(original)", "comment")
                        write_to_buffer(message_buffer, jc_data, "controller")
                        jc_data = command_set_nfc_ir_mcu_state(mcu, list(jc_data), last_output_report)
                        write_to_buffer(message_buffer, "SET_NFC_IR_STATE(spoofed)", "comment")
                        write_to_buffer(message_buffer, jc_data, "controller")
                    elif jc_data[1] == 0x31:
                        write_to_buffer(message_buffer, "NFC/IR mode report(original)", "comment")
                        write_to_buffer(message_buffer, jc_data, "controller")
                        mcu.update_nfc_report()
                        jc_data = jc_data[:50] + bytes(mcu)
                        write_to_buffer(message_buffer, "NFC/IR mode report(spoofed)", "comment")
                        write_to_buffer(message_buffer, jc_data, "controller")
                    else:
                        write_to_buffer(message_buffer, jc_data, "controller")

                # Change MCU state based on output report from Switch
                if reply is not None and reply[1] == 0x11:
                    sub_command = reply[11]
                    sub_command_data = reply[12:]
                    if mcu.get_action() not in (Action.READ_TAG, Action.READ_TAG_2, Action.READ_FINISHED):
                        if sub_command == 0x01:  # Request MCU state
                            write_to_buffer(message_buffer, "MCU status requested", "comment")
                            mcu.set_action(Action.REQUEST_STATUS)
                        elif sub_command == 0x02:  # Start tag discovery
                            if sub_command_data[0] == 0x04:  # 4: StartWaitingReceive
                                write_to_buffer(message_buffer, "Tag discovery started", "comment")
                                mcu.set_action(Action.START_TAG_DISCOVERY)
                            elif sub_command_data[0] == 0x01:  # 1: Start polling
                                write_to_buffer(message_buffer, "Started polling", "comment")
                                mcu.set_action(Action.START_TAG_POLLING)
                            elif sub_command_data[0] == 0x02:  # 2: Stop polling
                                write_to_buffer(message_buffer, "Stopped polling", "comment")
                                mcu.set_action(Action.NON)
                            elif sub_command_data[0] == 0x06:
                                write_to_buffer(message_buffer, "Tag read started", "comment")
                                mcu.set_action(Action.READ_TAG)
                            else:
                                print(f'Unknown sub_command_data arg {sub_command_data}')
                        else:
                            print(f'Unknown MCU sub command {sub_command}')
            else:
                write_to_buffer(message_buffer, jc_data, "controller")

            client_interrupt.sendall(jc_data)

    except KeyboardInterrupt:
        print("Closing sockets")

        time_new = perf_counter()
        print(f"Total Delta: {(time_new - time_old) * 1000}")
        print(f"Timer Counter: {timer_counter}")

        jc_ctrl.close()
        jc_itr.close()

        switch_itr.close()
        switch_ctrl.close()

    except OSError as e:
        print("Closing sockets")

        jc_ctrl.close()
        jc_itr.close()

        switch_itr.close()
        switch_ctrl.close()

        raise e

    finally:
        toggle_input_plugin(True)
        with open("messages.txt", "w") as f:
            f.write("\n".join(message_buffer))

        try:
            sys.exit(1)
        except SystemExit:
            os._exit(1)