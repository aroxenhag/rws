# RWS Timing Visualization

Tools for analyzing where time is spent in the RWS bridge during service calls.

## Quick Start

### 1. Enable Timing Logs

Start the RWS server with timing logs enabled and writing to a file:

```bash
ros2 launch rws rws_server_launch.py \
  enable_timing_logs:=True \
  timing_log_file:=/tmp/rws_timing.log
```

### 2. Generate Load

Make some service calls through the bridge to generate timing data.

### 3. Visualize

```bash
# Show timing breakdown from last 10 minutes
./scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --minutes 10

# Show as pie chart
./scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --chart-type pie

# Show breakdown by service
./scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --by-service

# Show detailed percentile statistics
./scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --percentiles

# Live monitoring (updates as logs arrive)
tail -f /tmp/rws_timing.log | ./scripts/rws_timing_visualizer.py
```

## Example Output

### Bar Chart (Default)
```
Average Time per Phase (Dispatch)
============================================================================
Cache Check          ███                          45.3μs   (12.5%)
Client Setup         ██                           30.2μs   ( 8.3%)
Ready Check          ████████                    152.7μs   (42.1%)
Serialize            ████████                    154.8μs   (42.7%)
Send Request         █                            12.1μs   ( 3.3%)

Average Total Dispatch Time: 395.1μs (0.40ms)
Service Calls Analyzed: 1247
```

### Pie Chart
```
Average Time Distribution in Bridge (Dispatch Phase)
==================================================
Serialize        ████████████████████               42.7%
Ready Check      ████████████████████               42.1%
Cache Check      ███                                12.5%
Client Setup     ██                                  8.3%
Send Request     █                                   3.3%
```

### Percentile Statistics
```
Percentile Statistics (all values in microseconds)
================================================================================
Phase                   Count       Mean     Median        P95        P99        Max
--------------------------------------------------------------------------------
Cache Check              1247       45.3       42.0       78.0      125.0      385.0
Client Setup             1247       30.2       28.0       52.0       89.0      234.0
Ready Check              1247      152.7      145.0      245.0      398.0      892.0
Serialize                1247      154.8      148.0      267.0      445.0      1023.0
Send                     1247       12.1       11.0       18.0       25.0       67.0
Total Dispatch           1247      395.1      385.0      612.0      943.0     1845.0
```

### By Service
```
Average Dispatch Time by Service
============================================================================
/robot/set_parameters    ████████████████████████   845.3μs
/robot/get_parameters    ████████████████           512.7μs
/robot/list_parameters   ██████████                 345.1μs

Call Counts per Service:
  /robot/get_parameters                                   856 calls
  /robot/list_parameters                                  245 calls
  /robot/set_parameters                                   146 calls
```

## Log Format

The timing logs use a structured format for easy parsing:

```
2024-10-01 15:23:45.123 RWS_TIMING|trace_id=abc123|service=/robot/cmd|cache_check_us=45|client_setup_us=30|ready_check_us=152|serialize_us=154|send_us=12|total_dispatch_us=395
```

### Fields:
- **trace_id**: Unique identifier for request tracing
- **service**: Service name being called
- **cache_check_us**: Time to check service cache (microseconds)
- **client_setup_us**: Time to create/reuse ROS2 client (microseconds)
- **ready_check_us**: Time to check if service is ready (microseconds)
- **serialize_us**: Time to serialize request to ROS2 format (microseconds)
- **send_us**: Time to dispatch async request (microseconds)
- **total_dispatch_us**: Total synchronous time before async dispatch (microseconds)

Additional fields for response:
- **deserialize_us**: Time to deserialize response (microseconds)
- **total_e2e_ms**: Total end-to-end time (milliseconds)

## Use Cases

### Finding Bottlenecks
```bash
# Show percentiles to identify slow phases
./scripts/rws_timing_visualizer.py -f /tmp/rws_timing.log -p

# Check P99 times - if high, investigate that phase
```

### Monitoring Specific Services
```bash
# Show which services are slowest
./scripts/rws_timing_visualizer.py -f /tmp/rws_timing.log --by-service

# Filter logs for specific service
grep "/robot/cmd" /tmp/rws_timing.log | ./scripts/rws_timing_visualizer.py
```

### Time Window Analysis
```bash
# Only look at last 5 minutes during load test
./scripts/rws_timing_visualizer.py -f /tmp/rws_timing.log --minutes 5
```

### Live Monitoring (Interactive TUI) ⭐ NEW!
```bash
# Real-time interactive dashboard
./scripts/rws_timing_live.py /tmp/rws_timing.log

# Custom refresh rate (2 seconds)
./scripts/rws_timing_live.py /tmp/rws_timing.log --refresh 2

# Interactive controls while running:
#   1 - Bar chart view
#   2 - Pie chart view
#   3 - Service breakdown view
#   4 - Percentile statistics view
#   5 - All views at once
#   w - Toggle time window (1min/5min/10min/30min/all)
#   r - Reset data
#   q - Quit
```

### Static Analysis (One-time)
```bash
# Generate static report
./scripts/rws_timing_visualizer.py -f /tmp/rws_timing.log --minutes 1
```

## Performance Optimization Tips

Based on the visualization:

1. **High Cache Check Time (>100μs)**
   - Cache may be stale/refreshing frequently
   - Consider increasing `SERVICE_CACHE_MS` in client_handler.hpp

2. **High Client Setup Time (>50μs)**
   - Clients are being created frequently
   - Normal for first call to each service
   - If consistently high, check client reuse logic

3. **High Ready Check Time (>200μs)**
   - Services may not be available when called
   - Consider warming up service connections
   - Check if services are starting slowly

4. **High Serialize Time (>300μs)**
   - Large message payloads
   - Complex message types
   - Normal for certain message types (OK if P95 < 500μs)

5. **High Send Time (>50μs)**
   - Usually indicates system load
   - Should be consistently low (<20μs)

## Troubleshooting

### No Data Found
```bash
# Check if timing logs are enabled
ros2 param get /rws_server enable_timing_logs

# Check if log file is being written
ls -lh /tmp/rws_timing.log
tail /tmp/rws_timing.log
```

### Script Errors
```bash
# Ensure Python 3 is used
python3 --version

# Check for required dependencies (all standard library)
python3 -c "import argparse, re, statistics, datetime"
```

### Log File Growing Too Large
```bash
# Rotate logs periodically
logrotate /etc/logrotate.d/rws_timing

# Or manual rotation
mv /tmp/rws_timing.log /tmp/rws_timing.log.old
```

## Integration with Docker

When running in Docker:

```bash
# Pass timing log file path to docker
docker exec -u emoco emoco-devbox bash -c \
  "source /opt/ros/jazzy/setup.bash && \
   ros2 launch rws rws_server_launch.py \
   enable_timing_logs:=True \
   timing_log_file:=/tmp/rws_timing.log"

# Visualize from host
docker exec -u emoco emoco-devbox bash -c \
  "python3 /workspace/rws/scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log"
```
