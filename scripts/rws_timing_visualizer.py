#!/usr/bin/env python3
"""
RWS Timing Visualizer

Parses RWS timing logs and creates ASCII charts showing where time is spent
in the bridge during service calls.

Usage:
    # Parse logs and show last 10 minutes
    ./rws_timing_visualizer.py --file /tmp/rws_timing.log --minutes 10

    # Parse from stdin (live monitoring)
    tail -f /tmp/rws_timing.log | ./rws_timing_visualizer.py

    # Show breakdown by service
    ./rws_timing_visualizer.py --file /tmp/rws_timing.log --by-service

    # Show percentile stats
    ./rws_timing_visualizer.py --file /tmp/rws_timing.log --percentiles
"""

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import statistics


class TimingData:
    """Stores timing measurements for service calls"""

    def __init__(self):
        self.cache_check = []
        self.client_setup = []
        self.ready_check = []
        self.serialize = []
        self.send = []
        self.total_dispatch = []
        self.deserialize = []
        self.total_e2e = []
        self.timestamps = []
        self.services = []


def parse_timing_line(line: str) -> Dict[str, any]:
    """Parse a RWS_TIMING log line"""
    if "RWS_TIMING" not in line:
        return None

    # Extract timestamp if present
    timestamp_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})', line)
    timestamp = None
    if timestamp_match:
        timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S.%f')

    # Parse key-value pairs
    timing_part = line[line.find("RWS_TIMING"):]
    pairs = timing_part.split('|')[1:]  # Skip "RWS_TIMING"

    data = {'timestamp': timestamp}
    for pair in pairs:
        if '=' in pair:
            key, value = pair.split('=', 1)
            # Try to convert to number, otherwise keep as string
            try:
                data[key] = int(value)
            except ValueError:
                try:
                    data[key] = float(value)
                except ValueError:
                    data[key] = value

    return data


def filter_by_time_window(data: TimingData, minutes: int) -> TimingData:
    """Filter data to only include entries from the last N minutes"""
    if not data.timestamps:
        return data

    cutoff = datetime.now() - timedelta(minutes=minutes)
    filtered = TimingData()

    for i, ts in enumerate(data.timestamps):
        if ts and ts >= cutoff:
            if i < len(data.cache_check):
                filtered.cache_check.append(data.cache_check[i])
            if i < len(data.client_setup):
                filtered.client_setup.append(data.client_setup[i])
            if i < len(data.ready_check):
                filtered.ready_check.append(data.ready_check[i])
            if i < len(data.serialize):
                filtered.serialize.append(data.serialize[i])
            if i < len(data.send):
                filtered.send.append(data.send[i])
            if i < len(data.total_dispatch):
                filtered.total_dispatch.append(data.total_dispatch[i])
            if i < len(data.services):
                filtered.services.append(data.services[i])
            filtered.timestamps.append(ts)

    return filtered


def parse_log_file(file_path: str = None, minutes: int = None) -> TimingData:
    """Parse timing data from log file or stdin"""
    data = TimingData()

    if file_path:
        with open(file_path, 'r') as f:
            lines = f.readlines()
    else:
        lines = sys.stdin.readlines()

    for line in lines:
        parsed = parse_timing_line(line)
        if not parsed:
            continue

        if 'cache_check_us' in parsed:
            data.cache_check.append(parsed['cache_check_us'])
            data.client_setup.append(parsed.get('client_setup_us', 0))
            data.ready_check.append(parsed.get('ready_check_us', 0))
            data.serialize.append(parsed.get('serialize_us', 0))
            data.send.append(parsed.get('send_us', 0))
            data.total_dispatch.append(parsed.get('total_dispatch_us', 0))
            data.timestamps.append(parsed.get('timestamp'))
            data.services.append(parsed.get('service', 'unknown'))

        if 'deserialize_us' in parsed:
            data.deserialize.append(parsed['deserialize_us'])
            data.total_e2e.append(parsed.get('total_e2e_ms', 0) * 1000)  # Convert to μs

    if minutes:
        data = filter_by_time_window(data, minutes)

    return data


def calculate_stats(values: List[float]) -> Dict[str, float]:
    """Calculate statistical summary"""
    if not values:
        return {'count': 0, 'mean': 0, 'median': 0, 'p95': 0, 'p99': 0, 'max': 0}

    sorted_vals = sorted(values)
    return {
        'count': len(values),
        'mean': statistics.mean(values),
        'median': statistics.median(values),
        'p95': sorted_vals[int(len(sorted_vals) * 0.95)] if len(sorted_vals) > 1 else sorted_vals[0],
        'p99': sorted_vals[int(len(sorted_vals) * 0.99)] if len(sorted_vals) > 1 else sorted_vals[0],
        'max': max(values),
    }


def draw_ascii_bar_chart(data: Dict[str, float], title: str, max_width: int = 60, unit: str = "μs"):
    """Draw an ASCII bar chart"""
    print(f"\n{title}")
    print("=" * (max_width + 20))

    if not data:
        print("No data available")
        return

    max_value = max(data.values())
    if max_value == 0:
        print("All values are zero")
        return

    for label, value in sorted(data.items(), key=lambda x: x[1], reverse=True):
        bar_length = int((value / max_value) * max_width)
        bar = "█" * bar_length
        percentage = (value / sum(data.values())) * 100 if sum(data.values()) > 0 else 0
        print(f"{label:20s} {bar} {value:8.1f}{unit} ({percentage:5.1f}%)")


