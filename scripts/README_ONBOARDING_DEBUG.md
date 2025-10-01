# Debugging Onboarding Hiccups

This guide helps diagnose why onboarding new smart plugs causes message gaps in your ROS system.

## Problem Description

When onboarding a new plug:
1. User selects a server via web page
2. Onboarder node creates a new ROS node for the plug
3. Onboarder creates a systemd service
4. **During this process, existing plug state messages experience gaps**

The gaps affect messages flowing through the RWS bridge to the web app.

## Quick Start

### 1. Collect Diagnostic Data

```bash
# Terminal 1: Start diagnostic monitoring
cd /Users/roxenhag/emoco-dev/rws
./scripts/diagnose_onboarding_hiccup.py \
  --topics /plug1/closed /plug2/closed /plug3/power \
  --output /tmp/onboarding_diag.log

# Terminal 2: Trigger onboarding through your web app
# (onboard a new plug)

# Terminal 1: Stop monitoring with Ctrl+C when done
```

### 2. Analyze Results

```bash
./scripts/analyze_onboarding_diag.py /tmp/onboarding_diag.log
```

The analysis will show:
- Correlation between graph changes and message gaps
- Timing of when new nodes/topics appear
- Recommendations for likely root causes

## What to Look For

### Scenario 1: Strong Correlation (DDS Discovery Issue)

**Symptoms:**
- Message gaps occur within 1-5s of new nodes appearing
- All topics affected simultaneously
- Brief gaps (1-3 seconds)

**Example output:**
```
🔗 Gaps within 5.0s of graph changes:
   ⚠️  Gap at 12.3s:
      Topic: /plug1/closed
      Gap duration: 2.1s
      Graph change at 12.1s (Δ0.2s):
         New nodes: /metering_socket_relay_abc123
         New topics: 7
```

**Root Cause:** DDS participant discovery is blocking callback execution

**Solutions:**

1. **Switch to CycloneDDS** (often handles discovery better):
   ```bash
   export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
   # Restart all ROS nodes
   ```

2. **Use static discovery** (if you know all nodes in advance):
   Create `cyclonedds.xml`:
   ```xml
   <?xml version="1.0" encoding="UTF-8" ?>
   <CycloneDDS>
     <Domain>
       <Discovery>
         <ParticipantIndex>auto</ParticipantIndex>
         <MaxAutoParticipantIndex>1000</MaxAutoParticipantIndex>
       </Discovery>
     </Domain>
   </CycloneDDS>
   ```
   Then: `export CYCLONEDDS_URI=file:///path/to/cyclonedds.xml`

3. **Isolate bridge node** - Run the bridge on a separate machine to avoid discovery overhead

4. **Increase executor threads** in the bridge (already using MultiThreadedExecutor, but may need tuning)

### Scenario 2: No Correlation (Other Causes)

**Symptoms:**
- Gaps occur randomly, not tied to graph changes
- Single topics affected
- Variable gap durations

**Possible causes:**

1. **WiFi network issues**
   ```bash
   # Check ping times to plug IPs
   ping -c 100 <plug_ip> | grep -E 'time=|loss'
   ```

2. **Plug node blocking**
   - Review [esp_power_meter.py](../../esp32-power-meter/emoco_device_node/emoco_device_node/esp_power_meter.py)
   - Look for blocking I/O in callbacks
   - Check if eRPC transport is blocking

3. **System resource exhaustion**
   - Check the diagnostic output for CPU/memory spikes
   - Look for network saturation

## Architecture Review

### How Messages Flow

```
Plug (WiFi) → Plug Node → ROS Topic → Bridge → WebSocket → Web App
                   ↓
            (Publishes at ~1Hz)
```

### Where Blocking Can Occur

1. **DDS Discovery (system-wide)**
   - When new node joins, ALL nodes update their participant lists
   - Can briefly block executor callbacks
   - Impact: All subscriptions delayed simultaneously

2. **Bridge Node (single point of failure)**
   - Bridge subscribes to all plug topics
   - All subscriptions run on same ROS node
   - If node blocks, all topics affected
   - Fix: Already using MultiThreadedExecutor ✓

3. **Onboarder Node (isolated)**
   - Blocking in onboarder should NOT affect plug nodes
   - Bug found: Busy-wait loop in service callback (see below)
   - Impact: Only affects onboarder's own responsiveness

