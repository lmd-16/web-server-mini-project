import socket 
import os 
import sys 
import re 
import datetime 

class WebServer: 
    def __init__(self, host='localhost', port=8080, www_dir='./www'):
        self.host = host
        self.port = port

        self.www_dir = www_dir
        self.server_socket = None

    def start_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)

            print(f"Server is listening on {self.host}:{self.port}")
            print(f"Serving files from {self.www_dir}")

            while True:
                client_socket, client_address = self.server_socket.accept()
                print(f"Connection from {client_address}")

                self.handle_client(client_socket, client_address)

        except KeyboardInterrupt:
            print("\nShutting down server...")
        except Exception as e: 
            print(f"Server error:"{e})
        finally:
            if self.server_socket:
                self.server_socket.close()
    def ensure_www_directory(self):
        if not os.path.exists(self.www_dir):
            os.makedirs(self.www_dir)
            print(f"Created directory: {self.www_dir}")

            index_path = os.path.join(self.www_dir, 'index.html')
            

    def handle_client(self, client_socket, client_address):
        try:
            raw_data = client_socket.recv(4096).decode('utf-8', errors='ignore')
            if not raw_data:
                client_socket.close()
                return
            method, path, version, headers, body = self.parse_http_request(raw_data)
            print(f"Request: {method} {path} {version}")

            if version != 'HTTP/1.1':
                self.handle_505(client_socket)
                client_socket.close()
                return
            
            self.process_request(client_socket, method, path, version, headers, body)

        except Exception as e: 
            print(f"Error handling client:{e}")
        finally: 
            client_socket.close()
            
    def parse_http_request(self, raw_data):
        lines = raw_data.split('\r\n')

        if len(lines) < 1: 
            raise ValueError("Empty request")
        
        request_line = lines[0].split()
        if len(request_line) != 3: 
            raise ValueError("Invalid request line")
        
        method = request_line[0]
        path = request_line[1]
        version = request_line[2] 

        headers = {}
        header_end = 1
        for i in range(1, len(lines)):
            if lines[i] == '':
                header_end = i
                break
            if ': ' in lines[i]:
                key, value = lines[i].split(': ',1)
                headers[key] = value 

            body = ''
            if header_end + 1 < len(lines):
                body = '\r\n'.join(lines[header_end + 1:])
            
            return method, path, version, headers, body

    def process_request(self,client_socket, method, path, version, headers, body):
        if path == '/':
            file_path = os.path.join(self.www_dir, 'index.html')
        else: 
            file_path = os.path.join(self.www_dir, path[1:])

        if not os.path.exists(file_path):
            self.handle_404(client_socket)
            return
        
    def handle_404(self):
        


    def handle_505(self):

    def send_file_response(self, client_socket, file_path):

