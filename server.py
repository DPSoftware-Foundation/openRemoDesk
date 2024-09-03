import socket
import threading
import keyboard
import brotli
import numpy as np
import cv2
import configparser
from PIL import ImageGrab
import struct
import queue
import pickle
import mouse
import pyogg

# Read configuration
config = configparser.ConfigParser()
config.read('serverconfig.ini')

# general config
compression = int(config["general"]["compression"])

# video config
jpegquality = int(config["video"]["quality"])
formatcodec = config["video"]["format"]
resX = int(config["video"]["x"])
resY = int(config["video"]["y"])

# audio config
aenable = bool(int(config["audio"]["enable"]))
abitrate = int(config["audio"]["bitrate"])

# server config
HOST = config["server"]["ip"]
PORT = int(config["server"]["port"])

screensize = ()

def capture_screen():
    global screensize
    screenshot = ImageGrab.grab()
    screensize = screenshot.size
    img_np = np.array(screenshot)
    img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
    return img_bgr

def imagenc(image, quality=90):
    if formatcodec == "webp":
        retval, buffer = cv2.imencode('.webp', image, [int(cv2.IMWRITE_WEBP_QUALITY), quality])
    elif formatcodec == "jpeg":
        retval, buffer = cv2.imencode('.jpeg', image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    elif formatcodec == "avif":
        retval, buffer = cv2.imencode('.avif', image, [int(cv2.IMWRITE_AVIF_QUALITY), quality])

    else:
        raise TypeError(f"{formatcodec} is not supported")

    if not retval:
        raise ValueError("image encoding failed.")

    return np.array(buffer).tobytes()

def translate_coordinates(x, y, resized_width, resized_height):
    translated_x = int(x * screensize[0] / resized_width)
    translated_y = int(y * screensize[1] / resized_height)
    return translated_x, translated_y

def convert_quality(quality):
    brotli_quality = int(quality / 100 * 11)
    lgwin = int(10 + (quality / 100 * (24 - 10)))

    return brotli_quality, lgwin

client_sockets = []
buffer = queue.Queue(maxsize=10)
first = True
running = False

def capture():
    while running:
        # Capture the screen
        screen_image = capture_screen()

        # Resize the image
        stretch_near = cv2.resize(screen_image, (resX, resY), interpolation=cv2.INTER_NEAREST)

        # Encode the image
        encoded = imagenc(stretch_near, jpegquality)

        #compressed = lz4.frame.compress(encoded, lz4.frame.COMPRESSIONLEVEL_MAX)

        bquality, lgwin = convert_quality(compression)

        compressed = brotli.compress(encoded, quality=bquality, lgwin=lgwin)

        data_length = struct.pack('!III', len(compressed), resX, resY)
        data2send = data_length + compressed

        buffer.put(data2send)

def handle_client():
    global running, first
    try:
        while running:
            data2send = buffer.get()

            for i in client_sockets:
                try:
                    i.sendall(data2send)
                except Exception as e:
                    i.close()
                    client_sockets.remove(i)

            if not client_sockets:
                running = False
                first = True
                print("No clients connected. Server is standby")
                break

    except socket.error:
        pass
    except Exception as e:
        print(f"Error in handle_client: {e}")

def handle_client_commands(client_socket):
    try:
        while True:
            try:
                # Receive the length of the data
                data_length = receive_exact(client_socket, 4)
                if not data_length:
                    break

                commandmetadata = struct.unpack('!I', data_length)
                command_data = receive_exact(client_socket, commandmetadata[0])
                command = pickle.loads(command_data)

                if command:
                    action = command["action"]
                    data = command["data"]

                    if action == "move_mouse":
                        x, y = data["x"], data["y"]
                        rx, ry = translate_coordinates(x, y, resX, resY)
                        print(f"move mouse to x: {rx} | y: {ry}")
                    elif action == "click_mouse":
                        button = data["button"]
                        state = data["state"]

                        if button == 1:
                            if state == "down":
                                mouse.press()
                            else:
                                mouse.release()
                        elif button == 2:
                            if state == "down":
                                mouse.press(mouse.MIDDLE)
                            else:
                                mouse.release(mouse.MIDDLE)
                        elif button == 3:
                            if state == "down":
                                mouse.press(mouse.RIGHT)
                            else:
                                mouse.release(mouse.RIGHT)
                        #elif button == 4:
                        #    mouse.wheel()
                        #elif button == 5:
                        #    mouse.wheel(-1)
                    elif action == "keyboard":
                        key = data["key"]
                        state = data["state"]

                        if state == "down":
                            keyboard.press(key)
                        else:
                            keyboard.release(key)
                    else:
                        print(command)
            except socket.error:
                break
    except Exception as e:
        print(f"Error in handle_client_commands: {e}")

def receive_exact(socket, n):
    """Helper function to receive exactly n bytes."""
    data = b''
    while len(data) < n:
        packet = socket.recv(n - len(data))
        if not packet:
            return None
        data += packet
    return data

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind((HOST, PORT))
s.listen()

print(f"Server started on {HOST}:{PORT}")

while True:
    conn, addr = s.accept()
    print(f'{addr} is connected')

    client_sockets.append(conn)

    if first:
        running = True
        # Start the capture thread
        capture_thread = threading.Thread(target=capture)
        handle_client_thread = threading.Thread(target=handle_client)
        capture_thread.start()
        handle_client_thread.start()

        first = False

    command_thread = threading.Thread(target=handle_client_commands, args=(conn,))
    command_thread.start()
