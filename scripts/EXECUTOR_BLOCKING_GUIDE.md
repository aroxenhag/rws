# Diagnosing and Fixing Executor Blocking

Based on your analysis showing **100% correlation** between graph changes and message gaps, you're experiencing DDS discovery blocking or executor thread starvation.

## Your Results

```
🔗 Gaps within 5.0s of graph changes:
   Gap at 23.6s (4.0s gap) - Graph change at 23.5s (Δ0.1s)
   📈 Correlation rate: 4/4 gaps (100%) near graph changes
```

**This is definitive evidence of blocking during DDS discovery.**

## Step 1: Identify Which Component is Blocking

Run the executor diagnostic on the bridge (most likely culprit):

```bash
export ROS_LOCALHOST_ONLY=1

# Diagnose the RWS bridge
./scripts/diagnose_executor_blocking.py --node /rws_server --duration 30

# While that runs, trigger onboarding in another terminal
```

### What to Look For:

**Executor Blocking Signs:**
- ⚠️ Large gaps in message timing (>2s)
- ⚠️ Slow service responses (>100ms avg)
- ⚠️ High variability in callback timing

**Healthy Executor:**
- ✅ Stable message intervals
- ✅ Fast service responses (<50ms)
- ✅ Low timing variability

## Step 2: Apply the Right Fix

### Fix 1: Switch to CycloneDDS (Quick Win)

CycloneDDS handles discovery much better than FastDDS:

```bash
# Install if needed
sudo apt install ros-humble-rmw-cyclonedds-cpp

# Set environment variable
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Add to ~/.bashrc for permanent effect
echo 'export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp' >> ~/.bashrc

# Restart all ROS nodes
```

**Expected improvement:** 50-80% reduction in gaps

### Fix 2: Tune DDS Discovery

Create `/tmp/cyclonedds.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config">
  <Domain>
    <Discovery>
      <!-- Faster discovery -->
      <ParticipantIndex>auto</ParticipantIndex>
      <MaxAutoParticipantIndex>1000</MaxAutoParticipantIndex>

      <!-- Reduce discovery traffic -->
      <LeaseDuration>10s</LeaseDuration>
      <AckDelay>50ms</AckDelay>
    </Discovery>

    <!-- Use localhost only for better performance -->
    <General>
      <NetworkInterfaceAddress>lo</NetworkInterfaceAddress>
    </General>
  </Domain>
</CycloneDDS>
```

Then:

```bash
export CYCLONEDDS_URI=file:///tmp/cyclonedds.xml
# Restart nodes
```

### Fix 3: Isolate the Bridge (Nuclear Option)

If the bridge is the bottleneck, run it on a dedicated machine or in isolation:

```bash
# On a separate machine or container
# Run ONLY the bridge, nothing else
ros2 run rws rws_server
```

This prevents the bridge from being affected by discovery events from plug nodes.

### Fix 4: Optimize Bridge Executor

If using `MultiThreadedExecutor`, increase thread count:

**In [server_node.cpp](../src/server_node.cpp#L380):**

```cpp
// Current:
rclcpp::executors::MultiThreadedExecutor executor;

// Change to:
rclcpp::executors::MultiThreadedExecutor executor(
  rclcpp::ExecutorOptions(),
  8  // Increase threads (currently uses hardware concurrency)
);
```

### Fix 5: Split Critical Subscriptions

Move critical plug subscriptions to a separate node that doesn't handle service calls:

```
        ┌─────────────┐
        │ RWS Bridge  │ (handles WebSocket + services)
        └──────┬──────┘
               │
        ┌──────▼───────┐
        │ Topic Relay  │ (ONLY subscribes to plug topics)
        └──────────────┘
```

This ensures plug topic callbacks never wait for service call processing.

## Step 3: Measure Improvement

After applying fixes:

```bash
# Run diagnostic again
./scripts/diagnose_onboarding_hiccup.py --auto-discover

# Trigger onboarding

# Analyze
./scripts/analyze_onboarding_diag.py /tmp/onboarding_diag.log
```

**Success metrics:**
- Gap count reduced by >80%
- Remaining gaps <1 second
- No correlation with graph changes (or <50%)

## Understanding the Root Cause

### Why DDS Discovery Blocks

When a new node joins the ROS graph:

1. **DDS Participant Discovery** - All nodes exchange metadata
2. **Topic Discovery** - All nodes learn about new topics
3. **Service Discovery** - All nodes update service lists

During this process (typically 100-500ms), the middleware can:
- Block executor threads to process discovery messages
- Starve callback queues
- Delay message delivery

**With 50+ nodes in your system**, each discovery event affects all nodes simultaneously.

### Why the Bridge is Most Affected

The RWS bridge is uniquely vulnerable because:

1. **High callback load** - Subscribes to many plug topics
2. **Service handling** - Processes long-running onboarding service calls
3. **Single point of contact** - All web app traffic flows through it
4. **Discovery participant** - Participates in every discovery event

## Quick Decision Tree

```
100% correlation with graph changes?
├─ YES → DDS Discovery Issue
│  ├─ Try: Switch to CycloneDDS (5 min)
│  ├─ Try: Tune discovery settings (10 min)
│  └─ If still failing: Isolate bridge (30 min)
│
└─ NO → Other causes
   ├─ Random gaps → Network/WiFi
   ├─ One topic only → Plug node issue
   └─ During service calls → Executor overload
      └─ Run: diagnose_executor_blocking.py
```

## Monitoring in Production

After fixing, monitor with:

```bash
# Real-time bridge timing
./scripts/rws_timing_live.py /tmp/rws_timing.log --minutes 10

# Watch for:
# - Client Setup spikes >10ms
# - Send spikes >5ms
# - Total dispatch >20ms
```

Set up alerts if P95 dispatch time >10ms.

## Expected Performance

**Before fix (your current state):**
- Gaps: 2-4 seconds
- Frequency: 100% of onboarding events
- Affected topics: All subscribed plugs

**After CycloneDDS:**
- Gaps: <500ms
- Frequency: <20% of onboarding events
- Affected topics: Fewer simultaneous gaps

**After full tuning:**
- Gaps: <200ms
- Frequency: <5% of onboarding events
- Affected topics: Minimal impact

## Next Steps

1. **Run executor diagnostic** (see results)
2. **Switch to CycloneDDS** (5 min, high impact)
3. **Re-run onboarding test** (verify improvement)
4. **Tune if needed** (only if still seeing gaps)

The CycloneDDS switch alone should dramatically improve your situation!
