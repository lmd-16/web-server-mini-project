import socket
import os
import re
import threading
from datetime import datetime, timezone
from email.utils import format_datetime


class ProxyServer:

    STATUS_MESSAGES = {
        200: "OK",
        304: "Not Modified",
        403: "Forbidden",
        404: "Not Found",
        505: "HTTP Version Not Supported",
    }

    def __init__(self, host='localhost', port=8888,
                 origin_host='localhost', origin_port=8080,
                 cache_dir='./cache'):
        self.host = host
        self.port = port
        self.origin_host = origin_host
        self.origin_port = origin_port
        self.cache_dir = cache_dir
        self.server_socket = None

        self.cache_meta = {}
        self.cache_lock = threading.Lock()

        self.ensure_cache_dir()

    def ensure_cache_dir(self):
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)
            print(f"Created cache directory: {self.cache_dir}")

    def start_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)

            print(f"Proxy listening on {self.host}:{self.port}")
            print(f"Forwarding to origin at {self.origin_host}:{self.origin_port}")
            print(f"Cache directory: {self.cache_dir}")

            while True:
                client_socket, client_address = self.server_socket.accept()
                thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, client_address)
                )
                thread.daemon = True
                thread.start()

        except KeyboardInterrupt:
            print("\nShutting down proxy...")
        except Exception as e:
            print(f"Proxy error: {e}")
        finally:
            if self.server_socket:
                self.server_socket.close()

    def handle_client(self, client_socket, client_address):
        try:
            raw_data = self.recv_request(client_socket)
            if not raw_data:
                return

            method, path, version, headers, body = self.parse_http_request(raw_data)
            print(f"[Proxy] {client_address} -> {method} {path} {version}")

            if version != 'HTTP/1.1':
                self.send_error(client_socket, 505, "Client sent an unsupported HTTP version")
                return

            self.handle_get(client_socket, path, headers)

        except Exception as e:
            print(f"[Proxy] Error handling client {client_address}: {e}")
        finally:
            try:
                client_socket.close()
            except Exception:
                pass

    def recv_request(self, client_socket):
        """Read until we have the full header block (\\r\\n\\r\\n)."""
        client_socket.settimeout(5)
        data = b''
        try:
            while b'\r\n\r\n' not in data:
                chunk = client_socket.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        return data.decode('utf-8', errors='ignore')

    def parse_http_request(self, raw_data):
        lines = raw_data.split('\r\n')
        if len(lines) < 1:
            raise ValueError("Empty request")

        request_line = lines[0].split()
        if len(request_line) != 3:
            raise ValueError("Invalid request line")

        method, path, version = request_line

        headers = {}
        header_end = 1
        for i in range(1, len(lines)):
            if lines[i] == '':
                header_end = i
                break
            if ': ' in lines[i]:
                key, value = lines[i].split(': ', 1)
                headers[key] = value

        body = ''
        if header_end + 1 < len(lines):
            body = '\r\n'.join(lines[header_end + 1:])

        return method, path, version, headers, body

    # Core proxy / caching logic
    def cache_file_path(self, path):
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', path.strip('/') or 'index')
        return os.path.join(self.cache_dir, safe_name)

    def handle_get(self, client_socket, path, client_headers=None):
        client_ims = None
        if client_headers:
            for key, value in client_headers.items():
                if key.lower() == 'if-modified-since':
                    client_ims = value
                    break

        with self.cache_lock:
            meta = self.cache_meta.get(path)

        if client_ims:
            upstream_ims = client_ims
            passthrough = True
        else:
            upstream_ims = meta['fetched_time_str'] if meta else None
            passthrough = False

        status_code, resp_headers, resp_body = self.forward_to_origin(path, upstream_ims)

        if status_code is None:
            print(f"[Proxy] Origin unreachable for {path}; closing client connection")
            return

        if status_code == 304:
            if passthrough:
                print(f"[Proxy] Client-conditional GET -> 304 for {path}")
                headers = {'X-Cache': 'HIT'}
                self.send_response(client_socket, 304, "Not Modified", headers, "")
                return
            elif meta:
                print(f"[Proxy] Cache HIT (revalidated) for {path}")
                try:
                    with open(meta['file'], 'rb') as f:
                        body = f.read()
                except FileNotFoundError:
                    # cache file vanished somehow; fall back to a fresh fetch
                    self.handle_cache_miss(client_socket, path)
                    return

                headers = dict(meta['headers'])
                headers['Content-Length'] = str(len(body))
                headers['X-Cache'] = 'HIT'
                self.send_response(client_socket, 200, "OK", headers, body)
                return

        if status_code == 200:
            print(f"[Proxy] Cache MISS/UPDATED for {path}")
            cache_file = self.cache_file_path(path)
            body_bytes = resp_body if isinstance(resp_body, bytes) else resp_body.encode('utf-8')

            with open(cache_file, 'wb') as f:
                f.write(body_bytes)

            fetched_time_str = format_datetime(datetime.now(timezone.utc), usegmt=True)
            stored_headers = {k: v for k, v in resp_headers.items()
                               if k.lower() not in ('content-length', 'connection', 'date')}

            with self.cache_lock:
                self.cache_meta[path] = {
                    'file': cache_file,
                    'fetched_time_str': fetched_time_str,
                    'headers': stored_headers,
                }

            headers = dict(stored_headers)
            headers['Content-Length'] = str(len(body_bytes))
            headers['X-Cache'] = 'MISS'
            self.send_response(client_socket, 200, "OK", headers, body_bytes)
            return

        message = self.STATUS_MESSAGES.get(status_code, "Error")
        self.send_response(client_socket, status_code, message, resp_headers, resp_body)


    def handle_cache_miss(self, client_socket, path):
        status_code, resp_headers, resp_body = self.forward_to_origin(path, None)
        if status_code is None:
            print(f"[Proxy] Origin unreachable for {path}; closing client connection")
            return
        message = self.STATUS_MESSAGES.get(status_code, "Error")
        self.send_response(client_socket, status_code, message, resp_headers, resp_body)

    def forward_to_origin(self, path, if_modified_since_str):
        """Sends GET <path> to the origin server. Returns (status, headers, body)."""
        try:
            origin_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            origin_socket.settimeout(5)
            origin_socket.connect((self.origin_host, self.origin_port))

            request_lines = [
                f"GET {path} HTTP/1.1",
                f"Host: {self.origin_host}:{self.origin_port}",
            ]
            if if_modified_since_str:
                request_lines.append(f"If-Modified-Since: {if_modified_since_str}")
            request_lines.append("Connection: close")
            request = "\r\n".join(request_lines) + "\r\n\r\n"

            origin_socket.sendall(request.encode('utf-8'))

            response_data = b''
            while True:
                chunk = origin_socket.recv(8192)
                if not chunk:
                    break
                response_data += chunk
            origin_socket.close()

            return self.parse_http_response(response_data)

        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            print(f"[Proxy] Error contacting origin ({self.origin_host}:{self.origin_port}): {e}")
            return None, None, None

    def parse_http_response(self, data):
        header_end = data.find(b'\r\n\r\n')
        if header_end == -1:
            return None, None, None

        header_bytes = data[:header_end]
        body = data[header_end + 4:]

        header_lines = header_bytes.decode('utf-8', errors='ignore').split('\r\n')
        status_line = header_lines[0].split(' ', 2)
        if len(status_line) < 2:
            return None, None, None
        status_code = int(status_line[1])

        headers = {}
        for line in header_lines[1:]:
            if ': ' in line:
                key, value = line.split(': ', 1)
                headers[key] = value

        if 'Content-Length' in headers:
            content_length = int(headers['Content-Length'])
            body = body[:content_length]

        return status_code, headers, body


    def send_response(self, client_socket, status_code, status_message, headers, body):
        response = f"HTTP/1.1 {status_code} {status_message}\r\n"

        headers = dict(headers) if headers else {}
        if 'Server' not in headers:
            headers['Server'] = 'ProxyServer/1.0'
        if 'Date' not in headers:
            headers['Date'] = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        if 'Connection' not in headers:
            headers['Connection'] = 'close'

        for key, value in headers.items():
            response += f"{key}: {value}\r\n"
        response += "\r\n"

        try:
            client_socket.sendall(response.encode('utf-8'))
            if body:
                if isinstance(body, bytes):
                    client_socket.sendall(body)
                else:
                    client_socket.sendall(body.encode('utf-8'))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print(f"[Proxy] Error sending response: {e}")

    def send_error(self, client_socket, status_code, detail=""):
        message = self.STATUS_MESSAGES.get(status_code, "Error")
        body = f"<h1>{status_code} {message}</h1><p>{detail}</p>"
        headers = {
            'Content-Type': 'text/html',
            'Content-Length': str(len(body)),
            'Connection': 'close',
        }
        self.send_response(client_socket, status_code, message, headers, body)


if __name__ == "__main__":
    proxy = ProxyServer(
        host='localhost',
        port=8888,
        origin_host='localhost',
        origin_port=8080,
        cache_dir='./cache'
    )
    proxy.start_server()