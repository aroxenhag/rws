#!/usr/bin/env python3
"""
Diagnostic tool to investigate onboarding hiccups in ROS system.

This script monitors:
1. ROS graph changes (nodes/topics/services)
2. Topic message rates (especially plug state topics)
3. System resources (CPU, memory, network)
4. DDS discovery events
5. Timing of operations

Usage:
    # Auto-discover all metering socket relay plugs
    ./diagnose_onboarding_hiccup.py --auto-discover --output /tmp/onboarding_diag.log

    # Or specify topics manually
    ./diagnose_onboarding_hiccup.py --topics /plug1/closed /plug2/closed --output /tmp/onboarding_diag.log

    # Then trigger onboarding in another terminal

    # Stop with Ctrl+C when done
"""

import argparse
import subprocess
import time
import threading
import json
import sys
import os
import re
from collections import defaultdict, deque
from datetime import datetime
from typing import Dict, List, Set

try:
    import psutil
except ImportError:
    print("⚠ Warning: psutil not installed. System resource monitoring disabled.")
    print("  Install with: pip3 install psutil")
    psutil = None


def discover_metering_socket_relay_topics() -> List[str]:
    """
    Auto-discover all metering_socket_relay 'closed' topics.

    Metering socket relay nodes are named like:
      /lab/elinstest/metering_socket_relay
      /hostname/TEST/metering_socket_relay

    They publish topics at their namespace level:
      /lab/elinstest/closed
      /lab/elinstest/connected
      /lab/elinstest/power
      etc.

    We find all nodes ending with 'metering_socket_relay' and monitor their 'closed' topics.
    """
    try:
        # Get all nodes
        result = subprocess.run(['ros2', 'node', 'list'],
                              capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            print("❌ Failed to list ROS nodes", file=sys.stderr)
            return []

        all_nodes = result.stdout.strip().split('\n')

        # Find nodes ending with 'metering_socket_relay'
        relay_nodes = [n for n in all_nodes if n.endswith('/metering_socket_relay')]

        if not relay_nodes:
            print("⚠️  No metering_socket_relay nodes found")
            print("    Available nodes:")
            for node in all_nodes[:10]:
                print(f"      {node}")
            if len(all_nodes) > 10:
                print(f"      ... and {len(all_nodes)-10} more")
            return []

        # Convert node names to topic paths
        # Node: /lab/elinstest/metering_socket_relay
        # Topic: /lab/elinstest/closed
        closed_topics = []
        for node in relay_nodes:
            # Remove '/metering_socket_relay' from end, add '/closed'
            namespace = node.rsplit('/metering_socket_relay', 1)[0]
            closed_topic = f"{namespace}/closed"
            closed_topics.append(closed_topic)

        return closed_topics

    except subprocess.TimeoutExpired:
        print("❌ Timeout while discovering topics", file=sys.stderr)
        return []
    except Exception as e:
        print(f"❌ Error discovering topics: {e}", file=sys.stderr)
        return []


class ROSGraphMonitor:
    """Monitor ROS2 graph for changes"""

    def __init__(self):
        self.nodes: Set[str] = set()
        self.topics: Set[str] = set()
        self.services: Set[str] = set()
        self.changes: List[dict] = []

    def update(self):
        """Check for changes in ROS graph"""
        timestamp = datetime.now().isoformat()

        # Get current nodes
        result = subprocess.run(['ros2', 'node', 'list'],
                              capture_output=True, text=True, timeout=5)
        current_nodes = set(result.stdout.strip().split('\n')) if result.returncode == 0 else set()

        # Get current topics
        result = subprocess.run(['ros2', 'topic', 'list'],
                              capture_output=True, text=True, timeout=5)
        current_topics = set(result.stdout.strip().split('\n')) if result.returncode == 0 else set()

        # Get current services
        result = subprocess.run(['ros2', 'service', 'list'],
                              capture_output=True, text=True, timeout=5)
        current_services = set(result.stdout.strip().split('\n')) if result.returncode == 0 else set()

        # Detect changes
        new_nodes = current_nodes - self.nodes
        removed_nodes = self.nodes - current_nodes
        new_topics = current_topics - self.topics
        removed_topics = self.topics - current_topics
        new_services = current_services - self.services
        removed_services = self.services - current_services

        if new_nodes or removed_nodes or new_topics or removed_topics or new_services or removed_services:
            change = {
                'timestamp': timestamp,
                'new_nodes': list(new_nodes),
                'removed_nodes': list(removed_nodes),
                'new_topics': list(new_topics),
                'removed_topics': list(removed_topics),
                'new_services': list(new_services),
                'removed_services': list(removed_services),
                'total_nodes': len(current_nodes),
                'total_topics': len(current_topics),
                'total_services': len(current_services)
            }
            self.changes.append(change)

            # Update state
            self.nodes = current_nodes
            self.topics = current_topics
            self.services = current_services

            return change

        return None


class TopicRateMonitor:
    """Monitor message rates on specific topics"""

    def __init__(self, topics: List[str], window_size: int = 50):
        self.topics = topics
        self.window_size = window_size
        self.message_times: Dict[str, deque] = {topic: deque(maxlen=window_size) for topic in topics}
        self.gaps_detected: List[dict] = []
        self.monitors: Dict[str, subprocess.Popen] = {}
        self.running = False

    def start(self):
        """Start monitoring topics"""
        self.running = True
        for topic in self.topics:
            thread = threading.Thread(target=self._monitor_topic, args=(topic,), daemon=True)
            thread.start()

    def _monitor_topic(self, topic: str):
        """Monitor a single topic for messages"""
        # Use ros2 topic echo with timestamps
        try:
            proc = subprocess.Popen(
                ['ros2', 'topic', 'echo', topic, '--no-arr'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            self.monitors[topic] = proc

            while self.running:
                line = proc.stdout.readline()
                if line:
                    # Record message received
                    now = time.time()
                    self.message_times[topic].append(now)

                    # Check for gaps (>2x expected rate)
                    if len(self.message_times[topic]) >= 2:
                        last_gap = now - self.message_times[topic][-2]
                        if last_gap > 2.0:  # Assuming ~1Hz, gap >2s is suspicious
                            gap_event = {
                                'timestamp': datetime.now().isoformat(),
                                'topic': topic,
                                'gap_seconds': round(last_gap, 3)
                            }
                            self.gaps_detected.append(gap_event)

        except Exception as e:
            print(f"Error monitoring topic {topic}: {e}", file=sys.stderr)

    def stop(self):
        """Stop monitoring"""
        self.running = False
        for proc in self.monitors.values():
            proc.terminate()

    def get_rates(self) -> Dict[str, float]:
        """Get current message rates (Hz)"""
        rates = {}
        now = time.time()
        for topic, times in self.message_times.items():
            if len(times) < 2:
                rates[topic] = 0.0
            else:
                # Calculate rate over the window
                time_span = now - times[0]
                if time_span > 0:
                    rates[topic] = len(times) / time_span
                else:
                    rates[topic] = 0.0
        return rates


class SystemResourceMonitor:
    """Monitor system resources"""

    def __init__(self):
        self.samples: List[dict] = []
        self.enabled = psutil is not None

    def sample(self):
        """Take a snapshot of system resources"""
        if not self.enabled:
            return None

        sample = {
            'timestamp': datetime.now().isoformat(),
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'memory_percent': psutil.virtual_memory().percent,
            'network_bytes_sent': psutil.net_io_counters().bytes_sent,
            'network_bytes_recv': psutil.net_io_counters().bytes_recv,
        }
        self.samples.append(sample)
        return sample


class OnboardingDiagnostics:
    """Main diagnostics coordinator"""

    def __init__(self, topics: List[str], output_file: str, interval: float = 0.5):
        self.topics = topics
        self.output_file = output_file
        self.interval = interval

        self.graph_monitor = ROSGraphMonitor()
        self.rate_monitor = TopicRateMonitor(topics)
        self.resource_monitor = SystemResourceMonitor()

        self.running = False
        self.start_time = None

    def start(self):
        """Start all monitoring"""
        print(f"🔍 Starting diagnostics...")
        print(f"📊 Monitoring topics: {', '.join(self.topics)}")
        print(f"📝 Output: {self.output_file}")
        print(f"⏱️  Sample interval: {self.interval}s")
        print()
        print("Trigger onboarding now. Press Ctrl+C when done.")
        print()

        self.start_time = time.time()
        self.running = True
        self.rate_monitor.start()

        # Initial graph snapshot
        self.graph_monitor.update()

        # Main monitoring loop
        try:
            last_graph_check = 0
            while self.running:
                now = time.time()
                elapsed = now - self.start_time

                # Check graph every 0.5s
                if now - last_graph_check >= 0.5:
                    change = self.graph_monitor.update()
                    if change:
                        self._log_event('GRAPH_CHANGE', change)
                        self._print_graph_change(change, elapsed)
                    last_graph_check = now

                # Sample resources
                if self.resource_monitor.enabled:
                    sample = self.resource_monitor.sample()

                # Check topic rates
                rates = self.rate_monitor.get_rates()
                self._log_event('TOPIC_RATES', {'rates': rates, 'elapsed': round(elapsed, 1)})

                # Check for gaps
                if self.rate_monitor.gaps_detected:
                    for gap in self.rate_monitor.gaps_detected:
                        self._log_event('MESSAGE_GAP', gap)
                        self._print_gap(gap, elapsed)
                    self.rate_monitor.gaps_detected.clear()

                # Print status
                self._print_status(elapsed, rates)

                time.sleep(self.interval)

        except KeyboardInterrupt:
            print("\n\n⏹️  Stopping diagnostics...")
        finally:
            self.stop()

    def stop(self):
        """Stop all monitoring"""
        self.running = False
        self.rate_monitor.stop()
        self._generate_report()

    def _log_event(self, event_type: str, data: dict):
        """Log event to file"""
        event = {
            'type': event_type,
            'timestamp': datetime.now().isoformat(),
            'elapsed': round(time.time() - self.start_time, 3),
            'data': data
        }
        with open(self.output_file, 'a') as f:
            f.write(json.dumps(event) + '\n')

    def _print_status(self, elapsed: float, rates: Dict[str, float]):
        """Print current status"""
        status = f"\r⏱️  {elapsed:6.1f}s | "
        for topic, rate in rates.items():
            topic_name = topic.split('/')[-1]
            status += f"{topic_name}: {rate:4.1f}Hz | "

        if self.resource_monitor.enabled and self.resource_monitor.samples:
            latest = self.resource_monitor.samples[-1]
            status += f"CPU: {latest['cpu_percent']:4.1f}% | "
            status += f"MEM: {latest['memory_percent']:4.1f}%"

        print(status, end='', flush=True)

    def _print_graph_change(self, change: dict, elapsed: float):
        """Print graph change event"""
        print(f"\n🔄 [{elapsed:6.1f}s] GRAPH CHANGE:")
        if change['new_nodes']:
            print(f"   ➕ New nodes: {', '.join(change['new_nodes'])}")
        if change['removed_nodes']:
            print(f"   ➖ Removed nodes: {', '.join(change['removed_nodes'])}")
        if change['new_topics']:
            print(f"   ➕ New topics: {len(change['new_topics'])} topics")
        if change['new_services']:
            print(f"   ➕ New services: {len(change['new_services'])} services")
        print(f"   📊 Total: {change['total_nodes']} nodes, {change['total_topics']} topics, {change['total_services']} services")
        print()

    def _print_gap(self, gap: dict, elapsed: float):
        """Print message gap event"""
        print(f"\n⚠️  [{elapsed:6.1f}s] MESSAGE GAP: {gap['topic']} - {gap['gap_seconds']}s gap")
        print()

    def _generate_report(self):
        """Generate summary report"""
        print("\n" + "="*70)
        print("📊 DIAGNOSTICS SUMMARY")
        print("="*70)

        total_time = time.time() - self.start_time
        print(f"\n⏱️  Total monitoring time: {total_time:.1f}s")

        # Graph changes
        print(f"\n🔄 Graph changes: {len(self.graph_monitor.changes)}")
        for i, change in enumerate(self.graph_monitor.changes, 1):
            print(f"   {i}. {change['timestamp']}")
            if change['new_nodes']:
                print(f"      New nodes: {', '.join(change['new_nodes'])}")

        # Message gaps
        print(f"\n⚠️  Message gaps detected: {len(self.rate_monitor.gaps_detected)}")
        gap_by_topic = defaultdict(list)
        # Read from log file to get all gaps
        with open(self.output_file, 'r') as f:
            for line in f:
                event = json.loads(line)
                if event['type'] == 'MESSAGE_GAP':
                    gap_by_topic[event['data']['topic']].append(event['data']['gap_seconds'])

        for topic, gaps in gap_by_topic.items():
            print(f"   {topic}: {len(gaps)} gaps, max: {max(gaps):.1f}s, avg: {sum(gaps)/len(gaps):.1f}s")

        # Resource spikes
        if self.resource_monitor.enabled and self.resource_monitor.samples:
            cpu_values = [s['cpu_percent'] for s in self.resource_monitor.samples]
            mem_values = [s['memory_percent'] for s in self.resource_monitor.samples]
            print(f"\n💻 System resources:")
            print(f"   CPU: max={max(cpu_values):.1f}%, avg={sum(cpu_values)/len(cpu_values):.1f}%")
            print(f"   Memory: max={max(mem_values):.1f}%, avg={sum(mem_values)/len(mem_values):.1f}%")

        print(f"\n📝 Full log: {self.output_file}")
        print("="*70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description='Diagnose onboarding hiccups in ROS system',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--topics', '-t', nargs='+',
                        help='Topics to monitor for message rate (e.g., /plug1/closed /plug2/closed)')
    parser.add_argument('--auto-discover', '-a', action='store_true',
                        help='Auto-discover all metering_socket_relay/closed topics')
    parser.add_argument('--output', '-o', default='/tmp/onboarding_diag.log',
                        help='Output log file (default: /tmp/onboarding_diag.log)')
    parser.add_argument('--interval', '-i', type=float, default=0.5,
                        help='Sampling interval in seconds (default: 0.5)')

    args = parser.parse_args()

    # Determine topics to monitor
    topics = []
    if args.auto_discover:
        print("🔍 Auto-discovering metering_socket_relay topics...")
        topics = discover_metering_socket_relay_topics()
        if not topics:
            print("❌ No topics found. Exiting.")
            return 1
        print(f"✅ Found {len(topics)} plug(s):")
        for topic in topics:
            # Extract plug name from topic path
            parts = topic.split('/')
            plug_name = '/'.join(parts[:-1]) if len(parts) > 1 else topic
            print(f"   📍 {plug_name}")
        print()
    elif args.topics:
        topics = args.topics
    else:
        print("❌ Error: Must specify either --topics or --auto-discover", file=sys.stderr)
        parser.print_help()
        return 1

    # Clear previous log
    if os.path.exists(args.output):
        os.remove(args.output)

    diagnostics = OnboardingDiagnostics(topics, args.output, args.interval)
    diagnostics.start()
    return 0


if __name__ == '__main__':
    sys.exit(main())
