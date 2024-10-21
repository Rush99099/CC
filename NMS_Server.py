import socket
import json
import threading

udp_server_address = ("localhost", 5000)
tcp_server_address = ("localhost", 6000)

# Socket UDP criada para receber mensagens de agentes
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_socket.bind(udp_server_address)
udp_socket.settimeout(10)

# Socket TCP criada para receber alertas
tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
tcp_socket.bind(tcp_server_address)
tcp_socket.listen(5)
tcp_socket.settimeout(10)

# Função para receber mensagens UDP
def udp_server():
    print("A captar mensagens UDP (NetTask)...")
    while True:
        data, agent_address = udp_socket.recvfrom(4096)
        message = json.loads(data.decode('utf-8'))
        print(f"Mensagem UDP recebida de {agent_address}: {message}")
        
        # Pega em todas as métricas e envia uma nova mensagem com a task
        task_response = {
            "task_id": "task-001",
            "sequence_number": message["sequence_number"] + 1,
            "task": "Collect CPU, RAM, and packet_loss"
        }
        udp_socket.sendto(json.dumps(task_response).encode('utf-8'), agent_address)

# Função para receber alertas TCP
def tcp_server():
    print("A captar alertas TCP (AlertFlow)...")
    while True:
        conn, addr = tcp_socket.accept()
        with conn:
            data = conn.recv(1024)
            if data:
                alert = json.loads(data.decode('utf-8'))
                print(f"Recebido alerta: {alert}")

# Threads para executar as funções
udp_thread = threading.Thread(target=udp_server)
tcp_thread = threading.Thread(target=tcp_server)

udp_thread.start()
tcp_thread.start()