4. **Individual Plug Nodes (isolated)**
   - Each plug is a separate ROS node
   - One slow plug shouldn't affect others
   - Exception: WiFi network saturation

## Known Issues

### Onboarder Blocking Bug

Location: [device_manager_node.py:78-89](../../onboarder-node/emoco_device_manager/emoco_device_manager/device_manager_node.py#L78-L89)

```python
def check_if_running(self, request, response):
    # ...
    while self.async_thread_is_running:  # 🚨 BLOCKS EXECUTOR
        time.sleep(0.1)
```

**Impact:** Only affects the onboarder node itself - shouldn't affect plug nodes or bridge

**But:** If your web app polls the onboarder status through the bridge, those service calls will be slow/blocked, potentially causing bridge executor contention.

## Advanced Debugging

### Monitor DDS Traffic

```bash
# With FastDDS
fastdds discovery -i 5

# With CycloneDDS
# Enable debug logging
export CYCLONEDDS_LOG_LEVEL=trace
export CYCLONEDDS_LOG_FILE=/tmp/dds_discovery.log
```

### Profile ROS Node

```bash
# Install ros2_tracing
sudo apt install ros-humble-ros2-tracing

# Trace during onboarding
ros2 trace --session-name onboarding

# In another terminal, trigger onboarding

# Stop trace and analyze
ros2 trace stop onboarding
babeltrace ~/.ros/tracing/onboarding
```

### Monitor Bridge Timing

If you've enabled bridge timing logs:

```bash
# Start bridge with timing
ros2 run rws rws_server --ros-args \
  -p enable_timing_logs:=true \
  -p timing_log_file:=/tmp/bridge_timing.log

# Monitor live
./scripts/rws_timing_live.py /tmp/bridge_timing.log --minutes 5
```

Look for:
- Service call latency spikes during onboarding
- Queue depth buildup
- Callback execution delays

## Testing the Fix

After making changes:

```bash
# 1. Start monitoring
./scripts/diagnose_onboarding_hiccup.py \
  --topics /plug1/closed /plug2/closed \
  --output /tmp/test_after_fix.log

# 2. Onboard 3 plugs in sequence

# 3. Analyze
./scripts/analyze_onboarding_diag.py /tmp/test_after_fix.log

# 4. Compare gap counts before/after
```

**Success criteria:**
- Gap count reduced by >80%
- Remaining gaps <1 second duration
- No correlation with graph changes

## Quick Reference

### Diagnostic Scripts

| Script | Purpose | Output |
|--------|---------|--------|
| `diagnose_onboarding_hiccup.py` | Real-time monitoring | Event log (JSON) |
| `analyze_onboarding_diag.py` | Post-analysis | Human-readable report |
| `rws_timing_live.py` | Bridge performance | Live TUI visualization |

### Key Files

| Component | File | Purpose |
|-----------|------|---------|
| RWS Bridge | [client_handler.cpp](../src/client_handler.cpp) | Handles subscriptions/services |
| Onboarder | [device_manager_node.py](../../onboarder-node/emoco_device_manager/emoco_device_manager/device_manager_node.py) | Creates new plug nodes |
| Plug Node | [esp_power_meter.py](../../esp32-power-meter/emoco_device_node/emoco_device_node/esp_power_meter.py) | Publishes plug state |

### ROS2 Environment Variables

```bash
# Use CycloneDDS
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Use FastDDS
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

# DDS Discovery tuning (CycloneDDS)
export CYCLONEDDS_URI=file:///path/to/config.xml

# Enable DDS logging
export CYCLONEDDS_LOG_LEVEL=debug
export FASTRTPS_DEFAULT_PROFILES_FILE=/path/to/profile.xml
```

## Getting Help

If gaps persist after trying these solutions:

1. Share the diagnostic output:
   ```bash
   ./scripts/analyze_onboarding_diag.py /tmp/onboarding_diag.log > report.txt
   ```

2. Include ROS environment info:
   ```bash
   ros2 doctor --report > ros_env.txt
   echo "RMW: $RMW_IMPLEMENTATION" >> ros_env.txt
   ```

3. Capture DDS discovery during onboarding:
   ```bash
   export CYCLONEDDS_LOG_LEVEL=info
   # Capture discovery events to file
   ```

4. Open an issue with:
   - `report.txt`
   - `ros_env.txt`
   - DDS logs
   - Rough estimate of: # of plugs, onboarding frequency, network topology
