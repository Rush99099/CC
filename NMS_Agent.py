from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import socket
import struct
import psutil
import time
import json
import subprocess
import threading
import re

UDP_PORT = 5005
BUFFER_SIZE = 1024
running = True
task_completed = False
task_completed_event = threading.Event()
alert_conditions = {} 
task_logs = []
alerts = []
alert_timestamps = {}
console = Console()
SERVER_IP = "10.0.4.10"

def get_agent_id():
    try:
        hostname = socket.gethostname()[:3]
        return hostname
    except Exception as e:
        console.print(f"[bold yellow]Não foi possível detectar o nome do agente: {e}[/bold yellow]")
        return input("Insira o ID do Agente manualmente: ")

def register_with_server(sock, server_ip, agent_id):
    sequence_number = 1
    register_message = struct.pack("!B H 3s", 1, sequence_number, agent_id.encode())
    sock.sendto(register_message, (server_ip, UDP_PORT))

    data, addr = sock.recvfrom(BUFFER_SIZE)
    message_type, sequence_number, ack_agent_id = struct.unpack("!B H 3s", data)

    if message_type == 2:
        confirmation_ack = struct.pack("!B H 3s", 2, sequence_number, agent_id.encode())
        sock.sendto(confirmation_ack, addr)

        data, addr = sock.recvfrom(BUFFER_SIZE)
        if len(data) >= 6:
            ack_type, _, ack_agent_id = struct.unpack("!B H 3s", data)
            if ack_type == 2 and ack_agent_id.decode().strip() == agent_id:
                console.print(f"[bold green]Agente {agent_id} registado com sucesso![/bold green]")
            else:
                console.print(f"[bold red]Erro: Mensagem de confirmação inválida do servidor.[/bold red]")
        else:
            console.print(f"[bold red]Erro: Mensagem de confirmação inválida do servidor.[/bold red]")
    else:
        console.print(f"[bold red]Erro: Mensagem de confirmação inválida do servidor.[/bold red]")

def encode_task_result(task_id, result):
    result_data = result.encode()
    return struct.pack(f"!B H {len(result_data)}s", 3, task_id, result_data)

def decode_task(data):
    sequence_number, task_id, task_type = struct.unpack("!H H B", data[1:6])
    interface_length = data[6]
    interface_check = data[7:7 + interface_length].decode()
    task_data = data[7 + interface_length:-2]
    frequency, duration = struct.unpack("!B B", data[-2:])
    task = {
        "Sequence_Number": sequence_number,
        "Task_ID": task_id,
        "Task_Type": task_type,
        "Interface_Check": interface_check,
        "Data": task_data.decode() if task_type not in [3, 4, 5] else socket.inet_ntoa(task_data),
        "Frequency": frequency,
        "Duration": duration
    }
    return task

def measure_cpu_periodic(frequency, duration, task_id, sock, server_ip, agent_id):
    start_time = time.time()
    readings = []
    
    while time.time() - start_time <= duration:
        cpu_usage = psutil.cpu_percent(interval=0)
        readings.append(cpu_usage)

        if len(readings) == frequency:
            avg_cpu = sum(readings) / len(readings)
            task_logs.append((task_id, f"Percentagem de uso da CPU: {avg_cpu:.2f}%"))
            readings.clear()
            result = f"Percentagem de uso da CPU: {avg_cpu:.2f}%"
            send_task_result(task_id, result, sock, server_ip, agent_id)

            if avg_cpu > alert_conditions["cpu_usage"]:
                send_alert(f"Alerta de uso elevado do CPU: {result}", server_ip)
                
        time.sleep(1)

def measure_ram_periodic(frequency, duration, task_id, sock, server_ip, agent_id):
    start_time = time.time()
    readings = []
    
    while time.time() - start_time <= duration:
        ram_usage = psutil.virtual_memory().percent
        readings.append(ram_usage)
        time.sleep(1)

        if len(readings) == frequency:
            avg_ram = sum(readings) / len(readings)
            task_logs.append((task_id, f"Percentagem de uso da RAM: {avg_ram:.2f}%"))
            readings.clear()
            
            result = f"Percentagem de uso da RAM: {avg_ram:.2f}%"
            send_task_result(task_id, result, sock, server_ip, agent_id)

            if avg_ram > alert_conditions["ram_usage"]:
                send_alert(f"Alerta de uso elevado de RAM: {result}", server_ip)

