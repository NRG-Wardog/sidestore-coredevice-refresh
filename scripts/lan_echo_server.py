import socket
import sys

def run_server(port=8888):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', port))
    server.listen(5)
    print(f"LAN TCP Server listening on 0.0.0.0:{port}...")
    sys.stdout.flush()

    while True:
        try:
            client, addr = server.accept()
            print(f"Accepted connection from {addr}")
            sys.stdout.flush()
            client.sendall(b"OK_LAN_TCP\n")
            client.close()
        except Exception as e:
            print(f"Error: {e}")
            sys.stdout.flush()

if __name__ == '__main__':
    run_server()