def draw_ascii_pie_chart(data: Dict[str, float], title: str, width: int = 50):
    """Draw an ASCII pie chart representation"""
    print(f"\n{title}")
    print("=" * width)

    if not data:
        print("No data available")
        return

    total = sum(data.values())
    if total == 0:
        print("All values are zero")
        return

    # Sort by value descending
    sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)

    for label, value in sorted_data:
        percentage = (value / total) * 100
        bar_length = int((percentage / 100) * (width - 25))
        bar = "█" * bar_length
        print(f"{label:15s} {bar:<{width-25}} {percentage:5.1f}%")


def create_timing_breakdown_chart(data: TimingData, chart_type: str = 'bar'):
    """Create chart showing time breakdown across bridge phases"""
    if not data.cache_check:
        print("No timing data available")
        return

    # Calculate average time per phase
    phases = {
        'Cache Check': statistics.mean(data.cache_check) if data.cache_check else 0,
        'Client Setup': statistics.mean(data.client_setup) if data.client_setup else 0,
        'Ready Check': statistics.mean(data.ready_check) if data.ready_check else 0,
        'Serialize': statistics.mean(data.serialize) if data.serialize else 0,
        'Send Request': statistics.mean(data.send) if data.send else 0,
    }

    if chart_type == 'pie':
        draw_ascii_pie_chart(phases, "Average Time Distribution in Bridge (Dispatch Phase)")
    else:
        draw_ascii_bar_chart(phases, "Average Time per Phase (Dispatch)")

    # Show total dispatch time
    if data.total_dispatch:
        total_avg = statistics.mean(data.total_dispatch)
        print(f"\nAverage Total Dispatch Time: {total_avg:.1f}μs ({total_avg/1000:.2f}ms)")
        print(f"Service Calls Analyzed: {len(data.total_dispatch)}")


def create_service_breakdown_chart(data: TimingData):
    """Create chart showing time breakdown by service"""
    if not data.services or not data.total_dispatch:
        print("No service data available")
        return

    # Group by service
    service_times = defaultdict(list)
    for i, service in enumerate(data.services):
        if i < len(data.total_dispatch):
            service_times[service].append(data.total_dispatch[i])

    # Calculate average per service
    service_avgs = {
        service: statistics.mean(times)
        for service, times in service_times.items()
    }

    draw_ascii_bar_chart(service_avgs, "Average Dispatch Time by Service")

    # Show call counts
    print("\nCall Counts per Service:")
    for service in sorted(service_times.keys(), key=lambda s: len(service_times[s]), reverse=True):
        print(f"  {service:50s} {len(service_times[service]):6d} calls")


def show_percentile_stats(data: TimingData):
    """Show percentile statistics for each phase"""
    print("\nPercentile Statistics (all values in microseconds)")
    print("=" * 80)

    phases = [
        ('Cache Check', data.cache_check),
        ('Client Setup', data.client_setup),
        ('Ready Check', data.ready_check),
        ('Serialize', data.serialize),
        ('Send', data.send),
        ('Total Dispatch', data.total_dispatch),
    ]

    print(f"{'Phase':<20} {'Count':>8} {'Mean':>10} {'Median':>10} {'P95':>10} {'P99':>10} {'Max':>10}")
    print("-" * 80)

    for phase_name, phase_data in phases:
        if not phase_data:
            continue
        stats = calculate_stats(phase_data)
        print(f"{phase_name:<20} {stats['count']:>8} {stats['mean']:>10.1f} "
              f"{stats['median']:>10.1f} {stats['p95']:>10.1f} {stats['p99']:>10.1f} {stats['max']:>10.1f}")


def main():
    parser = argparse.ArgumentParser(
        description='Visualize RWS bridge timing data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('--file', '-f', help='Log file to parse (default: stdin)')
    parser.add_argument('--minutes', '-m', type=int, help='Only show data from last N minutes')
    parser.add_argument('--by-service', action='store_true', help='Show breakdown by service')
    parser.add_argument('--percentiles', '-p', action='store_true', help='Show percentile statistics')
    parser.add_argument('--chart-type', '-t', choices=['bar', 'pie'], default='bar',
                        help='Chart type (default: bar)')

    args = parser.parse_args()

    # Parse data
    print("Parsing timing data...", file=sys.stderr)
    data = parse_log_file(args.file, args.minutes)

    if not data.cache_check and not data.total_dispatch:
        print("No RWS_TIMING data found in logs", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(data.total_dispatch)} service call timing records\n", file=sys.stderr)

    # Generate visualizations
    create_timing_breakdown_chart(data, args.chart_type)

    if args.by_service:
        create_service_breakdown_chart(data)

    if args.percentiles:
        show_percentile_stats(data)

    # Summary
    if data.timestamps:
        valid_timestamps = [ts for ts in data.timestamps if ts]
        if valid_timestamps:
            time_range = max(valid_timestamps) - min(valid_timestamps)
            print(f"\nTime Range: {time_range}")


if __name__ == '__main__':
    main()