def measure_latency(destination, packet_count):
    try:
        result = subprocess.run(
            ["ping", "-c", str(packet_count), destination],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # Extract the average latency (RTT) from the output
            for line in result.stdout.splitlines():
                if "rtt min/avg/max/mdev" in line:
                    avg_latency = line.split("/")[4]  # Extract the average RTT
                    return avg_latency + " ms"
        else:
            return f"Ping failed: {result.stderr}"
    except Exception as e:
        return f"Error measuring latency: {e}"

def measure_jitter(destination, packet_count=10):
    try:
        # Run the ping command
        result = subprocess.run(
            ["ping", "-c", str(packet_count), destination],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # Parse RTT times from ping output
            rtts = []
            for line in result.stdout.splitlines():
                if "time=" in line:
                    rtt_match = re.search(r"time=(\d+\.\d+)", line)
                    if rtt_match:
                        rtts.append(float(rtt_match.group(1)))

            # Calculate jitter (variation in RTTs)
            if len(rtts) > 1:
                jitter = max(rtts[i+1] - rtts[i] for i in range(len(rtts) - 1))
                return f"{jitter:.2f} ms"
            else:
                return "Not enough RTT data to calculate jitter."
        else:
            return f"Ping failed: {result.stderr}"
    except Exception as e:
        return f"Error measuring jitter: {e}"

def measure_packet_loss(destination, packet_count=10):
    try:
        result = subprocess.run(
            ["ping", "-c", str(packet_count), destination],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # Adapt to CORE's ping output format
            match = re.search(r"(\d+)% packet loss", result.stdout)
            if match:
                return f"{match.group(1)}% packet loss"
            else:
                return "Packet loss information not found in output."
        else:
            return f"Ping failed: {result.stderr}"
    except Exception as e:
        return f"Error measuring packet loss: {e}"

def measure_bandwidth():
    try:
        # Run iperf client and capture output
        result = subprocess.run(
            ["iperf", "-c", "10.0.4.10", "-u", "-b", "100M", "-t", "10"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            # Find the bandwidth in the output
            for line in result.stdout.splitlines():
                if "Mbits/sec" in line:
                    return line.split()[-2] + " Mbits/sec"
        else:
            return f"iperf failed: {result.stderr}"
    except Exception as e:
        return f"Error measuring bandwidth with iperf: {e}"

def send_alert(alert_message, server_ip):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_sock:
            tcp_sock.connect((server_ip, 5006))
            tcp_sock.sendall(alert_message.encode())
            alerts.append(alert_message)
            console.print(f"[bold yellow][ALERTFLOW][/bold yellow] Alerta enviado: {alert_message}")
    except Exception as e:
        console.print(f"[bold red][ALERTFLOW][/bold red] Falha ao enviar alerta: {e}")

def check_and_send_alert(metric_name, value, threshold, alert_message, server_ip):
    try:
        if value > threshold:
            now = time.time()
            if alert_timestamps.get(metric_name, 0) + 5 <= now:
                alert_timestamps[metric_name] = now
                send_alert(alert_message, server_ip)
                console.print(f"[bold yellow][ALERTFLOW][/bold yellow] {alert_message}")
    except Exception as e:
        console.print(f"[bold red]Erro no processamento do alerta: {e}[/bold red]")
        
def process_task(task, sock, server_ip, agent_id):
    global task_completed_event
    global waiting_for_task
    task_id = task["Task_ID"]
    task_type = task["Task_Type"]
    frequency = task.get("Frequency", 1)
    duration = task.get("Duration", 10)

    console.print(Panel(f"[bold cyan]Tarefa {task_id} recebida: Tipo {task_type}, Frequência {frequency}s, Duração {duration}s[/bold cyan]"))
    
    try:
        if task_type == 1:
            result = measure_cpu_periodic(frequency, duration, task_id, sock, server_ip, agent_id)
        elif task_type == 2:
            result = measure_ram_periodic(frequency, duration, task_id, sock, server_ip, agent_id)
        elif task_type == 3:
            result = measure_latency(task["Data"], frequency)
        elif task_type == 4:
            result = measure_jitter(task["Data"], frequency)
        elif task_type == 5:
            result = measure_packet_loss(task["Data"])
        elif task_type == 6:
            result = measure_bandwidth()
        else:
            result = "Tipo de tarefa não suportada"
            return None
        if result is not None:
            send_task_result(task_id, result, sock, server_ip, agent_id)
            console.print(Panel(f"[bold cyan]Tarefa {task_id} concluída! Pronto para a próxima tarefa.[/bold cyan]"))
        else:
            console.print(Panel(f"[bold cyan]Tarefa {task_id} concluída! Pronto para a próxima tarefa.[/bold cyan]"))
    except Exception as e:
        console.print(f"[bold red]Erro no processamento da tarefa {task_id}: {e}[/bold red]")
    finally:
        waiting_for_task = True
        task_completed_event.set()

def send_task_result(task_id, result, sock, server_ip, agent_id):
    try:
        result_data = result.encode()
        task_result_pdu = struct.pack(f"!B H 3s {len(result_data)}s", 3, task_id, agent_id.encode(), result_data)

        for attempt in range(3):
            sock.sendto(task_result_pdu, (server_ip, UDP_PORT))

            try:
                sock.settimeout(1)
                ack, _ = sock.recvfrom(BUFFER_SIZE)
                ack_type, ack_task_id = struct.unpack("!B H", ack[:3])
                if ack_type == 2 and ack_task_id == task_id:
                    return
                else:
                    console.print(f"[bold red]Invalid ACK received: {ack_type}, {ack_task_id} (expected {task_id}).[/bold red]")
            except socket.timeout:
                console.print(f"[bold yellow]Timeout: No ACK for Task {task_id} result (attempt {attempt + 1}).[/bold yellow]")
            attempt += 1
            print(f"Retrying... Attempt {attempt}")
        console.print(f"[bold red]Failed to receive ACK for Task {task_id} result after 3 attempts.[/bold red]")

    except Exception as e:
        console.print(f"[bold red]Error sending task result for Task {task_id}: {e}[/bold red]")

def listen_for_tasks(sock, server_ip, agent_id):
    global expected_sequence_number
    global waiting_for_task 
    waiting_for_task = True
    while running:
        try:
            data, addr = sock.recvfrom(BUFFER_SIZE)
            task = decode_task(data)
            sequence_number = task["Sequence_Number"]
            task_id = task["Task_ID"]

            message_type, sequence_number, task_id, task_type = struct.unpack("!B H H B", data[:6])

            if message_type == 3 and sequence_number == expected_sequence_number:
                ack_pdu = struct.pack("!B H", 2, sequence_number)
                sock.sendto(ack_pdu, addr)
                task = decode_task(data)

                if task:
                    process_task(task, sock, server_ip, agent_id)
                    expected_sequence_number += 1
            else:
                console.print(f"[bold red]Tarefa inválida recebida: {message_type}, {sequence_number} (esperado {expected_sequence_number}).[/bold red]")
                
        except socket.timeout:
            if waiting_for_task:
                console.print("\n[bold yellow]Aguardando tarefas do servidor...[/bold yellow]")
                waiting_for_task = False
        except Exception as e:
            continue

def display_logs():
    console.print("\n[bold blue]=== Logs das Tarefas ===[/bold blue]")
    if not task_logs:
        console.print("[bold red]Nenhum log de tarefa disponível.[/bold red]")
        return

    table = Table(title="Logs das Tarefas")
    table.add_column("ID da Tarefa", justify="center", style="cyan")
    table.add_column("Resultado", justify="left", style="magenta")

    for task_id, result in task_logs:
        table.add_row(str(task_id), result)

    console.print(table)
    
def display_alerts():
    console.print("\n[bold yellow]=== Alertas ===[/bold yellow]")
    if not alerts:
        console.print("[bold red]Nenhum alerta enviado.[/bold red]")
        return

    table = Table(title="Alertas")
    table.add_column("Mensagem do Alerta", justify="left", style="green")

    for alert in alerts:
        table.add_row(alert)

    console.print(table)

def display_agent_menu(agent_id, server_ip, sock):
    while True:
        console.print(f"\n[bold blue]=== Menu do Agente {agent_id} ===[/bold blue]")
        console.print("1. Ver Logs das Tarefas")
        console.print("2. Ver Alertas")
        console.print("3. Sair")

        choice = input("Selecione uma opção: ")

        if choice == "1":
            display_logs()
        elif choice == "2":
            display_alerts()
        elif choice == "3":
            console.print(f"[bold red]Encerrando o agente {agent_id}...[/bold red]")
            send_exit_signal(sock, server_ip)
            break
        else:
            console.print("[bold red]Erro: Opção inválida. Por favor, tente novamente.[/bold red]")

        task_completed_event.wait()

def load_alert_conditions():
        with open("alertflow_conditions.json", "r") as file:
            alert_conditions = json.load(file)
            return alert_conditions

def send_exit_signal(sock, server_ip):
    try:
        exit_pdu = struct.pack("!B H", 4, 0)
        sock.sendto(exit_pdu, (server_ip, UDP_PORT))
        console.print("[bold red]Sinal de saída enviado para o servidor. Agente encerrando...[/bold red]")
    except Exception as e:
        console.print(f"[bold red]Erro ao enviar sinal de saída: {e}[/bold red]")

def main():
    global alert_conditions
    global expected_sequence_number
    alert_conditions = load_alert_conditions()
    expected_sequence_number = 1
    agent_id = get_agent_id()
    server_ip = SERVER_IP  # Use predefined server IP

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(1)
        register_with_server(sock, server_ip, agent_id)

        listener_thread = threading.Thread(target=listen_for_tasks, args=(sock, server_ip, agent_id))
        listener_thread.daemon = False
        listener_thread.start()

        try:
            display_agent_menu(agent_id, server_ip, sock)
        finally:
            global running
            running = False
            listener_thread.join()
            console.print("[bold red]Agente encerrado.[/bold red]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Encerrando o agent...")