# RWS Live Timing Monitor Demo

## What It Looks Like

```
================================================================================
🚀 RWS Bridge Timing Monitor - LIVE MODE
================================================================================
📁 File: /tmp/rws_timing.log
🔄 Refresh: 1.0s | 📊 View: Bar Chart | ⏰ Window: 5min
⏱ Last update: 15:23:45

⏱ Average Time per Phase (Dispatch)
==============================================================================
Serialize        ████████████████████████████████  233.1μs (52.4%)
Ready Check      ████████████████████              168.9μs (38.0%)
Cache Check      ██                                 18.1μs ( 4.1%)
Send Request     █                                  12.3μs ( 2.8%)
Client Setup     █                                  12.1μs ( 2.7%)

📊 Average Total Dispatch: 444.6μs (0.44ms)
📈 Service Calls: 1,247

================================================================================
⌨ Controls: [1]Bar [2]Pie [3]Service [4]Percentiles [5]All | [w]Window [r]Reset [q]Quit
================================================================================
```

## Features

### 🎛 Interactive Controls
- **Press 1-5**: Switch between different visualization modes instantly
- **Press w**: Cycle through time windows (1min → 5min → 10min → 30min → all time)
- **Press r**: Reset accumulated data and start fresh
- **Press q**: Exit cleanly

### 📊 Multiple Views

#### 1. Bar Chart (Default)
Shows average time per phase with visual bars and percentages.

#### 2. Pie Chart
Shows time distribution as percentage slices.

#### 3. By Service
```
🎯 Average Dispatch Time by Service
==============================================================================
/robot/set_parameters    ████████████████████████  845.3μs
/robot/get_parameters    ████████████████          512.7μs
/robot/list_parameters   ██████████                345.1μs

📞 Call Counts:
  /robot/get_parameters                                   856 calls
  /robot/list_parameters                                  245 calls
  /robot/set_parameters                                   146 calls
```

#### 4. Percentile Statistics
```
📊 Percentile Statistics (microseconds)
====================================================================================
Phase              Count       Mean     Median        P95        P99        Max
------------------------------------------------------------------------------------
Cache Check         1247       45.3       42.0       78.0      125.0      385.0
Client Setup        1247       30.2       28.0       52.0       89.0      234.0
Ready Check         1247      152.7      145.0      245.0      398.0      892.0
Serialize           1247      154.8      148.0      267.0      445.0     1023.0
Send                1247       12.1       11.0       18.0       25.0       67.0
Total Dispatch      1247      395.1      385.0      612.0      943.0     1845.0
```

#### 5. All Views
Shows all visualizations stacked vertically for a complete picture.

### ⏰ Time Windows
Toggle with 'w' key to focus on recent data:
- **1min**: Last 60 seconds (real-time monitoring)
- **5min**: Last 5 minutes (short-term trends)
- **10min**: Last 10 minutes (medium-term analysis)
- **30min**: Last 30 minutes (longer trends)
- **all**: All accumulated data since start

### 🔄 Auto-Updates
- Continuously reads new log entries as they're written
- No need to restart - just keeps running
- Configurable refresh rate (default 1 second)

## Usage Examples

### Basic Monitoring
```bash
# Start monitoring with default 1-second refresh
./scripts/rws_timing_live.py /tmp/rws_timing.log
```

### Slower Refresh for Less CPU
```bash
# Update every 2 seconds instead
./scripts/rws_timing_live.py /tmp/rws_timing.log --refresh 2
```

### Workflow Example

1. **Start the bridge with timing logs:**
   ```bash
   ros2 launch rws rws_server_launch.py \
     enable_timing_logs:=True \
     timing_log_file:=/tmp/rws_timing.log
   ```

2. **In another terminal, start the monitor:**
   ```bash
   ./scripts/rws_timing_live.py /tmp/rws_timing.log
   ```

3. **Generate some load** (make service calls through the bridge)

4. **Watch the visualizations update in real-time!**

5. **Press different keys to explore:**
   - Press `1` to see bar chart
   - Press `3` to see which services are being called
   - Press `w` to focus on just the last 1 minute
   - Press `4` to check P95/P99 latencies

### Tips

- **Finding bottlenecks**: Use view 4 (percentiles) to see if P95/P99 are high
- **Service analysis**: Use view 3 to identify slow services
- **Real-time debugging**: Use 1-minute window during active debugging
- **Trend analysis**: Use 30-minute or "all time" window for longer trends
- **Performance testing**: Reset data with 'r', run test, then check results

## Why This Is Better Than Piping

**Old way (doesn't work well):**
```bash
tail -f /tmp/rws_timing.log | ./scripts/rws_timing_visualizer.py
# Problem: Static tool reads all at once, then exits
```

**New way (designed for live monitoring):**
```bash
./scripts/rws_timing_live.py /tmp/rws_timing.log
# Benefits:
# - Continuously updates
# - Interactive view switching
# - Time window filtering
# - No need to restart
```

## Technical Details

- **Non-blocking I/O**: Uses file seeking to efficiently read only new lines
- **Terminal control**: Uses termios for keyboard input without blocking
- **Clean shutdown**: Restores terminal state on exit
- **Error handling**: Creates log file if it doesn't exist yet
- **Memory efficient**: Can handle large log files by using time windows
