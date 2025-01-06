import socket
import struct
import json
import subprocess
import threading
import time
from prettytable import PrettyTable
import metrics
from rich.console import Console

UDP_IP = "10.0.4.10"
UDP_PORT = 5005
TCP_PORT = 5006
BUFFER_SIZE = 1024
pending_tasks = {}
task_queues = {}
agents = {}
sequence_number = 0
sequence_counters = {}
console = Console()


def start_iperf_server():
    try:
        process = subprocess.Popen(
            ["iperf", "-s", "-u"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        print("[IPERF] Servidor iperf iniciado no modo UDP.")
        return process
    except FileNotFoundError:
        print("[ERRO] O iperf não está instalado ou não foi encontrado.")
        return None
    except Exception as e:
        print(f"[ERRO] Falha ao iniciar o servidor iperf: {e}")
        return None

def stop_iperf_server(process):
    if process:
        process.terminate()
        print("[IPERF] Servidor iperf encerrado.")



def encode_task(task):
    global sequence_counters
    agent_id = task["Agent_ID"]
    task_id = task["Task_ID"]
    task_type = task["Task_Type"]
    interface_check = task.get("Interface_Check", "").encode()
    interface_check_length = len(interface_check)
    frequency = task.get("Frequency", 5)
    duration = task.get("Duration", 30)

    if agent_id not in sequence_counters:
        sequence_counters[agent_id] = 1

    sequence_number = sequence_counters[agent_id]
    sequence_counters[agent_id] += 1

    if task_type in [3, 4, 5]:
        destination = socket.inet_aton(task["Data"])
        task_data = struct.pack("!4s", destination)
    else:
        task_data = task["Data"].encode()

    pdu = struct.pack(f"!B H H B B {interface_check_length}s {len(task_data)}s B B",
        3,
        sequence_number,
        task_id,
        task_type,
        interface_check_length,
        interface_check,
        task_data,
        frequency,
        duration
    )

    return sequence_number, pdu

def handle_registration(data, addr, server_sock):
    if len(data) < 6:
        console.print(f"[bold red]Erro: Mensagem de registro incompleta de {addr}[/bold red]")
        return

    try:
        message_type, sequence_number, agent_id = struct.unpack("!B H 3s", data)
        agent_id = agent_id.decode().strip()

        if message_type == 1:
            console.print(f"[bold green]O agente {agent_id} está tentando se registar em {addr}[/bold green]")

            ack_message = struct.pack("!B H 3s", 2, sequence_number, agent_id.encode())
            server_sock.sendto(ack_message, addr)
            console.print(f"[bold yellow]ACK enviado para o agente {agent_id}[/bold yellow]")

            confirmation_data, _ = server_sock.recvfrom(BUFFER_SIZE)
            if len(confirmation_data) >= 6:
                try:
                    ack_type, sequence_number, ack_agent_id = struct.unpack("!B H 3s", confirmation_data)
                    if ack_type == 2 and ack_agent_id.decode().strip() == agent_id:
                        agents[agent_id] = addr
                        final_ack = struct.pack("!B H 3s", 2, sequence_number, agent_id.encode())
                        server_sock.sendto(final_ack, addr)
                    else:
                        console.print(f"[bold red]Erro: Mensagem de confirmação inválida de {addr}[/bold red]")
                except struct.error as e:
                    console.print(f"[bold red]Erro ao desempacotar a mensagem de confirmação: {e}[/bold red]")
            else:
                console.print(f"[bold red]Erro: Mensagem de confirmação incompleta de {addr}[/bold red]")
        elif message_type == 2:
            console.print(f"[bold yellow]Confirmação recebida do Agente {agent_id}[/bold yellow]")
            agents[agent_id] = addr
        else:
            console.print(f"[bold red]Erro: Mensagem de registro inválida de {addr}[/bold red]")
    except struct.error as e:
        console.print(f"[bold red]Erro ao desempacotar a mensagem de registro: {e}[/bold red]")

def send_tasks(sock):
    if not agents:
        console.print("[bold red]Nenhum agente registrado para enviar tarefas.[/bold red]")
        return

    for agent_id, addr in agents.items():
        if agent_id in pending_tasks and pending_tasks[agent_id]:
            sequence_number, task_pdu = pending_tasks[agent_id].pop(0)

            for attempt in range(3):
                try:
                    sock.sendto(task_pdu, addr)
                    sock.settimeout(2)
                    ack, ack_addr = sock.recvfrom(BUFFER_SIZE)
                    if ack_addr == addr:
                        handle_acknowledgment(ack, addr)
                        ack_type, ack_sequence_number = struct.unpack("!B H", ack[:3])
                        if ack_type == 2 and ack_sequence_number == sequence_number:
                            final_ack = struct.pack("!B H", 2, sequence_number)
                            sock.sendto(final_ack, addr)
                            break
                        else:
                            console.print(f"[bold red]Erro: ACK inválido recebido de {addr}[/bold red]")
                    else:
                        console.print(f"[bold red]Erro: ACK de {ack_addr} recebido de {addr}[/bold red]")
                except socket.timeout:
                    console.print(f"[bold yellow]Timeout ao enviar tarefa {sequence_number} para o Agente {agent_id}. Tentando novamente...[/bold yellow]")
                except Exception as e:
                    console.print(f"[bold red]Nenhuma tarefa pendente para o Agente {agent_id}: {e}[/bold red]")
                    resend_pending_tasks(agent_id, sock, addr)
                attempt += 1
        else:
            console.print(f"[bold red]Nenhuma tarefa pendente para o Agente {agent_id}.[/bold red]")                
def handle_acknowledgment(data, addr):
    try:
        if len(data) < 3:
            console.print(f"[bold red]Erro: Mensagem de ACK incompleta de {addr}[/bold red]")
            return

        ack_type, ack_sequence_number = struct.unpack("!B H", data[:3])

        if ack_type != 2:
            console.print(f"[bold red]Erro: ACK inválido recebido de {addr}[/bold red]")
            return
        
        for agent_id, task_queue in task_queues.items():
            for seq, task_pdu, _ in task_queue:
                if seq == ack_sequence_number:
                    task_queues[agent_id] = [t for t in task_queue if t[0] != seq]
                    return

    except struct.error as e:
        console.print(f"[bold red]Erro ao desempacotar a mensagem de ACK: {e}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]Erro ao processar a mensagem de ACK: {e}[/bold red]")

def handle_metric_data(agent_id, task_id, metric):
    try:
        if agent_id in agents:
            metrics.store_metric(agent_id, task_id, metric)
            console.print(f"[bold yellow]Metrica recebida para a Tarefa {task_id} do Agente {agent_id}: {metric}[/bold yellow]")
            ack_message = struct.pack("!B H", 2, task_id)
            addr = agents[agent_id]
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.sendto(ack_message, addr)
    except Exception as e:
        console.print(f"[bold red]Erro ao processar a métrica: {e}[/bold red]")

def handle_udp_server():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((UDP_IP, UDP_PORT))
        while True:
            data, addr = sock.recvfrom(BUFFER_SIZE)
            if not data:
                continue

            message_type = data[0]
            
            if message_type == 1:
                handle_registration(data, addr, sock)
            elif message_type == 2:
                handle_acknowledgment(data, addr)
            elif message_type == 3:
                try:
                    if len(data) < 6:
                        console.print(f"[bold red]Erro: Mensagem de tarefa incompleta de {addr}[/bold red]")
                        continue
                    task_id, agent_id = struct.unpack("!H 3s", data[1:6])
                    result = data[6:].decode("utf-8", errors="ignore")
                    agent_id = agent_id.decode().strip()
                    ack = struct.pack("!B H", 2, task_id)
                    sock.sendto(ack, addr)
                    handle_metric_data(agent_id, task_id, result)
                except struct.error as e:
                    console.print(f"[bold red]Erro ao desempacotar a mensagem de tarefa: {e}[/bold red]")
                except Exception as e:
                    console.print(f"[bold red]Erro ao processar a tarefa: {e}[/bold red]")
            elif message_type == 4:
                handle_exit_signal(data, addr)

def handle_tcp_alert_server():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
        tcp_sock.bind(("0.0.0.0", TCP_PORT))
        tcp_sock.listen(5)

        while True:
            conn, addr = tcp_sock.accept()
            try:
                data = conn.recv(BUFFER_SIZE)
                if data:
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    console.print(f"[bold yellow][{timestamp} ALERTA][/bold yellow] {data.decode()}")
            except Exception as e:
                console.print(f"[bold red]Erro ao processar alerta de {addr}: {e}[/bold red]")
            finally:
                conn.close()

def resend_pending_tasks(agent_id, sock, addr):
    now = time.time()
    if agent_id in pending_tasks:
        for sequence_number, (task_pdu, timestamp) in list(pending_tasks[agent_id].items()):
            if now - timestamp > 2:
                console.print(f"[bold yellow]Reenviando tarefa {sequence_number} para o Agente {agent_id}.[/bold yellow]")
                try:
                    sock.sendto(task_pdu, addr)
                    pending_tasks[agent_id][sequence_number] = (task_pdu, now)
                except Exception as e:
                    console.print(f"[bold red]Erro ao reenviar tarefa {sequence_number} para o Agente {agent_id}: {e}[/bold red]")

def load_tasks_from_file(file_path):
    try:
        with open(file_path, "r") as file:
            tasks = json.load(file)
            for task in tasks:
                agent_id = task["Agent_ID"]
                task_pdu = encode_task(task)
                pending_tasks.setdefault(agent_id, []).append(task_pdu)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        console.print(f"[bold red]Erro ao carregar tarefas do arquivo: {e}[/bold red]")

def display_agents():
    if not agents:
        console.print("[bold red]Nenhum agente registado.[/bold red]")
        return

    table = PrettyTable()
    table.field_names = ["Agent ID", "Address", "Last Seen"]
    for agent_id, (addr, timestamp) in agents.items():
        last_seen = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
        table.add_row([agent_id, addr, last_seen])
    print("\n=== Registered Agents ===")
    print(table)

def handle_exit_signal(data, addr):
    message_type, _ = struct.unpack("!B H", data[:3])
    
    if message_type == 4:
        console.print(f"[bold green]Sinal de saida recebido de {addr}.[/bold green]")
        for agent_id, agent_addr in agents.items():
            if agent_addr == addr:
                del agents[agent_id]
                console.print(f"[bold green]Agente {agent_id} removido.[/bold green]")
                break
    else:
        console.print(f"[bold red]Erro: Mensagem de saída inválida de {addr}[/bold red]")

def main():

    iperf_process = start_iperf_server()
    
    if iperf_process is None:
        print("[ERRO] Não foi possível iniciar o servidor iperf. Verifique a instalação.")
        exit(1)  # Sai do programa se o iperf não foi iniciado

    try:
        udp_server_thread = threading.Thread(target=handle_udp_server)
        udp_server_thread.daemon = True
        udp_server_thread.start()
        
        tcp_thread = threading.Thread(target=handle_tcp_alert_server)
        tcp_thread.daemon = True
        tcp_thread.start()
        
        while True:
            console.print("\n=== NMS Server ===")
            console.print("1. Carregar tarefas do arquivo")
            console.print("2. Enviar tarefas")
            console.print("3. Exibir agentes registrados")
            console.print("4. Exibir métricas")
            console.print("5. Sair")
            choice = input("Escolha uma opção: ")

            if choice == "1":
                file_path = input("Digite o caminho do arquivo: ")
                load_tasks_from_file(file_path)
            elif choice == "2":
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    send_tasks(sock)
            elif choice == "3":
                display_agents()
            elif choice == "4":
                metrics.display_metrics()
            elif choice == "5":
                console.print("[bold red]Encerrando o NMS Server...[/bold red]")
                break
            else:
                console.print("[bold red]Opção inválida. Tente novamente.[/bold red]")
    finally:
        stop_iperf_server(iperf_process)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Encerrando o servidor...")