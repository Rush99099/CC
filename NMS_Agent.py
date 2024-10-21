import socket
import json
import time

# Server addresses for UDP and TCP
udp_server_address = ("localhost", 5000)
tcp_server_address = ("localhost", 6000)

# Create UDP and TCP sockets
udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # UDP for NetTask
tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # TCP for AlertFlow
tcp_socket.connect(tcp_server_address)

# Simulate collecting metrics
def collect_metrics():
    return {
        "cpu_usage": 60,  # Example, replace with actual data collection logic
        "ram_usage": 40,
        "packet_loss": 0
    }

# Send alert via TCP (AlertFlow)
def send_alert(alert_type, description):
    alert = {
        "alert_id": "alert-001",
        "device_id": "r1",
        "alert_type": alert_type,
        "description": description,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
    }
    tcp_socket.sendall(json.dumps(alert).encode('utf-8'))

# UDP sequence number
sequence_number = 1

while True:
    # Collect metrics and send via UDP (NetTask)
    metrics = collect_metrics()
    message = {
        "task_id": "task-001",
        "sequence_number": sequence_number,
        "metric": metrics,
        "ack": True
    }
    udp_socket.sendto(json.dumps(message).encode('utf-8'), udp_server_address)

    # Check for threshold and send alert via TCP (AlertFlow)
    if metrics["cpu_usage"] > 80:
        send_alert("cpu_overload", f"CPU usage exceeded 80%: {metrics['cpu_usage']}%")
    
    # Increment sequence number for the next message
    sequence_number += 1

    # Wait before the next report
    time.sleep(3)
