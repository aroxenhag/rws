#!/usr/bin/env python3
"""
RWS Timing Live Monitor

Interactive TUI for monitoring RWS bridge timing in real-time.

Usage:
    # Monitor a log file (auto-updates)
    ./rws_timing_live.py /tmp/rws_timing.log

    # Monitor with custom refresh rate
    ./rws_timing_live.py /tmp/rws_timing.log --refresh 2

Controls:
    1 - Bar chart (default)
    2 - Pie chart
    3 - By service
    4 - Percentile stats
    5 - All views
    w - Toggle time window (1min/5min/10min/30min/all)
    r - Reset data
    q - Quit
"""

import argparse
import sys
import time
import os
import select
import termios
import tty
import shutil
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List
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
        self.trace_ids = []


class LiveMonitor:
    """Live monitoring TUI"""

    def __init__(self, log_file: str, refresh_rate: float = 1.0):
        self.log_file = log_file
        self.refresh_rate = refresh_rate
        self.data = TimingData()
        self.file_position = 0
        self.view_mode = 'bar'  # bar, pie, service, percentiles, all
        self.time_window = None  # None, 1, 5, 10, 30 (minutes)
        self.running = True
        self.last_update = time.time()
        self.output_buffer = []  # Buffer output to reduce flicker
        self.term_width = 80  # Default width
        self.term_height = 24  # Default height
        self.update_terminal_size()

    def update_terminal_size(self):
        """Update terminal dimensions"""
        try:
            self.term_width, self.term_height = shutil.get_terminal_size(fallback=(80, 24))
        except:
            self.term_width, self.term_height = 80, 24

    def clear_screen(self):
        """Clear terminal screen completely"""
        # Clear entire screen and move to home
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()

    def print_buffered(self, text: str = ""):
        """Add text to output buffer instead of printing immediately"""
        self.output_buffer.append(text)

    def flush_buffer(self):
        """Write all buffered output at once to reduce flicker"""
        # Update terminal size in case of resize
        self.update_terminal_size()

        # Hide cursor during update, show after
        sys.stdout.write('\033[?25l')  # Hide cursor

        # Clear entire screen and move to home (fixes resize issues)
        sys.stdout.write('\033[2J\033[H')

        # Write all content
        output = '\n'.join(self.output_buffer)
        sys.stdout.write(output)

        sys.stdout.write('\033[?25h')  # Show cursor
        sys.stdout.flush()
        self.output_buffer = []

    def read_new_lines(self):
        """Read new lines from log file since last check"""
        if not os.path.exists(self.log_file):
            return []

        try:
            with open(self.log_file, 'r') as f:
                f.seek(self.file_position)
                new_lines = f.readlines()
                self.file_position = f.tell()
                return new_lines
        except Exception as e:
            return []

    def parse_timing_line(self, line: str) -> Dict:
        """Parse a RWS_TIMING log line"""
        if "RWS_TIMING" not in line:
            return None

        import re
        timestamp_match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})', line)
        timestamp = None
        if timestamp_match:
            try:
                timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S.%f')
            except:
                pass

        timing_part = line[line.find("RWS_TIMING"):]
        pairs = timing_part.split('|')[1:]

        data = {'timestamp': timestamp}
        for pair in pairs:
            if '=' in pair:
                key, value = pair.split('=', 1)
                try:
                    data[key] = int(value)
                except ValueError:
                    try:
                        data[key] = float(value)
                    except ValueError:
                        data[key] = value.strip()

        return data

    def update_data(self):
        """Read new log lines and update data"""
        new_lines = self.read_new_lines()

        for line in new_lines:
            parsed = self.parse_timing_line(line)
            if not parsed:
                continue

            if 'cache_check_us' in parsed:
                self.data.cache_check.append(parsed['cache_check_us'])
                self.data.client_setup.append(parsed.get('client_setup_us', 0))
                self.data.ready_check.append(parsed.get('ready_check_us', 0))
                self.data.serialize.append(parsed.get('serialize_us', 0))
                self.data.send.append(parsed.get('send_us', 0))
                self.data.total_dispatch.append(parsed.get('total_dispatch_us', 0))
                self.data.timestamps.append(parsed.get('timestamp'))
                self.data.services.append(parsed.get('service', 'unknown'))
                self.data.trace_ids.append(parsed.get('trace_id', 'unknown'))

    def filter_by_time_window(self, data: TimingData) -> TimingData:
        """Filter data to time window"""
        if self.time_window is None or not data.timestamps:
            return data

        cutoff = datetime.now() - timedelta(minutes=self.time_window)
        filtered = TimingData()

        for i, ts in enumerate(data.timestamps):
            if ts and ts >= cutoff:
                if i < len(data.cache_check):
                    filtered.cache_check.append(data.cache_check[i])
                    filtered.client_setup.append(data.client_setup[i])
                    filtered.ready_check.append(data.ready_check[i])
                    filtered.serialize.append(data.serialize[i])
                    filtered.send.append(data.send[i])
                    filtered.total_dispatch.append(data.total_dispatch[i])
                    filtered.services.append(data.services[i])
                    filtered.trace_ids.append(data.trace_ids[i])
                    filtered.timestamps.append(ts)

        return filtered

    def draw_bar_chart(self, data: Dict[str, float], title: str, width: int = 50):
        """Draw ASCII bar chart"""
        self.print_buffered(f"\n{title}")
        self.print_buffered("=" * (width + 30))

        if not data:
            self.print_buffered("No data available")
            return

        max_value = max(data.values())
        if max_value == 0:
            self.print_buffered("All values are zero")
            return

        total = sum(data.values())
        for label, value in sorted(data.items(), key=lambda x: x[1], reverse=True):
            bar_length = int((value / max_value) * width)
            bar = "█" * bar_length
            percentage = (value / total) * 100 if total > 0 else 0
            self.print_buffered(f"{label:18s} {bar:<{width}} {value:8.1f}μs ({percentage:5.1f}%)")

    def draw_pie_chart(self, data: Dict[str, float], title: str, width: int = 40):
        """Draw ASCII pie chart"""
        self.print_buffered(f"\n{title}")
        self.print_buffered("=" * (width + 30))

        if not data:
            self.print_buffered("No data available")
            return

        total = sum(data.values())
        if total == 0:
            self.print_buffered("All values are zero")
            return

        sorted_data = sorted(data.items(), key=lambda x: x[1], reverse=True)

        for label, value in sorted_data:
            percentage = (value / total) * 100
            bar_length = int((percentage / 100) * width)
            bar = "█" * bar_length
            self.print_buffered(f"{label:18s} {bar:<{width}} {percentage:5.1f}%")

    def render_bar_view(self, data: TimingData):
        """Render bar chart view"""
        if not data.cache_check:
            self.print_buffered("\n⚠ No timing data available yet...")
            return

        phases = {
            'Cache Check': statistics.mean(data.cache_check),
            'Client Setup': statistics.mean(data.client_setup),
            'Ready Check': statistics.mean(data.ready_check),
            'Serialize': statistics.mean(data.serialize),
            'Send Request': statistics.mean(data.send),
        }

        self.draw_bar_chart(phases, "⏱ Average Time per Phase (Dispatch)")

        if data.total_dispatch:
            total_avg = statistics.mean(data.total_dispatch)
            self.print_buffered(f"\n📊 Average Total Dispatch: {total_avg:.1f}μs ({total_avg/1000:.2f}ms)")
            self.print_buffered(f"📈 Service Calls: {len(data.total_dispatch)}")

    def render_pie_view(self, data: TimingData):
        """Render pie chart view"""
        if not data.cache_check:
            self.print_buffered("\n⚠ No timing data available yet...")
            return

        phases = {
            'Cache Check': statistics.mean(data.cache_check),
            'Client Setup': statistics.mean(data.client_setup),
            'Ready Check': statistics.mean(data.ready_check),
            'Serialize': statistics.mean(data.serialize),
            'Send': statistics.mean(data.send),
        }

        self.draw_pie_chart(phases, "🥧 Time Distribution in Bridge")

        if data.total_dispatch:
            total_avg = statistics.mean(data.total_dispatch)
            self.print_buffered(f"\n📊 Average Total Dispatch: {total_avg:.1f}μs ({total_avg/1000:.2f}ms)")
            self.print_buffered(f"📈 Service Calls: {len(data.total_dispatch)}")

    def render_service_view(self, data: TimingData):
        """Render service breakdown view"""
        if not data.services or not data.total_dispatch:
            self.print_buffered("\n⚠ No service data available yet...")
            return

        service_times = defaultdict(list)
        for i, service in enumerate(data.services):
            if i < len(data.total_dispatch):
                service_times[service].append(data.total_dispatch[i])

        service_avgs = {
            service: statistics.mean(times)
            for service, times in service_times.items()
        }

        self.draw_bar_chart(service_avgs, "🎯 Average Dispatch Time by Service")

        self.print_buffered("\n📞 Call Counts:")
        for service in sorted(service_times.keys(), key=lambda s: len(service_times[s]), reverse=True)[:10]:
            count = len(service_times[service])
            avg = statistics.mean(service_times[service])
            self.print_buffered(f"  {service:45s} {count:6d} calls  (avg: {avg:7.1f}μs)")

    def render_percentile_view(self, data: TimingData):
        """Render percentile statistics"""
        if not data.cache_check:
            self.print_buffered("\n⚠ No timing data available yet...")
            return

        self.print_buffered("\n📊 Percentile Statistics (microseconds)")
        self.print_buffered("=" * self.term_width)

        phases = [
            ('Cache Check', data.cache_check),
            ('Client Setup', data.client_setup),
            ('Ready Check', data.ready_check),
            ('Serialize', data.serialize),
            ('Send', data.send),
            ('Total Dispatch', data.total_dispatch),
        ]

        self.print_buffered(f"{'Phase':<18} {'Count':>7} {'Mean':>9} {'Median':>9} {'P95':>9} {'P99':>9} {'Max':>9}")
        self.print_buffered("-" * 90)

        for phase_name, phase_data in phases:
            if not phase_data:
                continue

            sorted_vals = sorted(phase_data)
            stats = {
                'count': len(phase_data),
                'mean': statistics.mean(phase_data),
                'median': statistics.median(phase_data),
                'p95': sorted_vals[int(len(sorted_vals) * 0.95)] if len(sorted_vals) > 1 else sorted_vals[0],
                'p99': sorted_vals[int(len(sorted_vals) * 0.99)] if len(sorted_vals) > 1 else sorted_vals[0],
                'max': max(phase_data),
            }

            self.print_buffered(f"{phase_name:<18} {stats['count']:>7} {stats['mean']:>9.1f} "
                  f"{stats['median']:>9.1f} {stats['p95']:>9.1f} {stats['p99']:>9.1f} {stats['max']:>9.1f}")

    def render(self):
        """Render the current view"""
        # Header (clear_screen is now part of flush_buffer)
        self.print_buffered("=" * self.term_width)
        self.print_buffered("🚀 RWS Bridge Timing Monitor - LIVE MODE")
        self.print_buffered("=" * self.term_width)

        # Apply time window filter
        display_data = self.filter_by_time_window(self.data)

        # Window indicator
        window_str = f"{self.time_window}min" if self.time_window else "All time"
        view_name = {
            'bar': 'Bar Chart',
            'pie': 'Pie Chart',
            'service': 'By Service',
            'percentiles': 'Percentiles',
            'all': 'All Views'
        }.get(self.view_mode, 'Unknown')

        self.print_buffered(f"📁 File: {self.log_file}")
        self.print_buffered(f"🔄 Refresh: {self.refresh_rate}s | 📊 View: {view_name} | ⏰ Window: {window_str}")
        self.print_buffered(f"⏱ Last update: {time.strftime('%H:%M:%S', time.localtime(self.last_update))}")

        # Render based on view mode
        if self.view_mode == 'bar':
            self.render_bar_view(display_data)
        elif self.view_mode == 'pie':
            self.render_pie_view(display_data)
        elif self.view_mode == 'service':
            self.render_service_view(display_data)
        elif self.view_mode == 'percentiles':
            self.render_percentile_view(display_data)
        elif self.view_mode == 'all':
            self.render_bar_view(display_data)
            self.render_service_view(display_data)
            self.render_percentile_view(display_data)

        # Controls
        self.print_buffered("\n" + "=" * self.term_width)
        self.print_buffered("⌨ Controls: [1]Bar [2]Pie [3]Service [4]Percentiles [5]All | [w]Window [r]Reset [q]Quit")
        self.print_buffered("=" * self.term_width)

        # Flush all buffered output at once (reduces flicker)
        self.flush_buffer()

    def handle_input(self):
        """Handle keyboard input (non-blocking)"""
        if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
            key = sys.stdin.read(1)

            if key == 'q':
                self.running = False
            elif key == '1':
                self.view_mode = 'bar'
            elif key == '2':
                self.view_mode = 'pie'
            elif key == '3':
                self.view_mode = 'service'
            elif key == '4':
                self.view_mode = 'percentiles'
            elif key == '5':
                self.view_mode = 'all'
            elif key == 'w':
                # Cycle through time windows
                windows = [None, 1, 5, 10, 30]
                current_idx = windows.index(self.time_window) if self.time_window in windows else 0
                self.time_window = windows[(current_idx + 1) % len(windows)]
            elif key == 'r':
                # Reset data
                self.data = TimingData()
                self.file_position = 0

    def run(self):
        """Main loop"""
        # Setup terminal for non-blocking input
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())

            while self.running:
                self.update_data()
                self.render()
                self.last_update = time.time()

                # Sleep in small increments to check for input
                sleep_time = 0
                while sleep_time < self.refresh_rate and self.running:
                    self.handle_input()
                    time.sleep(0.1)
                    sleep_time += 0.1

        finally:
            # Restore terminal settings
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

        print("\n👋 Monitoring stopped.")


def main():
    parser = argparse.ArgumentParser(
        description='Live monitoring TUI for RWS bridge timing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('log_file', help='Log file to monitor')
    parser.add_argument('--refresh', '-r', type=float, default=1.0,
                        help='Refresh rate in seconds (default: 1.0)')
    parser.add_argument('--minutes', '-m', type=int, default=None,
                        help='Initial time window in minutes (default: all data)')

    args = parser.parse_args()

    if not os.path.exists(args.log_file):
        print(f"⚠ Warning: Log file {args.log_file} does not exist yet.")
        print(f"Creating file and waiting for data...")
        # Create empty file
        open(args.log_file, 'a').close()

    monitor = LiveMonitor(args.log_file, args.refresh)
    if args.minutes:
        monitor.window_minutes = args.minutes

    try:
        monitor.run()
    except KeyboardInterrupt:
        print("\n👋 Monitoring stopped by user.")


if __name__ == '__main__':
    main()
