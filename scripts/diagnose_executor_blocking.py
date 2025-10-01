#!/usr/bin/env python3
"""
Diagnose ROS executor blocking issues.

This script helps identify if a ROS node's executor is being starved by:
1. Monitoring callback execution times
2. Detecting callback queue buildup
3. Checking for blocking operations in callbacks

Usage:
    # Monitor a specific node
    ./diagnose_executor_blocking.py --node /rws_server

    # Monitor multiple nodes
    ./diagnose_executor_blocking.py --node /rws_server --node /device_manager
"""

import argparse
import subprocess
import time
import sys
import json
from datetime import datetime
from collections import defaultdict, deque


def get_node_info(node_name: str):
    """Get detailed info about a node"""
    try:
        # Get publishers
        result = subprocess.run(
            ['ros2', 'node', 'info', node_name],
            capture_output=True, text=True, timeout=10
        )

        if result.returncode == 0:
            return result.stdout
        return None
    except subprocess.TimeoutExpired:
        print(f"⚠️  Timeout getting info for {node_name}", file=sys.stderr)
        return None


def monitor_callback_rate(node_name: str, duration: int = 30):
    """
    Monitor callback execution rate by tracking topic publications.

    If a node publishes at 1Hz but we see bursts and gaps,
    the executor is likely being starved.
    """
    print(f"\n🔍 Monitoring callback rate for {node_name} ({duration}s)")
    print("   Looking for irregular message patterns (sign of executor blocking)\n")

    # Get topics published by this node
    result = subprocess.run(
        ['ros2', 'node', 'info', node_name],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        print(f"❌ Could not get info for {node_name}")
        return

    # Parse publishers
    lines = result.stdout.split('\n')
    publishers = []
    in_publishers = False

    for line in lines:
        if 'Publishers:' in line:
            in_publishers = True
            continue
        if in_publishers:
            if line.strip().startswith('/'):
                topic = line.strip().split(':')[0].strip()
                publishers.append(topic)
            elif 'Subscribers:' in line:
                break

    if not publishers:
        print(f"⚠️  No publishers found for {node_name}")
        return

    print(f"📊 Found {len(publishers)} published topics")
    for topic in publishers[:5]:
        print(f"   • {topic}")
    if len(publishers) > 5:
        print(f"   ... and {len(publishers)-5} more")

    # Monitor first topic for regularity
    monitor_topic = publishers[0]
    print(f"\n📡 Monitoring {monitor_topic} for regularity...")
    print("   (Irregular timing = executor blocking)\n")

    message_times = deque(maxlen=100)
    gaps = []

    proc = subprocess.Popen(
        ['ros2', 'topic', 'echo', monitor_topic, '--no-arr'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    start_time = time.time()
    last_msg_time = None

    try:
        while time.time() - start_time < duration:
            line = proc.stdout.readline()
            if line:
                now = time.time()
                message_times.append(now)

                if last_msg_time:
                    gap = now - last_msg_time

                    # If gap is >2x the expected rate, flag it
                    if len(message_times) >= 3:
                        # Calculate expected rate from recent messages
                        recent_gaps = [message_times[i] - message_times[i-1]
                                     for i in range(1, min(10, len(message_times)))]
                        expected = sum(recent_gaps) / len(recent_gaps)

                        if gap > expected * 2 and gap > 1.0:
                            gaps.append({
                                'time': now - start_time,
                                'gap': gap,
                                'expected': expected
                            })
                            print(f"⚠️  [{now - start_time:6.1f}s] Large gap: {gap:.2f}s (expected: {expected:.2f}s)")

                last_msg_time = now

    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()

    # Analysis
    print(f"\n📊 Analysis:")
    print(f"   Total messages: {len(message_times)}")
    print(f"   Large gaps detected: {len(gaps)}")

    if message_times:
        time_diffs = [message_times[i] - message_times[i-1]
                     for i in range(1, len(message_times))]
        if time_diffs:
            avg_gap = sum(time_diffs) / len(time_diffs)
            max_gap = max(time_diffs)
            min_gap = min(time_diffs)

            print(f"   Average interval: {avg_gap:.3f}s ({1/avg_gap:.1f}Hz)")
            print(f"   Min interval: {min_gap:.3f}s")
            print(f"   Max interval: {max_gap:.3f}s")
            print(f"   Variability: {(max_gap - min_gap):.3f}s")

            if max_gap > avg_gap * 3:
                print(f"\n⚠️  HIGH VARIABILITY - Likely executor blocking!")
                print(f"   Max gap is {max_gap/avg_gap:.1f}x the average")
            else:
                print(f"\n✅ Relatively stable - Executor appears healthy")


def check_service_response_time(node_name: str, service_name: str = None):
    """
    Check if service calls to this node are slow.
    Slow responses indicate blocking in executor.
    """
    print(f"\n🔍 Checking service response time for {node_name}")

    # Get services provided by this node
    result = subprocess.run(
        ['ros2', 'service', 'list'],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        print("❌ Could not list services")
        return

    # Find services that might belong to this node
    # (ROS2 doesn't have a direct way to query services by node)
    all_services = result.stdout.strip().split('\n')

    if service_name:
        test_service = service_name
    else:
        # Try to find a common service like list_parameters
        namespace = '/'.join(node_name.split('/')[:-1])
        test_service = f"{namespace}/list_parameters" if namespace else None

    if not test_service or test_service not in all_services:
        print(f"⚠️  No testable service found for {node_name}")
        return

    print(f"📡 Testing service: {test_service}")
    print("   Making 10 calls to measure response time...\n")

    response_times = []

    for i in range(10):
        start = time.time()
        try:
            # Call list_parameters service (safe, non-intrusive)
            result = subprocess.run(
                ['ros2', 'service', 'call', test_service,
                 'rcl_interfaces/srv/ListParameters', '{}'],
                capture_output=True, text=True, timeout=5
            )
            elapsed = time.time() - start
            response_times.append(elapsed)

            status = "✓" if result.returncode == 0 else "✗"
            print(f"   {i+1}/10 {status} {elapsed*1000:.1f}ms")

        except subprocess.TimeoutExpired:
            print(f"   {i+1}/10 ✗ TIMEOUT (>5s)")
            response_times.append(5.0)

        time.sleep(0.5)

    # Analysis
    if response_times:
        avg = sum(response_times) / len(response_times)
        max_time = max(response_times)
        min_time = min(response_times)

        print(f"\n📊 Service Response Times:")
        print(f"   Average: {avg*1000:.1f}ms")
        print(f"   Min: {min_time*1000:.1f}ms")
        print(f"   Max: {max_time*1000:.1f}ms")

        if max_time > 1.0:
            print(f"\n⚠️  SLOW SERVICE RESPONSES!")
            print(f"   Executor may be blocked by long-running callbacks")
        elif avg > 0.1:
            print(f"\n⚠️  Elevated response times")
            print(f"   Executor may be under load")
        else:
            print(f"\n✅ Fast service responses - Executor appears responsive")


def check_subscription_count(node_name: str):
    """
    Check how many subscriptions this node has.
    Too many subscriptions can overload a single-threaded executor.
    """
    print(f"\n🔍 Checking subscription load for {node_name}")

    result = subprocess.run(
        ['ros2', 'node', 'info', node_name],
        capture_output=True, text=True, timeout=10
    )

    if result.returncode != 0:
        print(f"❌ Could not get node info")
        return

    lines = result.stdout.split('\n')

    # Count publishers, subscribers, services, actions
    pub_count = 0
    sub_count = 0
    srv_count = 0

    for line in lines:
        if 'Subscribers:' in line:
            # Next lines until empty are subscribers
            continue
        if line.strip().startswith('/') and 'Publishers:' in '\n'.join(lines[:lines.index(line)]):
            pub_count += 1
        if line.strip().startswith('/') and 'Subscribers:' in '\n'.join(lines[:lines.index(line)]):
            sub_count += 1

    # Parse more carefully
    info_text = result.stdout
    pub_count = info_text.count('Publishers:')
    # Count topics in subscriber section
    in_subs = False
    sub_count = 0
    for line in lines:
        if 'Subscribers:' in line:
            in_subs = True
        elif 'Service Servers:' in line or 'Service Clients:' in line:
            in_subs = False
        elif in_subs and line.strip().startswith('/'):
            sub_count += 1

    print(f"📊 Callback Load:")
    print(f"   Subscriptions: {sub_count}")
    print(f"   Publishers: {pub_count}")

    if sub_count > 20:
        print(f"\n⚠️  HIGH SUBSCRIPTION COUNT!")
        print(f"   {sub_count} subscriptions may overload executor")
        print(f"   Consider: MultiThreadedExecutor or multiple nodes")
    elif sub_count > 10:
        print(f"\n⚠️  Moderate subscription count")
        print(f"   Monitor for callback buildup")
    else:
        print(f"\n✅ Reasonable subscription count")


def main():
    parser = argparse.ArgumentParser(
        description='Diagnose ROS executor blocking',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--node', '-n', required=True,
                       help='Node name to diagnose (e.g., /rws_server)')
    parser.add_argument('--duration', '-d', type=int, default=30,
                       help='Monitoring duration in seconds (default: 30)')
    parser.add_argument('--service', '-s',
                       help='Specific service to test (optional)')

    args = parser.parse_args()

    print("=" * 70)
    print("🔍 ROS EXECUTOR BLOCKING DIAGNOSTICS")
    print("=" * 70)
    print(f"\nTarget node: {args.node}")
    print(f"Duration: {args.duration}s\n")

    # Run diagnostics
    check_subscription_count(args.node)
    check_service_response_time(args.node, args.service)
    monitor_callback_rate(args.node, args.duration)

    print("\n" + "=" * 70)
    print("✅ Diagnostics complete")
    print("=" * 70)


if __name__ == '__main__':
    main()
