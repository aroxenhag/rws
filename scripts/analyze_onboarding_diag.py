#!/usr/bin/env python3
"""
Analyze onboarding diagnostics log to identify correlation between events.

Usage:
    ./analyze_onboarding_diag.py /tmp/onboarding_diag.log
"""

import argparse
import json
import sys
from collections import defaultdict
from typing import List, Dict


def load_events(log_file: str) -> List[dict]:
    """Load all events from log file"""
    events = []
    with open(log_file, 'r') as f:
        for line in f:
            events.append(json.loads(line))
    return events


def analyze_correlation(events: List[dict]):
    """Analyze correlation between graph changes and message gaps"""
    print("="*70)
    print("🔍 CORRELATION ANALYSIS")
    print("="*70)

    graph_changes = [e for e in events if e['type'] == 'GRAPH_CHANGE']
    message_gaps = [e for e in events if e['type'] == 'MESSAGE_GAP']

    if not graph_changes:
        print("\n⚠️  No graph changes detected during monitoring")
        return

    if not message_gaps:
        print("\n✅ No message gaps detected - system appears healthy!")
        return

    print(f"\n📊 Events summary:")
    print(f"   Graph changes: {len(graph_changes)}")
    print(f"   Message gaps: {len(message_gaps)}")

    # Find gaps that occurred near graph changes (within 5 seconds)
    correlation_window = 5.0  # seconds

    print(f"\n🔗 Gaps within {correlation_window}s of graph changes:")

    correlated = []
    for gap in message_gaps:
        gap_time = gap['elapsed']

        for change in graph_changes:
            change_time = change['elapsed']
            time_diff = abs(gap_time - change_time)

            if time_diff <= correlation_window:
                correlated.append({
                    'gap': gap,
                    'change': change,
                    'time_diff': time_diff
                })

                print(f"\n   ⚠️  Gap at {gap_time:.1f}s:")
                print(f"      Topic: {gap['data']['topic']}")
                print(f"      Gap duration: {gap['data']['gap_seconds']:.1f}s")
                print(f"      Graph change at {change_time:.1f}s (Δ{time_diff:.1f}s):")

                change_data = change['data']
                if change_data.get('new_nodes'):
                    print(f"         New nodes: {', '.join(change_data['new_nodes'])}")
                if change_data.get('new_topics'):
                    print(f"         New topics: {len(change_data['new_topics'])}")
                if change_data.get('new_services'):
                    print(f"         New services: {len(change_data['new_services'])}")
                break

    if not correlated:
        print("\n   ℹ️  No strong correlation found - gaps and graph changes are not close in time")
    else:
        print(f"\n   📈 Correlation rate: {len(correlated)}/{len(message_gaps)} gaps ({100*len(correlated)/len(message_gaps):.0f}%) near graph changes")


def analyze_graph_changes(events: List[dict]):
    """Analyze the pattern of graph changes"""
    print("\n" + "="*70)
    print("🔄 GRAPH CHANGE ANALYSIS")
    print("="*70)

    graph_changes = [e for e in events if e['type'] == 'GRAPH_CHANGE']

    if not graph_changes:
        return

    print(f"\n📊 Timeline of graph changes:\n")

    for i, change in enumerate(graph_changes, 1):
        data = change['data']
        print(f"{i}. At {change['elapsed']:.1f}s:")

        if data.get('new_nodes'):
            print(f"   ➕ Added {len(data['new_nodes'])} node(s): {', '.join(data['new_nodes'])}")
        if data.get('removed_nodes'):
            print(f"   ➖ Removed {len(data['removed_nodes'])} node(s): {', '.join(data['removed_nodes'])}")
        if data.get('new_topics'):
            print(f"   ➕ Added {len(data['new_topics'])} topic(s)")
        if data.get('new_services'):
            print(f"   ➕ Added {len(data['new_services'])} service(s)")

        print(f"   📊 Total after change: {data['total_nodes']} nodes, {data['total_topics']} topics, {data['total_services']} services")
        print()


def analyze_message_gaps(events: List[dict]):
    """Analyze message gap patterns"""
    print("="*70)
    print("⚠️  MESSAGE GAP ANALYSIS")
    print("="*70)

    message_gaps = [e for e in events if e['type'] == 'MESSAGE_GAP']

    if not message_gaps:
        print("\n✅ No message gaps detected!")
        return

    # Group by topic
    gaps_by_topic = defaultdict(list)
    for gap in message_gaps:
        topic = gap['data']['topic']
        gaps_by_topic[topic].append({
            'time': gap['elapsed'],
            'duration': gap['data']['gap_seconds']
        })

    print(f"\n📊 Gap summary by topic:\n")

    for topic, gaps in gaps_by_topic.items():
        durations = [g['duration'] for g in gaps]
        print(f"Topic: {topic}")
        print(f"   Count: {len(gaps)}")
        print(f"   Duration: min={min(durations):.1f}s, max={max(durations):.1f}s, avg={sum(durations)/len(durations):.1f}s")
        print(f"   Times: {', '.join([f'{g["time"]:.1f}s' for g in gaps[:5]])}")
        if len(gaps) > 5:
            print(f"          ... and {len(gaps)-5} more")
        print()


