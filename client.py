import pickle
import socket
import struct
import sys
import paramiko
import pygame
import threading
import numpy as np
import cv2
import time
import brotli
import logging

logging.basicConfig(level=logging.DEBUG)

class Client:
    def __init__(self, host='127.0.0.1', port=65432, protocol="tcp", user="", password="", start_size=(1280, 720)):
        self.host = host
        self.port = port
        self.protocol = protocol
        self.user = user
        self.password = password
        # System Variables
        self.screen = None
        self.clock = None
        self.running = False
        self.socket = None
        self.lock = threading.Lock()
        self.image_data = None
        self.reconnect_delay = 5  # Delay in seconds before attempting to reconnect
        self.start_time = time.time()
        self.frames_received = 0
        self.total_bytes_received = 0
        self.total_frame = 0
        self.current_screen_size = start_size
        self.new_screen_size = self.current_screen_size
        self.resize_screen_size = self.current_screen_size
        self.sshtransport = None
        self.no2compression = True

    def convert_mouse_position(self, mouse_x, mouse_y):
        """
        Converts the mouse position from the resized screen to the original screen size.
        """
        if self.current_screen_size == self.resize_screen_size:
            return mouse_x, mouse_y

        scale_x = self.current_screen_size[0] / self.resize_screen_size[0]
        scale_y = self.current_screen_size[1] / self.resize_screen_size[1]

        original_x = int(mouse_x * scale_x)
        original_y = int(mouse_y * scale_y)

        return original_x, original_y

    def send_action(self, action, **kwargs):
        """
        Send mouse action (click or drag) to the server.
        """
        try:
            # Create a dictionary to represent the mouse action
            data = {
                'action': action,  # 'click', 'drag'
                "data": kwargs
            }
            # Convert the dictionary to JSON and encode it
            data = pickle.dumps(data)
            # Send the data to the server
            if self.socket:
                self.socket.sendall(struct.pack('!I', len(data)) + data)
        except (socket.error, BrokenPipeError) as e:
            self.reconnect()

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.exit()
            if event.type == pygame.VIDEORESIZE:
                self.resize_screen_size = (event.w, event.h)

            if event.type == pygame.MOUSEBUTTONDOWN:
                mouse_x, mouse_y = pygame.mouse.get_pos()
                original_mouse_x, original_mouse_y = self.convert_mouse_position(mouse_x, mouse_y)
                self.send_action('click_mouse', state="down", x=original_mouse_x, y=original_mouse_y, button=event.button)

            if event.type == pygame.MOUSEBUTTONUP:
                mouse_x, mouse_y = pygame.mouse.get_pos()
                original_mouse_x, original_mouse_y = self.convert_mouse_position(mouse_x, mouse_y)
                self.send_action('click_mouse', state="up", x=original_mouse_x, y=original_mouse_y, button=event.button)

            if event.type == pygame.MOUSEMOTION:
                mouse_x, mouse_y = pygame.mouse.get_pos()
                original_mouse_x, original_mouse_y = self.convert_mouse_position(mouse_x, mouse_y)
                self.send_action('move_mouse', x=original_mouse_x, y=original_mouse_y)

            if event.type == pygame.KEYDOWN:
                self.send_action('keyboard', state="down", key=pygame.key.name(event.key))

            if event.type == pygame.KEYUP:
                self.send_action('keyboard', state="up", key=pygame.key.name(event.key))

    def init(self):
        pygame.init()
        # Set up the display
        self.screen = pygame.display.set_mode(self.current_screen_size, pygame.RESIZABLE)
        pygame.display.set_caption("OpenRemoDesk Client")

        self.running = True

        self.con2server = threading.Thread(target=self.connect_to_server, daemon=True)
        self.con2server.start()

        self.clock = pygame.time.Clock()
        while self.running:
            self.handle_events()
            self.receive_and_render()

            if self.new_screen_size != self.current_screen_size:
                self.current_screen_size = self.new_screen_size
                self.resize_screen_size = self.new_screen_size
                self.screen = pygame.display.set_mode(self.current_screen_size, pygame.RESIZABLE)

            # Update the display
            pygame.display.flip()

            # Cap the frame rate
            self.clock.tick(30)

        self.exit()

    def connect_to_server(self):
        while self.running:
            try:
                self.frames_received = 0
                self.total_bytes_received = 0
                self.show_message(f"Connecting to {self.host}:{self.port} with {self.protocol}")
                # Connect to the server
                if self.protocol == "tcp":
                    self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.socket.connect((self.host, self.port))
                elif self.protocol == "ssh":
                    self.sshtransport = paramiko.SSHClient()
                    self.sshtransport.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    self.sshtransport.connect(self.host, port=self.port, username=self.user, password=self.password)
                    self.show_message(f"Request main channel...")
                    channel1 = self.sshtransport.invoke_shell()
                    time.sleep(0.1)
                    self.show_message(f"Request remote channel...")
                    channel1.send(b'startremotedesktop\n')
                    time.sleep(0.1)
                    self.socket = self.sshtransport.invoke_shell()
                else:
                    print("protocol not found")
                    self.exit()

                # Start the data reception thread
                self.receive_thread = threading.Thread(target=self.receive_data, daemon=True)
                self.receive_thread.start()

                print(f"Connected")
                self.show_message(f"Connected to server at {self.host}:{self.port}")
                break  # Exit the loop if connection is successful

            except (socket.error, ConnectionRefusedError):
                self.show_message(f"Failed to connect to server. Retrying in {self.reconnect_delay} seconds...")
                time.sleep(self.reconnect_delay)

    def imagedec(self, encoded_bytes):
        encoded_array = np.frombuffer(encoded_bytes, dtype=np.uint8)
        image = cv2.imdecode(encoded_array, cv2.IMREAD_COLOR)
        return image

    def receive_data(self):
        self.show_message(f"Decoding...")
        while self.running:
            try:
                data_length = self._recvall(12)

                if not data_length:
                    break

                metadata = struct.unpack('!III', data_length)
                screensize = (metadata[1], metadata[2])

                # Update screen size only if it has changed
                if self.current_screen_size != screensize:
                    self.new_screen_size = screensize

                # Receive the data
                data = self._recvall(metadata[0])

                if data:
                    self.frames_received += 1
                    self.total_bytes_received += len(data)

                    # Decompress and lock the data
                    #decompressed_data = lz4.frame.decompress(data)
                    if self.no2compression:
                        frame_data = data
                    else:
                        try:
                            frame_data = brotli.decompress(data)
                            self.no2compression = False
                        except:
                            try:
                                frame_data = data
                                self.no2compression = True
                            except:
                                print("decode frame error")
                                continue

                    with self.lock:
                        time.sleep(0)
                        self.image_data = frame_data

                    print(f"Received data length: {len(data)}")

            except (socket.error, ConnectionResetError) as e:
                self.reconnect()
                break

    def _recvall(self, n, sock=None):
        """Helper function to receive exactly n bytes."""
        if not sock:
            sock = self.socket

        data = b''
        while len(data) < n:
            try:
                packet = sock.recv(n - len(data))
            except:
                break

            if not packet:
                return None
            data += packet
        return data

    def reconnect(self):
        print("reconnecting...")
        with self.lock:
            self.image_data = None  # Clear the image data on reconnect
        self.socket.close()
        if self.sshtransport:
            self.sshtransport.close()
        self.connect_to_server()

    def receive_and_render(self):
        with self.lock:
            if self.image_data:
                # Decode codec data
                cv2_image = self.imagedec(self.image_data)

                if cv2_image.any():
                    # Resize the image
                    if self.resize_screen_size != self.current_screen_size:
                        cv2_image = cv2.resize(cv2_image, self.resize_screen_size, interpolation=cv2.INTER_NEAREST)

                    # Convert OpenCV image to Pygame Surface
                    pygame_surface = self.cv22pygame(cv2_image)

                    # Render the image
                    self.screen.blit(pygame_surface, (0, 0))

        # Update the title bar with FPS and bitrate
        self.total_frame += 1
        if self.total_frame == 30:
            elapsed_time = time.time() - self.start_time
            fps = self.frames_received / elapsed_time if elapsed_time > 0 else 0
            bitrate = (self.total_bytes_received * 8) / (elapsed_time * 1024) if elapsed_time > 0 else 0  # kbps
            pygame.display.set_caption(f"OpenRemoDesk Client - FPS: {fps:.2f} - Bitrate: {int(bitrate)} Kbps")
            self.frames_received = 0
            self.total_bytes_received = 0
            self.total_frame = 0
            self.start_time = time.time()

    def cv22pygame(self, cv2_image):
        rgb_image = cv2.cvtColor(cv2_image, cv2.COLOR_BGR2RGB)
        pygame_surface = pygame.surfarray.make_surface(rgb_image.swapaxes(0, 1))
        return pygame_surface

    def show_message(self, message):
        # Clear the screen
        self.screen.fill((0, 0, 0))
        # Create a font object
        font = pygame.font.Font(None, 50)
        # Render the text
        text = font.render(message, True, (255, 255, 255))
        # Center the text
        text_rect = text.get_rect(center=(self.screen.get_width() / 2, self.screen.get_height() / 2))
        # Blit the text to the screen
        self.screen.blit(text, text_rect)
        pygame.display.flip()

    def exit(self):
        self.running = False
        if self.socket:
            self.socket.close()
        if self.sshtransport:
            self.sshtransport.close()
        pygame.quit()
        sys.exit()

# Instantiate and run the client
ORDClient = Client(
    host="192.168.1.5",
    #host="localhost",
    port=2222,
    protocol="ssh",
    user="remote",
    password="12345"
)

ORDClient.init()
