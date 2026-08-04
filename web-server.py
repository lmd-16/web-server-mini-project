import socket 
import os 
import sys 
import re 
import datetime 
import threading
import time
from email.utils import parsedate_to_datetime

class WebServer: 
    def __init__(self, host='localhost', port=8080, www_dir='./public'):
        self.host = host
        self.port = port

        self.www_dir = www_dir
        self.server_socket = None
        self.CHUNK_SIZE = 8192
        self.USE_CHUNKED_THRESHOLD = 1024 * 100 #chunk threshold is 100KB
        self.ensure_www_directory()

    def start_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)

            print(f"Server is listening on {self.host}:{self.port}")
            print(f"Serving files from {self.www_dir}")
            print(f"Multi-threading: ENABLED (one thread per client)")
            print(f"Chunked transfer: ENABLED (files > {self.USE_CHUNKED_THRESHOLD/1024:.1f}KB)")
            print(f"Chunk size: {self.CHUNK_SIZE} bytes")

            while True:
                client_socket, client_address = self.server_socket.accept()
                print(f"Connection from {client_address}")

                client_thread = threading.Thread( #thread created for each client
                    target = self.handle_client,
                    args = (client_socket, client_address)
                )
                client_thread.daemon = True
                client_thread.start()
                print(f"Thread started for {client_address} (Active threads: {threading.active_count()})")

                self.handle_client(client_socket, client_address)

        except KeyboardInterrupt:
            print("\nShutting down server...")
        except Exception as e: 
            print(f"Server error: {e}")
        finally:
            if self.server_socket:
                self.server_socket.close()

    def ensure_www_directory(self):
        if not os.path.exists(self.www_dir):
            os.makedirs(self.www_dir)
            print(f"Created directory: {self.www_dir}")

            # index_path = os.path.join(self.www_dir, 'index.html')
        
            
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
            try:
                client_socket.close()
            except:
                pass
            
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

        # 404 error
        if not os.path.exists(file_path):
            self.handle_404(client_socket)
            return
        
        # 403 error
        if not os.access(file_path, os.R_OK):
            self.handle_403(client_socket)
            return
        
        # handle here for 304 request
        if 'If-Modified-Since' in headers:
            modified_time = os.path.getmtime(file_path)
            client_time = parsedate_to_datetime(headers['If-Modified-Since']).timestamp()

            if modified_time <= client_time:
                self.handle_304(client_socket)
                return
            
        # all good - 200 OK
        self.send_file_response(client_socket, file_path)
        
    def handle_404(self, client_socket):
        status_code = 404 
        status_message = "Not Found"
        error_file = os.path.join(self.www_dir, 'error_pages', '404.html')
        if os.path.exists(error_file):
            with open(error_file, 'rb') as f: 
                body = f.read().decode('utf-8')
        # else:
            
        headers = {
            'Content-Type': 'text/html',
            'Content-Length': str(len(body)),
            'Connection': 'close'
        }
        self.build_http_response(client_socket, status_code, status_message, headers, body)

    def handle_505(self, client_socket):
        status_code = 505
        status_message = "HTTP Version Not Supported"
        body = self.read_error_page(505)

        error_file = os.path.join(self.www_dir, 'error_pages', '505.html')

        if os.path.exists(error_file):
            with open(error_file, 'rb') as f: 
                body = f.read().decode('utf-8')
        # else:

        headers = {
            'Content-Type': 'text/html',
            'Content-Length': str(len(body)),
            'Connection': 'close'
        }

        self.build_http_response(client_socket, status_code, status_message, headers, body)

    def handle_403(self, client_socket):
        status_code = 403
        status_message = "Forbidden"

        error_file = os.path.join(self.www_dir, 'error_pages', '403.html')

        if os.path.exists(error_file):
            with open(error_file, 'rb') as f: 
                body = f.read().decode('utf-8')
        else:
            body = "<h1>403 Forbidden</h1>"

        headers = {
            'Content-Type': 'text/html',
            'Content-Length': str(len(body)),
            'Connection': 'close'
        }

        self.build_http_response(client_socket, status_code, status_message, headers, body)

    def handle_304(self, client_socket):

        status_code = 304
        status_message = "Not Modified"

        headers = {
            'Connection': 'close'
        }

        self.build_http_response(client_socket, status_code, status_message, headers, "")
    
    def send_file_response(self, client_socket, file_path):
        status_code = 200
        status_message = "OK"

        with open(file_path, 'rb') as f:
            body = f.read()

        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.html' or ext == '.htm':
            content_type = 'text/html'
        elif ext == '.css':
            content_type = 'text/css'
        elif ext == '.js':
            content_type = 'application/javascript'
        elif ext == '.png':
            content_type = 'image/png'
        elif ext == '.jpg' or ext == '.jpeg':
            content_type = 'image/jpeg'
        elif ext == '.gif':
            content_type = 'image/gif'
        elif ext == '.txt':
            content_type = 'text/plain'
        else:
            content_type = 'application/octet-stream'        


        headers = {
            'Content-Type': content_type,
            'Content-Length': str(len(body)),
            'Connection': 'close'
        }

        self.build_http_response(client_socket, status_code, status_message, headers, body)

    def send_file_chunked(self, client_socket, file_path, file_handle):
        chunk_size = 8192 #8KB chunks
        try:
            while True:
                chunk = file_handle.read(chunk_size)
                if not chunk:
                    break
                client_socket.send(f"{len(chunk):X}\r\n".encode())
                client_socket.send(chunk)
                client_socket.send(b'\r\n')
            client_socket.send(b'0\r\n\r\n')
            return True
        except Exception as e:
            print(f"Error sending chunked file {file_path}: {e}")
            return False


    def build_http_response(self, client_socket, status_code, status_message, headers, body=""):
        response = f"HTTP/1.1 {status_code} {status_message}\r\n"
        if 'Server' not in headers:
            headers['Server'] = 'WebServer/1.0'
        if 'Date' not in headers:
            headers['Date'] = datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT')
        for key, value in headers.items():
            response += f"{key}: {value}\r\n"
        response += "\r\n"
        try:
            client_socket.send(response.encode('utf-8'))

            if body: 
                if isinstance(body,bytes):
                    client_socket.send(body)
                else:
                    client_socket.send(body.encode('utf-8'))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"Error sending repsonse:{e}")

    
if __name__ == "__main__":
    server = WebServer(www_dir='./public')
    server.start_server()