def analyze_topic_rates(events: List[dict]):
    """Analyze topic rate changes over time"""
    print("="*70)
    print("📈 TOPIC RATE ANALYSIS")
    print("="*70)

    rate_events = [e for e in events if e['type'] == 'TOPIC_RATES']

    if not rate_events:
        return

    # Get all topics
    topics = set()
    for event in rate_events:
        topics.update(event['data']['rates'].keys())

    print(f"\n📊 Rate changes during monitoring:\n")

    for topic in sorted(topics):
        rates = []
        times = []
        for event in rate_events:
            if topic in event['data']['rates']:
                rates.append(event['data']['rates'][topic])
                times.append(event['data']['elapsed'])

        if rates:
            min_rate = min(rates)
            max_rate = max(rates)
            avg_rate = sum(rates) / len(rates)

            # Find periods of low rate
            low_rate_periods = []
            for i, rate in enumerate(rates):
                if rate < 0.5 and avg_rate > 0.5:  # Significant drop
                    low_rate_periods.append(times[i])

            print(f"{topic}:")
            print(f"   Rate: min={min_rate:.1f}Hz, max={max_rate:.1f}Hz, avg={avg_rate:.1f}Hz")

            if low_rate_periods:
                print(f"   ⚠️  Low rate periods: {', '.join([f'{t:.1f}s' for t in low_rate_periods[:5]])}")
            print()


def generate_recommendations(events: List[dict]):
    """Generate debugging recommendations based on findings"""
    print("="*70)
    print("💡 RECOMMENDATIONS")
    print("="*70)

    graph_changes = [e for e in events if e['type'] == 'GRAPH_CHANGE']
    message_gaps = [e for e in events if e['type'] == 'MESSAGE_GAP']

    print()

    # Check correlation
    correlated_count = 0
    for gap in message_gaps:
        gap_time = gap['elapsed']
        for change in graph_changes:
            if abs(gap_time - change['elapsed']) <= 5.0:
                correlated_count += 1
                break

    if correlated_count > 0:
        print("🔍 Strong correlation between graph changes and message gaps detected!")
        print()
        print("Likely causes:")
        print("  1. DDS Discovery blocking - When new nodes join, DDS discovery can")
        print("     temporarily block callback processing")
        print("  2. Executor thread starvation - The ROS executor may be overloaded")
        print()
        print("Next steps:")
        print("  • Check ROS2 middleware (FastDDS vs CycloneDDS)")
        print("  • Review DDS discovery settings (SIMPLE vs STATIC)")
        print("  • Monitor executor thread usage")
        print("  • Consider separating critical topics to dedicated nodes")
        print()
        print("DDS tuning options:")
        print("  • Set RMW_IMPLEMENTATION=rmw_cyclonedds_cpp (often better for discovery)")
        print("  • Configure participant discovery peers for faster discovery")
        print("  • Increase executor thread pool if using MultiThreadedExecutor")
    else:
        print("ℹ️  No strong correlation between graph changes and gaps")
        print()
        print("Likely causes:")
        print("  1. Network issues - WiFi communication with plugs unstable")
        print("  2. Plug node performance - Individual plug nodes may be slow")
        print("  3. System resource contention - CPU/network saturation")
        print()
        print("Next steps:")
        print("  • Check WiFi signal strength to plugs")
        print("  • Monitor plug node CPU/memory usage")
        print("  • Review plug state publishing code for blocking operations")

    print()


def main():
    parser = argparse.ArgumentParser(
        description='Analyze onboarding diagnostics',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('log_file', help='Diagnostics log file from diagnose_onboarding_hiccup.py')

    args = parser.parse_args()

    try:
        events = load_events(args.log_file)
    except FileNotFoundError:
        print(f"❌ Error: Log file not found: {args.log_file}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in log file: {e}", file=sys.stderr)
        return 1

    if not events:
        print("❌ No events found in log file", file=sys.stderr)
        return 1

    print(f"\n📂 Analyzing: {args.log_file}")
    print(f"📊 Total events: {len(events)}\n")

    analyze_message_gaps(events)
    analyze_graph_changes(events)
    analyze_topic_rates(events)
    analyze_correlation(events)
    generate_recommendations(events)

    print("\n✅ Analysis complete\n")


if __name__ == '__main__':
    sys.exit(main())
